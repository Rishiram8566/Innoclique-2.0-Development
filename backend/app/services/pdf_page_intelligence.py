"""Stage 4.4 page-aware PDF text extraction with optional selective OCR.

This module never modifies source PDFs. OCR is attempted only on pages whose
native text layer is sparse. If OCR dependencies are missing, the page is
reported as image_only_or_ocr_unavailable rather than silently treated as read.
"""
from __future__ import annotations

import os
import shutil
from typing import Any

_DEFAULT_WINDOWS_TESSERACT = r"C:\Program Files\Tesseract-OCR\tesseract.exe"


def _configure_tesseract(module) -> None:
    """Point pytesseract at a Tesseract binary: TESSERACT_CMD, then PATH, then the default Windows install."""
    for candidate in (
        os.environ.get("TESSERACT_CMD"),
        shutil.which("tesseract"),
        _DEFAULT_WINDOWS_TESSERACT,
    ):
        if candidate and os.path.isfile(candidate):
            module.pytesseract.tesseract_cmd = candidate
            return


def extract_pdf_pages(pdf_path, *, enable_ocr: bool = True,
                      min_native_chars: int = 40, dpi: int = 180) -> dict[str, Any]:
    try:
        import fitz
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is required. Install with: pip install pymupdf") from exc

    pages = []
    all_text = []
    ocr_available = False
    ocr_error = None
    pytesseract = None
    Image = None

    if enable_ocr:
        try:
            import pytesseract as _pytesseract
            from PIL import Image as _Image
            _configure_tesseract(_pytesseract)
            pytesseract, Image = _pytesseract, _Image
            ocr_available = True
        except Exception as exc:
            ocr_error = f"OCR dependencies unavailable: {type(exc).__name__}: {exc}"

    with fitz.open(pdf_path) as document:
        for page_no, page in enumerate(document, start=1):
            native = (page.get_text("text") or "").strip()
            chosen = native
            method = "native_text" if native else "no_text"
            ocr_chars = 0
            if enable_ocr and len(native) < min_native_chars and ocr_available:
                try:
                    pix = page.get_pixmap(dpi=dpi, alpha=False)
                    image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
                    ocr_text = (pytesseract.image_to_string(image) or "").strip()
                    ocr_chars = len(ocr_text)
                    if len(ocr_text) > len(native):
                        chosen = ocr_text
                        method = "ocr"
                    elif native:
                        method = "native_text_sparse_ocr_attempted"
                    else:
                        method = "ocr_no_text_found"
                except Exception as exc:
                    method = "ocr_failed"
                    ocr_error = f"Page {page_no}: {type(exc).__name__}: {exc}"
            elif not native and enable_ocr and not ocr_available:
                method = "image_only_ocr_unavailable"

            rect = page.rect
            page_record = {
                "page_number": page_no,
                "width_points": round(float(rect.width), 2),
                "height_points": round(float(rect.height), 2),
                "native_text_characters": len(native),
                "ocr_text_characters": ocr_chars,
                "selected_text_characters": len(chosen),
                "extraction_method": method,
                "review_required": method in {
                    "image_only_ocr_unavailable", "ocr_failed", "ocr_no_text_found"
                },
            }
            pages.append(page_record)
            all_text.append(f"\n[PAGE {page_no} | {method}]\n{chosen}")

    text = "\n".join(all_text).strip()
    return {
        "text": text,
        "pages": pages,
        "page_count": len(pages),
        "native_text_page_count": sum(p["native_text_characters"] >= min_native_chars for p in pages),
        "ocr_page_count": sum(p["extraction_method"] == "ocr" for p in pages),
        "image_only_or_unreadable_page_count": sum(p["review_required"] for p in pages),
        "ocr_available": ocr_available,
        "ocr_error": ocr_error,
        "extraction_mode": "native_text_plus_selective_ocr" if enable_ocr else "native_text_only",
    }
