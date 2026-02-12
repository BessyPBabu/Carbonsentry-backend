from PIL import Image
import io
import base64
from pdf2image import convert_from_path
import os


class DocumentPreprocessor:
    MAX_SIZE_MB = 4
    MAX_SIZE_BYTES = MAX_SIZE_MB * 1024 * 1024
    
    def process(self, file_path):
        """
        Process document and return base64 encoded image
        Returns: (success, image_base64, error)
        """
        try:
            if file_path.lower().endswith('.pdf'):
                return self._process_pdf(file_path)
            else:
                return self._process_image(file_path)
        except Exception as e:
            return False, None, str(e)
    
    def _process_pdf(self, file_path):
        """Convert PDF to image (first page)"""
        try:
            images = convert_from_path(file_path, first_page=1, last_page=1)
            
            if not images:
                return False, None, "No pages in PDF"
            
            return self._optimize_image(images[0])
            
        except Exception as e:
            return False, None, f"PDF processing error: {str(e)}"
    
    def _process_image(self, file_path):
        """Process image file"""
        try:
            img = Image.open(file_path)
            return self._optimize_image(img)
        except Exception as e:
            return False, None, f"Image processing error: {str(e)}"
    
    def _optimize_image(self, img):
        """Optimize image size and return base64"""
        try:
            # Convert to RGB if needed
            if img.mode in ('RGBA', 'P'):
                img = img.convert('RGB')
            
            # Resize if too large
            max_dimension = 2048
            if img.width > max_dimension or img.height > max_dimension:
                img.thumbnail((max_dimension, max_dimension), Image.Resampling.LANCZOS)
            
            # Save to bytes
            buffer = io.BytesIO()
            img.save(buffer, format='JPEG', quality=85, optimize=True)
            
            # Check size
            if buffer.tell() > self.MAX_SIZE_BYTES:
                # Reduce quality if still too large
                buffer = io.BytesIO()
                img.save(buffer, format='JPEG', quality=70, optimize=True)
            
            # Encode to base64
            buffer.seek(0)
            image_base64 = base64.b64encode(buffer.read()).decode('utf-8')
            
            return True, image_base64, None
            
        except Exception as e:
            return False, None, f"Image optimization error: {str(e)}"