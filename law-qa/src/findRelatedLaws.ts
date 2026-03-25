import { searchLawFromAPI } from "./searchLawFromAPI.js";
import type { LawMatch, LawSearchRawFn } from "./types.js";

function normCompact(s: string): string {
  return s.replace(/\s+/g, "");
}

function sim(a: string, b: string): number {
  const A = normCompact(a);
  const B = normCompact(b);
  let m = 0;
  const len = Math.max(A.length, B.length) || 1;
  for (let i = 0; i < Math.min(A.length, B.length); i++) if (A[i] === B[i]) m++;
  return m / len;
}

/**
 * 본법 기준으로 시행령·시행규칙만 API 재검색 (존재·연관성 있는 것만).
 */
export async function findRelatedLaws(
  baseLaw: LawMatch,
  searchRaw: LawSearchRawFn,
): Promise<LawMatch[]> {
  if (baseLaw.lawType !== "법") return [];
  const out: LawMatch[] = [];

  for (const [suffix, want] of [
    [" 시행령", "시행령"],
    [" 시행규칙", "시행규칙"],
  ] as const) {
    const q = baseLaw.lawName + suffix;
    const m = await searchLawFromAPI(q, searchRaw);
    if (!m || m.lawType !== want) continue;
    if (
      !normCompact(m.lawName).includes(normCompact(baseLaw.lawName)) &&
      sim(m.lawName, q) < 0.55
    ) {
      continue;
    }
    if (!out.some((x) => x.lawId === m.lawId)) out.push(m);
  }
  return out;
}
