import assert from "node:assert/strict";
import test from "node:test";

import {
  citationPdfPage,
  knowledgeResultTitle,
  knowledgeSourceKind,
  originLabel,
  parseRetrievalInput,
} from "../src/lib/retrieval.ts";


test("retrieval input applies bounded defaults and rejects unsafe ranges", () => {
  const valid = parseRetrievalInput({ q: "  revenue growth  " });
  assert.equal(valid.success, true);
  if (valid.success) {
    assert.deepEqual(valid.data, { q: "revenue growth", limit: 20, offset: 0 });
  }
  assert.equal(parseRetrievalInput({ q: "x" }).success, false);
  assert.equal(parseRetrievalInput({ q: "revenue", limit: 51 }).success, false);
  assert.equal(parseRetrievalInput({ q: "revenue", offset: 1001 }).success, false);
});

test("retrieval labels generated content separately from original Evidence", () => {
  assert.equal(originLabel("original_source"), "原始來源");
  assert.equal(originLabel("generated_note"), "AI 產生筆記");
  assert.equal(originLabel("generated_claim"), "AI 產生 Claim");
});

test("citation locator exposes only a valid positive PDF page", () => {
  const base = {
    assetId: "asset",
    revisionId: "revision",
    evidenceId: "evidence",
    validityStatus: "valid",
  };
  assert.equal(citationPdfPage({ ...base, locator: { page_number: 7 } }), 7);
  assert.equal(citationPdfPage({ ...base, locator: { page_number: 0 } }), null);
  assert.equal(citationPdfPage({ ...base, locator: { start_seconds: 5 } }), null);
});

test("retrieval display identifies Instagram Reel and Threads evidence from locators", () => {
  const citation = (kind: string) => ({
    assetId: "asset",
    revisionId: "revision",
    evidenceId: "evidence",
    validityStatus: "valid",
    locator: { kind },
  });
  const reel = {
    resultType: "evidence" as const,
    title: "PDF source page ?",
    citations: [citation("video_segment")],
  };
  assert.equal(knowledgeSourceKind(reel), "IG");
  assert.equal(knowledgeResultTitle(reel), "Instagram Reel 來源片段");

  const threads = {
    resultType: "evidence" as const,
    title: "PDF source page ?",
    citations: [citation("threads_post")],
  };
  assert.equal(knowledgeSourceKind(threads), "TH");
  assert.equal(knowledgeResultTitle(threads), "Threads 來源片段");
});
