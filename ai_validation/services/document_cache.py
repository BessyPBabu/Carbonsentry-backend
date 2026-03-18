import hashlib
import json
import logging

from django.core.cache import cache

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 60 * 60 * 24 * 30


def _file_hash(file_path: str) -> str:
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
    h = _file_hash(file_path)
    if not h:
        return ""
    return f"docval:{step}:{h}"


def get_cached(file_path: str, step: str) -> dict | None:
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
    key = cache_key(file_path, step)
    if not key:
        return
    try:
        cache.set(key, json.dumps(result), timeout=CACHE_TTL_SECONDS)
        logger.info("document_cache: SET  | step=%s hash=%s", step, key[-16:])
    except Exception as exc:
        logger.warning("document_cache.set_cached: error — %s", exc)