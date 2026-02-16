"""Embedding client — talks to Ollama for vector embeddings."""

import httpx
import numpy as np

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "nomic-embed-text"


class EmbeddingClient:
    """Generate embeddings via Ollama's /api/embed endpoint."""

    def __init__(self, ollama_url: str = DEFAULT_OLLAMA_URL, model: str = DEFAULT_MODEL):
        self.ollama_url = ollama_url.rstrip("/")
        self.model = model
        self._client = httpx.Client(timeout=30.0)

    def embed(self, text: str) -> np.ndarray:
        """Embed a single text string. Returns a float32 numpy array."""
        resp = self._client.post(
            f"{self.ollama_url}/api/embed",
            json={"model": self.model, "input": text},
        )
        resp.raise_for_status()
        data = resp.json()
        return np.array(data["embeddings"][0], dtype=np.float32)

    def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        """Embed multiple texts in one call."""
        resp = self._client.post(
            f"{self.ollama_url}/api/embed",
            json={"model": self.model, "input": texts},
        )
        resp.raise_for_status()
        data = resp.json()
        return [np.array(emb, dtype=np.float32) for emb in data["embeddings"]]

    def is_available(self) -> bool:
        """Check if Ollama is reachable."""
        try:
            resp = self._client.get(f"{self.ollama_url}/api/tags", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False
