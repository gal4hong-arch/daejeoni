"""역할(상급자·시의원·시민) 시뮬 토의 — 단일 LLM 호출로 구조화된 발언 생성."""

from __future__ import annotations

import json
import re
from sqlalchemy.orm import Session

from app.services.llm_client import chat_completion

ROLE_CATALOG: dict[str, tuple[str, str]] = {
    "supervisor": (
        "상급자 · 행정 책임",
        "시청 내부 관점: 예산·집행·책임, 규정·리스크, 실무 타당성을 중심으로 짧게 발언한다.",
    ),
    "councilor": (
        "시의원 · 의정",
        "의회·주민 대표 관점: 감시·견제, 정책 쟁점, 예산·조례와의 관계를 중심으로 발언한다.",
    ),
    "citizen": (
        "시민 · 이용자",
        "일반 시민 관점: 알기 쉬운 말, 불편·권리·서비스 이용, 기대를 중심으로 발언한다.",
    ),
}

_ROUNDTABLE_SYSTEM = """너는 지자체 안건에 대한 **역할별 토의 시뮬레이션** 작성기다.
사용자가 제시한 안건을 바탕으로, 지정된 역할들만 등장시켜 각각 **한 번씩** 발언하게 하라.
각 발언은 해당 역할의 관점·어조를 유지하고, 서로 짧게 반응하거나 보완해도 되나 **과장·허위 사실은 금지**다.
한국어로만 작성한다.

출력은 **JSON 한 개만**(설명·마크다운·코드펜스 금지). 형식:
{
  "turns": [
    {"role_id": "supervisor|councilor|citizen 중 하나", "label": "표시용 짧은 호칭", "content": "발언 본문(2~5문단)"}
  ]
}
- turns 배열 순서는 **역할이 대화에 나오는 순서**로 하되, 사용자가 요청한 역할만 포함한다.
- label은 역할을 한눈에 알 수 있게(예: 상급자(행정), 시의원, 시민 대표).
"""


def _strip_json_block(raw: str) -> str:
    t = (raw or "").strip()
    if not t:
        return ""
    if "```" in t:
        m = re.search(r"```(?:json)?\s*([\s\S]*?)```", t)
        if m:
            t = m.group(1).strip()
    return t


def _parse_turns(raw: str, allowed: list[str]) -> list[dict[str, str]]:
    t = _strip_json_block(raw)
    if not t:
        return []
    try:
        d = json.loads(t)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(d, dict):
        return []
    arr = d.get("turns")
    if not isinstance(arr, list):
        return []
    allow = set(allowed)
    out: list[dict[str, str]] = []
    for item in arr:
        if not isinstance(item, dict):
            continue
        rid = str(item.get("role_id") or "").strip()
        if rid not in allow:
            continue
        lab = str(item.get("label") or ROLE_CATALOG[rid][0]).strip()[:120]
        content = str(item.get("content") or "").strip()
        if len(content) < 10:
            continue
        out.append({"role_id": rid, "label": lab, "content": content[:12000]})
    return out


def run_roundtable(
    db: Session,
    *,
    user_id: str,
    model: str,
    premise: str,
    roles: list[str],
) -> tuple[list[dict[str, str]], str]:
    """
    반환: (turns dict 목록, 포맷된 단일 문자열 — 폴백·복사용)
    """
    order = [r for r in roles if r in ROLE_CATALOG]
    if not order:
        return [], ""

    role_lines = []
    for rid in order:
        title, hint = ROLE_CATALOG[rid]
        role_lines.append(f"- {rid} ({title}): {hint}")
    user_block = (
        "다음 안건(또는 질문)에 대해, 아래 역할들만 포함하여 토의를 시뮬레이션하라.\n\n"
        f"【안건】\n{premise.strip()}\n\n"
        "【포함할 역할】\n" + "\n".join(role_lines)
    )

    raw = chat_completion(
        db,
        user_id=user_id,
        model=model,
        system=_ROUNDTABLE_SYSTEM,
        user=user_block,
        temperature=0.45,
        max_tokens=8192,
    )
    turns = _parse_turns(raw, order)
    if turns:
        return turns, format_answer_from_turns(turns)

    plain = (raw or "").strip()[:20000]
    if not plain:
        return [], ""
    if "【역할별" not in plain[:120]:
        plain = "【역할별 토의 · 시뮬레이션】(모델 원문)\n\n" + plain
    return [], plain


def format_answer_from_turns(turns: list[dict[str, str]]) -> str:
    if not turns:
        return ""
    lines = ["【역할별 토의 · 시뮬레이션】\n"]
    for t in turns:
        lines.append(f"── {t['label']} ──\n{t['content']}\n")
    return "\n".join(lines).strip()
