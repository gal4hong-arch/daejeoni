"""legal_routed_pipeline: 1단계 LLM JSON 파서."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.legal_routed_pipeline import _parse_law_route_from_llm  # noqa: E402


class LegalRoutedParseTests(unittest.TestCase):
    def test_full_schema(self) -> None:
        raw = """{
          "intent_summary": "목적 조 확인",
          "law_focus": "국가계약",
          "titles": ["국가를 당사자로 하는 계약에 관한 법률"],
          "notes_for_search": "약칭 국가계약법"
        }"""
        titles, a = _parse_law_route_from_llm(raw)
        self.assertEqual(len(titles), 1)
        self.assertIn("국가를 당사자", titles[0])
        assert a is not None
        self.assertIn("목적", a.intent_summary)
        self.assertIn("국가계약", a.law_focus)
        self.assertIn("국가계약법", a.notes_for_search)

    def test_titles_only_legacy_shape(self) -> None:
        raw = '{"titles": ["도로법"]}'
        titles, a = _parse_law_route_from_llm(raw)
        self.assertEqual(titles, ["도로법"])
        assert a is not None
        self.assertEqual(a.intent_summary, "")
        self.assertEqual(a.law_focus, "")

    def test_code_fence(self) -> None:
        raw = '```json\n{"intent_summary":"x","law_focus":"y","titles":[],"notes_for_search":""}\n```'
        titles, a = _parse_law_route_from_llm(raw)
        self.assertEqual(titles, [])
        assert a is not None
        self.assertEqual(a.intent_summary, "x")


if __name__ == "__main__":
    unittest.main()
