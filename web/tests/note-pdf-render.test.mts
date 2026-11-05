import assert from "node:assert/strict";
import { writeFile } from "node:fs/promises";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

import { generateNotePdfBlob } from "../src/components/notes/NotePdfDocument";
import { createNotePdfSnapshot } from "../src/lib/note-pdf";

const testDirectory = dirname(fileURLToPath(import.meta.url));
const fontDirectory = resolve(testDirectory, "../public/fonts/pdf");

test("generates a real A4 PDF from rich text, table, spreadsheet, and citations", async () => {
  const repeatedParagraphs = Array.from({ length: 38 }, (_, index) => ({
    type: "paragraph",
    content: [{ type: "text", text: `第 ${index + 1} 段繁體中文內容，用來驗證多頁分頁與可選取文字。` }],
  }));
  const snapshot = createNotePdfSnapshot({
    title: "Personal Brain Notes PDF 匯出驗證",
    savingStatus: "failed",
    citations: [{
      id: "citation-pdf-1",
      source_label: "Phase 4 研究報告",
      source_url: "https://example.com/research",
      supporting_quote: "這是保留於 Personal Note 的 Citation Snapshot。",
      validity_status: "valid",
      locator_json: { page: 12, source_type: "research_report" },
      created_at: "2026-11-04T00:00:00.000Z",
    }],
    content: {
      type: "doc",
      content: [
        { type: "heading", attrs: { level: 1 }, content: [{ type: "text", text: "H1 研究摘要" }] },
        {
          type: "paragraph",
          content: [
            { type: "text", text: "粗體", marks: [{ type: "bold" }] },
            { type: "text", text: "、中文斜體", marks: [{ type: "italic" }] },
            { type: "text", text: "、彩色螢光筆", marks: [
              { type: "textStyle", attrs: { color: "#2563eb", fontFamily: "\"Noto Serif TC\", serif", fontSize: "18px" } },
              { type: "highlight", attrs: { color: "rgba(250, 204, 21, 0.38)" } },
              { type: "underline" },
            ] },
            { type: "text", text: "、等寬 ABC 123", marks: [
              { type: "textStyle", attrs: { fontFamily: "var(--font-mono)" } },
            ] },
          ],
        },
        { type: "bulletList", content: [
          { type: "listItem", content: [{ type: "paragraph", content: [{ type: "text", text: "清單項目一" }] }] },
          { type: "listItem", content: [{ type: "paragraph", content: [{ type: "text", text: "清單項目二" }] }] },
        ] },
        { type: "table", content: [
          { type: "tableRow", content: [
            { type: "tableHeader", content: [{ type: "paragraph", content: [{ type: "text", text: "欄位" }] }] },
            { type: "tableHeader", content: [{ type: "paragraph", content: [{ type: "text", text: "說明" }] }] },
          ] },
          { type: "tableRow", content: [
            { type: "tableCell", content: [{ type: "paragraph", content: [{ type: "text", text: "A" }] }] },
            { type: "tableCell", content: [{ type: "paragraph", content: [{ type: "text", text: "表格列不跨頁切斷" }] }] },
          ] },
        ] },
        {
          type: "spreadsheetBlock",
          attrs: { data: { rows: 2, cols: 3, cells: {
            "0,0": { raw: "項目" }, "0,1": { raw: "值" }, "0,2": { raw: "合計" },
            "1,0": { raw: "測試" }, "1,1": { raw: "21" }, "1,2": { raw: "=B2*2" },
          } } },
        },
        ...repeatedParagraphs,
      ],
    },
  });

  const blob = await generateNotePdfBlob(snapshot, {
    sansRegular: resolve(fontDirectory, "NotoSansTC-Regular.otf"),
    sansBold: resolve(fontDirectory, "NotoSansTC-Bold.otf"),
    serifRegular: resolve(fontDirectory, "NotoSerifTC-Regular.otf"),
    serifBold: resolve(fontDirectory, "NotoSerifTC-Bold.otf"),
  });
  const buffer = Buffer.from(await blob.arrayBuffer());

  assert.equal(blob.type, "application/pdf");
  assert.equal(buffer.subarray(0, 5).toString(), "%PDF-");
  assert.ok(buffer.byteLength > 10_000, `expected embedded-font PDF, got ${buffer.byteLength} bytes`);

  if (process.env.NOTE_PDF_FIXTURE_OUTPUT) {
    await writeFile(process.env.NOTE_PDF_FIXTURE_OUTPUT, buffer);
  }
});
