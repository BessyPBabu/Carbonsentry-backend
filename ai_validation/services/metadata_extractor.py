from .gemini_client import GeminiClient
from .validators import ResponseParser, DataValidator
from ..models import AIAuditLog, ExtractedMetadata
from decimal import Decimal


class MetadataExtractor:
    def __init__(self):
        self.client = GeminiClient()
        self.parser = ResponseParser()
        self.validator = DataValidator()
    
    def extract(self, image_base64, validation):
        """Extract structured metadata from document"""
        prompt = self._get_prompt()
        
        import PIL.Image
        import io
        import base64
        img_bytes = base64.b64decode(image_base64)
        img = PIL.Image.open(io.BytesIO(img_bytes))
        
        success, response, error, response_time = self.client.call_with_retry(prompt, img)
        
        AIAuditLog.objects.create(
            document_validation=validation,
            validation_step='extraction',
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
        
        # Validate and clean data
        cleaned_data = self._validate_extracted_data(data)
        
        # Create ExtractedMetadata
        metadata = ExtractedMetadata.objects.create(
            document_validation=validation,
            document=validation.document,
            co2_value=cleaned_data.get('co2_value'),
            co2_unit=cleaned_data.get('co2_unit'),
            co2_extraction_confidence=cleaned_data.get('co2_confidence'),
            issue_date=cleaned_data.get('issue_date'),
            issue_date_confidence=cleaned_data.get('issue_date_confidence'),
            expiry_date=cleaned_data.get('expiry_date'),
            expiry_date_confidence=cleaned_data.get('expiry_date_confidence'),
            issuing_authority=cleaned_data.get('issuing_authority', ''),
            issuing_authority_confidence=cleaned_data.get('issuing_authority_confidence'),
            certificate_number=cleaned_data.get('certificate_number', ''),
            verification_standard=cleaned_data.get('verification_standard', ''),
            raw_extracted_data=data
        )
        
        return True, metadata, None
    
    def _validate_extracted_data(self, data):
        """Validate and clean extracted data"""
        cleaned = {}
        
        # Validate CO2 value
        co2_value = data.get('co2_value')
        if co2_value is not None:
            valid, result = self.validator.validate_co2_value(co2_value)
            if valid:
                cleaned['co2_value'] = Decimal(str(result))
                cleaned['co2_confidence'] = Decimal(str(data.get('co2_confidence', 0)))
            else:
                cleaned['co2_value'] = None
                cleaned['co2_confidence'] = Decimal('0')
        
        # Validate and normalize unit
        cleaned['co2_unit'] = self.validator.normalize_unit(data.get('co2_unit'))
        
        # Validate dates
        issue_date_str = data.get('issue_date')
        if issue_date_str:
            valid, result = self.validator.validate_date(issue_date_str)
            if valid and result:
                cleaned['issue_date'] = result
                cleaned['issue_date_confidence'] = Decimal(str(data.get('issue_date_confidence', 0)))
        
        expiry_date_str = data.get('expiry_date')
        if expiry_date_str:
            valid, result = self.validator.validate_date(expiry_date_str)
            if valid and result:
                cleaned['expiry_date'] = result
                cleaned['expiry_date_confidence'] = Decimal(str(data.get('expiry_date_confidence', 0)))
        
        # Other fields
        cleaned['issuing_authority'] = data.get('issuing_authority', '')[:500]
        cleaned['issuing_authority_confidence'] = Decimal(str(data.get('issuing_authority_confidence', 0)))
        cleaned['certificate_number'] = data.get('certificate_number', '')[:255]
        cleaned['verification_standard'] = data.get('verification_standard', '')[:100]
        
        return cleaned
    
    def _get_prompt(self):
        return """Extract structured data from this carbon compliance certificate.

Return ONLY valid JSON with these exact fields:
{
  "co2_value": <number or null>,
  "co2_unit": <"tonnes" | "kg" | "metric_tons" | null>,
  "co2_confidence": <0-100>,
  "issue_date": <"YYYY-MM-DD" or null>,
  "issue_date_confidence": <0-100>,
  "expiry_date": <"YYYY-MM-DD" or null>,
  "expiry_date_confidence": <0-100>,
  "issuing_authority": <string or null>,
  "issuing_authority_confidence": <0-100>,
  "certificate_number": <string or null>,
  "verification_standard": <string or null>
}

CRITICAL RULES:
1. If a field is not clearly visible, set it to null
2. For co2_value, extract ONLY the numerical value (e.g., from "1250 tonnes CO2e" extract 1250)
3. For dates, convert to YYYY-MM-DD format
4. Do NOT guess or infer values not explicitly present
5. Confidence score indicates how certain you are (100 = very certain, 0 = not found)

Look for:
- CO2/emission values: May be labeled as "Total Emissions", "Carbon Footprint", "CO2 Equivalent"
- Issue date: Look for "Issue Date", "Date of Issue", "Certified on"
- Expiry date: Look for "Valid Until", "Expiry", "Expires"
- Issuing authority: Organization that issued the certificate
- Certificate number: Unique identifier

Do not include any text outside the JSON object."""