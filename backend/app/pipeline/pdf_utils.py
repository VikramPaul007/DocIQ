"""PDF -> per-page text (direct layer) or a rasterized PNG (OCR fallback),
via pypdfium2. Not abstracted behind a Protocol like the LLM/OCR providers —
there's no alternate PDF backend to swap in, so a provider abstraction here
would be unused flexibility.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

import pypdfium2 as pdfium

# Below this many characters, the page's text layer is treated as "no real
# text" (e.g. a scanned page where pdfium only picks up stray artifacts) and
# it's rasterized for OCR instead.
MIN_TEXT_LAYER_CHARS = 20
RASTER_SCALE = 300 / 72  # pdfium's native unit is 72 dpi; render at 300 dpi


@dataclass
class PageSource:
    method: str  # "text_layer" | "ocr"
    text: str | None  # populated when method == "text_layer"
    png_bytes: bytes | None  # populated when method == "ocr"


class CorruptPdfError(ValueError):
    pass


def open_pdf(data: bytes) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        raise CorruptPdfError(str(exc)) from exc


def extract_page(doc: pdfium.PdfDocument, index: int) -> PageSource:
    page = doc[index]
    try:
        text_page = page.get_textpage()
        try:
            text = text_page.get_text_range().strip()
        finally:
            text_page.close()

        if len(text) >= MIN_TEXT_LAYER_CHARS:
            return PageSource(method="text_layer", text=text, png_bytes=None)

        bitmap = page.render(scale=RASTER_SCALE)
        pil_image = bitmap.to_pil()
        buf = io.BytesIO()
        pil_image.save(buf, format="PNG")
        return PageSource(method="ocr", text=None, png_bytes=buf.getvalue())
    finally:
        page.close()
