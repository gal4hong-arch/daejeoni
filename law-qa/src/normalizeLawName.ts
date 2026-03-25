import { LAW_ALIAS_MAP } from "./aliases.js";

/**
 * 별칭 매핑을 적용해 정식 법령명(또는 원문)으로 변환.
 */
export function normalizeLawName(name: string): string {
  const t = name.trim();
  if (!t) return t;
  if (LAW_ALIAS_MAP[t]) return LAW_ALIAS_MAP[t];
  const compact = t.replace(/\s+/g, "");
  for (const [k, v] of Object.entries(LAW_ALIAS_MAP)) {
    if (k.replace(/\s+/g, "") === compact) return v;
  }
  return t;
}
