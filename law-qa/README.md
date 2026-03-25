# @innocurve/law-qa

법령명 추출·별칭 정규화·(주입형) lawSearch 응답 파싱·본법 1건 선택·시행령/규칙 연동·하단 출력 포맷.

**실제 `OC`·`lawSearch.do` HTTP는 `cap/backend`의 `law_resolution.py`에서 처리합니다.**  
이 패키지는 동일 규칙을 TypeScript로 재사용·단위테스트하기 위한 레이어입니다.

## 파일 구조

```
cap/law-qa/
  src/
    types.ts              # LawMatch, LawSearchRawFn, LawSearchError
    aliases.ts            # LAW_ALIAS_MAP, mergeAliases
    extractLawNames.ts
    normalizeLawName.ts
    classifyLawType.ts
    collectHits.ts        # collectLawHitsFromSearchJson
    pickBestLaw.ts
    searchLawFromAPI.ts
    findRelatedLaws.ts
    buildLawLinksOutput.ts
    index.ts
    lawResolution.spec.ts
  examples/usage.ts
```

## 설치·실행

```bash
cd cap/law-qa
npm install
npm test
npx tsx examples/usage.ts
```

## `LawSearchRawFn`

```ts
type LawSearchRawFn = (query: string, searchMode: string) => Promise<unknown>;
```

- 백엔드: `GET /api/.../law-search?q=&search=` 가 JSON 본문을 그대로 반환하도록 두고 연결.
- 테스트: 고정 JSON을 반환하는 async 함수 주입.

## 별칭 확장

```ts
import { mergeAliases, normalizeLawName } from "@innocurve/law-qa";

const ALIASES = mergeAliases({ 새약칭: "정식 법령명 …" });
// normalizeLawName은 패키지 기본 테이블만 사용 — 확장 시 래퍼 함수를 프로젝트 쪽에서 작성
```

Python 쪽은 `LAW_ALIAS_MAP`에 키를 추가하면 됩니다.

## 백엔드 연동

- `app/services/law_resolution.py` — 채팅 답변 생성 후 `resolve_laws_for_answer_text` 호출
- 응답 필드 `law_appendix`, `legal_debug.resolution`, `legal_debug.links` 갱신
