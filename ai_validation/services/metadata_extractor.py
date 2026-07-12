import base64
import io
import logging
from datetime import date
from decimal import Decimal

import PIL.Image

from .langchain_client import LangChainClient, CALL_TIMEOUT_EXTRACTION
from .prompt_registry import EXTRACTION_PROMPT
from .document_cache import get_cached, set_cached
from .validators import DataValidator
from ..models import AIAuditLog, ExtractedMetadata
from .schemas import MetadataOutput

logger = logging.getLogger(__name__)


def _json_safe(obj):
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, date):
        return obj.isoformat()
    return obj


class MetadataExtractor:

    def __init__(self):
        self.client = LangChainClient()
        self.validator = DataValidator()

    def extract(
        self, image_base64: str, validation, file_path: str = ""
    ) -> tuple[bool, ExtractedMetadata | None, str | None]:
        if file_path:
            cached = get_cached(file_path, "extraction")
            if cached:
                metadata = self._save(validation, cached, raw=cached)
                return (True, metadata, None) if metadata else (False, None, "cache_save_failed")

        try:
            PIL.Image.open(io.BytesIO(base64.b64decode(image_base64)))
        except Exception as exc:
            logger.warning(
                "metadata_extractor: image decode failed validation=%s — %s", validation.id, exc
            )
            return True, self._empty(validation, {"decode_error": str(exc)}), None

        ok, output, err = self.client.call_structured(
            prompt=EXTRACTION_PROMPT,
            image_base64=image_base64,
            schema=MetadataOutput,
            step="extraction",
            timeout_seconds=CALL_TIMEOUT_EXTRACTION,
        )

        self._log(validation, ok, output, err)

        if not ok or output is None:
            logger.warning(
                "metadata_extractor: gemini failed validation=%s — %s", validation.id, err
            )
            return True, self._empty(validation, {"api_error": err}), None

        cleaned = self._clean(output)
        safe = _json_safe(cleaned)

        if file_path:
            set_cached(file_path, "extraction", safe)

        metadata = self._save(validation, cleaned, raw=safe)
        if metadata is None:
            return False, None, "db_save_failed"

        return True, metadata, None

    def _clean(self, output: MetadataOutput) -> dict:
        cleaned = {}

        ok, val = self.validator.validate_co2_value(output.co2_value)
        cleaned["co2_value"] = Decimal(str(val)) if ok and val is not None else None
        cleaned["co2_confidence"] = (
            Decimal(str(min(100, max(0, output.co2_confidence))))
            if ok and val is not None
            else Decimal("0")
        )
        cleaned["co2_unit"] = self.validator.normalize_unit(output.co2_unit)

        ok, val = self.validator.validate_date(output.issue_date, is_expiry=False)
        if ok and val:
            cleaned["issue_date"] = val
            cleaned["issue_date_confidence"] = Decimal(str(min(100, max(0, output.issue_date_confidence))))

        ok, val = self.validator.validate_date(output.expiry_date, is_expiry=True)
        if ok and val:
            cleaned["expiry_date"] = val
            cleaned["expiry_date_confidence"] = Decimal(str(min(100, max(0, output.expiry_date_confidence))))

        cleaned["issuing_authority"] = str(output.issuing_authority or "")[:500]
        cleaned["issuing_authority_confidence"] = Decimal(str(min(100, max(0, output.issuing_authority_confidence))))
        cleaned["certificate_number"] = str(output.certificate_number or "")[:255]
        cleaned["verification_standard"] = str(output.verification_standard or "")[:100]

        return cleaned

    def _save(self, validation, data: dict, raw: dict) -> ExtractedMetadata | None:
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
                    "raw_extracted_data": raw,
                },
            )
            return metadata
        except Exception as exc:
            logger.error(
                "metadata_extractor: db save failed validation=%s — %s", validation.id, exc
            )
            return None

    def _empty(self, validation, raw: dict) -> ExtractedMetadata | None:
        try:
            metadata, _ = ExtractedMetadata.objects.get_or_create(
                document_validation=validation,
                defaults={"document": validation.document, "raw_extracted_data": _json_safe(raw)},
            )
            return metadata
        except Exception as exc:
            logger.error(
                "metadata_extractor: empty save failed validation=%s — %s", validation.id, exc
            )
            return None

    def _log(self, validation, ok: bool, output, err: str | None):
        try:
            AIAuditLog.objects.create(
                document_validation=validation,
                validation_step="extraction",
                prompt_sent=EXTRACTION_PROMPT,
                raw_response=str(output.model_dump()) if output else (err or ""),
                success=ok,
                error_message=err or "",
                model_used="gemini-2.5-flash",
            )
        except Exception as exc:
            logger.error(
                "metadata_extractor: audit log failed validation=%s — %s", validation.id, exc
            )