from .gemini_client import GeminiClient
from .validators import ResponseParser
from ..models import AIAuditLog
from ..constants import VALID_DOCUMENT_TYPES
from decimal import Decimal


class RelevanceClassifier:
    def __init__(self):
        self.client = GeminiClient()
        self.parser = ResponseParser()
    
    def classify(self, image_base64, validation):
        """Classify document relevance"""
        prompt = self._get_prompt()
        
        import PIL.Image
        import io
        import base64
        img_bytes = base64.b64decode(image_base64)
        img = PIL.Image.open(io.BytesIO(img_bytes))
        
        success, response, error, response_time = self.client.call_with_retry(prompt, img)
        
        AIAuditLog.objects.create(
            document_validation=validation,
            validation_step='relevance',
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
            'is_relevant': data.get('is_relevant', False),
            'document_type': data.get('document_type', 'Unknown'),
            'confidence': Decimal(str(data.get('confidence', 0))),
            'indicators': data.get('indicators', [])
        }
        
        return True, result, None
    
    def _get_prompt(self):
        valid_types = ', '.join(VALID_DOCUMENT_TYPES)
        
        return f"""Classify if this document is a carbon compliance certificate or emission report.

Valid document types:
{valid_types}

Return ONLY valid JSON:
{{
  "is_relevant": true/false,
  "document_type": "one of the valid types above or Unknown",
  "confidence": 0-100,
  "indicators": ["indicator1", "indicator2"]
}}

Criteria for is_relevant=true:
- Document mentions carbon, CO2, emissions, or greenhouse gases
- Contains certification or verification information
- Related to environmental compliance

Indicators to look for:
- ISO 14064 mentioned
- Carbon credit/offset terminology
- Emission values with units
- Verifier signatures
- Certificate numbers

Do not include any text outside the JSON object."""