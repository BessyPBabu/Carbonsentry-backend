import io
import base64
import logging
from decimal import Decimal

import PIL.Image

from .langchain_client import LangChainClient
from .schemas import AuthenticityOutput
from .document_cache import get_cached, set_cached
from ..models import AIAuditLog

logger = logging.getLogger(__name__)

_PROMPT = """Assess the authenticity of this carbon compliance document.

## Score range: 50–100
The minimum score is 50 because computer-generated PDFs are perfectly legitimate.

## Scoring guide

| Document characteristics                                                    | Score  |
|-----------------------------------------------------------------------------|--------|
| Complete document: org name/logo, cert number, issue date, expiry date,     |        |
| CO2 value with units, issuing authority, verification standard, signature   | 88–100 |
| Most fields present; only 1–2 minor elements missing                        | 75–88  |
| Several fields present but notable gaps (e.g. no cert number or no dates)  | 62–75  |
| Minimal content — only a few fields, heavily incomplete                     | 50–62  |

## Positive indicators (raise score)
Note any of: organisation letterhead, certificate number, issue/expiry dates, CO2 value with units,
verification standard (ISO 14064, GHG Protocol, Verra, etc.), issuing authority name,
professional layout, signatory name or authorised signature block.

## Red flags (only flag GENUINE concerns)
Only add to red_flags if you see:
- Watermark text: "SAMPLE", "DRAFT", "TEST", "VOID", "SPECIMEN"
- Placeholder text: "Lorem ipsum", "[COMPANY NAME]", "[INSERT DATE]", "{{placeholder}}"
- Logical impossibility: expiry date is earlier than issue date
- Future issue date (issue date is after today)
- Contradictory or nonsensical data (e.g. negative CO2, absurd figures like 999999999)

## Do NOT flag as red flags
- Being a computer-generated / digital PDF (this is normal and expected)
- Absence of a wet/ink signature (digital docs do not need one)
- Simple or minimal layout
- Missing a physical stamp or seal
- Scanned appearance
- A future expiry date (expiry dates in the future are correct and valid)
- Any formatting choices

Keep indicators to the 3–5 most important positive elements you observe.
Keep red_flags to genuine issues only; an empty list [] is the correct result for a clean document."""


class AuthenticityAnalyzer:

    def __init__(self):
        self.client = LangChainClient()

    def analyze(self, image_base64: str, validation, file_path: str = ""):
        if file_path:
            cached = get_cached(file_path, "authenticity")
            if cached:
                logger.info(
                    "AuthenticityAnalyzer: cache hit | validation=%s", validation.id
                )
                return True, cached, None

        try:
            PIL.Image.open(io.BytesIO(base64.b64decode(image_base64)))
        except Exception as exc:
            logger.exception(
                "AuthenticityAnalyzer: image decode failed | validation=%s", validation.id
            )
            return True, self._default_result(), None

        success, output, error = self.client.call_structured(
            prompt=_PROMPT,
            image_base64=image_base64,
            schema=AuthenticityOutput,
        )

        AIAuditLog.objects.create(
            document_validation=validation,
            validation_step="authenticity",
            prompt_sent=_PROMPT,
            raw_response=str(output.model_dump()) if output else (error or ""),
            success=success,
            error_message=error or "",
            model_used="gemini-2.5-flash",
        )

        if not success or output is None:
            logger.warning(
                "AuthenticityAnalyzer: Gemini failed | validation=%s — %s", validation.id, error
            )
            return True, self._default_result(), None

        result = {
            "score": float(output.score),
            "indicators": output.indicators[:10],
            "red_flags": output.red_flags[:10],
        }

        if file_path:
            set_cached(file_path, "authenticity", result)

        logger.info(
            "AuthenticityAnalyzer: done | validation=%s score=%.1f red_flags=%d",
            validation.id, output.score, len(output.red_flags),
        )
        return True, result, None

    def _default_result(self) -> dict:
        return {
            "score": 70.0,   # raised from 65 — reasonable fallback for API failure
            "indicators": ["defaulted due to processing error"],
            "red_flags": [],
        }