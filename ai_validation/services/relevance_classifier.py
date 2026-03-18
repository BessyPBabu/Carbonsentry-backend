import io
import base64
import logging
from decimal import Decimal

import PIL.Image

from .langchain_client import LangChainClient
from .schemas import RelevanceOutput
from .document_cache import get_cached, set_cached
from ..models import AIAuditLog
from ..constants import VALID_DOCUMENT_TYPES

logger = logging.getLogger(__name__)

_VALID_TYPES_STR = "\n".join(f"- {t}" for t in VALID_DOCUMENT_TYPES)

_PROMPT = f"""Look at this document. Is it related to carbon emissions, environmental compliance, or sustainability?

Valid document types:
{_VALID_TYPES_STR}

Rules:
- Set is_relevant to TRUE if the document contains ANY of: carbon, CO2, emissions, greenhouse gas, sustainability, environmental, climate, GHG, carbon footprint, carbon offset, carbon credit, ISO 14064
- Set is_relevant to FALSE only if clearly unrelated (invoice, contract, ID card, medical record)
- Pick the closest type from the valid list
- confidence: 0-100
- indicators: 1-3 things you saw in the document

When in doubt, set is_relevant=true."""


class RelevanceClassifier:

    def __init__(self):
        self.client = LangChainClient()

    def classify(self, image_base64: str, validation, file_path: str = ""):
        if file_path:
            cached = get_cached(file_path, "relevance")
            if cached:
                logger.info(
                    "RelevanceClassifier: cache hit | validation=%s", validation.id
                )
                return True, cached, None

        try:
            PIL.Image.open(io.BytesIO(base64.b64decode(image_base64)))
        except Exception as exc:
            logger.exception(
                "RelevanceClassifier: image decode failed | validation=%s", validation.id
            )
            return True, self._default_relevant(), None

        success, output, error = self.client.call_structured(
            prompt=_PROMPT,
            image_base64=image_base64,
            schema=RelevanceOutput,
        )

        AIAuditLog.objects.create(
            document_validation=validation,
            validation_step="relevance",
            prompt_sent=_PROMPT,
            raw_response=str(output.model_dump()) if output else (error or ""),
            success=success,
            error_message=error or "",
            model_used="gemini-2.5-flash",
        )

        if not success or output is None:
            logger.warning(
                "RelevanceClassifier: Gemini failed | validation=%s — %s", validation.id, error
            )
            return True, self._default_relevant(), None

        doc_type = self._normalise_type(output.document_type)

        result = {
            "is_relevant": output.is_relevant,
            "document_type": doc_type,
            "confidence": float(output.confidence),
            "indicators": output.indicators,
        }

        if file_path:
            set_cached(file_path, "relevance", result)

        logger.info(
            "RelevanceClassifier: done | validation=%s relevant=%s type=%s confidence=%.1f",
            validation.id, output.is_relevant, doc_type, output.confidence,
        )
        return True, result, None

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

    def _default_relevant(self) -> dict:
        return {
            "is_relevant": True,
            "document_type": "Emission Report",
            "confidence": 60.0,
            "indicators": ["defaulted due to processing error"],
        }