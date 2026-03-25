# TRD.md

# Technical Requirements Document
Version: 2.0

---

# 1. 아키텍처 개요

## 주요 구성
- Frontend
- Backend API
- AI Orchestrator
- Retrieval Engine
- Topic Manager (NEW)
- User Model Config
- Legal API Adapter
- Supabase (Postgres + pgvector)

---

# 2. 핵심 구조

## 2.1 Conversation Stream
- 사용자 전체 대화 흐름

## 2.2 Topic Session (핵심 변경)
- 업무 단위 자동 생성
- 메시지와 연결됨

---

# 3. 데이터 모델

## conversation_streams
- id
- user_id
- title

## topic_sessions
- id
- conversation_stream_id
- title
- topic_label
- work_type

## chat_messages
- id
- conversation_stream_id
- content

## message_topic_maps
- message_id
- topic_session_id

## topic_classifications
- message_id
- detected_topic
- decision_type

---

# 4. Topic Manager (핵심 컴포넌트)

## 역할
- 주제 분류
- topic 매칭
- topic 생성/연결

## 흐름
1. 메시지 입력
2. Topic Classifier 실행
3. 기존 topic 비교
4. routing 결정

---

# 5. 검색 구조

- Vector + Keyword Hybrid
- Topic 기반 context aggregation

---

# 6. 법령 구조

- API 호출
- snapshot 저장
- topic session 연결

---

# 7. 사용자 AI 설정

## 테이블
- user_api_keys
- user_model_preferences

## 적용
- 모델 선택
- API Key 적용
- topic별 override 가능

---

# 8. AI 실행 흐름

1. 메시지 입력
2. Topic 분류
3. Retrieval
4. 법령 조회 (optional)
5. 모델 선택
6. 답변 생성

---

# 9. 보안

- API Key 암호화 저장
- 문서 접근 권한 필터링
- 로그 기록

---

# 10. 확장성

- 멀티 기관
- 대시민 확장 가능
- 모델 provider 교체 가능