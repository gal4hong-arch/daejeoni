-- Supabase / Postgres — TRD 데이터 모델 + RAG·법령 MVP 확장
-- pgvector: CREATE EXTENSION IF NOT EXISTS vector;

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

CREATE TABLE conversation_streams (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_conversation_streams_user ON conversation_streams(user_id);

CREATE TABLE topic_sessions (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_stream_id UUID NOT NULL REFERENCES conversation_streams(id) ON DELETE CASCADE,
    title TEXT NOT NULL DEFAULT '',
    topic_label TEXT NOT NULL DEFAULT '',
    work_type TEXT NOT NULL DEFAULT 'general',
    model_override TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_topic_sessions_stream ON topic_sessions(conversation_stream_id);

CREATE TABLE chat_messages (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    conversation_stream_id UUID NOT NULL REFERENCES conversation_streams(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
    content TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_chat_messages_stream_created ON chat_messages(conversation_stream_id, created_at);

CREATE TABLE message_topic_maps (
    message_id UUID NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    topic_session_id UUID NOT NULL REFERENCES topic_sessions(id) ON DELETE CASCADE,
    PRIMARY KEY (message_id, topic_session_id)
);

CREATE TABLE topic_classifications (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    message_id UUID NOT NULL REFERENCES chat_messages(id) ON DELETE CASCADE,
    detected_topic TEXT NOT NULL DEFAULT '',
    decision_type TEXT NOT NULL CHECK (decision_type IN ('matched', 'new_topic', 'ambiguous')),
    work_type TEXT NOT NULL DEFAULT 'general',
    confidence REAL NOT NULL DEFAULT 0,
    entities_json TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE user_api_keys (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT NOT NULL,
    provider TEXT NOT NULL,
    encrypted_key TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, provider)
);

CREATE TABLE user_model_preferences (
    user_id TEXT PRIMARY KEY,
    default_model TEXT NOT NULL DEFAULT '',
    task_models_json TEXT NOT NULL DEFAULT '{}',
    dual_api_reporter_sub_first BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 기존 DB: ALTER TABLE user_model_preferences ADD COLUMN IF NOT EXISTS dual_api_reporter_sub_first BOOLEAN NOT NULL DEFAULT false;

-- RAG 문서 단위 (다중 문서 선택)
CREATE TABLE kb_documents (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    source_kind TEXT NOT NULL DEFAULT 'manual',
    source_url TEXT,
    shared_globally BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 기존 DB: ALTER TABLE kb_documents ADD COLUMN IF NOT EXISTS shared_globally BOOLEAN NOT NULL DEFAULT false;
CREATE INDEX idx_kb_documents_user ON kb_documents(user_id);

-- RAG 청크 (embedding_json: OpenAI 등 임베딩 JSON 배열 / pgvector 전환 시 컬럼 추가 가능)
CREATE TABLE kb_chunks (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT NOT NULL,
    document_id UUID REFERENCES kb_documents(id) ON DELETE SET NULL,
    source_title TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    embedding_json TEXT,
    topic_session_id UUID REFERENCES topic_sessions(id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_kb_chunks_user ON kb_chunks(user_id);
CREATE INDEX idx_kb_chunks_document ON kb_chunks(document_id);

-- 법령 스냅샷
-- ========== RLS (선택): PostgREST/클라이언트 직접 접근 시 ==========
-- 백엔드가 Postgres에 직접 연결(SQLAlchemy)하면 RLS는 DB 역할에 따라 우회될 수 있습니다.
-- Supabase Auth와 anon 키로 테이블을 직접 읽는 경우에만 아래를 적용하세요.

-- ALTER TABLE conversation_streams ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY conv_own ON conversation_streams FOR ALL USING (auth.uid()::text = user_id);
-- (나머지 테이블도 user_id = auth.uid()::text 패턴으로 동일)

CREATE TABLE legal_snapshots (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    topic_session_id UUID REFERENCES topic_sessions(id) ON DELETE SET NULL,
    query TEXT NOT NULL,
    response_json TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 감사 로그
CREATE TABLE audit_logs (
    id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    user_id TEXT NOT NULL,
    action TEXT NOT NULL,
    detail_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_audit_logs_user ON audit_logs(user_id);
CREATE INDEX idx_audit_logs_created ON audit_logs(created_at);
