import { describe, expect, it } from "vitest";
import {
  buildLawLinksOutput,
  collectLawHitsFromSearchJson,
  extractLawNames,
  normalizeLawName,
  pickBestLaw,
  searchLawFromAPI,
  type LawMatch,
} from "./index.js";

describe("law-qa", () => {
  it("normalizeLawName", () => {
    expect(normalizeLawName("국가계약법")).toBe("국가를 당사자로 하는 계약에 관한 법률");
  });

  it("extractLawNames", () => {
    const t = "국가계약법 및 시행령을 검토하세요.";
    expect(extractLawNames(t)).toContain("국가계약법");
  });

  it("pickBestLaw", () => {
    const hits: LawMatch[] = [
      { lawName: "국가를 당사자로 하는 계약에 관한 법률", lawId: "a", lawType: "법" },
      { lawName: "국가를 당사자로 하는 계약에 관한 법률 시행령", lawId: "b", lawType: "시행령" },
    ];
    const b = pickBestLaw(hits, "국가를 당사자로 하는 계약에 관한 법률");
    expect(b?.lawId).toBe("a");
  });

  it("searchLawFromAPI with mock", async () => {
    const data = {
      law: [{ 법령명한글: "허구 테스트법", lsiSeq: "999" }],
    };
    const m = await searchLawFromAPI("허구 테스트법", async () => data);
    expect(m?.lawId).toBe("999");
  });

  it("buildLawLinksOutput", () => {
    const s = buildLawLinksOutput(
      { lawName: "본법", lawId: "1", lawType: "법" },
      [{ lawName: "본법 시행령", lawId: "2", lawType: "시행령" }],
    );
    expect(s).toContain("📘");
    expect(s).toContain("[시행령]");
  });
});
