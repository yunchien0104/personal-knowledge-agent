import assert from "node:assert/strict";
import test from "node:test";

import {
  appendImportedText,
  parseNoteImportResponse,
  plainTextToParagraphs,
} from "../src/lib/note-import.ts";

test("import response uses the authoritative server snapshot", () => {
  const result = parseNoteImportResponse({
    data: {
      note_id: "note-1",
      import_id: "import-1",
      operation: "full",
      snapshot_title: "伺服器標題",
      snapshot_text: "伺服器快照",
    },
  });

  assert.equal(result.snapshot_title, "伺服器標題");
  assert.equal(result.snapshot_text, "伺服器快照");
});

test("multi-line imports become valid separate Tiptap paragraphs", () => {
  assert.deepEqual(plainTextToParagraphs("第一段\n\n第二段"), [
    { type: "paragraph", content: [{ type: "text", text: "第一段" }] },
    { type: "paragraph" },
    { type: "paragraph", content: [{ type: "text", text: "第二段" }] },
  ]);
});

test("import appends without replacing existing rich editor nodes", () => {
  const existing = {
    type: "doc",
    content: [{ type: "spreadsheet", attrs: { workbookId: "workbook-1" } }],
  };

  const result = appendImportedText(existing, "匯入內容\n來源");

  assert.equal(result.content?.[0]?.type, "spreadsheet");
  assert.deepEqual(result.content?.slice(1), [
    { type: "paragraph", content: [{ type: "text", text: "匯入內容" }] },
    { type: "paragraph", content: [{ type: "text", text: "來源" }] },
  ]);
});

test("malformed import responses fail instead of inserting stale sidebar data", () => {
  assert.throws(
    () => parseNoteImportResponse({ data: { snapshot_text: "missing fields" } }),
    /回傳格式不完整/,
  );
});
