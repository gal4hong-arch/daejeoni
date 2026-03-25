"""대화 의도 분류 (Intent Classifier)."""

import re


def classify_intent(message: str) -> str:
    """
    chat | document | simulation | legal_focus
    """
    t = message.strip()
    if re.search(r"(시뮬|의원|의회.*질의|상급자.*리뷰|가상.*질문)", t):
        return "simulation"
    if re.search(r"(보고서|공문|설명자료|의회.*답변|답변자료|문서로\s*만들)", t):
        return "document"
    if re.search(r"(법령|조례|시행령|법제처|근거법)", t):
        return "legal_focus"
    return "chat"


def intent_to_task(intent: str, explicit_task: str) -> str:
    if explicit_task and explicit_task != "chat":
        return explicit_task
    if intent == "document":
        return "report"
    if intent == "simulation":
        return "simulation"
    return "chat"
