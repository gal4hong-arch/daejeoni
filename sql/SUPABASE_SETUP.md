# Supabase Postgres를 앱 DB로 쓰기

백엔드는 **`DATABASE_URL` 하나**로 SQLAlchemy가 붙습니다. Supabase **Auth**(JWT)와 **Postgres**(데이터)는 역할이 다릅니다.

| 구분 | 역할 |
|------|------|
| `SUPABASE_URL` / `SUPABASE_ANON_KEY` | 프론트 로그인·`supabase-js` |
| `SUPABASE_JWT_SECRET` | API `Authorization: Bearer` **서명 검증** (운영에서 필수 권장) |
| **`DATABASE_URL`** | 채팅·RAG·API 키·설정 등 **앱 테이블 전부** |

---

## 1. 준비 (Supabase 대시보드)

1. **Project Settings → Database**
   - **Connection string** → **URI** 형식 복사  
   - 비밀번호는 Database 비밀번호(초기에 설정한 값). 잊었으면 **Reset database password**로 재설정.
2. **SQLAlchemy용**으로는 URI를 아래처럼 바꿉니다.
   - 스킴를 **`postgresql+psycopg2://`** 로 시작하게 둡니다 (드라이버 명시).
   - 예:  
     `postgresql+psycopg2://postgres.[ref]:[YOUR-PASSWORD]@aws-0-[region].pooler.supabase.com:6543/postgres`
3. **Settings → API → JWT Secret**  
   - `SUPABASE_JWT_SECRET`에 넣어 Bearer 토큰을 검증합니다. (비우면 개발 편의용 비검증 경로만 사용)  
   - 일부 프로젝트는 JWT가 **RS256** 또는 **ES256**(비대칭)입니다. 이 경우 백엔드는 `SUPABASE_URL` 기준 JWKS(`…/auth/v1/.well-known/jwks.json`)로 검증하므로 **`SUPABASE_URL`이 `.env`에 있어야** 합니다. (레거시 HS256만 쓰는 경우에도 URL을 두는 것을 권장)

---

## 2. `cap/backend/.env` 설정 예

```env
# Supabase Postgres (예: Transaction pooler 6543 — 서버리스·다수 연결에 적합)
DATABASE_URL=postgresql+psycopg2://postgres.xxxxx:비밀번호@aws-0-ap-northeast-2.pooler.supabase.com:6543/postgres

# 코드에서 sslmode 가 없으면 자동으로 sslmode=require 를 붙입니다. 직접 넣어도 됩니다.
# DATABASE_URL=postgresql+psycopg2://...@db.xxxxx.supabase.co:5432/postgres?sslmode=require

SUPABASE_URL=https://xxxxx.supabase.co
SUPABASE_ANON_KEY=eyJ...
SUPABASE_JWT_SECRET=여기에_JWT_Secret
```

**로컬 SQLite로 되돌리려면** 다시 `DATABASE_URL=sqlite:///./data/platform.db` 만 쓰면 됩니다.

---

## 3. 스키마 생성

- **첫 기동** 시 `app/main.py` → `init_db()` → `Base.metadata.create_all()` 이 **빈 DB에 테이블을 만듭니다** (SQLite와 동일한 ORM 모델).
- 수동으로 SQL을 넣고 싶다면 [`supabase_schema.sql`](supabase_schema.sql)을 참고하되, ORM은 `String(36)` UUID 등 **타입이 SQL 파일과 다를 수 있음**.  
  **권장**: 빈 Postgres에 **앱만 띄워서 `create_all`** 로 맞추거나, 이미 SQLite를 쓰 중이면 **데이터 마이그레이션은 별도 덤프/복원**이 필요합니다.

---

## 4. 주의사항 (요약)

1. **SSL**  
   - Supabase는 SSL 필수. `normalize_database_url`이 `sslmode`가 없으면 `require`를 붙입니다.
2. **연결 풀 (Pooler)**  
   - **6543** Transaction pooler: 연결 수 제한이 많고 서버리스에 맞음.  
   - **5432** (Direct 또는 Session pooler): 장시간 세션·마이그레이션 도구에 유리.  
   - PgBouncer **transaction** 모드에서 드물게 prepared statement 관련 오류가 나면, **Direct 5432**로 바꿔 보세요.
3. **RLS (Row Level Security)**  
   - 앱이 **Postgres에 직접 연결**(DB 비밀번호·pooler)이면, 연결 역할이 **보통 RLS를 우회**합니다.  
   - **anon 키로 테이블을 브라우저에서 직접 읽는** 구조가 아니라면, RLS는 필수는 아닙니다.  
   - `supabase_schema.sql` 주석의 RLS는 PostgREST 직접 노출용 예시입니다.
4. **`user_id`**  
   - API는 JWT의 `sub`(Supabase 사용자 ID)를 `user_id`로 씁니다. **SQLite에서 쓰던 데모 ID와 Supabase 로그인 사용자는 다름** → DB를 갈아끼우면 데이터가 비어 있는 것이 정상입니다.
5. **`FERNET_KEY`**  
   - 사용자 API 키 암호화용. **운영에서는 한 번 정하고 바꾸지 않는 것**을 권장(키 변경 시 기존 암호문 복호화 불가).
6. **SQLite → Postgres 이전**  
   - 자동 스크립트는 없음. 필요 시 `pgloader` 등으로 이전하거나, 새로 Supabase에만 시작합니다.
7. **`could not translate host name "db....supabase.co" … Name or service not known` (Windows 등)**  
   - **원인**: `db.<project>.supabase.co` 가 **IPv6(A 레코드 없음)** 로만 풀리는 경우가 있고, PC·회선·DNS에 따라 이름 해석이 실패하거나 psycopg2가 IPv4만 시도해 연결이 깨질 수 있습니다.  
   - **권장**: 대시보드 **Database → Connection string** 에서 **`aws-0-…pooler.supabase.com`** 호스트(포트 **6543** Transaction pooler 또는 **5432** Session pooler)를 쓴 URI로 `DATABASE_URL` 을 설정하세요. Pooler는 보통 **IPv4** 로도 풀립니다.  
   - **추가**: DNS를 `8.8.8.8` 등으로 바꿔 보거나, Supabase 프로젝트에서 **IPv4** 옵션(유료)을 쓰는 방법도 있습니다.
8. **FK 타입 불일치 (`message_id` varchar vs `id` bigint 등)**  
   - `init_db()` 가 `topic_classifications` 등을 만들 때 **기존 `chat_messages` 행**이 ORM과 다른 타입(예: `id` 가 `bigint`)이면 PostgreSQL이 FK를 거절합니다.  
   - **원인**: 예전에 수동으로 만든 테이블, 다른 스키마, 또는 잘못된 SQL로 만든 테이블이 남아 있는 경우입니다. 앱은 UUID 문자열을 `VARCHAR(36)` 으로 씁니다.  
   - **조치 (개발·스테이징, 데이터 날려도 될 때)**: SQL Editor에서 [`supabase_reset_app_tables.sql`](supabase_reset_app_tables.sql) 을 실행한 뒤 백엔드를 다시 띄워 `create_all` 로 테이블을 맞춥니다. 운영 DB는 **백업 후** 동일 절차를 검토하세요.

---

## 5. 확인

- 서버 기동 후 `GET /api/v1/health` — DB 연결 확인  
- `GET /docs` 로 로그인 후 스트림·RAG 한 번 호출해 Supabase **Table Editor**에서 행이 생기는지 확인
