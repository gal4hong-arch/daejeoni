/**
 * 사용 예: searchRaw는 실제로는 FastAPI 프록시 등에서 구현.
 */
import {
  buildLawLinksOutput,
  extractLawNames,
  findRelatedLaws,
  normalizeLawName,
  searchLawFromAPI,
  type LawMatch,
  type LawSearchRawFn,
} from "../src/index.js";

async function demo() {
  const fakeJson: Record<string, unknown> = {
    law: [
      { 법령명한글: "국가를 당사자로 하는 계약에 관한 법률", lsiSeq: "1" },
      { 법령명한글: "국가를 당사자로 하는 계약에 관한 법률 시행령", lsiSeq: "2" },
    ],
  };

  const searchRaw: LawSearchRawFn = async (query, _mode) => {
    console.log("검색:", query, "mode:", _mode);
    return fakeJson;
  };

  const answer =
    "국가계약법에 따라 절차를 밟아야 합니다. 국가를 당사자로 하는 계약에 관한 법률을 확인하세요.";
  const names = extractLawNames(answer);
  console.log("추출:", names);
  console.log("정규화:", normalizeLawName(names[0]!));

  const main = (await searchLawFromAPI(names[0]!, searchRaw)) as LawMatch;
  const related = await findRelatedLaws(main, searchRaw);
  console.log(buildLawLinksOutput(main, related));
}

demo().catch(console.error);
