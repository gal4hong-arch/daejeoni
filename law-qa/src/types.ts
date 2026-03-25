/**
 * 법령 매칭·표시에 쓰는 공통 타입.
 */
export type LawType = "법" | "시행령" | "시행규칙" | "기타";

/** lawSearch.do 1건에 대응 */
export interface LawMatch {
  lawName: string;
  lawId: string;
  lawType: LawType;
}

/**
 * lawSearch.do 호출을 추상화 (query + search 모드).
 * OC·URL은 백엔드 프록시에서 처리하는 것을 권장.
 */
export type LawSearchRawFn = (query: string, searchMode: string) => Promise<unknown>;

export class LawSearchError extends Error {
  constructor(
    message: string,
    public readonly cause?: unknown,
  ) {
    super(message);
    this.name = "LawSearchError";
  }
}
