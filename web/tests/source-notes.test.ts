import assert from "node:assert/strict";
import test from "node:test";

import {
  pdfPageFromLocator,
  safeSourceNoteError,
  sourceNoteDisplayState,
  shouldPollSourceNoteWorkspaces,
  type SourceNoteWorkspace,
} from "../src/lib/source-notes.ts";
import {
  sourceKind,
  supportsSourceNote,
} from "../src/components/personal-brain/api.ts";

test("PDF Evidence locator resolves only a positive page", () => {
  assert.equal(pdfPageFromLocator({ kind: "pdf_page", page_number: 3 }), 3);
  assert.equal(pdfPageFromLocator({ kind: "pdf_page", page_number: 0 }), null);
  assert.equal(pdfPageFromLocator({ kind: "video", start_seconds: 3 }), null);
  assert.equal(pdfPageFromLocator(null), null);
});

test("Source Note errors expose only stable allowlisted messages", () => {
  assert.match(safeSourceNoteError("unsupported_claim") ?? "", /原文支持/);
  assert.match(safeSourceNoteError("invalid_evidence_id") ?? "", /不存在/);
  assert.match(safeSourceNoteError("quote_mismatch") ?? "", /原文不符/);
  assert.match(safeSourceNoteError("duplicate_evidence_id") ?? "", /重複引用/);
  assert.equal(
    safeSourceNoteError("provider-secret-detail"),
    "Source Note 產生失敗，請稍後再試。",
  );
  assert.equal(safeSourceNoteError(null), null);
});

test("Source Note display state distinguishes generation, completion, and retry", () => {
  const workspace = (status: string, note: SourceNoteWorkspace["note"] = null) => ({
    asset: { id: "asset", status: "ready", activeRevisionId: "revision" },
    job: { id: "job", status, errorCode: status === "failed" ? "quote_mismatch" : null },
    note,
  });
  const note = {
    id: "note", title: "Title", body: "Summary", bodyRevision: 1,
    updatedAt: "2026-10-22T00:00:00Z", modelProvider: "openai", modelName: "sol",
    promptVersion: "source-note-v1", revisionId: "revision", pdfUrl: null,
    sourceUrl: null, topics: [], manualTopicOverride: false, claims: [],
  };

  assert.equal(sourceNoteDisplayState(null), "idle");
  assert.equal(sourceNoteDisplayState(workspace("queued")), "generating");
  assert.equal(sourceNoteDisplayState(workspace("completed")), "generating");
  assert.equal(sourceNoteDisplayState(workspace("completed", note)), "completed");
  assert.equal(sourceNoteDisplayState(workspace("failed")), "failed");
});

test("polls while a source note job can still publish into all knowledge", () => {
  const workspace = (status: string, note: SourceNoteWorkspace["note"] = null) => ({
    asset: { id: "asset", status: "ready", activeRevisionId: "revision" },
    job: { id: "job", status, errorCode: null },
    note,
  });
  const note = {
    id: "note", title: "Title", body: "Summary", bodyRevision: 1,
    updatedAt: "2026-10-22T00:00:00Z", modelProvider: "openai", modelName: "sol",
    promptVersion: "source-note-v1", revisionId: "revision", pdfUrl: null,
    sourceUrl: null, topics: [], manualTopicOverride: false, claims: [],
  };
  assert.equal(shouldPollSourceNoteWorkspaces({ asset: workspace("queued") }), true);
  assert.equal(shouldPollSourceNoteWorkspaces({ asset: workspace("completed") }), true);
  assert.equal(
    shouldPollSourceNoteWorkspaces({ asset: workspace("completed", note) }),
    false,
  );
  assert.equal(shouldPollSourceNoteWorkspaces({ asset: workspace("failed") }), false);
});

test("Instagram image posts use the Instagram Library type and support Source Notes", () => {
  assert.equal(sourceKind("instagram_post"), "IG");
  assert.equal(supportsSourceNote("instagram_post"), true);
  assert.equal(supportsSourceNote("instagram_reel"), true);
});
