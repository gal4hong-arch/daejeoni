# 행정 AI 업무지원 플랫폼 (cap)

설계 문서와 구현물은 다음과 같이 정리되어 있습니다.

| 문서 / 경로 | 설명 |
|-------------|------|
| [PRD.md](PRD.md) | 제품 요구 |
| [TRD.md](TRD.md) | 기술·데이터 모델 |
| [AI_SPEC.md](AI_SPEC.md) | AI 파이프라인·정책 |
| [핵심기능-기술요구사항.md](핵심기능-기술요구사항.md) | 구현용 요약 |
| [sql/supabase_schema.sql](sql/supabase_schema.sql) | Postgres/Supabase DDL |
| [backend/](backend/) | FastAPI MVP (대화·토픽·RAG·법령 스텁·설정) |

## 빠른 실행

`backend/README.md` 참고. API 서버는 **`backend` 폴더 안에서** `uvicorn app.main:app ...` 하거나, 상위 폴더에서 `--app-dir backend`(또는 루트의 `run_backend.py`)로 실행하세요. 루트에서 `uvicorn app.main:app`만 실행하면 `No module named 'app'` 오류가 납니다.

저장소 전체 `poetry install`이 환경에 따라 실패할 수 있으므로, 백엔드만 검증할 때는 해당 README의 **pip 일괄 설치** 안내를 사용할 수 있습니다.
