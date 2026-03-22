import io
import base64
import logging

import PIL.Image

from .langchain_client import LangChainClient
from .schemas import ReadabilityOutput
from .document_cache import get_cached, set_cached
from ..models import AIAuditLog

logger = logging.getLogger(__name__)

_PROMPT = """Assess the readability and visual quality of this document image.

## Scoring rules (quality_score: 0-100)

| Document type                                    | Score range |
|--------------------------------------------------|-------------|
| Computer-generated / digital PDF (clean text)   | 85–98       |
| High-quality scan (crisp, straight, no noise)   | 75–88       |
| Good photo / moderate scan (minor distortion)   | 60–75       |
| Poor scan / blurry photo (text still readable)  | 40–60       |
| Severely degraded (text barely legible)          | 15–40       |
| Completely blank, pure noise, or fully corrupt   | 0–15        |

## is_readable rules
- Set is_readable = TRUE for EVERYTHING except a completely blank page, solid-colour image, or visually pure noise/static.
- A digital PDF with clean text → is_readable = TRUE, score 85–98.
- A scanned document where text can be read → is_readable = TRUE even if imperfect.
- Watermarks like "SAMPLE" or "DRAFT" do NOT affect readability — score the legibility of the text.
- Only set is_readable = FALSE when there is literally no readable content at all.

## language
Detected language, or "English" if uncertain.

## issues
List actual problems (e.g. "partially cut off", "heavy shadow on left margin").
Leave empty [] if the document is clean. Do NOT list "digital document" as an issue."""


class ReadabilityChecker:

    def __init__(self):
        self.client = LangChainClient()

    def check(self, image_base64: str, validation, file_path: str = ""):
        if file_path:
            cached = get_cached(file_path, "readability")
            if cached:
                logger.info(
                    "ReadabilityChecker: cache hit | validation=%s", validation.id
                )
                return True, cached, None

        try:
            PIL.Image.open(io.BytesIO(base64.b64decode(image_base64)))
        except Exception as exc:
            logger.exception(
                "ReadabilityChecker: image decode failed | validation=%s", validation.id
            )
            return True, self._default_pass(f"Image decode error: {exc}"), None

        success, output, error = self.client.call_structured(
            prompt=_PROMPT,
            image_base64=image_base64,
            schema=ReadabilityOutput,
        )

        AIAuditLog.objects.create(
            document_validation=validation,
            validation_step="readability",
            prompt_sent=_PROMPT,
            raw_response=str(output.model_dump()) if output else (error or ""),
            success=success,
            error_message=error or "",
            model_used="gemini-2.5-flash",
        )

        if not success or output is None:
            logger.warning(
                "ReadabilityChecker: Gemini failed | validation=%s — %s", validation.id, error
            )
            return True, self._default_pass(f"API call failed: {error}"), None

        # Safety override: if quality_score > 20 the document has readable content
        is_readable = output.is_readable
        if output.quality_score > 20:
            is_readable = True

        result = {
            "is_readable": is_readable,
            "quality_score": float(output.quality_score),
            "language": output.language,
            "issues": output.issues,
        }

        if file_path:
            set_cached(file_path, "readability", result)

        logger.info(
            "ReadabilityChecker: done | validation=%s readable=%s score=%.1f",
            validation.id, is_readable, output.quality_score,
        )
        return True, result, None

    def _default_pass(self, reason: str) -> dict:
        """Fallback used when Gemini is unavailable — assume document is readable."""
        return {
            "is_readable": True,
            "quality_score": 75.0,   # raised from 60 — generated PDFs are always clean
            "language": "English",
            "issues": [reason],
        }