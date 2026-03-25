import { normalizeLawName } from "./normalizeLawName.js";
import type { LawMatch } from "./types.js";

function normCompact(s: string): string {
  return s.replace(/\s+/g, "");
}

function similarity(a: string, b: string): number {
  const A = normCompact(a);
  const B = normCompact(b);
  if (!A.length || !B.length) return 0;
  let match = 0;
  const len = Math.max(A.length, B.length);
  for (let i = 0; i < Math.min(A.length, B.length); i++) {
    if (A[i] === B[i]) match++;
  }
  let lcs = 0;
  for (let i = 0; i < A.length; i++) {
    for (let j = 0; j < B.length; j++) {
      let k = 0;
      while (i + k < A.length && j + k < B.length && A[i + k] === B[j + k]) k++;
      lcs = Math.max(lcs, k);
    }
  }
  return lcs / len;
}

/**
 * 검색 히트 중 1건만 선택 (정확 일치 → 법 우선 → 유사도).
 */
export function pickBestLaw(hits: LawMatch[], targetName: string): LawMatch | null {
  if (!hits.length) return null;
  const want = normCompact(normalizeLawName(targetName));
  const exact = hits.filter((h) => normCompact(h.lawName) === want);
  if (exact.length) {
    const laws = exact.filter((h) => h.lawType === "법");
    return laws[0] ?? exact[0]!;
  }
  const lawHits = hits.filter((h) => h.lawType === "법");
  const pool = lawHits.length ? lawHits : hits;
  const scored = pool.map((h) => ({
    h,
    s: similarity(h.lawName, normalizeLawName(targetName)),
  }));
  scored.sort((a, b) => b.s - a.s);
  const best = scored[0]!;
  if (best.s < 0.38) return null;
  return best.h;
}
