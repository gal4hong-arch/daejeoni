import type { LawType } from "./types.js";

/**
 * 법령명 문자열로 표시용 타입 분류.
 */
export function classifyLawType(name: string): LawType {
  const n = name.trim();
  if (n.includes("시행규칙")) return "시행규칙";
  if (n.includes("시행령")) return "시행령";
  if (n.includes("법률") || (n.endsWith("법") && !n.includes("시행") && !n.includes("규칙"))) return "법";
  return "기타";
}
