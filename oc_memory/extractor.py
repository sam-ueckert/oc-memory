"""Memory extraction — uses local LLM via Ollama to parse conversations into cells.

Two API paths are supported:
  - /api/generate  (default, llama3.2 style): flat prompt, plain text response
  - /api/chat      (Hermes/ChatML style): system+user roles, optional JSON format enforcement

Nous Hermes and Hermes 3 models are auto-detected by model name prefix and
routed to the chat API path with role-separated prompts and JSON format mode,
matching Hermes's ChatML training format for best extraction quality.

Environment variables:
  OC_MEMORY_EXTRACTOR_MODEL  Override extraction model (default: llama3.2:3b)
  OC_MEMORY_EXTRACTOR_API    Force API path: "chat" | "generate" (default: auto)
  OC_MEMORY_EXTRACTOR_JSON   Force JSON format mode: "1" | "true" (default: auto)
"""

import json
import re
from typing import Optional

import httpx

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.2:3b"

# Model name prefixes that use ChatML and benefit from the chat API path
HERMES_MODEL_PREFIXES = ("nous-hermes", "hermes")

# ── Prompt strings ─────────────────────────────────────────────────────────────

_EXTRACT_FLAT_PROMPT = """\
Extract key facts from the following text as a JSON array. Each item needs:
- "scene": topic name (lowercase, short, e.g. "infrastructure", "health", "projects")
- "cell_type": one of: fact, decision, preference, task, risk, plan, lesson
- "salience": 0.0-1.0 importance score. Score high (0.8-1.0) for: personal health info, \
key decisions, security-critical facts. Score medium (0.5-0.7) for: technical details, \
routine tasks. Score low (0.1-0.4) for: transient info, small talk.
- "content": compressed factual statement (1-2 sentences max)

Text:
{text}

JSON array:"""

_EXTRACT_SYSTEM_PROMPT = """\
You are a memory extraction system. Output ONLY a valid JSON array — no explanation, \
no markdown fences, no preamble.

Each element of the array must be a JSON object with exactly these fields:
- "scene": short topic label, lowercase, 1-2 words (e.g. "infrastructure", "health", "workflow")
- "cell_type": one of: fact, decision, preference, task, risk, plan, lesson
- "salience": float 0.0-1.0 (0.8-1.0 = critical; 0.5-0.7 = important; 0.1-0.4 = minor)
- "content": concise self-contained factual statement, 1-2 sentences

Rules:
- Return ONLY the JSON array.
- Extract 3-12 items. Quality over quantity.
- Each item must be understandable without the original text.
- Skip heartbeats, pleasantries, and temporary/transient context."""

_EXTRACT_USER_TEMPLATE = "Extract structured memories from this text:\n\n{text}"

_SUMMARY_FLAT_PROMPT = """\
Summarize these memory cells into a single coherent paragraph under 80 words.
Keep it factual and reusable for future reasoning.

Cells:
{cells}

Summary:"""

_SUMMARY_SYSTEM_PROMPT = """\
Summarize a list of memory cells into a single factual paragraph under 80 words.
Output ONLY the summary text. No preamble, no labels, no explanation."""

_SUMMARY_USER_TEMPLATE = "Summarize these memory cells:\n\n{cells}"


class MemoryExtractor:
    """Extract structured memory cells from text using a local Ollama LLM.

    Automatically uses the Ollama chat API (/api/chat) with role-separated
    system/user messages when a Nous Hermes or Hermes 3 model is detected,
    matching their ChatML training format. All other models default to the
    simpler /api/generate path.

    Args:
        ollama_url:    Ollama server URL (default: http://localhost:11434)
        model:         Model name (default: llama3.2:3b)
        use_chat_api:  True → /api/chat, False → /api/generate, None → auto-detect
        json_format:   True → set Ollama format=json (Hermes Pro/Hermes 3 only)
    """

    def __init__(
        self,
        ollama_url: str = DEFAULT_OLLAMA_URL,
        model: str = DEFAULT_MODEL,
        use_chat_api: Optional[bool] = None,
        json_format: Optional[bool] = None,
    ):
        self.ollama_url = ollama_url.rstrip("/")
        self.model = model

        # Match both bare names ("hermes3") and namespaced names ("user/nous-hermes2:tag")
        model_base = model.lower().split("/")[-1].split(":")[0]
        is_hermes = any(model_base.startswith(p) for p in HERMES_MODEL_PREFIXES)
        self.use_chat_api = use_chat_api if use_chat_api is not None else is_hermes
        self.json_format = json_format if json_format is not None else is_hermes

        self._client = httpx.Client(timeout=120.0)

    # ── Private API callers ────────────────────────────────────────────────────

    def _call_generate(self, prompt: str, max_tokens: int = 2000, temperature: float = 0.1) -> str:
        resp = self._client.post(
            f"{self.ollama_url}/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": temperature, "num_predict": max_tokens},
            },
        )
        resp.raise_for_status()
        return resp.json()["response"]

    def _call_chat(
        self,
        system: str,
        user: str,
        max_tokens: int = 2000,
        temperature: float = 0.1,
    ) -> str:
        payload: dict = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {"temperature": temperature, "num_predict": max_tokens},
        }
        if self.json_format:
            # Hermes 2 Pro and Hermes 3 support Ollama's format=json for strict JSON output.
            # This prevents natural-language preamble but may wrap the array in an object;
            # _parse_cells handles both array and common object-wrapped forms.
            payload["format"] = "json"
        resp = self._client.post(f"{self.ollama_url}/api/chat", json=payload)
        resp.raise_for_status()
        return resp.json()["message"]["content"]

    # ── JSON parsing ───────────────────────────────────────────────────────────

    def _parse_cells(self, raw: str, source: str) -> list[dict]:
        """Parse a JSON array of memory cells from model output.

        Handles:
        - Bare JSON array:          [{"scene": ...}, ...]
        - Markdown fenced:          ```json\n[...]\n```
        - Object-wrapped (Hermes):  {"items": [...]} / {"memories": [...]} / {"cells": [...]}
        - Embedded array:           ...prose... [...] ...prose...
        """
        raw = re.sub(r"```json\s*|```\s*", "", raw).strip()

        cells = None

        # Attempt full parse
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                cells = parsed
            elif isinstance(parsed, dict):
                for key in ("items", "memories", "cells", "results", "data"):
                    if isinstance(parsed.get(key), list):
                        cells = parsed[key]
                        break
        except json.JSONDecodeError:
            pass

        # Fall back to finding the first [...] block in the text
        if cells is None:
            match = re.search(r"\[.*?\]", raw, re.DOTALL)
            if match:
                try:
                    parsed = json.loads(match.group())
                    if isinstance(parsed, list):
                        cells = parsed
                except json.JSONDecodeError:
                    pass

        if not cells:
            return []

        for cell in cells:
            cell["source"] = source
        return cells

    # ── Public API ─────────────────────────────────────────────────────────────

    def extract_cells(self, text: str, source: str = "") -> list[dict]:
        """Extract structured memory cells from text (conversation, notes, etc)."""
        if self.use_chat_api:
            raw = self._call_chat(
                system=_EXTRACT_SYSTEM_PROMPT,
                user=_EXTRACT_USER_TEMPLATE.format(text=text),
            )
        else:
            raw = self._call_generate(_EXTRACT_FLAT_PROMPT.format(text=text))

        return self._parse_cells(raw, source)

    def generate_summary(self, cells: list[dict]) -> str:
        """Generate a scene summary from a list of memory cells."""
        cell_text = "\n".join(
            f"- [{c.get('cell_type', 'fact')}] {c.get('content', '')}" for c in cells[:15]
        )
        if self.use_chat_api:
            return self._call_chat(
                system=_SUMMARY_SYSTEM_PROMPT,
                user=_SUMMARY_USER_TEMPLATE.format(cells=cell_text),
                max_tokens=200,
                temperature=0.05,
            ).strip()
        else:
            return self._call_generate(
                _SUMMARY_FLAT_PROMPT.format(cells=cell_text),
                max_tokens=200,
                temperature=0.05,
            ).strip()

    def is_available(self) -> bool:
        try:
            resp = self._client.get(f"{self.ollama_url}/api/tags", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False
