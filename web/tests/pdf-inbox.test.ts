import assert from "node:assert/strict";
import test from "node:test";

import { deriveIngestionStatus, safeIngestionError } from "../src/lib/pdf-inbox-status.ts";

test("Inbox maps queue and Asset states to the four product states", () => {
  assert.equal(deriveIngestionStatus("pending", "queued"), "queued");
  assert.equal(deriveIngestionStatus("pending", "retry_wait"), "queued");
  assert.equal(deriveIngestionStatus("processing", "running"), "processing");
  assert.equal(deriveIngestionStatus("ready", "completed"), "completed");
  assert.equal(deriveIngestionStatus("unsupported", "failed"), "failed");
});

test("Inbox exposes allowlisted messages rather than backend error details", () => {
  assert.equal(safeIngestionError("scanned_pdf"), "掃描版 PDF 目前不支援 OCR。");
  assert.equal(safeIngestionError("unknown_internal_detail"), "PDF 處理失敗，請稍後再試。");
  assert.equal(safeIngestionError(null), null);
});
