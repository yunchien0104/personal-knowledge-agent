import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const output = new URL("../fixtures/spreadsheet/synthetic-spreadsheet.xlsx", import.meta.url);
const preview = new URL("../fixtures/spreadsheet/preview.png", import.meta.url);

const workbook = Workbook.create();
const sheet = workbook.worksheets.add("Model");
sheet.showGridLines = false;
sheet.getRange("A2:E2").values = [["Synthetic model", null, null, null, null]];
sheet.getRange("A2:E2").format.font = { name: "Arial", size: 14, bold: true, color: "#111827" };
sheet.getRange("A4:E7").values = [
  ["Item", "Q1", "Q2", "Q3", "Total"],
  ["Alpha", 12, 14, 16, null],
  ["Beta", 8, 11, 13, null],
  ["Grand Total", null, null, null, null],
];
sheet.getRange("E5").formulas = [["=SUM(B5:D5)"]];
sheet.getRange("E5:E6").fillDown();
sheet.getRange("B7").formulas = [["=SUM(B5:B6)"]];
sheet.getRange("B7:E7").fillRight();
sheet.getRange("A4:E4").format = {
  fill: "#3730A3",
  font: { name: "Arial", size: 10, bold: true, color: "#FFFFFF" },
  horizontalAlignment: "center",
  verticalAlignment: "center",
};
sheet.getRange("A5:E7").format.font = { name: "Arial", size: 10, color: "#111827" };
sheet.getRange("B5:E7").format.numberFormat = "#,##0";
sheet.getRange("A7:E7").format.font = { name: "Arial", size: 10, bold: true, color: "#111827" };
sheet.getRange("A4:E7").format.borders = { preset: "outside", style: "thin", color: "#CBD5E1" };
sheet.getRange("A4:E7").format.autofitColumns();
sheet.getRange("A:E").format.verticalAlignment = "center";
sheet.freezePanes.freezeRows(4);
workbook.recalculate();

const check = await workbook.inspect({ kind: "table", range: "Model!A2:E7", include: "values,formulas", tableMaxRows: 10, tableMaxCols: 6 });
console.log(check.ndjson);
const errors = await workbook.inspect({ kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!", options: { useRegex: true, maxResults: 50 }, summary: "fixture formula scan" });
console.log(errors.ndjson);

const rendered = await workbook.render({ sheetName: "Model", range: "A1:E8", scale: 2 });
await fs.writeFile(preview, new Uint8Array(await rendered.arrayBuffer()));
const file = await SpreadsheetFile.exportXlsx(workbook);
await file.save(output.pathname);
