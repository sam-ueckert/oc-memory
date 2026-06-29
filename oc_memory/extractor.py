"""Memory extraction — uses local LLM via Ollama to parse conversations into cells."""

import json
import re

import httpx

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.2:3b"


class MemoryExtractor:
    def __init__(self, ollama_url: str = DEFAULT_OLLAMA_URL, model: str = DEFAULT_MODEL):
        self.ollama_url = ollama_url.rstrip("/")
        self.model = model
        self._client = httpx.Client(timeout=120.0)

    def extract_cells(self, text: str, source: str = "") -> list[dict]:
        """Extract structured memory cells from text (conversation, notes, etc)."""
        prompt = f"""Extract key facts from the following text as a JSON array. Each item needs:
- "scene": topic name (lowercase, short, e.g. "infrastructure", "health", "projects")
- "cell_type": one of: fact, decision, preference, task, risk, plan, lesson
- "salience": 0.0-1.0 importance score. Score high (0.8-1.0) for: personal health info, key decisions, security-critical facts. Score medium (0.5-0.7) for: technical details, routine tasks. Score low (0.1-0.4) for: transient info, small talk.
- "content": compressed factual statement (1-2 sentences max)

Text:
{text}

JSON array:"""

        resp = self._client.post(
            f"{self.ollama_url}/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 2000},
            },
        )
        resp.raise_for_status()
        raw = resp.json()["response"]

        # Clean up response — extract JSON array from potentially chatty output
        raw = re.sub(r"```json\s*|```\s*", "", raw).strip()

        # Try direct parse first
        try:
            cells = json.loads(raw)
            if isinstance(cells, list):
                for cell in cells:
                    cell["source"] = source
                return cells
        except json.JSONDecodeError:
            pass

        # Try to find array in response
        match = re.search(r'\[.*\]', raw, re.DOTALL)
        if match:
            try:
                cells = json.loads(match.group())
                if isinstance(cells, list):
                    for cell in cells:
                        cell["source"] = source
                    return cells
            except json.JSONDecodeError:
                pass

        return []

    def generate_summary(self, cells: list[dict]) -> str:
        """Generate a scene summary from cells."""
        cell_text = "\n".join(
            f"- [{c.get('cell_type', 'fact')}] {c.get('content', '')}" for c in cells[:15]
        )
        prompt = f"""Summarize these memory cells into a single coherent paragraph under 80 words.
Keep it factual and reusable for future reasoning.

Cells:
{cell_text}

Summary:"""

        resp = self._client.post(
            f"{self.ollama_url}/api/generate",
            json={
                "model": self.model,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.05, "num_predict": 200},
            },
        )
        resp.raise_for_status()
        return resp.json()["response"].strip()

    def is_available(self) -> bool:
        try:
            resp = self._client.get(f"{self.ollama_url}/api/tags", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False
