import { collectLawHitsFromSearchJson } from "./collectHits.js";
import { normalizeLawName } from "./normalizeLawName.js";
import { pickBestLaw } from "./pickBestLaw.js";
import type { LawMatch, LawSearchRawFn } from "./types.js";

const SEARCH_MODES = ["1", "0", "2"] as const;

/**
 * lawSearch.do JSON을 searchRaw로 받아 가장 적합한 1건만 반환.
 */
export async function searchLawFromAPI(
  name: string,
  searchRaw: LawSearchRawFn,
): Promise<LawMatch | null> {
  const qn = normalizeLawName(name.trim());
  if (!qn) return null;

  for (const sm of SEARCH_MODES) {
    try {
      const data = await searchRaw(qn, sm);
      const hits = collectLawHitsFromSearchJson(data);
      if (!hits.length) continue;
      const best = pickBestLaw(hits, qn);
      if (best) return best;
    } catch {
      /* 다음 모드 */
    }
  }
  return null;
}
