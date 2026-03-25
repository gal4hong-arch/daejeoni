from datetime import datetime

from pydantic import BaseModel, Field, field_serializer, field_validator


class StreamCreate(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=128)
    title: str = Field(default="", max_length=512)


class StreamCreateAuth(BaseModel):
    title: str = Field(default="새 대화", max_length=512)


class StreamOut(BaseModel):
    id: str
    user_id: str
    title: str

    model_config = {"from_attributes": True}


class TopicOut(BaseModel):
    id: str
    conversation_stream_id: str
    title: str
    topic_label: str
    work_type: str
    model_override: str | None

    model_config = {"from_attributes": True}


class TopicPatch(BaseModel):
    title: str | None = Field(default=None, max_length=512)
    topic_label: str | None = Field(default=None, max_length=256)


class ChatRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=128)
    content: str = Field(..., min_length=1)
    use_legal: bool = False
    task: str = Field(default="chat", description="chat | report | memo | simulation")
    document_ids: list[str] | None = None


class ChatRequestAuth(BaseModel):
    content: str = Field(..., min_length=1)
    use_legal: bool = False
    task: str = Field(default="chat", description="chat | report | memo | simulation")
    document_ids: list[str] | None = Field(default=None, description="검색에 사용할 kb_documents id 목록")
    skip_assistant: bool = Field(
        default=False,
        description="True면 사용자 메시지만 저장·토픽 라우팅하고 행정 AI 답변은 생성하지 않음(분할 검토 모드용).",
    )


class RoundtableTurnOut(BaseModel):
    role_id: str
    label: str
    content: str


class RoundtableRequestAuth(BaseModel):
    """역할 토의: premise + supervisor | councilor | citizen 조합."""

    premise: str = Field(..., min_length=3, max_length=12000)
    roles: list[str] = Field(..., min_length=1, max_length=3)

    @field_validator("roles")
    @classmethod
    def validate_roles(cls, v: list[str]) -> list[str]:
        allowed = frozenset({"supervisor", "councilor", "citizen"})
        out: list[str] = []
        for x in v:
            s = str(x).strip()
            if s in allowed and s not in out:
                out.append(s)
        if not out:
            raise ValueError("역할은 supervisor, councilor, citizen 중 하나 이상이어야 합니다.")
        return out


class RoundtableResponse(BaseModel):
    answer: str
    turns: list[RoundtableTurnOut]
    model_used: str


class DocxExportAuthIn(BaseModel):
    """검토 초안 등을 .docx로 내려받기."""

    content: str = Field(..., min_length=1, max_length=500_000)
    title: str | None = Field(default=None, max_length=200)
    filename_base: str | None = Field(default=None, max_length=120, description="확장자 제외 파일명")


def _normalize_prior_reporter_replies(v: object) -> list[str] | None:
    """보고자 답변 다회차 맥락(최근 5개, 각 12k자)."""
    if v is None:
        return None
    if not isinstance(v, list):
        return None
    out: list[str] = []
    for x in v[-5:]:
        s = str(x).strip()
        if not s:
            continue
        out.append(s[:12000])
    return out or None


class ReviewTurnRequestAuth(BaseModel):
    """분할 검토: reporter_brief 가 있으면 최근 1턴(질의+행정AI) 중심 검토."""

    role_id: str = Field(..., description="supervisor | councilor | citizen")
    system_prompt_override: str | None = Field(
        default=None,
        max_length=32000,
        description="비우면 서버 기본 페르소나(역할 카탈로그) 사용",
    )
    reporter_brief: str | None = Field(
        default=None,
        max_length=32000,
        description="검토 패널에서 생성한 요약 보고(이후 턴 검토 맥락)",
    )
    prior_reviewer_opinion: str | None = Field(
        default=None,
        max_length=50000,
        description="보고자 답변 직후 재검토: 직전 검토자 의견 전문",
    )
    reporter_reply_followup: str | None = Field(
        default=None,
        max_length=50000,
        description="보고자 답변 직후 재검토: 방금 생성된 보고자 답변",
    )
    prior_reporter_replies: list[str] | None = Field(
        default=None,
        description="이전 보고자 답변들(시간순). 반복·진전 여부 판단용",
    )

    @field_validator("prior_reporter_replies", mode="before")
    @classmethod
    def _v_prior_reporter_replies_turn(cls, v: object) -> list[str] | None:
        return _normalize_prior_reporter_replies(v)

    @field_validator("role_id")
    @classmethod
    def validate_review_role(cls, v: str) -> str:
        s = str(v).strip()
        if s not in frozenset({"supervisor", "councilor", "citizen"}):
            raise ValueError("role_id는 supervisor, councilor, citizen 중 하나여야 합니다.")
        return s


class ReviewTurnResponse(BaseModel):
    answer: str
    model_used: str


class ReviewBootstrapRequestAuth(BaseModel):
    role_id: str = Field(..., description="supervisor | councilor | citizen")
    system_prompt_override: str | None = Field(default=None, max_length=32000)

    @field_validator("role_id")
    @classmethod
    def validate_review_role_bootstrap(cls, v: str) -> str:
        s = str(v).strip()
        if s not in frozenset({"supervisor", "councilor", "citizen"}):
            raise ValueError("role_id는 supervisor, councilor, citizen 중 하나여야 합니다.")
        return s


class ReviewBootstrapResponse(BaseModel):
    report: str
    review: str
    model_used: str


class ReviewReporterReplyRequestAuth(BaseModel):
    reviewer_opinion: str = Field(..., min_length=1, max_length=50000)
    reporter_brief: str | None = Field(default=None, max_length=32000)
    prior_reporter_replies: list[str] | None = Field(
        default=None,
        description="이전 보고자 답변(시간순). 동일 약속 반복 방지",
    )
    prior_reviewer_opinions: list[str] | None = Field(
        default=None,
        description="이전 턴 검토자 의견(시간순). 패널 대화는 DB에 없어 클라이언트가 전달.",
    )

    @field_validator("prior_reporter_replies", mode="before")
    @classmethod
    def _v_prior_reporter_replies_rep(cls, v: object) -> list[str] | None:
        return _normalize_prior_reporter_replies(v)

    @field_validator("prior_reviewer_opinions", mode="before")
    @classmethod
    def _v_prior_reviewer_opinions_rep(cls, v: object) -> list[str] | None:
        return _normalize_prior_reporter_replies(v)


class ReviewReporterReplyResponse(BaseModel):
    reply: str
    model_used: str


class ChatResponse(BaseModel):
    answer: str
    topic_session_id: str
    decision_type: str
    detected_topic: str
    work_type: str
    confidence: float
    sources: list[dict]
    legal_note: str | None = None
    legal_debug: dict | None = Field(
        default=None,
        description="법제처 API 연동 로그·바로가기 링크(프론트 시스템 로그/새 창)",
    )
    law_appendix: str | None = Field(
        default=None,
        description="답변에 포함된 '📘 관련 법령' 블록(중복 표시·복사용)",
    )
    model_used: str
    intent: str = "chat"
    entities_json: str | None = None
    chat_trace: dict | None = Field(
        default=None,
        description="LLM·RAG·법령 사용 여부 요약(클라이언트 로그용)",
    )


class ChunkIngest(BaseModel):
    user_id: str
    source_title: str = ""
    content: str
    topic_session_id: str | None = None


class ChunkIngestAuth(BaseModel):
    source_title: str = ""
    content: str = Field(..., min_length=1)
    topic_session_id: str | None = None
    document_id: str | None = Field(default=None, description="기존 문서에 청크 추가; 없으면 새 문서 생성")


class KbDocumentOut(BaseModel):
    id: str
    title: str
    chunk_count: int = 0
    source_kind: str = "manual"
    source_url: str | None = None
    shared_globally: bool = False
    is_owner: bool = True


class UrlIngestAuth(BaseModel):
    url: str = Field(..., min_length=8, max_length=2048)
    source_title: str = Field(default="", max_length=512)


class KbDocumentPatchAuth(BaseModel):
    """소유 문서의 표시 제목만 수정."""

    title: str = Field(..., min_length=1, max_length=512)


class UserLawStatOut(BaseModel):
    """자주 찾는 법령(조회 누적) + RAG 등록 여부."""

    law_id: str = Field(
        ...,
        description="누적 키: 답변 링크·검색 조문이면 일련번호(MST/lsiSeq)일 수 있고, "
        "법령 임베딩 입력값은 보통 본문 API의 법령ID(ID 파라미터)입니다. 둘은 같은 법령이라도 숫자가 다를 수 있습니다.",
    )
    law_title: str
    hit_count: int
    last_access_at: datetime
    rag_document_id: str | None = None

    model_config = {"from_attributes": True}


class LawIngestAuth(BaseModel):
    law_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="lawService.do 본문 조회의 ID 파라미터(법령ID). "
        "lawSearch 조문 결과의 「법령일련번호」(MST)와는 다른 값일 수 있으니 혼동하지 마세요.",
    )
    law_title: str = Field(default="", max_length=512)


class TopicMergeIn(BaseModel):
    stream_id: str
    into_topic_id: str
    from_topic_ids: list[str] = Field(..., min_length=1)


class TopicSplitIn(BaseModel):
    move_last_n: int = Field(default=1, ge=1, le=500)


class DocumentChainIn(BaseModel):
    topic_id: str
    kind: str = Field(description="report|memo|explanation|council|simulation")
    legal_excerpt: str | None = None


class SimulationChainIn(BaseModel):
    topic_id: str
    scenario_hint: str = ""
    legal_excerpt: str | None = None


class AuditLogOut(BaseModel):
    id: str
    action: str
    detail_json: str
    created_at: datetime | None = None

    model_config = {"from_attributes": True}

    @field_serializer("created_at")
    def _dt(self, v: datetime | None) -> str | None:
        return v.isoformat() if v else None


class ChatMessageOut(BaseModel):
    id: str
    role: str
    content: str
    created_at: datetime | None = None

    model_config = {"from_attributes": True}

    @field_serializer("created_at")
    def _dt(self, v: datetime | None) -> str | None:
        return v.isoformat() if v else None


class UserPromptOut(BaseModel):
    """내 질문(유저 메시지) 목록 — 세션 이동·스크롤용."""

    stream_id: str
    stream_title: str = ""
    message_id: str
    preview: str = ""
    created_at: datetime | None = None

    @field_serializer("created_at")
    def _dt(self, v: datetime | None) -> str | None:
        return v.isoformat() if v else None


class MeOut(BaseModel):
    user_id: str
    email: str | None = None


class UserDataResetIn(BaseModel):
    """허용 범위: chat, review_drafts, embeddings, prompts, topics, logs, api_keys"""

    scopes: list[str] = Field(..., min_length=1, max_length=16)


class UserDataResetOut(BaseModel):
    ok: bool = True
    detail: dict = Field(default_factory=dict)


class LLMKeysIn(BaseModel):
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    google_api_key: str | None = None


class UserSettingsIn(BaseModel):
    default_model: str = ""
    task_models: dict[str, str] = Field(default_factory=dict)
    openai_api_key: str | None = None


class UserSettingsOut(BaseModel):
    user_id: str
    default_model: str
    task_models: dict[str, str]
    providers_with_keys: dict[str, bool] = Field(
        default_factory=dict,
        description="openai/anthropic/google 사용 가능(저장된 키 또는 서버 OpenAI env)",
    )
