import io
import logging
import math
import mimetypes
import os

logger = logging.getLogger(__name__)

ALLOWED_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp", ".tiff", ".tif"}
ALLOWED_MIMES = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/webp",
    "image/tiff",
}
MIN_SIZE_BYTES = 1_024       # 1 KB — blank files rejected
MAX_SIZE_BYTES = 20_971_520  # 20 MB


def _file_entropy(file_path: str) -> float:
    try:
        with open(file_path, "rb") as f:
            data = f.read(65_536)  # sample first 64 KB only
        if not data:
            return 0.0
        counts = [0] * 256
        for b in data:
            counts[b] += 1
        n = len(data)
        entropy = 0.0
        for c in counts:
            if c:
                p = c / n
                entropy -= p * math.log2(p)
        return entropy
    except OSError as exc:
        logger.warning("input_gate._file_entropy: read error %s — %s", file_path, exc)
        return 8.0  # assume valid if we can't read


def run(file_path: str) -> tuple[bool, str]:
    """
    Returns (passed: bool, reason: str).
    If passed=False the document must be rejected immediately without any Gemini call.
    Covers: missing file, wrong extension, MIME mismatch, size limits, blank/zero-entropy files.
    """
    if not file_path or not os.path.exists(file_path):
        return False, "file_not_found"

    size = os.path.getsize(file_path)
    if size < MIN_SIZE_BYTES:
        logger.info("input_gate: rejected %s — too small (%d bytes)", file_path, size)
        return False, "file_too_small"

    if size > MAX_SIZE_BYTES:
        logger.info("input_gate: rejected %s — too large (%d bytes)", file_path, size)
        return False, "file_too_large"

    ext = os.path.splitext(file_path)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        logger.info("input_gate: rejected %s — extension '%s' not allowed", file_path, ext)
        return False, f"unsupported_extension:{ext}"

    # MIME sniff from file header (first 512 bytes)
    try:
        import magic  # python-magic
        detected_mime = magic.from_file(file_path, mime=True)
    except Exception:
        # python-magic not available — fall back to mimetypes
        detected_mime, _ = mimetypes.guess_type(file_path)
        detected_mime = detected_mime or "application/octet-stream"

    if detected_mime not in ALLOWED_MIMES:
        logger.info(
            "input_gate: rejected %s — MIME '%s' not allowed", file_path, detected_mime
        )
        return False, f"unsupported_mime:{detected_mime}"

    # Entropy check — blank pages, solid-colour images, and zero-byte-padded files
    # score very low (< 1.0). Legitimate documents score above 3.0.
    entropy = _file_entropy(file_path)
    if entropy < 1.0:
        logger.info(
            "input_gate: rejected %s — entropy %.2f (blank or corrupt)", file_path, entropy
        )
        return False, "blank_or_corrupt"

    return True, "ok"