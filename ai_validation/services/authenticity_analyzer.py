import base64
import io
import logging

import PIL.Image

from .langchain_client import LangChainClient, CALL_TIMEOUT_AUTHENTICITY
from .prompt_registry import AUTHENTICITY_PROMPT
from .document_cache import get_cached, set_cached
from ..models import AIAuditLog
from .schemas import AuthenticityOutput

logger = logging.getLogger(__name__)


class AuthenticityAnalyzer:

    def __init__(self):
        self.client = LangChainClient()

    def analyze(
        self, image_base64: str, validation, file_path: str = ""
    ) -> tuple[bool, dict | None, str | None]:
        if file_path:
            cached = get_cached(file_path, "authenticity")
            if cached:
                return True, cached, None

        try:
            PIL.Image.open(io.BytesIO(base64.b64decode(image_base64)))
        except Exception as exc:
            logger.warning(
                "authenticity_analyzer: image decode failed validation=%s — %s", validation.id, exc
            )
            return True, self._default(), None

        ok, output, err = self.client.call_structured(
            prompt=AUTHENTICITY_PROMPT,
            image_base64=image_base64,
            schema=AuthenticityOutput,
            step="authenticity",
            timeout_seconds=CALL_TIMEOUT_AUTHENTICITY,
        )

        self._log(validation, ok, output, err)

        if not ok or output is None:
            logger.warning(
                "authenticity_analyzer: gemini failed validation=%s — %s", validation.id, err
            )
            return True, self._default(), None

        result = {
            "score": float(output.score),
            "indicators": output.indicators[:10],
            "red_flags": output.red_flags[:10],
        }

        if file_path:
            set_cached(file_path, "authenticity", result)

        return True, result, None

    def _log(self, validation, ok: bool, output, err: str | None):
        try:
            AIAuditLog.objects.create(
                document_validation=validation,
                validation_step="authenticity",
                prompt_sent=AUTHENTICITY_PROMPT,
                raw_response=str(output.model_dump()) if output else (err or ""),
                success=ok,
                error_message=err or "",
                model_used="gemini-2.5-flash",
            )
        except Exception as exc:
            logger.error(
                "authenticity_analyzer: audit log failed validation=%s — %s", validation.id, exc
            )

    def _default(self) -> dict:
        return {
            "score": 70.0,
            "indicators": ["api_unavailable_defaulted"],
            "red_flags": [],
        }