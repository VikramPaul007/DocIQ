"""OCR provider abstraction. ``PaddleOcrProvider`` is CPU-only and only
installed in the worker image (the ``[project.optional-dependencies] worker``
extra — see pyproject.toml); the api image and tests use ``FakeOcrProvider``.
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from typing import Protocol


@dataclass
class OcrPage:
    text: str
    confidence: float | None


class OcrProvider(Protocol):
    def ocr_page(self, png_bytes: bytes) -> OcrPage: ...


class FakeOcrProvider:
    """Test double: returns the same canned text/confidence for every page."""

    def __init__(self, text: str = "", confidence: float | None = 1.0):
        self.text = text
        self.confidence = confidence

    def ocr_page(self, png_bytes: bytes) -> OcrPage:
        return OcrPage(text=self.text, confidence=self.confidence)


class PaddleOcrProvider:
    """Lazily constructs one PaddleOCR pipeline (loading models is expensive)
    and reuses it for every page.

    ``enable_mkldnn=False``: the oneDNN CPU execution path hits an internal
    PaddlePaddle/PIR executor bug on this build — "Unimplemented:
    ConvertPirAttribute2RuntimeAttribute" — verified against the pinned
    paddleocr/paddlepaddle versions in requirements; this is the workaround,
    not a guess. Doc-orientation/unwarping/textline-orientation are disabled
    by default: scanned HR documents aren't expected to be rotated, and
    skipping those stages is meaningfully faster on CPU.
    """

    def __init__(self) -> None:
        from paddleocr import PaddleOCR

        self._ocr = PaddleOCR(
            lang="en",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            enable_mkldnn=False,
        )

    def ocr_page(self, png_bytes: bytes) -> OcrPage:
        with tempfile.NamedTemporaryFile(suffix=".png") as f:
            f.write(png_bytes)
            f.flush()
            results = list(self._ocr.predict(f.name))

        texts: list[str] = []
        scores: list[float] = []
        for result in results:
            res = result.json.get("res", {})
            texts.extend(res.get("rec_texts", []))
            scores.extend(res.get("rec_scores", []))

        confidence = sum(scores) / len(scores) if scores else None
        return OcrPage(text="\n".join(texts), confidence=confidence)
