-- 개발용: 앱 테이블만 제거 후 백엔드 기동 시 ORM create_all 로 재생성.
-- FK/타입 불일치(bigint id 등)로 init_db 가 실패할 때 Supabase SQL Editor 에서 실행.
-- 운영 데이터가 있으면 백업 후에만 사용하세요.

DROP TABLE IF EXISTS user_law_stats CASCADE;
DROP TABLE IF EXISTS legal_snapshots CASCADE;
DROP TABLE IF EXISTS kb_chunks CASCADE;
DROP TABLE IF EXISTS topic_classifications CASCADE;
DROP TABLE IF EXISTS message_topic_maps CASCADE;
DROP TABLE IF EXISTS chat_messages CASCADE;
DROP TABLE IF EXISTS topic_sessions CASCADE;
DROP TABLE IF EXISTS kb_documents CASCADE;
DROP TABLE IF EXISTS audit_logs CASCADE;
DROP TABLE IF EXISTS user_api_keys CASCADE;
DROP TABLE IF EXISTS user_model_preferences CASCADE;
DROP TABLE IF EXISTS conversation_streams CASCADE;
