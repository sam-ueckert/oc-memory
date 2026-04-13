"""Embedding client — backward-compatible wrapper that delegates to the active backend.

The original implementation talked directly to Ollama. This version keeps the same
EmbeddingClient API but internally uses `get_backend()` so callers transparently get
the ONNX backend (or Ollama if configured).
"""

from __future__ import annotations

import subprocess
from typing import Optional

import numpy as np

from .embedding_backends import (
    EmbeddingBackend,
    OllamaBackend,
    get_backend,
)

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "nomic-embed-text"


class EmbeddingClient:
    """Backward-compatible embedding client.

    Wraps the active backend (ONNX by default, Ollama if configured).
    Existing callers that instantiate EmbeddingClient(ollama_url=..., model=...)
    will continue to work — when ollama_url/model are provided they select the
    Ollama backend explicitly.
    """

    def __init__(
        self,
        ollama_url: Optional[str] = None,
        model: Optional[str] = None,
        backend: Optional[EmbeddingBackend] = None,
    ):
        if backend is not None:
            # Explicit backend injection (e.g., for testing)
            self._backend = backend
        elif ollama_url is not None or model is not None:
            # Legacy: caller passed Ollama params explicitly → use Ollama backend
            self._backend = OllamaBackend(
                ollama_url=ollama_url or DEFAULT_OLLAMA_URL,
                model=model or DEFAULT_MODEL,
            )
        else:
            # Default: use configured backend (ONNX or env-selected)
            self._backend = get_backend()

    @property
    def backend(self) -> EmbeddingBackend:
        return self._backend

    def embed(self, text: str) -> np.ndarray:
        """Embed a single text string."""
        return self._backend.embed(text)

    def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        """Embed multiple texts."""
        return self._backend.embed_batch(texts)

    def is_available(self) -> bool:
        """Check if the active backend can produce embeddings."""
        return self._backend.is_available()


def ensure_ollama_running(ssh_host: str = "localhost") -> bool:
    """Check if Ollama is running on remote host, start if not."""
    try:
        result = subprocess.run(
            [
                "ssh",
                ssh_host,
                "systemctl --user is-active ollama 2>/dev/null || "
                "pgrep -f 'ollama serve' > /dev/null && echo active || echo inactive",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if "active" in result.stdout:
            return True

        # Try to start it
        subprocess.run(
            ["ssh", ssh_host, "nohup ollama serve > /tmp/ollama.log 2>&1 &"],
            capture_output=True,
            timeout=10,
        )
        return True
    except Exception:
        return False
