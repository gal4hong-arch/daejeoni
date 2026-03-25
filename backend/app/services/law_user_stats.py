"""사용자별 법령 조회 빈도 누적·조회."""

from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import unquote

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import UserLawStat


def law_id_from_law_go_url(url: str | None) -> str | None:
    if not url:
        return None
    m = re.search(r"lsiSeq=([^&]+)", url)
    if not m:
        return None
    return unquote(m.group(1)).strip() or None


def record_law_hits(db: Session, user_id: str, refs: list[dict[str, str]]) -> None:
    """
    한 턴에서 동일 law_id는 1회만 가산.
    refs: {law_id, law_title? | title? | label?}
    """
    seen: set[str] = set()
    now = datetime.utcnow()
    for r in refs:
        lid = (r.get("law_id") or "").strip()
        if not lid or lid in seen:
            continue
        seen.add(lid)
        title = (r.get("law_title") or r.get("title") or r.get("label") or "").strip()[:512] or lid

        row = (
            db.execute(
                select(UserLawStat).where(UserLawStat.user_id == user_id, UserLawStat.law_id == lid)
            )
            .scalar_one_or_none()
        )
        if row:
            row.hit_count = int(row.hit_count or 0) + 1
            row.last_access_at = now
            if title and title != lid and (not row.law_title or row.law_title == lid or len(title) > len(row.law_title)):
                row.law_title = title
        else:
            db.add(
                UserLawStat(
                    user_id=user_id,
                    law_id=lid,
                    law_title=title,
                    hit_count=1,
                    last_access_at=now,
                )
            )


def links_for_stats(
    *,
    use_legal: bool,
    oc: str,
    used_law_refs: list[dict[str, str]],
    resolved_links: list[dict[str, str]],
) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if use_legal and oc.strip() and used_law_refs:
        for r in used_law_refs:
            lid = (r.get("law_mst") or r.get("law_id") or "").strip()
            if not lid:
                continue
            t = (r.get("title") or r.get("label") or "").strip()
            out.append({"law_id": lid, "law_title": t})
        return out
    for L in resolved_links or []:
        lid = law_id_from_law_go_url(L.get("url"))
        if not lid:
            continue
        out.append({"law_id": lid, "law_title": (L.get("label") or "").strip()})
    return out


def list_law_popularity(db: Session, user_id: str, *, limit: int = 80) -> list[UserLawStat]:
    lim = max(1, min(limit, 200))
    return list(
        db.execute(
            select(UserLawStat)
            .where(UserLawStat.user_id == user_id)
            .order_by(UserLawStat.hit_count.desc(), UserLawStat.last_access_at.desc())
            .limit(lim)
        )
        .scalars()
        .all()
    )
