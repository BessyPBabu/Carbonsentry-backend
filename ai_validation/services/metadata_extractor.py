import io
import base64
import logging
from decimal import Decimal

import PIL.Image

from .langchain_client import LangChainClient
from .schemas import MetadataOutput
from .document_cache import get_cached, set_cached
from .validators import DataValidator
from ..models import AIAuditLog, ExtractedMetadata

logger = logging.getLogger(__name__)

_PROMPT = """Extract data from this carbon compliance document.

Rules:
1. co2_value: number only (from "1,250 tonnes CO2e" extract 1250.0). null if not found.
2. co2_unit: "tonnes", "kg", or "metric_tons". Default "tonnes".
3. issue_date: YYYY-MM-DD. Look for "Issue Date", "Date of Issue", "Certified on". null if not found.
4. expiry_date: YYYY-MM-DD. Look for "Valid Until", "Expiry Date", "Expires". null if not found.
   Expiry dates in the future are correct and valid — do not reject them.
5. issuing_authority: organisation that issued this. Empty string if not found.
6. certificate_number: any ID or reference number. Empty string if not found.
7. verification_standard: e.g. "ISO 14064", "GHG Protocol". Empty string if not found.
8. confidence fields: 0-100. Use 70 as default if value is found but not perfectly clear.

Use null for missing numbers, empty string for missing text. Do not guess."""


class MetadataExtractor:

    def __init__(self):
        self.client = LangChainClient()
        self.validator = DataValidator()

    def extract(self, image_base64: str, validation, file_path: str = ""):
        if file_path:
            cached = get_cached(file_path, "extraction")
            if cached:
                logger.info(
                    "MetadataExtractor: cache hit | validation=%s", validation.id
                )
                # rebuild ExtractedMetadata from cached dict
                metadata = self._save_metadata(validation, cached)
                return True, metadata, None

        try:
            PIL.Image.open(io.BytesIO(base64.b64decode(image_base64)))
        except Exception as exc:
            logger.exception(
                "MetadataExtractor: image decode failed | validation=%s", validation.id
            )
            return True, self._create_empty_metadata(validation, {"decode_error": str(exc)}), None

        success, output, error = self.client.call_structured(
            prompt=_PROMPT,
            image_base64=image_base64,
            schema=MetadataOutput,
        )

        AIAuditLog.objects.create(
            document_validation=validation,
            validation_step="extraction",
            prompt_sent=_PROMPT,
            raw_response=str(output.model_dump()) if output else (error or ""),
            success=success,
            error_message=error or "",
            model_used="gemini-2.5-flash",
        )

        if not success or output is None:
            logger.warning(
                "MetadataExtractor: Gemini failed | validation=%s — %s", validation.id, error
            )
            return True, self._create_empty_metadata(validation, {"api_error": error}), None

        cleaned = self._clean(output)

        if file_path:
            set_cached(file_path, "extraction", cleaned)

        metadata = self._save_metadata(validation, cleaned)
        if metadata is None:
            return False, None, "Failed to save metadata"

        logger.info(
            "MetadataExtractor: done | validation=%s co2=%s expiry=%s",
            validation.id, cleaned.get("co2_value"), cleaned.get("expiry_date"),
        )
        return True, metadata, None

    def _clean(self, output: MetadataOutput) -> dict:
        cleaned = {}

        # co2 value
        valid, result = self.validator.validate_co2_value(output.co2_value)
        if valid and result is not None:
            cleaned["co2_value"] = Decimal(str(result))
            cleaned["co2_confidence"] = Decimal(str(min(100, max(0, output.co2_confidence))))
        else:
            cleaned["co2_value"] = None
            cleaned["co2_confidence"] = Decimal("0")

        cleaned["co2_unit"] = self.validator.normalize_unit(output.co2_unit)

        # dates
        valid, result = self.validator.validate_date(output.issue_date, is_expiry=False)
        if valid and result:
            cleaned["issue_date"] = result
            cleaned["issue_date_confidence"] = Decimal(str(output.issue_date_confidence))

        valid, result = self.validator.validate_date(output.expiry_date, is_expiry=True)
        if valid and result:
            cleaned["expiry_date"] = result
            cleaned["expiry_date_confidence"] = Decimal(str(output.expiry_date_confidence))

        cleaned["issuing_authority"] = str(output.issuing_authority or "")[:500]
        cleaned["issuing_authority_confidence"] = Decimal(str(output.issuing_authority_confidence))
        cleaned["certificate_number"] = str(output.certificate_number or "")[:255]
        cleaned["verification_standard"] = str(output.verification_standard or "")[:100]

        return cleaned

    def _save_metadata(self, validation, data: dict):
        try:
            # get_or_create so cache replay doesn't cause duplicate errors
            metadata, _ = ExtractedMetadata.objects.update_or_create(
                document_validation=validation,
                defaults={
                    "document": validation.document,
                    "co2_value": data.get("co2_value"),
                    "co2_unit": data.get("co2_unit", "tonnes"),
                    "co2_extraction_confidence": data.get("co2_confidence"),
                    "issue_date": data.get("issue_date"),
                    "issue_date_confidence": data.get("issue_date_confidence"),
                    "expiry_date": data.get("expiry_date"),
                    "expiry_date_confidence": data.get("expiry_date_confidence"),
                    "issuing_authority": data.get("issuing_authority", ""),
                    "issuing_authority_confidence": data.get("issuing_authority_confidence"),
                    "certificate_number": data.get("certificate_number", ""),
                    "verification_standard": data.get("verification_standard", ""),
                    "raw_extracted_data": data,
                },
            )
            return metadata
        except Exception as exc:
            logger.exception(
                "MetadataExtractor._save_metadata: failed | validation=%s", validation.id
            )
            return None

    def _create_empty_metadata(self, validation, raw_data: dict):
        try:
            metadata, _ = ExtractedMetadata.objects.get_or_create(
                document_validation=validation,
                defaults={
                    "document": validation.document,
                    "raw_extracted_data": raw_data,
                },
            )
            return metadata
        except Exception as exc:
            logger.exception(
                "MetadataExtractor._create_empty_metadata: failed | validation=%s", validation.id
            )
            return None