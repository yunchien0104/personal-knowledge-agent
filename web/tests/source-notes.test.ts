import assert from "node:assert/strict";
import test from "node:test";

import { pdfPageFromLocator, safeSourceNoteError } from "../src/lib/source-notes.ts";

test("PDF Evidence locator resolves only a positive page", () => {
  assert.equal(pdfPageFromLocator({ kind: "pdf_page", page_number: 3 }), 3);
  assert.equal(pdfPageFromLocator({ kind: "pdf_page", page_number: 0 }), null);
  assert.equal(pdfPageFromLocator({ kind: "video", start_seconds: 3 }), null);
  assert.equal(pdfPageFromLocator(null), null);
});

test("Source Note errors expose only stable allowlisted messages", () => {
  assert.match(safeSourceNoteError("unsupported_claim") ?? "", /原文支持/);
  assert.equal(
    safeSourceNoteError("provider-secret-detail"),
    "Source Note 產生失敗，請稍後再試。",
  );
  assert.equal(safeSourceNoteError(null), null);
});
