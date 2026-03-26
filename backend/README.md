# 행정 AI 업무지원 플랫폼 MVP (Backend)

[PRD.md](../PRD.md), [TRD.md](../TRD.md), [AI_SPEC.md](../AI_SPEC.md), [핵심기능-기술요구사항.md](../핵심기능-기술요구사항.md)에 맞춘 **로컬 실행용 API**입니다.

## 기능 요약

- **Conversation Stream** + **Topic Session** 자동 분류·라우팅 (규칙·유사도 기반, 선택 시 OpenAI 보강)
- **문서 청크 인입 + BM25** 기반 검색 (하이브리드의 키워드 측면; 벡터는 Supabase/pgvector 연동 시 확장)
- **법령 어댑터** (`legal_adapter`) + 스냅샷; Intent `legal_focus` 시 자동 조회
- **법령 QA 보강** (`law_resolution`): LLM이 뽑은 제목·키워드로 **지능형 법령검색** `lawSearch.do?target=aiSearch`를 먼저 호출하고, 필요 시 기존 `target=law`로 폴백 → 본법 확정 → 시행령·시행규칙 연동 → 답변 하단 `📘 관련 법령`·`legal_debug.links` (`LAW_GO_KR_OC` 필요)
- **Intent** + **엔티티** JSON(토픽 LLM 분류 시)
- **하이브리드 RAG**: BM25 + OpenAI 임베딩 코사인; **다중 문서**(`kb_documents` + `document_ids` 필터)
- **문서 종류**: 보고서·공문·설명자료·의회 답변·시뮬 템플릿 + **에이전트 체인**(작성→상급자→법령 / 시뮬→법령)
- **안건 병합·분리** API; **감사 로그**; **OpenAI / Anthropic / Gemini** 통합 호출
- **모델 우선순위**: topic override → task → user default → system fallback
- **API Key Fernet 암호화** 저장
- **멀티유저**: Supabase Auth(이메일/비밀번호) + JWT로 API 보호; `ALLOW_DEMO_USER_HEADER`로 로컬 데모
- **UI** (`/ui`): 접이식 사이드바, 세션/RAG/키 관리, 채팅 로그에 작업 이벤트 표시, 답변·검토 초안 복사·양식 반영 시 Word(.docx) 다운로드(`POST /api/v1/topics/auth/export-docx`, `python-docx` 필요)

## 실행

### `ModuleNotFoundError: No module named 'app'`

FastAPI 패키지는 **`cap/backend/app/`** 아래에 있습니다. 아래 중 **하나**로 실행해야 합니다.

1. **권장**: 백엔드 디렉터리로 이동 후 기동  
   `cd cap/backend` → `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000`
2. **저장소 루트에 머물 때**: Uvicorn의 `--app-dir`로 모듈 경로 지정  
   `uvicorn app.main:app --reload --host 0.0.0.0 --port 8000 --app-dir cap/backend`  
   (Windows 경로: `--app-dir cap\backend` 도 동일)
3. 저장소 루트의 **`run_backend.py`** 또는 **`run-backend.ps1`** 실행 (내부적으로 위와 동일)

루트에서 `uvicorn app.main:app ...` 만 치면 Python이 `app`을 찾지 못해 위 오류가 납니다.

---

**`poetry run uvicorn …` vs `poetry shell` 후 `uvicorn …`**  
둘 다 **같은 가상환경**에서 앱을 띄웁니다. 차이는 `poetry run`은 **한 번만** 그 환경을 쓰고 끝나고, `poetry shell`은 셸을 **Poetry venv로 바꾼 뒤** 같은 터미널에서 연속으로 명령을 치기 좋다는 점뿐입니다. 서버 동작은 동일합니다.

서버 기동 시 터미널에 **채팅 UI / API 문서 URL**이 한 번 더 출력됩니다(Uvicorn 기본 로그와 함께).

저장소 루트에서 Poetry로 전체 의존성을 쓸 수 있으면:

```bash
poetry install
cd cap/backend
copy .env.example .env   # Windows — 필요 시 FERNET_KEY, OPENAI_API_KEY 설정
poetry run uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Poetry 설치가 다른 패키지(예: kaleido) 때문에 막히면, **백엔드 디렉터리에서** pip로 설치한 뒤 동일하게 `uvicorn`을 실행할 수 있습니다.

```bash
cd cap/backend
python -m pip install -r requirements.txt
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

(한 줄로 최소만 쓰려면 예전처럼 `pip install fastapi "uvicorn[standard]" httpx cryptography python-multipart sqlalchemy openai rank-bm25 python-dotenv pydantic supabase PyJWT psycopg2-binary python-docx` 도 가능하지만, RAG·PDF·LLM 전 기능을 쓰려면 `requirements.txt` 전체를 권장합니다.)

- API 문서: http://127.0.0.1:8000/docs  
- 채팅 UI: http://127.0.0.1:8000/ui  

## 환경 변수

| 변수 | 설명 |
|------|------|
| `DATABASE_URL` | 기본 `sqlite:///./data/platform.db` |
| `FERNET_KEY` | Fernet 키 (미설정 시 개발용 임시 키; 운영에서는 반드시 고정 키) |
| `OPENAI_API_KEY` | OpenAI (없으면 사용자 DB 저장 키 또는 기능 제한) |
| `ANTHROPIC_API_KEY` | Claude(Anthropic) — 로컬·서버 공통, 비우면 사용자 저장 키만 |
| `GOOGLE_API_KEY` | Gemini(Google). `GEMINI_API_KEY` 로 동일 지정 가능(별칭) |
| `LAW_GO_KR_OC` | **국가법령정보 OC 인증값** (있으면 `www.law.go.kr/DRF/lawSearch.do` 호출). [가이드](https://open.law.go.kr/LSO/openApi/guideList.do) · [OC 변경](https://open.law.go.kr/LSO/usr/usrOcInfoMod.do) |
| `LAW_GO_KR_BASE_URL` | 기본 `https://www.law.go.kr/DRF/lawSearch.do` |
| `LAW_GO_KR_TARGET` / `LAW_GO_KR_TARGET_FALLBACK` | 기본 `aiSearch` → 실패 시 `law` 본문 검색(`search=2`) |
| `LAW_GO_KR_TIMEOUT` | 초 단위 타임아웃 (기본 25) |
| `LAW_GO_KR_SERVICE_URL` | 본문 조회 기본 `https://www.law.go.kr/DRF/lawService.do` (`OC`, `target`, `type`, `ID`) |
| `LAW_GO_KR_SERVICE_TYPE` | `JSON` 또는 `XML` (가이드 예시는 XML) |
| `LAW_GO_KR_BODY_TARGET` | 본법 본문 1차 `target` (기본 `eflaw` = 현행법령·시행일 본문 API) |
| `LAW_GO_KR_BODY_TARGET_FALLBACK` | `eflaw`가 비어 있거나 오류 JSON이면 재시도할 `target` (기본 `law`) |
| `LAW_GO_KR_SERVICE_MAX_IDS` | 검색 결과에서 본문 조회할 법령 ID 개수 상한 (기본 2, 최대 5) |
| `LAW_GO_KR_SERVICE_FETCH` | `false`면 검색만 하고 `lawService.do` 호출 안 함 (기본 `true`) |
| (본문 API) | **전체 본문**: 가이드상 `JO` 생략 시 모든 조. 질의에 「n조」가 없으면 `JO`를 보내지 않음. **특정 조만**: 질문에 조가 있으면 `JO=6자리`로 `lawService.do`에 전달. 채팅 로그의 `legal_debug.body_fetches`에 `body_plain_len`·`body_preview`(추출 평문 앞부분)가 포함되어 본문 조회 결과를 확인할 수 있음. |
| `LEGAL_API_BASE_URL` | `LAW_GO_KR_OC`가 **없을 때만** `{base}/search?q=` 프록시용 (레거시) |
| `SYSTEM_FALLBACK_MODEL` | 기본 모델명 문자열 |
| `SUPABASE_URL` | Supabase 프로젝트 URL (`supabase-py`용) |
| `SUPABASE_ANON_KEY` | Supabase anon(public) API 키 (프론트 Auth + `GET /config/public` 노출) |
| `SUPABASE_JWT_SECRET` | **운영 필수** — Bearer **서명 검증**에 사용. 비워 두면 Secret 없이 `sub`만 디코딩(로컬 편의, 배포 시 위험) |
| `ALLOW_DEMO_USER_HEADER` | `true`면 `X-Demo-User` 헤더로 JWT 없이 사용자 구분(로컬 전용) |

## Supabase

- **Auth**: 웹 UI가 `@supabase/supabase-js`로 회원가입/로그인 후 `access_token`을 API에 `Authorization: Bearer`로 붙입니다. `SUPABASE_JWT_SECRET`이 있으면 서명 검증, 없으면 로컬용으로 토큰 페이로드만 디코딩합니다.
- **데이터 저장**: `DATABASE_URL`을 **Supabase Postgres** URI로 설정 (`postgresql+psycopg2://...`). 첫 기동 시 SQLAlchemy가 테이블을 생성합니다. **단계별·주의사항**은 [../sql/SUPABASE_SETUP.md](../sql/SUPABASE_SETUP.md) 참고. (참고용 DDL: [../sql/supabase_schema.sql](../sql/supabase_schema.sql))
- **인증 API** (헤더: Bearer 또는 데모 시 `X-Demo-User`):
  - `POST/GET /api/v1/streams/auth`, `GET/DELETE /api/v1/streams/auth/{id}`, `GET/POST .../messages`, `POST .../auth/{stream_id}/roundtable` (역할 토의: `premise`, `roles`: `supervisor` \| `councilor` \| `citizen`)
  - `POST /api/v1/documents/chunks/auth`
  - `GET/PUT /api/v1/users/me`, `PUT /api/v1/users/me/llm-keys`, `PUT .../me/settings`
  - `GET /api/v1/topics/auth/stream/{stream_id}`, `POST /api/v1/topics/auth/{topic_id}/compose`
- **supabase-py**: `app/supabase_client.py`는 서버 측 보조용. `/api/v1/health`의 `supabase`는 URL·anon 키 설정 여부입니다.
- RLS 예시는 `supabase_schema.sql` 주석을 참고하세요. SQLAlchemy 직접 연결 시 DB 역할에 따라 RLS가 우회될 수 있습니다.
