"""pdf_rag_ingest 단위 테스트 (pymupdf 필요)."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fitz  # noqa: E402

from app.services.pdf_rag_ingest import (  # noqa: E402
    TEXT_ONLY_PAGE_BATCH,
    _pdf_text_only_page_range,
)


def _minimal_pdf_bytes() -> bytes:
    doc = fitz.open()
    for i in range(3):
        p = doc.new_page()
        p.insert_text((72, 100 + i * 20), f"P{i + 1}_chunk_marker")
    raw = doc.tobytes()
    doc.close()
    return raw


class PdfRagIngestTests(unittest.TestCase):
    def test_text_only_page_range_batches(self) -> None:
        raw = _minimal_pdf_bytes()
        doc = fitz.open(stream=raw, filetype="pdf")
        try:
            self.assertEqual(doc.page_count, 3)
            t01 = _pdf_text_only_page_range(doc, 0, 2)
            self.assertIn("P1_chunk_marker", t01)
            self.assertIn("P2_chunk_marker", t01)
            self.assertNotIn("P3_chunk_marker", t01)
            t2 = _pdf_text_only_page_range(doc, 2, 3)
            self.assertIn("P3_chunk_marker", t2)
        finally:
            doc.close()

    def test_text_only_batch_constant_positive(self) -> None:
        self.assertGreaterEqual(TEXT_ONLY_PAGE_BATCH, 1)


if __name__ == "__main__":
    unittest.main()
