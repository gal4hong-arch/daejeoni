import { LAW_ALIAS_MAP } from "./aliases.js";

/**
 * LLM 응답 등에서 법령명·약칭 후보를 순서 유지·중복 제거로 추출.
 */
export function extractLawNames(text: string): string[] {
  const t = text.trim();
  if (!t) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  const add = (s: string) => {
    const x = s.replace(/\s+/g, " ").trim();
    if (x.length < 3 || seen.has(x)) return;
    seen.add(x);
    out.push(x);
  };

  for (const pat of [
    /([가-힣0-9·\s]{2,85}?(?:법률|시행령|시행규칙))/g,
    /([가-힣0-9·]{2,55}법)(?!\s*원)/g,
  ]) {
    let m: RegExpExecArray | null;
    while ((m = pat.exec(t)) !== null) add(m[1]!);
  }

  const keys = Object.keys(LAW_ALIAS_MAP).sort((a, b) => b.length - a.length);
  for (const alias of keys) {
    if (t.includes(alias)) add(alias);
  }
  return out;
}
