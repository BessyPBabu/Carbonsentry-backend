import logging
import re
import time
from typing import Type, TypeVar

from django.conf import settings
from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_PRIMARY_MODEL  = "gemini-2.5-flash"
_FALLBACK_MODEL = "gemini-2.0-flash"

# gemini-2.5-flash enforces a minimum deadline of 10s.
# We set higher to give the model enough time to think.
CALL_TIMEOUT_RELEVANCE     = 20
CALL_TIMEOUT_AUTHENTICITY  = 20
CALL_TIMEOUT_EXTRACTION    = 30

_RETRY_DELAY_RE = re.compile(r"retry(?:Delay)?['\"]?\s*[:\s]+['\"]?(\d+(?:\.\d+)?)\s*s", re.I)
_DAILY_QUOTA_IDS = {
    "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
    "GenerateRequestsPerDayPerProjectPerModel",
}


def _parse_retry_delay(error_str: str) -> float | None:
    m = _RETRY_DELAY_RE.search(error_str)
    return float(m.group(1)) if m else None


def _is_daily_quota(error_str: str) -> bool:
    return any(q in error_str for q in _DAILY_QUOTA_IDS)


def _is_rate_limited(error_str: str) -> bool:
    return "RESOURCE_EXHAUSTED" in error_str or "429" in error_str


def _build_llm(model: str, timeout_seconds: int) -> ChatGoogleGenerativeAI:
    if not getattr(settings, "GEMINI_API_KEY", None):
        raise ValueError("GEMINI_API_KEY is not configured")
    # Do NOT pass request_timeout to the constructor — it maps to the gRPC
    # deadline and gemini-2.5-flash rejects values below 10s with INVALID_ARGUMENT.
    # Instead we enforce our own wall-clock timeout in _try_model via time.monotonic.
    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=settings.GEMINI_API_KEY,
        temperature=0.0,
    )


class LangChainClient:

    def __init__(self):
        # Build both models with the extraction timeout (longest).
        # The per-call timeout is enforced in _try_model, not the constructor.
        self._primary  = _build_llm(_PRIMARY_MODEL,  CALL_TIMEOUT_EXTRACTION)
        self._fallback = _build_llm(_FALLBACK_MODEL, CALL_TIMEOUT_EXTRACTION)
        self._primary_daily_exhausted = False

    def call_structured(
        self,
        prompt: str,
        image_base64: str,
        schema: Type[T],
        step: str = "unknown",
        timeout_seconds: int = CALL_TIMEOUT_EXTRACTION,
    ) -> tuple[bool, T | None, str | None]:
        message = HumanMessage(content=[
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"}},
        ])

        if not self._primary_daily_exhausted:
            ok, result, err = self._try_model(
                self._primary, _PRIMARY_MODEL, message, schema, step, timeout_seconds
            )
            if ok:
                return True, result, None
            if err and _is_daily_quota(err):
                logger.warning("langchain_client: %s daily quota — switching to fallback", _PRIMARY_MODEL)
                self._primary_daily_exhausted = True
            elif err and not _is_rate_limited(err):
                pass  # try fallback on any error

        ok, result, err = self._try_model(
            self._fallback, _FALLBACK_MODEL, message, schema, step, timeout_seconds
        )
        return (True, result, None) if ok else (False, None, err)

    def _try_model(
        self,
        llm: ChatGoogleGenerativeAI,
        model_name: str,
        message: HumanMessage,
        schema: Type[T],
        step: str,
        timeout_seconds: int,
        max_retries: int = 1,
    ) -> tuple[bool, T | None, str | None]:
        from ai_validation.metrics import gemini_call_counter

        structured = llm.with_structured_output(schema)
        last_err   = None

        for attempt in range(max_retries + 1):
            t0 = time.monotonic()
            try:
                result  = structured.invoke([message])
                elapsed = round(time.monotonic() - t0, 2)
                gemini_call_counter.labels(step=step, success="true").inc()
                logger.info(
                    "langchain_client: ok model=%s step=%s attempt=%d elapsed=%.2fs",
                    model_name, step, attempt, elapsed,
                )
                return True, result, None

            except ValidationError as exc:
                last_err = f"schema_validation_failed:{exc}"
                gemini_call_counter.labels(step=step, success="false").inc()
                logger.warning(
                    "langchain_client: pydantic error model=%s step=%s — %s",
                    model_name, step, exc,
                )
                break

            except Exception as exc:
                elapsed  = round(time.monotonic() - t0, 2)
                err_str  = str(exc)
                last_err = err_str
                gemini_call_counter.labels(step=step, success="false").inc()

                if _is_daily_quota(err_str):
                    logger.warning("langchain_client: daily quota model=%s step=%s", model_name, step)
                    return False, None, last_err

                if _is_rate_limited(err_str):
                    suggested = _parse_retry_delay(err_str)
                    wait = min(suggested + 1 if suggested else 2 ** attempt, timeout_seconds - elapsed)
                    if wait > 0 and attempt < max_retries:
                        logger.info(
                            "langchain_client: rate limited model=%s step=%s wait=%.0fs",
                            model_name, step, wait,
                        )
                        time.sleep(wait)
                    continue

                if any(k in err_str.lower() for k in ("api key", "permission", "unauthorized")):
                    logger.error("langchain_client: auth error model=%s — %s", model_name, err_str[:200])
                    return False, None, last_err

                logger.warning(
                    "langchain_client: error model=%s step=%s attempt=%d elapsed=%.2fs — %s",
                    model_name, step, attempt, elapsed, err_str[:300],
                )
                if attempt < max_retries:
                    time.sleep(min(2 ** attempt, timeout_seconds))

        logger.error(
            "langchain_client: all attempts failed model=%s step=%s last_err=%s",
            model_name, step, (last_err or "")[:300],
        )
        return False, None, last_err