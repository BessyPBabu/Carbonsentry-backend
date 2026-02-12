from .gemini_client import GeminiClient
from .validators import ResponseParser
from ..models import AIAuditLog
from decimal import Decimal


class ReadabilityChecker:
    def __init__(self):
        self.client = GeminiClient()
        self.parser = ResponseParser()
    
    def check(self, image_base64, validation):
        """Check if document is readable"""
        prompt = self._get_prompt()
        
        # Prepare image for Gemini
        import PIL.Image
        import io
        import base64
        img_bytes = base64.b64decode(image_base64)
        img = PIL.Image.open(io.BytesIO(img_bytes))
        
        success, response, error, response_time = self.client.call_with_retry(prompt, img)
        
        # Log audit trail
        AIAuditLog.objects.create(
            document_validation=validation,
            validation_step='readability',
            prompt_sent=prompt,
            raw_response=response if success else error,
            success=success,
            error_message=error or '',
            model_used='gemini-1.5-flash'
        )
        
        if not success:
            return False, None, error
        
        # Parse response
        json_success, data, parse_error = self.parser.parse_json(response)
        
        if not json_success:
            return False, None, parse_error
        
        result = {
            'is_readable': data.get('is_readable', False),
            'quality_score': Decimal(str(data.get('quality_score', 0))),
            'language': data.get('language', 'Unknown'),
            'issues': data.get('issues', [])
        }
        
        return True, result, None
    
    def _get_prompt(self):
        return """Analyze if this document is readable and processable.

Return ONLY valid JSON with these exact fields:
{
  "is_readable": true/false,
  "quality_score": 0-100,
  "language": "English" or other,
  "issues": ["issue1", "issue2"]
}

Criteria for is_readable=true:
- Text is clearly visible and legible
- Document is not severely corrupted
- At least 70% of content is readable

Issues to detect:
- Low resolution
- Blurry text
- Partial scan
- Rotated/skewed
- Heavy watermark obscuring text

Do not include any text outside the JSON object."""