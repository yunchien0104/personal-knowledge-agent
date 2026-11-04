import assert from "node:assert/strict";
import test from "node:test";

import {
  createNotePdfSnapshot,
  notePdfFilename,
  resolvePdfColor,
  spreadsheetRows,
} from "../src/lib/note-pdf.ts";

test("PDF snapshot preserves unsaved rich-text JSON and citation snapshots", () => {
  const content = {
    type: "doc",
    content: [{
      type: "paragraph",
      content: [{
        type: "text",
        text: "尚未儲存的中文 italic text",
        marks: [
          { type: "italic" },
          { type: "textStyle", attrs: { color: "#2563eb", fontSize: "18px" } },
        ],
      }],
    }],
  };
  const snapshot = createNotePdfSnapshot({
    title: "  匯出測試  ",
    content,
    savingStatus: "conflict",
    exportedAt: "2026-11-04T00:00:00.000Z",
    citations: [{
      id: "citation-1",
      source_label: "研究報告",
      source_url: "https://example.com/source",
      supporting_quote: "引用快照內容",
      validity_status: "valid",
      locator_json: { page: 3, source_type: "research_report" },
      created_at: "2026-11-04T00:00:00.000Z",
    }],
  });

  assert.equal(snapshot.title, "匯出測試");
  assert.equal(snapshot.includesLocalDraft, true);
  assert.equal(snapshot.savingStatus, "conflict");
  assert.deepEqual(snapshot.content, content);
  assert.equal(snapshot.citations[0].locator_json?.page, 3);

  content.content[0].content[0].text = "later mutation";
  assert.equal(snapshot.content.content?.[0].content?.[0].text, "尚未儲存的中文 italic text");
});

test("spreadsheet export renders the same supported formulas as the editor", () => {
  const rows = spreadsheetRows({
    rows: 4,
    cols: 3,
    cells: {
      "0,0": { raw: "項目" },
      "0,1": { raw: "數值" },
      "0,2": { raw: "結果" },
      "1,0": { raw: "甲" },
      "1,1": { raw: "10" },
      "1,2": { raw: "=B2*2" },
      "2,0": { raw: "乙" },
      "2,1": { raw: "15" },
      "2,2": { raw: "=SUM(B2:B3)" },
      "3,0": { raw: "平均" },
      "3,1": { raw: "=AVERAGE(B2:B3)" },
      "3,2": { raw: "=C2+C3" },
    },
  });

  assert.deepEqual(rows, [
    ["項目", "數值", "結果"],
    ["甲", "10", "20"],
    ["乙", "15", "25"],
    ["平均", "12.50", "45"],
  ]);
});

test("PDF filename and editor color tokens are normalized safely", () => {
  assert.equal(notePdfFilename("研究/筆記:2026"), "研究_筆記_2026.pdf");
  assert.equal(notePdfFilename("  "), "未命名筆記.pdf");
  assert.equal(resolvePdfColor("var(--note-color-purple)", "#000000"), "#9333ea");
  assert.equal(resolvePdfColor("url(javascript:bad)", "#000000"), "#000000");
});
