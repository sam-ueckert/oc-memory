"""Memory extraction — uses local LLM via Ollama to parse text into memory cells."""

import json
import re

import httpx

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "llama3.2:3b"


class MemoryExtractor:
    """Extract structured memory cells from freeform text using a local LLM."""

    def __init__(self, ollama_url: str = DEFAULT_OLLAMA_URL, model: str = DEFAULT_MODEL):
        self.ollama_url = ollama_url.rstrip("/")
        self.model = model
        self._client = httpx.Client(timeout=120.0)

    def extract_cells(self, text: str, source: str = "") -> list[dict]:
        """Extract structured memory cells from text.

        Returns a list of dicts with keys: scene, cell_type, salience, content, source.
        """
        prompt = f"""You are a memory extraction system. Convert the following text into structured memory cells.

Return ONLY a JSON array. Each object must have:
- "scene": topic/category name (lowercase, short, e.g. "infrastructure", "health", "projects")
- "cell_type": one of: fact, decision, preference, task, risk, plan, lesson
- "salience": 0.0-1.0 importance score. Score high (0.8-1.0) for: personal info, key decisions, security-critical facts, strong preferences. Score medium (0.5-0.7) for: technical details, routine tasks, general info. Score low (0.1-0.4) for: transient info, small talk, already-known facts.
- "content": compressed factual statement (1-2 sentences max)

Text to extract from:
{text}

Return ONLY the JSON array, no other text."""

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

        raw = re.sub(r"```json\s*|```\s*", "", raw).strip()

        try:
            cells = json.loads(raw)
            if not isinstance(cells, list):
                return []
            for cell in cells:
                cell["source"] = source
            return cells
        except json.JSONDecodeError:
            return []

    def generate_summary(self, cells: list[dict]) -> str:
        """Generate a scene summary from a list of cells."""
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
        """Check if Ollama + the configured model are reachable."""
        try:
            resp = self._client.get(f"{self.ollama_url}/api/tags", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False
