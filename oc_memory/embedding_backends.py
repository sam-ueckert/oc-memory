"""Embedding backends — abstract interface with ONNX (default) and Ollama (fallback/opt-in)."""

from __future__ import annotations

import os
import ssl
import urllib.request
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

import numpy as np

# ── Constants ─────────────────────────────────────────────────────────────────

MODEL_CACHE_DIR = Path.home() / ".cache" / "oc-memory" / "models" / "bge-small-en-v1.5-onnx"

# HuggingFace ONNX model files for BAAI/bge-small-en-v1.5
HF_BASE = "https://huggingface.co/BAAI/bge-small-en-v1.5/resolve/main"
MODEL_FILES = {
    "model.onnx": f"{HF_BASE}/onnx/model.onnx",
    "tokenizer.json": f"{HF_BASE}/tokenizer.json",
    "tokenizer_config.json": f"{HF_BASE}/tokenizer_config.json",
    "special_tokens_map.json": f"{HF_BASE}/special_tokens_map.json",
    "config.json": f"{HF_BASE}/config.json",
}

ONNX_EMBEDDING_DIM = 384


# ── Abstract base ─────────────────────────────────────────────────────────────


class EmbeddingBackend(ABC):
    """Abstract interface for embedding backends."""

    @abstractmethod
    def embed(self, text: str) -> np.ndarray:
        """Embed a single text string. Returns float32 array."""

    @abstractmethod
    def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        """Embed multiple texts. Returns list of float32 arrays."""

    @abstractmethod
    def is_available(self) -> bool:
        """Return True if this backend can produce embeddings right now."""

    @property
    @abstractmethod
    def dim(self) -> int:
        """Dimensionality of embeddings produced by this backend."""


# ── ONNX backend ──────────────────────────────────────────────────────────────


def _make_ssl_context() -> ssl.SSLContext | None:
    """Return an SSL context that respects corporate proxy CA bundles.

    Honours (in priority order):
      1. OC_MEMORY_SSL_NO_VERIFY=1  — disable verification entirely (last resort)
      2. REQUESTS_CA_BUNDLE / SSL_CERT_FILE — path to a custom CA bundle
      3. System default CAs (certifi if installed, otherwise platform CAs)
    """
    if os.environ.get("OC_MEMORY_SSL_NO_VERIFY", "").strip() == "1":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    ca_bundle = os.environ.get("REQUESTS_CA_BUNDLE") or os.environ.get("SSL_CERT_FILE")
    if ca_bundle:
        ctx = ssl.create_default_context(cafile=ca_bundle)
        return ctx

    # Try certifi for a well-maintained CA bundle
    try:
        import certifi  # type: ignore
        ctx = ssl.create_default_context(cafile=certifi.where())
        return ctx
    except ImportError:
        pass

    return None  # fall back to urllib default


def _show_download_progress(block_num: int, block_size: int, total_size: int) -> None:
    downloaded = block_num * block_size
    if total_size > 0:
        pct = min(100, downloaded * 100 // total_size)
        mb = downloaded / 1024 / 1024
        total_mb = total_size / 1024 / 1024
        print(f"\r  {mb:.1f}/{total_mb:.1f} MB ({pct}%)", end="", flush=True)


def download_onnx_model(cache_dir: Path = MODEL_CACHE_DIR) -> Path:
    """Download bge-small-en-v1.5 ONNX model to cache_dir if not already present.

    Returns the cache_dir path.
    """
    onnx_dir = cache_dir
    onnx_dir.mkdir(parents=True, exist_ok=True)

    ssl_ctx = _make_ssl_context()
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl_ctx) if ssl_ctx else urllib.request.HTTPSHandler()
    )

    for filename, url in MODEL_FILES.items():
        dest = onnx_dir / filename
        if dest.exists():
            continue
        print(f"Downloading {filename}...")
        try:
            req = urllib.request.Request(url)
            with opener.open(req) as resp, open(dest, "wb") as fh:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                block = 8192
                while chunk := resp.read(block):
                    fh.write(chunk)
                    downloaded += len(chunk)
                    _show_download_progress(downloaded // block, block, total)
            print()  # newline after progress
        except Exception as exc:
            # Clean up partial download
            if dest.exists():
                dest.unlink()
            raise RuntimeError(f"Failed to download {filename} from {url}: {exc}") from exc

    return onnx_dir


def is_model_downloaded(cache_dir: Path = MODEL_CACHE_DIR) -> bool:
    """Return True if the ONNX model files are already present."""
    required = {"model.onnx", "tokenizer.json", "tokenizer_config.json"}
    return all((cache_dir / f).exists() for f in required)


class ONNXBackend(EmbeddingBackend):
    """CPU-only ONNX embedding backend using bge-small-en-v1.5 (384-dim).

    Auto-downloads model files on first use if not already cached.
    """

    def __init__(self, cache_dir: Path = MODEL_CACHE_DIR, auto_download: bool = True):
        self._cache_dir = cache_dir
        self._auto_download = auto_download
        self._session = None
        self._tokenizer = None

    def _ensure_loaded(self) -> None:
        if self._session is not None:
            return

        if not is_model_downloaded(self._cache_dir):
            if self._auto_download:
                print("bge-small-en-v1.5 ONNX model not found — downloading...")
                download_onnx_model(self._cache_dir)
            else:
                raise RuntimeError(
                    f"ONNX model not found at {self._cache_dir}. "
                    "Set auto_download=True or run download_onnx_model()."
                )

        try:
            import onnxruntime as ort  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "onnxruntime is required for ONNXBackend. Install it: pip install onnxruntime"
            ) from exc

        try:
            from tokenizers import Tokenizer  # type: ignore
        except ImportError as exc:
            raise ImportError(
                "tokenizers is required for ONNXBackend. Install it: pip install tokenizers"
            ) from exc

        model_path = self._cache_dir / "model.onnx"
        tokenizer_path = self._cache_dir / "tokenizer.json"

        opts = ort.SessionOptions()
        opts.inter_op_num_threads = 1
        opts.intra_op_num_threads = max(1, (os.cpu_count() or 2) // 2)

        self._session = ort.InferenceSession(
            str(model_path),
            sess_options=opts,
            providers=["CPUExecutionProvider"],
        )
        self._tokenizer = Tokenizer.from_file(str(tokenizer_path))
        self._tokenizer.enable_padding(
            pad_id=0,
            pad_token="[PAD]",
            length=None,  # dynamic per batch
        )
        self._tokenizer.enable_truncation(max_length=512)

    @property
    def dim(self) -> int:
        return ONNX_EMBEDDING_DIM

    def is_available(self) -> bool:
        try:
            import onnxruntime  # noqa: F401  # type: ignore
            from tokenizers import Tokenizer  # noqa: F401  # type: ignore
            return True
        except ImportError:
            return False

    def _run_inference(self, texts: list[str]) -> list[np.ndarray]:
        self._ensure_loaded()
        encodings = self._tokenizer.encode_batch(texts)

        input_ids = np.array([enc.ids for enc in encodings], dtype=np.int64)
        attention_mask = np.array([enc.attention_mask for enc in encodings], dtype=np.int64)
        token_type_ids = np.zeros_like(input_ids, dtype=np.int64)

        outputs = self._session.run(
            None,
            {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": token_type_ids,
            },
        )

        # outputs[0] is last_hidden_state: (batch, seq_len, 384)
        # Mean pool over non-padding tokens, then L2-normalize
        hidden = outputs[0]  # (B, T, D)
        mask = attention_mask[:, :, np.newaxis].astype(np.float32)  # (B, T, 1)
        summed = (hidden * mask).sum(axis=1)  # (B, D)
        counts = mask.sum(axis=1).clip(min=1e-9)  # (B, 1)
        pooled = summed / counts  # (B, D)

        # L2 normalize
        norms = np.linalg.norm(pooled, axis=1, keepdims=True).clip(min=1e-9)
        normalized = pooled / norms

        return [normalized[i].astype(np.float32) for i in range(len(texts))]

    def embed(self, text: str) -> np.ndarray:
        return self._run_inference([text])[0]

    def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        if not texts:
            return []
        return self._run_inference(texts)


# ── Ollama backend ────────────────────────────────────────────────────────────


DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "nomic-embed-text"
OLLAMA_EMBEDDING_DIM = 768  # nomic-embed-text default


class OllamaBackend(EmbeddingBackend):
    """Ollama-based embedding backend (wraps httpx calls to Ollama API)."""

    def __init__(
        self,
        ollama_url: str = DEFAULT_OLLAMA_URL,
        model: str = DEFAULT_OLLAMA_MODEL,
        timeout: float = 30.0,
    ):
        self._ollama_url = ollama_url.rstrip("/")
        self._model = model
        self._timeout = timeout
        self._client = None

    def _get_client(self):
        if self._client is None:
            try:
                import httpx  # type: ignore
            except ImportError as exc:
                raise ImportError(
                    "httpx is required for OllamaBackend. "
                    "Install it: pip install 'oc-memory[ollama]'"
                ) from exc
            import httpx
            self._client = httpx.Client(timeout=self._timeout)
        return self._client

    @property
    def dim(self) -> int:
        # nomic-embed-text produces 768-dim; we don't know the exact dim without querying
        return OLLAMA_EMBEDDING_DIM

    def is_available(self) -> bool:
        try:
            client = self._get_client()
            resp = client.get(f"{self._ollama_url}/api/tags", timeout=5.0)
            return resp.status_code == 200
        except Exception:
            return False

    def embed(self, text: str) -> np.ndarray:
        client = self._get_client()
        resp = client.post(
            f"{self._ollama_url}/api/embed",
            json={"model": self._model, "input": text},
        )
        resp.raise_for_status()
        data = resp.json()
        return np.array(data["embeddings"][0], dtype=np.float32)

    def embed_batch(self, texts: list[str]) -> list[np.ndarray]:
        if not texts:
            return []
        client = self._get_client()
        resp = client.post(
            f"{self._ollama_url}/api/embed",
            json={"model": self._model, "input": texts},
        )
        resp.raise_for_status()
        data = resp.json()
        return [np.array(emb, dtype=np.float32) for emb in data["embeddings"]]


# ── Factory ───────────────────────────────────────────────────────────────────


def get_backend(config: Optional[dict] = None) -> EmbeddingBackend:
    """Return the appropriate embedding backend.

    Selection logic:
    1. If OC_MEMORY_BACKEND=ollama → OllamaBackend (reads OLLAMA_URL)
    2. Otherwise → ONNXBackend (default)
       - If ONNX model not downloaded yet and Ollama is available, falls back to Ollama
       - If neither works, raises a clear error

    Args:
        config: Optional dict with keys:
            - backend: "onnx" | "ollama" (overrides env var)
            - ollama_url: str
            - ollama_model: str
            - onnx_cache_dir: Path | str
    """
    cfg = config or {}

    # Determine requested backend
    backend_env = os.environ.get("OC_MEMORY_BACKEND", "").lower()
    requested = cfg.get("backend", backend_env or "onnx").lower()

    ollama_url = cfg.get("ollama_url", os.environ.get("OLLAMA_URL", DEFAULT_OLLAMA_URL))
    ollama_model = cfg.get("ollama_model", DEFAULT_OLLAMA_MODEL)
    onnx_cache_dir = Path(cfg.get("onnx_cache_dir", MODEL_CACHE_DIR))

    if requested == "ollama":
        return OllamaBackend(ollama_url=ollama_url, model=ollama_model)

    # Default: ONNX — with graceful fallback
    onnx = ONNXBackend(cache_dir=onnx_cache_dir, auto_download=True)

    # Check if onnxruntime + tokenizers are importable
    if not onnx.is_available():
        # Libraries not installed — try Ollama fallback
        ollama = OllamaBackend(ollama_url=ollama_url, model=ollama_model)
        if ollama.is_available():
            print(
                "Warning: onnxruntime/tokenizers not installed. "
                "Falling back to Ollama embedding backend."
            )
            return ollama
        raise RuntimeError(
            "No embedding backend available. "
            "Install onnxruntime + tokenizers, or ensure Ollama is running. "
            "Hint: pip install onnxruntime tokenizers"
        )

    # Libraries available — model will be auto-downloaded on first embed() call
    # But if model not downloaded yet and Ollama is available, we can use it as immediate fallback
    if not is_model_downloaded(onnx_cache_dir):
        ollama = OllamaBackend(ollama_url=ollama_url, model=ollama_model)
        if ollama.is_available():
            # Still return ONNX (it will auto-download on first use)
            # Only fall back if download fails
            pass  # ONNX will download on demand — that's fine

    return onnx
