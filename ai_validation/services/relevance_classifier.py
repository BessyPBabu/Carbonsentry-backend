import base64
import io
import logging

import PIL.Image

from .langchain_client import LangChainClient, CALL_TIMEOUT_RELEVANCE
from .prompt_registry import RELEVANCE_PROMPT
from .document_cache import get_cached, set_cached
from ..models import AIAuditLog
from .schemas import RelevanceOutput
from ..constants import VALID_DOCUMENT_TYPES

logger = logging.getLogger(__name__)


class RelevanceClassifier:

    def __init__(self):
        self.client = LangChainClient()

    def classify(
        self, image_base64: str, validation, file_path: str = ""
    ) -> tuple[bool, dict | None, str | None]:
        if file_path:
            cached = get_cached(file_path, "relevance")
            if cached:
                return True, cached, None

        try:
            PIL.Image.open(io.BytesIO(base64.b64decode(image_base64)))
        except Exception as exc:
            logger.warning(
                "relevance_classifier: image decode failed validation=%s — %s", validation.id, exc
            )
            return True, self._default(), None

        ok, output, err = self.client.call_structured(
            prompt=RELEVANCE_PROMPT,
            image_base64=image_base64,
            schema=RelevanceOutput,
            step="relevance",
            timeout_seconds=CALL_TIMEOUT_RELEVANCE,
        )

        self._log(validation, ok, output, err)

        if not ok or output is None:
            logger.warning(
                "relevance_classifier: gemini failed validation=%s — %s", validation.id, err
            )
            return True, self._default(), None

        result = {
            "is_relevant": output.is_relevant,
            "document_type": self._normalise_type(output.document_type),
            "confidence": float(output.confidence),
            "indicators": output.indicators[:5],
        }

        if file_path:
            set_cached(file_path, "relevance", result)

        return True, result, None

    def _log(self, validation, ok: bool, output, err: str | None):
        try:
            AIAuditLog.objects.create(
                document_validation=validation,
                validation_step="relevance",
                prompt_sent=RELEVANCE_PROMPT,
                raw_response=str(output.model_dump()) if output else (err or ""),
                success=ok,
                error_message=err or "",
                model_used="gemini-2.5-flash",
            )
        except Exception as exc:
            logger.error("relevance_classifier: audit log failed validation=%s — %s", validation.id, exc)

    def _normalise_type(self, detected: str) -> str:
        if not detected:
            return "Emission Report"
        d = detected.lower()
        for valid in VALID_DOCUMENT_TYPES:
            if any(word in d for word in valid.lower().split()):
                return valid
        if any(k in d for k in ("carbon", "credit", "offset")):
            return "Carbon Credit Certificate"
        if any(k in d for k in ("emission", "ghg", "greenhouse")):
            return "Emission Report"
        if any(k in d for k in ("sustainability", "esg")):
            return "Sustainability Certificate"
        if "iso" in d:
            return "ISO 14064 Certificate"
        return "Emission Report"

    def _default(self) -> dict:
        return {
            "is_relevant": True,
            "document_type": "Emission Report",
            "confidence": 70.0,
            "indicators": ["api_unavailable_defaulted"],
        }