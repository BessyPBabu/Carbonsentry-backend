import google.generativeai as genai
from django.conf import settings
import time
import logging

logger = logging.getLogger(__name__)


class GeminiClient:
    def __init__(self):
        if not settings.GEMINI_API_KEY:
            raise ValueError("GEMINI_API_KEY not configured in settings")
        
        genai.configure(api_key=settings.GEMINI_API_KEY)
        self.model = genai.GenerativeModel('models/gemini-2.5-flash')
    
    def call(self, prompt, image_data=None, temperature=0.1):
        try:
            start_time = time.time()
            
            if image_data:
                response = self.model.generate_content(
                    [prompt, image_data],
                    generation_config={'temperature': temperature}
                )
            else:
                response = self.model.generate_content(
                    prompt,
                    generation_config={'temperature': temperature}
                )
            
            response_time_ms = int((time.time() - start_time) * 1000)
            
            if not response.text:
                logger.error("Gemini returned empty response")
                return False, None, "Empty response from API", response_time_ms
            
            return True, response.text, None, response_time_ms
            
        except Exception as e:
            error_msg = str(e)
            logger.error(f"Gemini API error: {error_msg}")
            
            if 'quota' in error_msg.lower():
                error_msg = "API quota exceeded"
            elif 'api key' in error_msg.lower():
                error_msg = "Invalid API key"
            elif 'rate limit' in error_msg.lower():
                error_msg = "Rate limit exceeded"
            
            return False, None, error_msg, 0
    
    def call_with_retry(self, prompt, image_data=None, max_retries=2):
        for attempt in range(max_retries + 1):
            success, response, error, response_time = self.call(prompt, image_data)
            
            if success:
                return success, response, error, response_time
            
            if attempt < max_retries:
                wait_time = 2 ** attempt
                logger.warning(f"Retrying after {wait_time}s (attempt {attempt + 1}/{max_retries})")
                time.sleep(wait_time)
        
        return success, response, error, response_time