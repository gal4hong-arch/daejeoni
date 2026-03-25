"""대화 세션 제목: 안건(토픽) 라우팅의 주제 요약·첫 메시지 폴백."""

import re


def title_from_first_message(content: str, *, max_len: int = 52) -> str:
    """첫 줄만 사용, 공백 정리, 길이 제한(말줄임)."""
    line = (content or "").split("\n", 1)[0].strip()
    line = re.sub(r"\s+", " ", line)
    if not line:
        return "새 대화"
    if len(line) <= max_len:
        return line
    return line[: max_len - 1].rstrip() + "…"


def stream_title_from_topic(
    detected_topic: str | None,
    fallback_message: str,
    *,
    max_len: int = 52,
) -> str:
    """안건 라우팅의 주제 요약(detected_topic)을 세션 제목으로. 비어 있으면 첫 메시지 기반."""
    t = re.sub(r"\s+", " ", (detected_topic or "").strip())
    if t:
        if len(t) <= max_len:
            return t
        return t[: max_len - 1].rstrip() + "…"
    return title_from_first_message(fallback_message, max_len=max_len)
