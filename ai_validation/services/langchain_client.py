import concurrent.futures as _cf
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


CALL_TIMEOUT_RELEVANCE    = 20
CALL_TIMEOUT_AUTHENTICITY = 20
CALL_TIMEOUT_EXTRACTION   = 30


_MAX_RETRY_SLEEP = 10

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


def _build_llm(model: str) -> ChatGoogleGenerativeAI:
    if not getattr(settings, "GEMINI_API_KEY", None):
        raise ValueError("GEMINI_API_KEY is not configured")
    # request_timeout is intentionally omitted — it maps to the gRPC deadline and
    # gemini-2.5-flash rejects values below 10 s with INVALID_ARGUMENT.
    # Wall-clock enforcement is done in _timed_invoke via threading.
    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=settings.GEMINI_API_KEY,
        temperature=0.0,
    )


def _timed_invoke(structured_llm, message: HumanMessage, timeout_seconds: int):
   
    pool = _cf.ThreadPoolExecutor(max_workers=1)
    fut = pool.submit(structured_llm.invoke, [message])
    try:
        # fut.result() re-raises any exception thrown inside the thread
        return fut.result(timeout=timeout_seconds)
    except _cf.TimeoutError:
        raise
    finally:
        # Non-blocking — if the thread is still hitting Gemini it runs to
        # completion in the background without blocking this task.
        pool.shutdown(wait=False)


class LangChainClient:

    def __init__(self):
        self._primary  = _build_llm(_PRIMARY_MODEL)
        self._fallback = _build_llm(_FALLBACK_MODEL)
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
                logger.warning(
                    "langchain_client: %s daily quota exhausted — switching to fallback permanently",
                    _PRIMARY_MODEL,
                )
                self._primary_daily_exhausted = True
            else:
                logger.warning(
                    "langchain_client: primary model failed, falling back | step=%s error=%s",
                    step, (err or "")[:200],
                )

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
        last_err = None

        for attempt in range(max_retries + 1):
            t0 = time.monotonic()
            try:
                result = _timed_invoke(structured, message, timeout_seconds)
                elapsed = round(time.monotonic() - t0, 2)
                gemini_call_counter.labels(step=step, success="true").inc()
                logger.info(
                    "langchain_client: ok model=%s step=%s attempt=%d elapsed=%.2fs",
                    model_name, step, attempt, elapsed,
                )
                return True, result, None

            except _cf.TimeoutError:
                elapsed = round(time.monotonic() - t0, 2)
                last_err = f"invoke_timeout_after_{timeout_seconds}s"
                gemini_call_counter.labels(step=step, success="false").inc()
                logger.warning(
                    "langchain_client: invoke timed out model=%s step=%s "
                    "timeout=%ds elapsed=%.2fs",
                    model_name, step, timeout_seconds, elapsed,
                )
                # timeout is not retriable — the API is too slow right now
                return False, None, last_err

            except ValidationError as exc:
                last_err = f"schema_validation_failed:{exc}"
                gemini_call_counter.labels(step=step, success="false").inc()
                logger.warning(
                    "langchain_client: pydantic error model=%s step=%s — %s",
                    model_name, step, exc,
                )
                # schema errors won't improve with a retry
                break

            except Exception as exc:
                elapsed = round(time.monotonic() - t0, 2)
                err_str = str(exc)
                last_err = err_str
                gemini_call_counter.labels(step=step, success="false").inc()

                if _is_daily_quota(err_str):
                    logger.warning(
                        "langchain_client: daily quota model=%s step=%s", model_name, step
                    )
                    return False, None, last_err

                if _is_rate_limited(err_str):
                    suggested = _parse_retry_delay(err_str)
                    # cap sleep so multiple rate-limited calls don't exhaust the Celery
                    # soft time limit (120 s) just on wait time
                    wait = min(suggested + 1 if suggested else 2 ** attempt, _MAX_RETRY_SLEEP)
                    if wait > 0 and attempt < max_retries:
                        logger.info(
                            "langchain_client: rate limited model=%s step=%s sleeping=%.0fs",
                            model_name, step, wait,
                        )
                        time.sleep(wait)
                    continue

                if any(k in err_str.lower() for k in ("api key", "permission", "unauthorized")):
                    logger.error(
                        "langchain_client: auth error model=%s — %s", model_name, err_str[:200]
                    )
                    return False, None, last_err

                logger.warning(
                    "langchain_client: error model=%s step=%s attempt=%d elapsed=%.2fs — %s",
                    model_name, step, attempt, elapsed, err_str[:300],
                )
                if attempt < max_retries:
                    # exponential back-off, capped to protect Celery time budget
                    sleep_for = min(2 ** attempt, _MAX_RETRY_SLEEP)
                    time.sleep(sleep_for)

        logger.error(
            "langchain_client: all attempts failed model=%s step=%s last_err=%s",
            model_name, step, (last_err or "")[:300],
        )
        return False, None, last_err