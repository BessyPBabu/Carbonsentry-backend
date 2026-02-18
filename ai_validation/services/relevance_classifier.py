import io
import base64
import logging
from decimal import Decimal

import PIL.Image

from .gemini_client import GeminiClient
from .validators import ResponseParser
from ..models import AIAuditLog
from ..constants import VALID_DOCUMENT_TYPES

logger = logging.getLogger(__name__)


class RelevanceClassifier:

    def __init__(self):
        self.client = GeminiClient()
        self.parser = ResponseParser()

    def classify(self, image_base64, validation):
        prompt = self._get_prompt()

        try:
            img = PIL.Image.open(io.BytesIO(base64.b64decode(image_base64)))
        except Exception as e:
            logger.exception("classify: failed to decode image for validation %s", validation.id)
            return True, self._default_relevant(), None

        success, response, error, _ = self.client.call_with_retry(prompt, img)

        AIAuditLog.objects.create(
            document_validation=validation,
            validation_step='relevance',
            prompt_sent=prompt,
            raw_response=response if success else (error or ''),
            success=success,
            error_message=error or '',
            model_used='gemini-2.5-flash',
        )

        if not success:
            logger.warning("classify: Gemini call failed for validation %s — %s", validation.id, error)
            return True, self._default_relevant(), None

        json_ok, data, parse_err = self.parser.parse_json(response)

        if not json_ok:
            logger.warning("classify: JSON parse failed for validation %s — %s", validation.id, parse_err)
            return True, self._default_relevant(), None

        is_relevant = data.get('is_relevant', True)
        confidence = float(data.get('confidence', 60) or 60)
        doc_type = self._normalize_doc_type(data.get('document_type', ''))
        indicators = data.get('indicators', [])

        if not isinstance(indicators, list):
            indicators = []

        result = {
            'is_relevant': is_relevant,
            'document_type': doc_type,
            'confidence': Decimal(str(max(0, min(100, confidence)))),
            'indicators': indicators,
        }

        logger.info(
            "classify: validation=%s relevant=%s type=%s confidence=%.1f",
            validation.id, is_relevant, doc_type, confidence,
        )
        return True, result, None

    def _normalize_doc_type(self, detected):
        if not detected:
            return 'Emission Report'

        d = detected.lower()
        for valid in VALID_DOCUMENT_TYPES:
            if any(word in d for word in valid.lower().split()):
                return valid

        if any(k in d for k in ('carbon', 'credit', 'offset')):
            return 'Carbon Credit Certificate'
        if any(k in d for k in ('emission', 'ghg', 'greenhouse')):
            return 'Emission Report'
        if any(k in d for k in ('sustainability', 'esg')):
            return 'Sustainability Certificate'
        if 'iso' in d:
            return 'ISO 14064 Certificate'

        logger.debug("_normalize_doc_type: no match for '%s', defaulting", detected)
        return 'Emission Report'

    def _default_relevant(self):
        return {
            'is_relevant': True,
            'document_type': 'Emission Report',
            'confidence': Decimal('60'),
            'indicators': ['defaulted to relevant due to processing error'],
        }

    def _get_prompt(self):
        valid_types = '\n'.join(f'- {t}' for t in VALID_DOCUMENT_TYPES)
        return f"""Look at this document. Is it related to carbon emissions, environmental compliance, or sustainability?

Return ONLY this JSON (no other text):
{{
  "is_relevant": true,
  "document_type": "Emission Report",
  "confidence": 75,
  "indicators": ["contains emission data", "has certificate format"]
}}

Valid document types:
{valid_types}

Rules:
- Set is_relevant to TRUE if the document contains ANY of: carbon, CO2, emissions, greenhouse gas, sustainability, environmental, climate, GHG, carbon footprint, carbon offset, carbon credit, ISO 14064
- Set is_relevant to FALSE only if clearly unrelated (invoice, contract, ID card, medical record)
- Pick the closest type from the valid list
- confidence: 0-100
- indicators: 1-3 things you saw

When in doubt, set is_relevant=true."""