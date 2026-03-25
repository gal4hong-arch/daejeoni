"""국가법령정보 OPEN API 공통 상수."""

OPENAPI_GUIDE_LIST_URL = "https://open.law.go.kr/LSO/openApi/guideList.do"

DEFAULT_LAW_SEARCH_URL = "https://www.law.go.kr/DRF/lawSearch.do"
DEFAULT_LAW_SERVICE_URL = "https://www.law.go.kr/DRF/lawService.do"

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; InnocurveGovAI/1.0; +https://www.law.go.kr)",
    "Accept": "application/json, application/xml, text/xml, text/plain, */*",
}
