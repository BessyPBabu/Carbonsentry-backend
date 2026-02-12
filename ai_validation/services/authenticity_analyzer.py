from .gemini_client import GeminiClient
from .validators import ResponseParser
from ..models import AIAuditLog
from decimal import Decimal


class AuthenticityAnalyzer:
    def __init__(self):
        self.client = GeminiClient()
        self.parser = ResponseParser()
    
    def analyze(self, image_base64, validation):
        """Analyze document authenticity"""
        prompt = self._get_prompt()
        
        import PIL.Image
        import io
        import base64
        img_bytes = base64.b64decode(image_base64)
        img = PIL.Image.open(io.BytesIO(img_bytes))
        
        success, response, error, response_time = self.client.call_with_retry(prompt, img)
        
        AIAuditLog.objects.create(
            document_validation=validation,
            validation_step='authenticity',
            prompt_sent=prompt,
            raw_response=response if success else error,
            success=success,
            error_message=error or '',
            model_used='gemini-1.5-flash'
        )
        
        if not success:
            return False, None, error
        
        json_success, data, parse_error = self.parser.parse_json(response)
        
        if not json_success:
            return False, None, parse_error
        
        result = {
            'score': Decimal(str(data.get('score', 50))),
            'indicators': data.get('indicators', []),
            'red_flags': data.get('red_flags', [])
        }
        
        return True, result, None
    
    def _get_prompt(self):
        return """Analyze the authenticity of this carbon compliance certificate.

Return ONLY valid JSON:
{
  "score": 0-100,
  "indicators": ["positive indicator1", "positive indicator2"],
  "red_flags": ["concern1", "concern2"]
}

Authenticity Indicators (increase score):
- Official logos present
- Professional formatting
- Watermark or security features
- Signature present
- Certificate number visible
- Issuing authority clearly stated
- Consistent fonts and layout

Red Flags (decrease score):
- Poor image quality (suggests screenshot)
- Missing signature
- No certificate number
- No issuing authority
- Issue date in the future
- Expiry date before issue date
- Unprofessional layout
- Spelling errors in official text

Calculate score based on:
- 80-100: Strong authenticity indicators, no red flags
- 60-79: Some indicators, minor concerns
- 40-59: Mixed signals
- 0-39: Multiple red flags or missing critical elements

Do not include any text outside the JSON object."""