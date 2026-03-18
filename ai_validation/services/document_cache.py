import hashlib
import json
import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)

# how long to keep a cached validation result
# 30 days — long enough that re-validating the same file gives the same answer
CACHE_TTL_SECONDS = 60 * 60 * 24 * 30


def _file_hash(file_path: str) -> str:
    """SHA-256 of the file bytes — identical file = identical hash."""
    sha = hashlib.sha256()
    try:
        with open(file_path, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                sha.update(chunk)
        return sha.hexdigest()
    except OSError as exc:
        logger.warning("document_cache._file_hash: could not read %s — %s", file_path, exc)
        return ""


def cache_key(file_path: str, step: str) -> str:
    """
    Unique cache key per (document content, pipeline step).
    Two different files with the same name get different keys.
    The same file renamed gets the same key.
    """
    h = _file_hash(file_path)
    if not h:
        return ""
    return f"docval:{step}:{h}"


def get_cached(file_path: str, step: str) -> dict | None:
    """Return cached result dict, or None on miss."""
    key = cache_key(file_path, step)
    if not key:
        return None
    try:
        raw = cache.get(key)
        if raw is None:
            return None
        result = json.loads(raw)
        logger.info("document_cache: HIT | step=%s hash=%s", step, key[-16:])
        return result
    except Exception as exc:
        logger.warning("document_cache.get_cached: error — %s", exc)
        return None


def set_cached(file_path: str, step: str, result: dict) -> None:
    """Store result dict in cache."""
    key = cache_key(file_path, step)
    if not key:
        return
    try:
        cache.set(key, json.dumps(result), timeout=CACHE_TTL_SECONDS)
        logger.info("document_cache: SET  | step=%s hash=%s", step, key[-16:])
    except Exception as exc:
        logger.warning("document_cache.set_cached: error — %s", exc)