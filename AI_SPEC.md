# AI_SPEC.md

Version: 2.0

---

# 1. AI 목표

- 근거 기반 응답
- 법령 기반 정확성
- 주제 자동 분류
- 문서 생성 자동화

---

# 2. 핵심 구조

## 기존
RAG + 생성

## 변경
RAG + Topic Classification + Agent Chain

---

# 3. AI 구성

1. Intent Classifier
2. Topic Classifier (NEW)
3. Topic Matcher (NEW)
4. Topic Router (NEW)
5. Retrieval Orchestrator
6. Legal Adapter
7. Model Resolver
8. Answer Generator
9. Document Composer
10. Reviewer Agent
11. Legal Checker
12. Simulation Agent

---

# 4. Topic Classification

## 입력
- 메시지
- 최근 대화

## 출력
- topic_label
- work_type
- entities
- confidence

---

# 5. Topic Routing

## 결정 유형
- matched
- new_topic
- ambiguous

---

# 6. 모델 선택 정책

우선순위:
1. topic override
2. task model
3. user default
4. system fallback

---

# 7. 답변 정책

- 법령 우선
- 내부 문서 보조
- 출처 구분
- 추정 최소화

---

# 8. 문서 생성 정책

- 템플릿 기반
- 세션 요약 기반
- 행정 문체 유지

---

# 9. 에이전트 체인

## 문서 생성
writer → reviewer → legal_checker

## 시뮬레이션
writer → simulation → legal_checker

---

# 10. 실패 처리

- 근거 부족 → 명시
- 법령 실패 → 경고
- 모델 실패 → fallback

---

# 11. 품질 기준

좋은 답변:
- 근거 있음
- 법령 정확
- 실무 사용 가능

나쁜 답변:
- 환각
- 법령 오류
- 근거 없음

---

# 12. 핵심 차별점

- Topic-aware AI
- Legal-aware AI
- Document-aware AI