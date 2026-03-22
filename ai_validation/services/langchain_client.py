import base64
import io
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

# Primary model — gemini-2.5-flash (20 req/day on free tier)
_PRIMARY_MODEL = "gemini-2.5-flash"

# Fallback model — gemini-2.0-flash has a separate daily quota from 2.5-flash.
# If the primary hits its daily limit, we transparently switch to the fallback.
_FALLBACK_MODEL = "gemini-2.0-flash"

_TEMPERATURE = 0.0

# Quota IDs that indicate a **daily** cap has been exhausted.
# For these we should NOT retry — just fail fast and let the fallback model run.
_DAILY_QUOTA_IDS = {
    "GenerateRequestsPerDayPerProjectPerModel-FreeTier",
    "GenerateRequestsPerDayPerProjectPerModel",
}

# Regex to extract the retry delay in seconds from a 429 error message.
# Google returns strings like "Please retry in 57.118s" or "retryDelay: '57s'"
_RETRY_DELAY_RE = re.compile(r"retry(?:Delay)?['\"]?\s*[:\s]+['\"]?(\d+(?:\.\d+)?)\s*s", re.I)


def _parse_retry_delay(error_str: str) -> float | None:
    """Extract the suggested retry delay in seconds from a 429 error string."""
    m = _RETRY_DELAY_RE.search(error_str)
    if m:
        return float(m.group(1))
    return None


def _is_daily_quota_error(error_str: str) -> bool:
    """Return True when the quota violation is a *daily* limit (not per-minute)."""
    return any(qid in error_str for qid in _DAILY_QUOTA_IDS)


def _is_quota_error(error_str: str) -> bool:
    return "RESOURCE_EXHAUSTED" in error_str or "429" in error_str


def _build_llm(model: str) -> ChatGoogleGenerativeAI:
    if not settings.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not configured in settings")
    return ChatGoogleGenerativeAI(
        model=model,
        google_api_key=settings.GEMINI_API_KEY,
        temperature=_TEMPERATURE,
    )


class LangChainClient:

    def __init__(self):
        self._primary_llm  = _build_llm(_PRIMARY_MODEL)
        self._fallback_llm = _build_llm(_FALLBACK_MODEL)

        # Track at the instance level whether the primary is daily-exhausted so
        # we can skip straight to the fallback for subsequent calls in the same
        # Celery worker process without wasting time on doomed 429s.
        self._primary_daily_exhausted = False

    def call_structured(
        self,
        prompt: str,
        image_base64: str,
        schema: Type[T],
        max_retries: int = 2,
    ) -> tuple[bool, T | None, str | None]:

        image_data = {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"},
        }
        message = HumanMessage(content=[{"type": "text", "text": prompt}, image_data])

        # Try primary model first (unless we already know it's daily-exhausted)
        if not self._primary_daily_exhausted:
            success, result, error = self._call_with_model(
                self._primary_llm, _PRIMARY_MODEL, message, schema, max_retries
            )
            if success:
                return True, result, None

            # If the primary failed due to a daily quota, record it and fall through
            if error and _is_daily_quota_error(error):
                logger.warning(
                    "LangChainClient: %s daily quota exhausted — switching to %s for all "
                    "subsequent calls in this process",
                    _PRIMARY_MODEL, _FALLBACK_MODEL,
                )
                self._primary_daily_exhausted = True
            elif error and not _is_quota_error(error):
                # Non-quota failure (auth, network, validation) — don't try fallback
                return False, None, error

        # Fallback model
        logger.info(
            "LangChainClient: trying fallback model %s | schema=%s",
            _FALLBACK_MODEL, schema.__name__,
        )
        return self._call_with_model(
            self._fallback_llm, _FALLBACK_MODEL, message, schema, max_retries
        )

    def _call_with_model(
        self,
        llm: ChatGoogleGenerativeAI,
        model_name: str,
        message: HumanMessage,
        schema: Type[T],
        max_retries: int,
    ) -> tuple[bool, T | None, str | None]:
        structured_llm = llm.with_structured_output(schema)
        last_error = None

        for attempt in range(max_retries + 1):
            try:
                start  = time.time()
                result: T = structured_llm.invoke([message])
                elapsed = round(time.time() - start, 2)

                logger.info(
                    "LangChainClient._call_with_model: success | model=%s schema=%s "
                    "attempt=%d elapsed=%ss",
                    model_name, schema.__name__, attempt, elapsed,
                )
                return True, result, None

            except ValidationError as exc:
                last_error = f"Pydantic validation failed: {exc}"
                logger.warning(
                    "LangChainClient: ValidationError on %s attempt %d — %s",
                    model_name, attempt, exc,
                )

            except Exception as exc:
                error_str = str(exc)
                last_error = error_str

                if _is_quota_error(error_str):
                    # Daily quota: no point retrying the same model
                    if _is_daily_quota_error(error_str):
                        logger.warning(
                            "LangChainClient: %s daily quota hit — not retrying this model",
                            model_name,
                        )
                        return False, None, last_error

                    # Per-minute quota: respect the API's suggested retry delay
                    suggested = _parse_retry_delay(error_str)
                    if suggested and suggested > 0:
                        wait = min(suggested + 1, 65)   # cap at 65s so tasks don't stall forever
                        logger.info(
                            "LangChainClient: %s rate-limited — waiting %.0fs as suggested "
                            "(attempt %d/%d)",
                            model_name, wait, attempt + 1, max_retries + 1,
                        )
                        time.sleep(wait)
                    else:
                        # Fallback exponential backoff if we couldn't parse the delay
                        wait = 2 ** attempt
                        logger.info(
                            "LangChainClient: %s rate-limited — waiting %ds (attempt %d/%d)",
                            model_name, wait, attempt + 1, max_retries + 1,
                        )
                        time.sleep(wait)

                elif any(k in error_str.lower() for k in ("api key", "permission")):
                    # Auth failure — retrying won't help
                    logger.error("LangChainClient: auth error on %s — %s", model_name, error_str)
                    return False, None, last_error

                else:
                    logger.warning(
                        "LangChainClient: error on %s attempt %d — %s",
                        model_name, attempt, error_str,
                    )
                    if attempt < max_retries:
                        time.sleep(2 ** attempt)

            # If we've used all retries, stop
            if attempt >= max_retries:
                break

        logger.error(
            "LangChainClient._call_with_model: all attempts failed | model=%s schema=%s "
            "last_error=%s",
            model_name, schema.__name__, last_error,
        )
        return False, None, last_error