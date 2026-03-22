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

_PROMPT = """Extract structured data from this carbon compliance document.

## Fields to extract

### co2_value
Extract the numeric CO2/emissions figure only (no units).
Examples: "1,250 tonnes CO2e" → 1250.0 | "500 kg CO2" → 500.0
Use null if no emissions figure is present.

### co2_unit
One of: "tonnes", "kg", "metric_tons". Default "tonnes" when unit is ambiguous or missing.

### issue_date
The date the certificate or report was issued/certified.
Look for: "Issue Date", "Date of Issue", "Certified on", "Report Date", "Date Issued"
Format: YYYY-MM-DD. Use null if not found.

### expiry_date
The date the certificate expires or the reporting period ends.
Look for: "Valid Until", "Expiry Date", "Expires", "Valid Through", "Period End"
Format: YYYY-MM-DD. Use null if not found.
NOTE: A future expiry date (e.g. next year) is correct and valid — do not reject it.

### issuing_authority
Name of the organisation that issued, verified, or certified this document.
Examples: "Bureau Veritas", "SGS", "TÜV Rheinland", "DNV", "EcoAct"
Use empty string "" if not identifiable.

### certificate_number
Any reference number, certificate ID, or document identifier.
Examples: "BV-2024-001", "CERT/ISO/2024/1234", "REF: CS-789"
Use empty string "" if not present.

### verification_standard
The standard or protocol referenced.
Examples: "ISO 14064", "GHG Protocol", "Verra VCS", "Gold Standard", "PAS 2060"
Use empty string "" if not mentioned.

## Confidence scoring (0–100)

Score each field independently based on how clearly the value appears in the document:

| Situation                                               | Confidence |
|---------------------------------------------------------|------------|
| Value is explicitly labelled and clearly readable       | 85–98      |
| Value is present but label is implied or formatting     |            |
| is non-standard                                         | 70–85      |
| Value is likely present but partially obscured/unclear  | 50–70      |
| Value is inferred / uncertain                           | 30–50      |
| Field is completely absent from document                | 0          |

Use 85 as the default when a field is clearly present but you're not certain of exact digits.

## Output rules
- co2_value: number only, null if absent
- Dates: YYYY-MM-DD string, null if absent
- Text fields: empty string "" if absent, never null
- Do not guess values that are not visible in the document"""


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

        valid, result = self.validator.validate_co2_value(output.co2_value)
        if valid and result is not None:
            cleaned["co2_value"] = Decimal(str(result))
            cleaned["co2_confidence"] = Decimal(str(min(100, max(0, output.co2_confidence))))
        else:
            cleaned["co2_value"] = None
            cleaned["co2_confidence"] = Decimal("0")

        cleaned["co2_unit"] = self.validator.normalize_unit(output.co2_unit)

        valid, result = self.validator.validate_date(output.issue_date, is_expiry=False)
        if valid and result:
            cleaned["issue_date"] = result
            cleaned["issue_date_confidence"] = Decimal(str(min(100, max(0, output.issue_date_confidence))))

        valid, result = self.validator.validate_date(output.expiry_date, is_expiry=True)
        if valid and result:
            cleaned["expiry_date"] = result
            cleaned["expiry_date_confidence"] = Decimal(str(min(100, max(0, output.expiry_date_confidence))))

        cleaned["issuing_authority"] = str(output.issuing_authority or "")[:500]
        cleaned["issuing_authority_confidence"] = Decimal(str(min(100, max(0, output.issuing_authority_confidence))))
        cleaned["certificate_number"] = str(output.certificate_number or "")[:255]
        cleaned["verification_standard"] = str(output.verification_standard or "")[:100]

        return cleaned

    def _save_metadata(self, validation, data: dict):
        try:
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