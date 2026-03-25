/**
 * 별칭 → 정식 법령명. 키만 추가하면 확장.
 */
export const LAW_ALIAS_MAP: Readonly<Record<string, string>> = {
  국가계약법: "국가를 당사자로 하는 계약에 관한 법률",
  지방계약법: "지방자치단체를 당사자로 하는 계약에 관한 법률",
} as const;

export function mergeAliases(extra: Record<string, string>): Record<string, string> {
  return { ...LAW_ALIAS_MAP, ...extra };
}
