import assert from "node:assert/strict";
import test from "node:test";

import {
  referenceSearchSchema,
  referenceSourceSchema,
  sourceKindForCategory,
} from "../src/lib/reference-sources.ts";

test("reference search validates category, filter and bounded pagination", () => {
  const valid = referenceSearchSchema.parse({
    category: "knowledge",
    sourceType: "instagram_reel",
    query: "synthetic",
    limit: "20",
    offset: "40",
  });
  assert.deepEqual(valid, {
    category: "knowledge",
    sourceType: "instagram_reel",
    query: "synthetic",
    limit: 20,
    offset: 40,
  });
  assert.equal(referenceSearchSchema.safeParse({ category: "research", sourceType: "pdf" }).success, false);
  assert.equal(referenceSearchSchema.safeParse({ category: "personal", limit: 51 }).success, false);
  assert.equal(referenceSearchSchema.safeParse({ category: "notes" }).success, false);
});

test("reference result contract preserves category and source provenance", () => {
  const row = referenceSourceSchema.parse({
    id: "11111111-1111-4111-8111-111111111111",
    category: "knowledge",
    title: "Synthetic PDF source note",
    summary: "Summary",
    content: "Content",
    source_type: "pdf",
    source_url: "https://example.test/source.pdf",
    source_asset_id: "22222222-2222-4222-8222-222222222222",
    source_revision_id: "33333333-3333-4333-8333-333333333333",
    research_run_id: null,
    created_at: "2026-01-01T00:00:00.000Z",
    total_count: "2",
  });
  assert.equal(row.source_type, "pdf");
  assert.equal(row.total_count, 2);
  assert.equal(sourceKindForCategory(row.category), "knowledge");
});

test("each reference category maps to its own immutable import source kind", () => {
  assert.equal(sourceKindForCategory("research"), "research");
  assert.equal(sourceKindForCategory("knowledge"), "knowledge");
  assert.equal(sourceKindForCategory("personal"), "personal");
});
