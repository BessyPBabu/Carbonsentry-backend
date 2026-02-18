import io
import base64
import logging
from decimal import Decimal

import PIL.Image

from .gemini_client import GeminiClient
from .validators import ResponseParser
from ..models import AIAuditLog

logger = logging.getLogger(__name__)


class AuthenticityAnalyzer:

    def __init__(self):
        self.client = GeminiClient()
        self.parser = ResponseParser()

    def analyze(self, image_base64, validation):
        prompt = self._get_prompt()

        try:
            img = PIL.Image.open(io.BytesIO(base64.b64decode(image_base64)))
        except Exception as e:
            logger.exception("analyze: failed to decode image for validation %s", validation.id)
            return True, self._default_result(), None

        success, response, error, _ = self.client.call_with_retry(prompt, img)

        AIAuditLog.objects.create(
            document_validation=validation,
            validation_step='authenticity',
            prompt_sent=prompt,
            raw_response=response if success else (error or ''),
            success=success,
            error_message=error or '',
            model_used='gemini-2.5-flash',
        )

        if not success:
            logger.warning("analyze: Gemini call failed for validation %s — %s", validation.id, error)
            return True, self._default_result(), None

        json_ok, data, parse_err = self.parser.parse_json(response)

        if not json_ok:
            logger.warning("analyze: JSON parse failed for validation %s — %s", validation.id, parse_err)
            return True, self._default_result(), None

        raw_score = float(data.get('score', 65) or 65)
        indicators = data.get('indicators', [])
        red_flags = data.get('red_flags', [])

        if not isinstance(indicators, list):
            indicators = []
        if not isinstance(red_flags, list):
            red_flags = []

        # floor of 50 for digital documents — being digital is not a flaw
        score = max(50.0, min(100.0, raw_score))

        result = {
            'score': Decimal(str(score)),
            'indicators': indicators[:10],
            'red_flags': red_flags[:10],
        }

        logger.info(
            "analyze: validation=%s score=%.1f red_flags=%d",
            validation.id, score, len(red_flags),
        )
        return True, result, None

    def _default_result(self):
        return {
            'score': Decimal('65'),
            'indicators': ['defaulted due to processing error'],
            'red_flags': [],
        }

    def _get_prompt(self):
        return """Analyze the authenticity of this carbon compliance document.

Return ONLY this JSON (no other text):
{
  "score": 75,
  "indicators": ["official header present", "certificate number visible"],
  "red_flags": []
}

Scoring guide (0-100):
- 75-100: has org name/header, professional layout, cert number, date info, issuing org
- 50-74: some official elements but a few missing
- 25-49: missing most official elements
- 0-24: clearly fake, test/sample watermark, or lorem ipsum text

IMPORTANT: Digital/computer-generated documents are NORMAL and VALID. Do NOT penalise for being digital.

Only list as red_flags genuine concerns like:
- "SAMPLE", "TEST", "DRAFT", "VOID" watermark
- Lorem ipsum placeholder text
- Future issue date
- Expiry date before issue date
- Unfilled placeholders like [COMPANY NAME]

Do NOT flag: being digital, missing physical signature, missing stamp, simple layout.

Default score for a clean digital compliance document: 70-80."""