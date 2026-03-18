import io
import base64
import logging

import PIL.Image

from .langchain_client import LangChainClient
from .schemas import AuthenticityOutput
from .document_cache import get_cached, set_cached
from ..models import AIAuditLog

logger = logging.getLogger(__name__)

_PROMPT = """Analyze the authenticity of this carbon compliance document.

Scoring guide (50-100, floor is 50 because digital docs are normal and valid):
- 75-100: has org name/header, professional layout, cert number, date info, issuing org
- 60-74: some official elements but a few missing
- 50-59: missing most official elements

IMPORTANT: Digital/computer-generated documents are NORMAL. Do NOT penalise for being digital.

Only list as red_flags genuine concerns like:
- "SAMPLE", "TEST", "DRAFT", "VOID" watermark
- Lorem ipsum placeholder text
- Future issue date
- Expiry date before issue date
- Unfilled placeholders like [COMPANY NAME]

Do NOT flag: being digital, missing physical signature, missing stamp, simple layout."""


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
            "score": 65.0,
            "indicators": ["defaulted due to processing error"],
            "red_flags": [],
        }