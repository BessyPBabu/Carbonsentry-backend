import io
import os
import base64
import logging
import subprocess

from PIL import Image
from pdf2image import convert_from_path

logger = logging.getLogger(__name__)

MAX_SIZE_BYTES = 4 * 1024 * 1024  


class DocumentPreprocessor:

    def __init__(self):
        self._check_poppler()

    def _check_poppler(self):
        try:
            subprocess.run(['pdftoppm', '-v'], capture_output=True)
        except FileNotFoundError:
            logger.warning(
                "poppler-utils not found — PDF processing will fail. "
                "Install: sudo apt-get install poppler-utils"
            )

    def process(self, file_path):
        if not os.path.exists(file_path):
            logger.error("process: file not found at %s", file_path)
            return False, None, f"File not found: {file_path}"

        if os.path.getsize(file_path) == 0:
            logger.error("process: file is empty at %s", file_path)
            return False, None, f"File is empty: {file_path}"

        logger.info("process: starting — %s (%d bytes)", file_path, os.path.getsize(file_path))

        try:
            ext = os.path.splitext(file_path)[1].lower()
            if ext == '.pdf':
                return self._process_pdf(file_path)
            return self._process_image(file_path)
        except Exception as e:
            logger.exception("process: unexpected error for %s", file_path)
            return False, None, f"Processing error: {e}"

    def _process_pdf(self, file_path):
        try:
            images = convert_from_path(file_path, first_page=1, last_page=1, dpi=300, fmt='jpeg')
            if not images:
                logger.error("_process_pdf: no pages found in %s", file_path)
                return False, None, "No pages found in PDF"

            logger.info("_process_pdf: converted page size %s", images[0].size)
            return self._optimize_image(images[0])

        except Exception as e:
            error_msg = str(e)
            logger.exception("_process_pdf: failed for %s", file_path)
            if 'poppler' in error_msg.lower() or 'pdftoppm' in error_msg.lower():
                return False, None, "poppler-utils not installed. Run: sudo apt-get install poppler-utils"
            return False, None, f"PDF processing error: {error_msg}"

    def _process_image(self, file_path):
        try:
            img = Image.open(file_path)
            img.load()
            return self._optimize_image(img)
        except Exception as e:
            logger.exception("_process_image: failed for %s", file_path)
            return False, None, f"Image processing error: {e}"

    def _optimize_image(self, img):
        try:
            if img.mode != 'RGB':
                img = img.convert('RGB')

            # resize only if really large
            if img.width > 2048 or img.height > 2048:
                img.thumbnail((2048, 2048), Image.Resampling.LANCZOS)
                logger.info("_optimize_image: resized to %s", img.size)

            for quality in (92, 80, 65):
                buf = io.BytesIO()
                img.save(buf, format='JPEG', quality=quality, optimize=True)
                if buf.tell() <= MAX_SIZE_BYTES:
                    buf.seek(0)
                    encoded = base64.b64encode(buf.read()).decode('utf-8')
                    logger.info("_optimize_image: encoded at quality=%d, size=%d bytes", quality, buf.tell())
                    return True, encoded, None

            # last resort — shrink harder
            img.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format='JPEG', quality=60, optimize=True)
            buf.seek(0)
            logger.warning("_optimize_image: had to aggressively shrink to fit size limit")
            return True, base64.b64encode(buf.read()).decode('utf-8'), None

        except Exception as e:
            logger.exception("_optimize_image: failed")
            return False, None, f"Image optimization error: {e}"