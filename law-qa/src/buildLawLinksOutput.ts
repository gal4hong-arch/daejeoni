import type { LawMatch } from "./types.js";

/**
 * 답변 하단 블록: 본법 1줄 필수, 시행령·규칙은 있을 때만, 중복 제거.
 */
export function buildLawLinksOutput(mainLaw: LawMatch, relatedLaws: LawMatch[]): string {
  const lines = ["", "📘 관련 법령", `- [법] ${mainLaw.lawName}`];
  const seenIds = new Set<string>([mainLaw.lawId]);
  const seenNames = new Set<string>([mainLaw.lawName.replace(/\s+/g, "")]);

  for (const r of relatedLaws) {
    const nc = r.lawName.replace(/\s+/g, "");
    if (seenIds.has(r.lawId) || seenNames.has(nc)) continue;
    if (r.lawType === "시행령") {
      lines.push(`- [시행령] ${r.lawName}`);
    } else if (r.lawType === "시행규칙") {
      lines.push(`- [시행규칙] ${r.lawName}`);
    } else continue;
    seenIds.add(r.lawId);
    seenNames.add(nc);
  }
  return lines.join("\n");
}
