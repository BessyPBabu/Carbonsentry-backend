import base64
import io
import logging
import time
from typing import Type, TypeVar

from django.conf import settings
from langchain_core.messages import HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, ValidationError

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

# Gemini model to use — same as before, just via LangChain now
_MODEL = "gemini-2.5-flash"

# temperature=0 makes the model as deterministic as possible
# this is the single biggest lever for consistent results
_TEMPERATURE = 0.0


def _build_llm() -> ChatGoogleGenerativeAI:
    if not settings.GEMINI_API_KEY:
        raise ValueError("GEMINI_API_KEY not configured in settings")
    return ChatGoogleGenerativeAI(
        model=_MODEL,
        google_api_key=settings.GEMINI_API_KEY,
        temperature=_TEMPERATURE,
    )


class LangChainClient:
    """
    Drop-in replacement for GeminiClient.
    Uses LangChain's with_structured_output() so the LLM response is
    automatically parsed and validated against a Pydantic schema.
    This eliminates the entire category of JSON-parse failures and
    silent default fallbacks that caused inconsistent validation results.
    """

    def __init__(self):
        self._llm = _build_llm()

    def call_structured(
        self,
        prompt: str,
        image_base64: str,
        schema: Type[T],
        max_retries: int = 2,
    ) -> tuple[bool, T | None, str | None]:
        """
        Call Gemini with a Pydantic schema.
        Returns (success, parsed_output, error_message).
        On success, output is a fully validated instance of `schema`.
        On failure, output is None and error_message describes what went wrong.
        """
        structured_llm = self._llm.with_structured_output(schema)

        # convert base64 image to the format LangChain / Gemini expects
        image_data = {
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{image_base64}"},
        }

        message = HumanMessage(content=[{"type": "text", "text": prompt}, image_data])

        last_error = None
        for attempt in range(max_retries + 1):
            try:
                start = time.time()
                result: T = structured_llm.invoke([message])
                elapsed = round(time.time() - start, 2)

                logger.info(
                    "LangChainClient.call_structured: success | schema=%s attempt=%d elapsed=%ss",
                    schema.__name__, attempt, elapsed,
                )
                return True, result, None

            except ValidationError as exc:
                # Pydantic rejected the LLM output — log it and retry
                last_error = f"Pydantic validation failed: {exc}"
                logger.warning(
                    "LangChainClient.call_structured: ValidationError on attempt %d — %s",
                    attempt, exc,
                )

            except Exception as exc:
                error_str = str(exc)
                last_error = error_str
                logger.warning(
                    "LangChainClient.call_structured: error on attempt %d — %s",
                    attempt, error_str,
                )

                # don't retry on auth / quota errors — they won't self-heal
                if any(k in error_str.lower() for k in ("api key", "quota", "permission")):
                    break

            if attempt < max_retries:
                wait = 2 ** attempt
                logger.info("LangChainClient: retrying in %ds", wait)
                time.sleep(wait)

        logger.error(
            "LangChainClient.call_structured: all attempts failed | schema=%s last_error=%s",
            schema.__name__, last_error,
        )
        return False, None, last_error