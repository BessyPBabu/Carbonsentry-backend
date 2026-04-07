import base64
import io
import logging
import os
import subprocess

from PIL import Image
from pdf2image import convert_from_path

logger = logging.getLogger(__name__)

MAX_SIZE_BYTES = 4 * 1024 * 1024
MAX_DIM = 1024


class DocumentPreprocessor:

    def __init__(self):
        try:
            subprocess.run(["pdftoppm", "-v"], capture_output=True, check=False)
        except FileNotFoundError:
            logger.warning("document_preprocessor: poppler not found — PDF processing will fail")

    def process(self, file_path: str) -> tuple[bool, str | None, str | None]:
        if not os.path.exists(file_path):
            return False, None, f"file_not_found:{file_path}"

        if os.path.getsize(file_path) == 0:
            return False, None, "file_is_empty"

        try:
            ext = os.path.splitext(file_path)[1].lower()
            if ext == ".pdf":
                return self._process_pdf(file_path)
            return self._process_image(file_path)
        except Exception as exc:
            logger.error("document_preprocessor: unexpected error %s — %s", file_path, exc)
            return False, None, f"preprocessing_error:{exc}"

    def _process_pdf(self, file_path: str) -> tuple[bool, str | None, str | None]:
        try:
            # 150 DPI is sufficient for AI classification and metadata extraction.
            # 300 DPI added 2-4 s of CPU time and was immediately downscaled anyway.
            images = convert_from_path(
                file_path, first_page=1, last_page=1, dpi=150, fmt="jpeg"
            )
            if not images:
                return False, None, "pdf_no_pages"
            return self._encode(images[0])
        except Exception as exc:
            err = str(exc)
            if "poppler" in err.lower() or "pdftoppm" in err.lower():
                return False, None, "poppler_not_installed"
            logger.error("document_preprocessor: pdf error %s — %s", file_path, err)
            return False, None, f"pdf_error:{err}"

    def _process_image(self, file_path: str) -> tuple[bool, str | None, str | None]:
        try:
            img = Image.open(file_path)
            img.load()
            return self._encode(img)
        except Exception as exc:
            logger.error("document_preprocessor: image error %s — %s", file_path, exc)
            return False, None, f"image_error:{exc}"

    def _encode(self, img: Image.Image) -> tuple[bool, str | None, str | None]:
        try:
            if img.mode != "RGB":
                img = img.convert("RGB")
            if img.width > MAX_DIM or img.height > MAX_DIM:
                img.thumbnail((MAX_DIM, MAX_DIM), Image.Resampling.LANCZOS)

            for quality in (88, 75, 60):
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=quality, optimize=True)
                if buf.tell() <= MAX_SIZE_BYTES:
                    buf.seek(0)
                    return True, base64.b64encode(buf.read()).decode(), None

            # Last resort: aggressive resize
            img.thumbnail((768, 768), Image.Resampling.LANCZOS)
            buf = io.BytesIO()
            img.save(buf, format="JPEG", quality=55, optimize=True)
            buf.seek(0)
            logger.warning("document_preprocessor: had to aggressively shrink image")
            return True, base64.b64encode(buf.read()).decode(), None

        except Exception as exc:
            logger.error("document_preprocessor: encode error — %s", exc)
            return False, None, f"encode_error:{exc}"