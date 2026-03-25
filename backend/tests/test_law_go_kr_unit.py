"""law_go_kr parse·relevance·client 단위 테스트 (네트워크 없음)."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.law_go_kr.client import law_search_request, law_service_request  # noqa: E402
from app.services.law_go_kr.parse import law_go_kr_json_looks_like_error  # noqa: E402
from app.services.law_go_kr.statute_body import fetch_statute_service_body  # noqa: E402
from app.services.law_go_kr.fetch import (  # noqa: E402
    _collect_tagged_service_ids,
    _roots_from_entries,
)
from app.services.law_go_kr.parse import (  # noqa: E402
    extract_law_ids_from_many,
    extract_law_link_entries,
    key_looks_like_law_id_field,
    law_search_query_variants,
    law_service_basic_meta_ids,
    law_service_body_stats_for_debug,
    law_service_data_for_llm,
    law_service_json_body_plain,
    search_json_hit_titles,
    search_json_total_count_hint,
)
from app.services.law_go_kr.jo_param import parse_law_service_jo_from_query  # noqa: E402
from app.services.law_go_kr.relevance import extract_relevant_excerpts, score_text_against_query  # noqa: E402


class LawGoKrJoParamTests(unittest.TestCase):
    def test_jo_article1(self) -> None:
        self.assertEqual(parse_law_service_jo_from_query("대한민국 헌법 1조 알려줘"), "000100")

    def test_jo_article2_guide_sample(self) -> None:
        self.assertEqual(parse_law_service_jo_from_query("자동차관리법 제2조"), "000200")

    def test_jo_article10_branch2_guide_sample(self) -> None:
        self.assertEqual(parse_law_service_jo_from_query("제10조의2"), "001002")

    def test_jo_no_article(self) -> None:
        self.assertIsNone(parse_law_service_jo_from_query("국가계약법 요약해줘"))


class LawGoKrParseTests(unittest.TestCase):
    def test_query_variants(self) -> None:
        v = law_search_query_variants("국가계약법에 대해 알려줘", max_variants=5)
        self.assertTrue(len(v) >= 1)

    def test_extract_law_ids(self) -> None:
        root = {"법령명한글": "X법", "법령ID": "12345"}
        ids = extract_law_ids_from_many([root], 5)
        self.assertIn("12345", ids)

    def test_search_json_hints(self) -> None:
        j = {"totalCnt": 3, "LawSearch": {"law": [{"법령명한글": "도로법", "법령ID": "1"}]}}
        self.assertEqual(search_json_total_count_hint(j), 3)
        titles = search_json_hit_titles(j, limit=5)
        self.assertIn("도로법", titles)

    def test_key_looks_like_id(self) -> None:
        self.assertTrue(key_looks_like_law_id_field("lsiSeq"))
        self.assertFalse(key_looks_like_law_id_field("page"))

    def test_link_entries_admrul(self) -> None:
        root = {"admRulSeq": "2000000005155", "행정규칙명": "테스트규칙"}
        links = extract_law_link_entries([root], [], "질의", limit=5)
        urls = [x["url"] for x in links]
        self.assertTrue(any("admRulSeq=" in u for u in urls))

    def test_law_service_basic_meta_ids(self) -> None:
        data = {
            "법령": {
                "기본정보": {
                    "법령ID": "003728",
                    "법령일련번호": "281061",
                }
            }
        }
        lid, mst = law_service_basic_meta_ids(data)
        self.assertEqual(lid, "003728")
        self.assertEqual(mst, "281061")


class LawGoKrRelevanceTests(unittest.TestCase):
    def test_score_and_excerpts(self) -> None:
        self.assertGreater(score_text_against_query("계약 체결 절차", "계약"), 0.0)
        data = {"a": {"text": "국가계약법에 따른 계약 절차는 다음과 같다"}, "b": "무관한 문자열"}
        ex = extract_relevant_excerpts(data, "계약 절차", max_excerpts=2, max_chars_each=200)
        self.assertTrue(any("계약" in e for e in ex))

    def test_excerpts_fallback_longest_when_no_token_overlap(self) -> None:
        data = {"x": {"조문내용": "국가를 당사자로 하는 계약의 체결에 관한 특별한 규정이다."}}
        ex = extract_relevant_excerpts(data, "zzz", max_excerpts=2, max_chars_each=500)
        self.assertTrue(ex and "국가" in ex[0])


class LawGoKrErrorJsonTests(unittest.TestCase):
    def test_error_msg_detected(self) -> None:
        self.assertTrue(
            law_go_kr_json_looks_like_error({"result": "x", "msg": "OPEN API 호출 시 오류가 발생하였습니다."})
        )
        self.assertFalse(law_go_kr_json_looks_like_error({"법령": {"조문": "x"}}))


class LawGoKrStatuteBodyTests(unittest.TestCase):
    def test_eflaw_then_law_fallback(self) -> None:
        client = MagicMock()
        r_bad = MagicMock()
        r_bad.text = '{"result":"err","msg":"OPEN API 호출 시 오류가 발생하였습니다."}'
        r_bad.status_code = 200
        r_bad.headers = {}
        r_bad.url = "http://example/DRF/lawService.do?bad=1"
        r_ok = MagicMock()
        r_ok.text = '{"조문":{"조문단위":[{"조문번호":1,"조문내용":"테스트 본문입니다."}]}}'
        r_ok.status_code = 200
        r_ok.headers = {}
        r_ok.url = "http://example/DRF/lawService.do?ok=1"
        client.get.side_effect = [r_bad, r_ok]
        st, _b, data, eff, _u = fetch_statute_service_body(
            client,
            service_url="http://example/DRF/lawService.do",
            oc="oc",
            law_id="999",
            response_type="JSON",
            primary="eflaw",
            fallback="law",
        )
        self.assertEqual(st, 200)
        self.assertEqual(eff, "law")
        self.assertIsInstance(data, dict)

    def test_eflaw_only_success(self) -> None:
        client = MagicMock()
        r_ok = MagicMock()
        r_ok.text = '{"조문":{"조문단위":[{"조문내용":"' + "가" * 400 + '"}]}}'
        r_ok.status_code = 200
        r_ok.headers = {}
        r_ok.url = "http://example/DRF/lawService.do?x=1"
        client.get.return_value = r_ok
        st, _b, data, eff, _u = fetch_statute_service_body(
            client,
            service_url="http://example/DRF/lawService.do",
            oc="oc",
            law_id="1",
            response_type="JSON",
        )
        self.assertEqual(eff, "eflaw")
        self.assertGreater(len(str(data)), 50)

    def test_detail_link_used_before_id_fallback(self) -> None:
        client = MagicMock()
        r_link = MagicMock()
        r_link.text = '{"조문":{"조문단위":[{"조문내용":"' + "나" * 400 + '"}]}}'
        r_link.status_code = 200
        r_link.headers = {}
        r_link.url = "http://example/DRF/lawService.do?MST=61603"
        client.get.return_value = r_link
        st, _b, data, eff, url = fetch_statute_service_body(
            client,
            service_url="http://example/DRF/lawService.do",
            oc="myoc",
            law_id="001444",
            response_type="JSON",
            primary="eflaw",
            fallback="law",
            detail_link="/DRF/lawService.do?OC=old&target=law&MST=61603&efYd=19880225&type=HTML",
        )
        self.assertEqual(st, 200)
        self.assertEqual(eff, "law")
        self.assertIn("MST=61603", url)
        client.get.assert_called_once()
        p = client.get.call_args.kwargs["params"]
        self.assertEqual(p["OC"], "myoc")
        self.assertEqual(p["type"], "JSON")
        self.assertEqual(p["MST"], "61603")
        self.assertIsInstance(data, dict)

    def test_jo_uses_eflaw_mst_efyd_xml_then_json(self) -> None:
        client = MagicMock()
        r_xml = MagicMock()
        r_xml.text = "<Law>short</Law>"
        r_xml.status_code = 200
        r_xml.headers = {}
        r_xml.url = "http://example/DRF/lawService.do?xml=1"
        r_json = MagicMock()
        r_json.text = '{"조문":{"조문단위":[{"조문내용":"' + "다" * 400 + '"}]}}'
        r_json.status_code = 200
        r_json.headers = {}
        r_json.url = "http://example/DRF/lawService.do?json=1"
        client.get.side_effect = [r_xml, r_json]
        st, _b, data, eff, url = fetch_statute_service_body(
            client,
            service_url="http://example/DRF/lawService.do",
            oc="oc",
            law_id="166520",
            response_type="JSON",
            service_extra={"JO": "000300"},
            mst="166520",
            ef_yd="20151007",
        )
        self.assertEqual(st, 200)
        self.assertEqual(eff, "eflaw")
        self.assertIsInstance(data, dict)
        self.assertEqual(client.get.call_count, 2)
        self.assertIn("json=1", url)


class LawGoKrBodyDebugStatsTests(unittest.TestCase):
    def test_stats_has_len_and_preview(self) -> None:
        data = {"조문": {"조문단위": [{"조문내용": "테스트 본문 " * 30}]}}
        s = law_service_body_stats_for_debug(data, preview_max=80)
        self.assertGreater(s["body_plain_len"], 50)
        self.assertIn("테스트", s["body_preview"])
        self.assertNotIn("preview_note", s)

    def test_stats_plain_empty_uses_json_note(self) -> None:
        s = law_service_body_stats_for_debug({"foo": 1, "bar": "x"}, preview_max=100)
        self.assertEqual(s["body_plain_len"], 0)
        self.assertEqual(s["preview_note"], "조문키 추출 0자 — JSON 앞부분")


class LawGoKrLawServicePlainTests(unittest.TestCase):
    def test_body_plain_nested_조문단위(self) -> None:
        data = {
            "법령": {
                "조문": {
                    "조문단위": [
                        {
                            "조문번호": "1",
                            "조문제목": "목적",
                            "조문내용": "이 법은 국가를 당사자로 하는 계약에 관하여 필요한 사항을 규정함을 목적으로 한다.",
                        }
                    ]
                }
            },
            "meta": "x" * 5000,
        }
        plain = law_service_json_body_plain(data, max_chars=50_000)
        self.assertIn("이 법은 국가", plain)
        self.assertIn("조문내용:", plain)
        llm = law_service_data_for_llm(data, max_chars=800)
        self.assertIn("이 법은 국가", llm)

    def test_int_scalar_fields_eflaw_style(self) -> None:
        data = {"조문": {"조문단위": [{"조문번호": 3, "조문가지번호": 0, "조문내용": "제3조 본문"}]}}
        plain = law_service_json_body_plain(data, max_chars=5000)
        self.assertIn("조문번호:", plain)
        self.assertIn("제3조 본문", plain)


class LawGoKrClientTests(unittest.TestCase):
    def test_law_search_request_json(self) -> None:
        client = MagicMock()
        resp = MagicMock()
        resp.text = '{"LawSearch":{"totalCnt":"1"}}'
        resp.headers = {"content-type": "application/json"}
        resp.status_code = 200
        resp.url = "http://example/DRF/lawSearch.do?OC=testoc"
        client.get.return_value = resp
        st, body, data, req_url = law_search_request(
            client,
            base_url="http://example/DRF/lawSearch.do",
            oc="testoc",
            target="law",
            extra={"query": "자동차", "display": "5", "page": "1"},
            query=None,
        )
        self.assertEqual(st, 200)
        self.assertIsInstance(data, dict)
        self.assertIn("lawSearch.do", req_url)
        client.get.assert_called_once()
        call_kw = client.get.call_args
        self.assertIn("params", call_kw.kwargs)
        p = call_kw.kwargs["params"]
        self.assertEqual(p["OC"], "testoc")
        self.assertEqual(p["target"], "law")
        self.assertEqual(p["query"], "자동차")

    def test_law_service_request(self) -> None:
        client = MagicMock()
        resp = MagicMock()
        resp.text = "{}"
        resp.headers = {}
        resp.status_code = 200
        resp.url = "http://example/DRF/lawService.do?OC=oc&target=admrul"
        client.get.return_value = resp
        st, _, data, u = law_service_request(
            client,
            service_url="http://example/DRF/lawService.do",
            oc="oc",
            target="admrul",
            law_id="999",
            response_type="JSON",
        )
        self.assertEqual(st, 200)
        self.assertIn("lawService.do", u)
        p = client.get.call_args.kwargs["params"]
        self.assertEqual(p["target"], "admrul")
        self.assertEqual(p["ID"], "999")

    def test_law_service_eflaw_target(self) -> None:
        client = MagicMock()
        resp = MagicMock()
        resp.text = "{}"
        resp.headers = {}
        resp.status_code = 200
        resp.url = "http://example/DRF/lawService.do?eflaw=1"
        client.get.return_value = resp
        st, _, data, _u = law_service_request(
            client,
            service_url="http://example/DRF/lawService.do",
            oc="oc",
            target="eflaw",
            law_id="1747",
            response_type="JSON",
        )
        self.assertEqual(st, 200)
        p = client.get.call_args.kwargs["params"]
        self.assertEqual(p["target"], "eflaw")
        self.assertEqual(p["ID"], "1747")

    def test_law_service_request_passes_extra_jo(self) -> None:
        client = MagicMock()
        resp = MagicMock()
        resp.text = "{}"
        resp.headers = {}
        resp.status_code = 200
        resp.url = "http://example/DRF/lawService.do?JO=000100"
        client.get.return_value = resp
        law_service_request(
            client,
            service_url="http://example/DRF/lawService.do",
            oc="oc",
            target="eflaw",
            law_id="61603",
            response_type="JSON",
            extra={"JO": "000100"},
        )
        p = client.get.call_args.kwargs["params"]
        self.assertEqual(p["JO"], "000100")


class LawGoKrFetchHelpersTests(unittest.TestCase):
    def test_collect_tagged_prioritizes_law(self) -> None:
        law_root = {"법령ID": "1", "법령명한글": "A법"}
        adm_root = {"admRulSeq": "2", "행정규칙명": "B"}
        entries = [(law_root, "law"), (adm_root, "admrul")]
        picked = _collect_tagged_service_ids(entries, limit=2)
        self.assertEqual(len(picked), 2)
        self.assertEqual(picked[0], ("1", "law"))
        self.assertEqual(picked[1], ("2", "admrul"))

    def test_roots_from_entries(self) -> None:
        self.assertEqual(len(_roots_from_entries([({"x": 1}, "law"), (None, "law")])), 1)


if __name__ == "__main__":
    unittest.main()
