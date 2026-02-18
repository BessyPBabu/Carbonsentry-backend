import io
import base64
import logging
from decimal import Decimal

import PIL.Image

from .gemini_client import GeminiClient
from .validators import ResponseParser, DataValidator
from ..models import AIAuditLog, ExtractedMetadata

logger = logging.getLogger(__name__)


class MetadataExtractor:

    def __init__(self):
        self.client = GeminiClient()
        self.parser = ResponseParser()
        self.validator = DataValidator()

    def extract(self, image_base64, validation):
        prompt = self._get_prompt()

        try:
            img = PIL.Image.open(io.BytesIO(base64.b64decode(image_base64)))
        except Exception as e:
            logger.exception("extract: failed to decode image for validation %s", validation.id)
            return True, self._create_empty_metadata(validation, {'decode_error': str(e)}), None

        success, response, error, _ = self.client.call_with_retry(prompt, img)

        AIAuditLog.objects.create(
            document_validation=validation,
            validation_step='extraction',
            prompt_sent=prompt,
            raw_response=response if success else (error or ''),
            success=success,
            error_message=error or '',
            model_used='gemini-2.5-flash',
        )

        if not success:
            logger.warning("extract: Gemini call failed for validation %s — %s", validation.id, error)
            # don't fail the pipeline — create empty metadata and continue
            return True, self._create_empty_metadata(validation, {'api_error': error}), None

        json_ok, data, parse_err = self.parser.parse_json(response)

        if not json_ok:
            logger.warning("extract: JSON parse failed for validation %s — %s", validation.id, parse_err)
            return True, self._create_empty_metadata(validation, {'parse_error': parse_err}), None

        cleaned = self._clean_data(data)

        try:
            metadata = ExtractedMetadata.objects.create(
                document_validation=validation,
                document=validation.document,
                co2_value=cleaned.get('co2_value'),
                co2_unit=cleaned.get('co2_unit', 'tonnes'),
                co2_extraction_confidence=cleaned.get('co2_confidence'),
                issue_date=cleaned.get('issue_date'),
                issue_date_confidence=cleaned.get('issue_date_confidence'),
                expiry_date=cleaned.get('expiry_date'),
                expiry_date_confidence=cleaned.get('expiry_date_confidence'),
                issuing_authority=cleaned.get('issuing_authority', ''),
                issuing_authority_confidence=cleaned.get('issuing_authority_confidence'),
                certificate_number=cleaned.get('certificate_number', ''),
                verification_standard=cleaned.get('verification_standard', ''),
                raw_extracted_data=data,
            )
        except Exception as e:
            logger.exception("extract: failed to save ExtractedMetadata for validation %s", validation.id)
            return False, None, f"Failed to save metadata: {e}"

        logger.info(
            "extract: validation=%s co2=%s expiry=%s",
            validation.id, cleaned.get('co2_value'), cleaned.get('expiry_date'),
        )
        return True, metadata, None

    def _clean_data(self, data):
        cleaned = {}

        # co2 value
        valid, result = self.validator.validate_co2_value(data.get('co2_value'))
        if valid and result is not None:
            cleaned['co2_value'] = Decimal(str(result))
            cleaned['co2_confidence'] = self._safe_decimal(data.get('co2_confidence', 70))
        else:
            if not valid:
                logger.warning("_clean_data: invalid co2_value — %s", result)
            cleaned['co2_value'] = None
            cleaned['co2_confidence'] = Decimal('0')

        cleaned['co2_unit'] = self.validator.normalize_unit(data.get('co2_unit'))

        # issue date (not in future)
        valid, result = self.validator.validate_date(data.get('issue_date'), is_expiry=False)
        if valid and result:
            cleaned['issue_date'] = result
            cleaned['issue_date_confidence'] = self._safe_decimal(data.get('issue_date_confidence', 70))
        elif not valid:
            logger.warning("_clean_data: invalid issue_date '%s' — %s", data.get('issue_date'), result)

        # expiry date (future is valid — was the original bug)
        valid, result = self.validator.validate_date(data.get('expiry_date'), is_expiry=True)
        if valid and result:
            cleaned['expiry_date'] = result
            cleaned['expiry_date_confidence'] = self._safe_decimal(data.get('expiry_date_confidence', 70))
        elif not valid:
            logger.warning("_clean_data: invalid expiry_date '%s' — %s", data.get('expiry_date'), result)

        cleaned['issuing_authority'] = str(data.get('issuing_authority') or '')[:500]
        cleaned['issuing_authority_confidence'] = self._safe_decimal(data.get('issuing_authority_confidence', 60))
        cleaned['certificate_number'] = str(data.get('certificate_number') or '')[:255]
        cleaned['verification_standard'] = str(data.get('verification_standard') or '')[:100]

        return cleaned

    def _safe_decimal(self, value, default=0):
        try:
            return Decimal(str(max(0, min(100, float(value or default)))))
        except (TypeError, ValueError):
            return Decimal(str(default))

    def _create_empty_metadata(self, validation, raw_data):
        try:
            return ExtractedMetadata.objects.create(
                document_validation=validation,
                document=validation.document,
                raw_extracted_data=raw_data,
            )
        except Exception as e:
            logger.exception("_create_empty_metadata: failed for validation %s", validation.id)
            return None

    def _get_prompt(self):
        return """Extract data from this carbon compliance document.

Return ONLY this JSON (no other text, no explanation):
{
  "co2_value": 1250.5,
  "co2_unit": "tonnes",
  "co2_confidence": 85,
  "issue_date": "2024-01-15",
  "issue_date_confidence": 90,
  "expiry_date": "2025-01-15",
  "expiry_date_confidence": 90,
  "issuing_authority": "Green Certification Body",
  "issuing_authority_confidence": 80,
  "certificate_number": "CERT-2024-001",
  "verification_standard": "ISO 14064"
}

Rules:
1. co2_value: number only (from "1,250 tonnes CO2e" extract 1250). null if not found.
2. co2_unit: "tonnes", "kg", or "metric_tons". Default "tonnes".
3. issue_date: YYYY-MM-DD. Look for "Issue Date", "Date of Issue", "Certified on". null if not found.
4. expiry_date: YYYY-MM-DD. Look for "Valid Until", "Expiry Date", "Expires". null if not found. Expiry dates are in the future — that is correct.
5. issuing_authority: organisation that issued this. Empty string if not found.
6. certificate_number: any ID or reference number. Empty string if not found.
7. verification_standard: e.g. "ISO 14064", "GHG Protocol". Empty string if not found.
8. confidence fields: 0-100. Use 70 as default if value is found but not perfectly clear.

Use null for missing numbers, empty string for missing text. Do not guess."""