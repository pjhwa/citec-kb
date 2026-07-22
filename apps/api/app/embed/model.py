"""Sentence embedding with intfloat/multilingual-e5-base (768-d)."""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Sequence

logger = logging.getLogger("citec.embed")

# Canonical id stored in embeddings.model (stable across host vs container paths).
MODEL_ID = "intfloat/multilingual-e5-base"
EMBEDDING_DIM = 768

# Prefer container-local snapshot under HF_HOME=/models (compose mount: ./models).
# Never fall back to other projects' paths — models must live under this repo's models/.
_DEFAULT_SNAP = (
    "/models/hub/models--intfloat--multilingual-e5-base/"
    "snapshots/d128750597153bb5987e10b1c3493a34e5a4502a"
)


def _resolve_load_path() -> str:
    env = os.getenv("EMBEDDING_MODEL")
    if env:
        return env
    if os.path.isdir(_DEFAULT_SNAP):
        return _DEFAULT_SNAP
    # Host-side tests without compose: repo-relative models/ only
    _repo_snap = os.path.join(
        os.path.dirname(__file__),
        "..",
        "..",
        "..",
        "..",
        "models",
        "hub",
        "models--intfloat--multilingual-e5-base",
        "snapshots",
        "d128750597153bb5987e10b1c3493a34e5a4502a",
    )
    _repo_snap = os.path.normpath(_repo_snap)
    if os.path.isdir(_repo_snap):
        return _repo_snap
    return MODEL_ID


# Path or hub id used only for loading weights.
DEFAULT_MODEL = _resolve_load_path()


@lru_cache(maxsize=1)
def get_model(load_path: str = DEFAULT_MODEL):
    """Load SentenceTransformer once (process-local cache)."""
    from sentence_transformers import SentenceTransformer

    # Cap CPU thread thrash on shared hosts.
    try:
        import torch

        n = int(os.getenv("EMBED_TORCH_THREADS", "4"))
        torch.set_num_threads(max(1, n))
        torch.set_num_interop_threads(1)
    except Exception:  # noqa: BLE001
        pass

    logger.info("loading embedding model path=%s id=%s", load_path, MODEL_ID)
    # local_files_only when path is a directory (offline-safe).
    kwargs: dict = {}
    if os.path.isdir(load_path):
        kwargs["local_files_only"] = True
    model = SentenceTransformer(load_path, **kwargs)
    # e5-base default 512; keep modest for CPU throughput.
    max_len = int(os.getenv("EMBED_MAX_SEQ_LENGTH", "512"))
    try:
        model.max_seq_length = max_len
    except Exception:  # noqa: BLE001
        pass
    dim = getattr(model, "get_embedding_dimension", None) or getattr(
        model, "get_sentence_embedding_dimension", None
    )
    if callable(dim):
        logger.info("embedding model ready dim=%s max_seq=%s", dim(), max_len)
    else:
        logger.info("embedding model ready max_seq=%s", max_len)
    return model


def embed_passages(texts: Sequence[str], model_name: str | None = None) -> list[list[float]]:
    """Encode passages with e5 'passage:' prefix. model_name ignored (always MODEL_ID weights)."""
    del model_name  # API compat; always use DEFAULT_MODEL path
    model = get_model()
    prefixed = [f"passage: {t}" for t in texts]
    encode_bs = int(os.getenv("EMBED_ENCODE_BATCH", os.getenv("EMBED_BATCH_SIZE", "16")))
    vecs = model.encode(
        prefixed,
        normalize_embeddings=True,
        show_progress_bar=False,
        batch_size=max(1, encode_bs),
        convert_to_numpy=True,
    )
    return [v.tolist() for v in vecs]


def embed_query(text: str, model_name: str | None = None) -> list[float]:
    del model_name
    model = get_model()
    vec = model.encode(
        [f"query: {text}"],
        normalize_embeddings=True,
        show_progress_bar=False,
        convert_to_numpy=True,
    )[0]
    return vec.tolist()


def try_load_model() -> tuple[bool, str]:
    try:
        m = get_model()
        dim_fn = getattr(m, "get_embedding_dimension", None) or getattr(
            m, "get_sentence_embedding_dimension", None
        )
        dim = dim_fn() if callable(dim_fn) else EMBEDDING_DIM
        return True, f"ok model_id={MODEL_ID} path={DEFAULT_MODEL} dim={dim}"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)
