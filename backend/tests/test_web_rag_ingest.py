"""web_rag_ingest URL 정규화·SSRF 헬퍼 단위 테스트."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.web_rag_ingest import _page_title_from_html, normalize_canonical_url, url_fetch_allowed


def test_normalize_canonical_url_sorts_query_and_strips_fragment() -> None:
    assert (
        normalize_canonical_url("https://Example.COM/path?b=2&a=1#frag")
        == "https://example.com/path?a=1&b=2"
    )


def test_normalize_canonical_url_adds_https_when_missing_scheme() -> None:
    assert normalize_canonical_url("GOV.kr/foo") == "https://gov.kr/foo"


def test_url_fetch_allowed_rejects_localhost() -> None:
    ok, msg = url_fetch_allowed("http://localhost:8080/x")
    assert ok is False
    assert "localhost" in msg


def test_url_fetch_allowed_rejects_private_ip_host() -> None:
    ok, _ = url_fetch_allowed("http://192.168.1.1/")
    assert ok is False


def test_url_fetch_allowed_accepts_public_host() -> None:
    ok, msg = url_fetch_allowed("https://www.example.com/page")
    assert ok is True
    assert msg == ""


def test_page_title_from_html_normalizes_whitespace() -> None:
    html = "<html><head><title>  안녕   페이지  </title></head><body></body></html>"
    assert _page_title_from_html(html) == "안녕 페이지"
