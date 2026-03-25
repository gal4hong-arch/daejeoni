import { classifyLawType } from "./classifyLawType.js";
import type { LawMatch } from "./types.js";

const ID_KEYS = [
  "법령일련번호",
  "lsiSeq",
  "법령ID",
  "lawId",
  "law_id",
  "행정규칙ID",
  "자치법규ID",
  "admRulId",
  "MST",
  "법령MST",
] as const;

function displayName(d: Record<string, unknown>): string {
  const v =
    d["법령명한글"] ??
    d["법령명"] ??
    d["lawNm"] ??
    d["법령명_한글"] ??
    d["법령명약칭"];
  return typeof v === "string" ? v.trim() : "";
}

function idFromDict(d: Record<string, unknown>): string {
  for (const k of ID_KEYS) {
    const v = d[k];
    if (v == null) continue;
    const s = String(v).trim();
    if (/^[A-Za-z0-9._-]+$/.test(s) && /\d/.test(s) && s.length <= 32) return s;
  }
  return "";
}

/**
 * lawSearch.do JSON 트리에서 LawMatch 목록 수집 (lawId 기준 중복 제거).
 */
export function collectLawHitsFromSearchJson(data: unknown): LawMatch[] {
  const seen = new Set<string>();
  const out: LawMatch[] = [];

  const walk = (node: unknown): void => {
    if (node == null) return;
    if (Array.isArray(node)) {
      for (const x of node) walk(x);
      return;
    }
    if (typeof node === "object") {
      const d = node as Record<string, unknown>;
      const name = displayName(d);
      const id = idFromDict(d);
      if (name && id && !seen.has(id)) {
        seen.add(id);
        out.push({ lawName: name, lawId: id, lawType: classifyLawType(name) });
      }
      for (const v of Object.values(d)) walk(v);
    }
  };

  walk(data);
  return out;
}
