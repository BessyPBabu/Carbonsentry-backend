import google.generativeai as genai
from django.conf import settings
import json
import time

genai.configure(api_key=settings.GEMINI_API_KEY)


class GeminiClient:
    def __init__(self):
        self.model = genai.GenerativeModel('gemini-1.5-flash')
    
    def call(self, prompt, image_data=None, temperature=0.1):
        """
        Call Gemini API with prompt and optional image
        Returns: (success, response_text, error)
        """
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
            
            return True, response.text, None, response_time_ms
            
        except Exception as e:
            return False, None, str(e), 0
    
    def call_with_retry(self, prompt, image_data=None, max_retries=2):
        """Call with exponential backoff retry"""
        for attempt in range(max_retries + 1):
            success, response, error, response_time = self.call(prompt, image_data)
            
            if success:
                return success, response, error, response_time
            
            if attempt < max_retries:
                time.sleep(2 ** attempt)  # Exponential backoff
        
        return success, response, error, response_time