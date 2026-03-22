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

_PROMPT = f"""Determine whether this document is related to carbon emissions, environmental compliance, or sustainability reporting.

## Valid document types
{_VALID_TYPES_STR}

## Relevance decision
Set is_relevant = TRUE if the document contains ANY of the following keywords or topics:
  carbon, CO2, CO₂, emissions, greenhouse gas, GHG, sustainability, environmental,
  climate, carbon footprint, carbon offset, carbon credit, carbon neutral,
  ISO 14064, GHG Protocol, Verra, Gold Standard, PAS 2060, scope 1, scope 2, scope 3,
  tonnes CO2e, tCO2e, metric tons, emission factor, verification, certification

Set is_relevant = FALSE only when the document is CLEARLY unrelated, for example:
  plain purchase invoice, employment contract, bank statement, medical record,
  sales quotation with no environmental content, payroll summary, NDA, meeting minutes

When in doubt → set is_relevant = TRUE.

## Confidence scoring (0–100)
| Situation                                                      | Score  |
|----------------------------------------------------------------|--------|
| Document is obviously a carbon/emissions certificate or report | 90–100 |
| Document clearly relates to sustainability but type is mixed   | 75–90  |
| Document has some environmental content but is partially unclear| 60–75  |
| Marginal relevance — a few keywords but mostly something else  | 40–60  |
| Clearly unrelated document                                     | 0–30   |

## indicators
List 1–3 specific things you observed (e.g. "CO2 value in tonnes", "ISO 14064 logo", "verification statement").

Pick the closest type from the valid list above."""


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
            "confidence": 70.0,   # raised from 60 — safe fallback for API failures
            "indicators": ["defaulted due to processing error"],
        }