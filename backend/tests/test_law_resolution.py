"""law_resolution 단위 테스트 (네트워크 없음). unittest로 실행: python tests/test_law_resolution.py"""

import sys
import unittest
from pathlib import Path

# cap/backend 를 루트로 실행할 때 app 임포트
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.law_resolution import (  # noqa: E402
    LawMatch,
    _pick_from_ai_search_hits,
    build_law_links_output,
    collect_law_hits_from_search_json,
    extract_law_names,
    normalize_law_name,
    pick_best_law,
)


class LawResolutionTests(unittest.TestCase):
    def test_normalize_alias(self) -> None:
        self.assertEqual(normalize_law_name("국가계약법"), "국가를 당사자로 하는 계약에 관한 법률")
        self.assertEqual(
            normalize_law_name("지방계약법"),
            "지방자치단체를 당사자로 하는 계약에 관한 법률",
        )

    def test_extract_law_names(self) -> None:
        t = "국가계약법에 따라 국가를 당사자로 하는 계약에 관한 법률 시행령을 봅니다."
        names = extract_law_names(t)
        self.assertIn("국가계약법", names)

    def test_collect_and_pick(self) -> None:
        data = {
            "LawSearch": {
                "law": [
                    {"법령명한글": "국가를 당사자로 하는 계약에 관한 법률", "lsiSeq": "100"},
                    {"법령명한글": "국가를 당사자로 하는 계약에 관한 법률 시행령", "lsiSeq": "101"},
                ]
            }
        }
        hits = collect_law_hits_from_search_json(data)
        self.assertEqual(len(hits), 2)
        best = pick_best_law(hits, "국가를 당사자로 하는 계약에 관한 법률")
        self.assertIsNotNone(best)
        assert best is not None
        self.assertEqual(best.law_type, "법")
        self.assertEqual(best.law_id, "100")

    def test_ai_search_keyword_uses_first_hit_when_no_name_match(self) -> None:
        """지능형 검색: 키워드만 있고 법령명과 유사도가 낮으면 API 순서 첫 건."""
        hits = [
            LawMatch("도로교통법", "a", "법"),
            LawMatch("교통사고처리특례법", "b", "법"),
        ]
        p = _pick_from_ai_search_hits(hits, "qqq_unrelated_kw")
        self.assertIsNotNone(p)
        assert p is not None
        self.assertEqual(p.law_id, "a")

    def test_collect_ai_search_article_order_first(self) -> None:
        """aiSearch 법령조문 배열 순서를 중복 제거 시 우선한다."""
        data = {
            "aiSearch": {
                "법령조문": [
                    {
                        "법령명": "행정업무의 운영 및 혁신에 관한 규정 시행규칙",
                        "법령ID": "007319",
                        "법령일련번호": "252189",
                    },
                    {
                        "법령명": "공공기관의 운영에 관한 법률",
                        "법령ID": "010375",
                        "법령일련번호": "276057",
                    },
                ]
            }
        }
        hits = collect_law_hits_from_search_json(data)
        self.assertEqual(len(hits), 2)
        self.assertEqual(hits[0].law_id, "007319")
        self.assertEqual(hits[1].law_id, "010375")

    def test_pick_best_law_includes_non_beop_for_ai_search(self) -> None:
        """'법'만 후보로 두지 않아 시행규칙·령이 경쟁할 수 있다."""
        hits = [
            LawMatch("행정업무의 운영 및 혁신에 관한 규정 시행규칙", "007319", "시행규칙"),
            LawMatch("공공기관의 운영에 관한 법률", "010375", "법"),
        ]
        title = "행정기관 공문 작성에 관한 규칙"
        user = "행정기관 공문작성 세부지침이나 규칙"
        best = pick_best_law(hits, title, user_context=user)
        self.assertIsNotNone(best)
        assert best is not None
        self.assertEqual(best.law_id, "007319")

    def test_build_output(self) -> None:
        main = LawMatch("본법", "1", "법")
        rel = [LawMatch("본법 시행령", "2", "시행령")]
        s = build_law_links_output(main, rel)
        self.assertIn("📘 관련 법령", s)
        self.assertIn("[법] 본법", s)
        self.assertIn("[시행령]", s)


if __name__ == "__main__":
    unittest.main()
