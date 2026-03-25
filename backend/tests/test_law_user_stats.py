from app.services.law_user_stats import law_id_from_law_go_url, links_for_stats


def test_law_id_from_url() -> None:
    assert law_id_from_law_go_url("https://www.law.go.kr/lsInfoP.do?lsiSeq=12345") == "12345"
    assert law_id_from_law_go_url(None) is None


def test_links_for_stats_routed() -> None:
    refs = links_for_stats(
        use_legal=True,
        oc="x",
        used_law_refs=[{"law_id": "1", "title": "A법"}, {"law_id": "2", "label": "B"}],
        resolved_links=[{"label": "ignored", "url": "https://www.law.go.kr/lsInfoP.do?lsiSeq=9"}],
    )
    assert len(refs) == 2
    assert refs[0]["law_id"] == "1"


def test_links_for_stats_prefers_mst() -> None:
    refs = links_for_stats(
        use_legal=True,
        oc="x",
        used_law_refs=[{"law_id": "009640", "law_mst": "268803", "title": "재난법"}],
        resolved_links=[],
    )
    assert refs == [{"law_id": "268803", "law_title": "재난법"}]


def test_links_for_stats_from_resolution_only() -> None:
    refs = links_for_stats(
        use_legal=False,
        oc="x",
        used_law_refs=[],
        resolved_links=[
            {"label": "[법] X", "url": "https://www.law.go.kr/lsInfoP.do?lsiSeq=999"},
        ],
    )
    assert refs == [{"law_id": "999", "law_title": "[법] X"}]
