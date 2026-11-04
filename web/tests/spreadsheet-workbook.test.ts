import assert from "node:assert/strict";
import test from "node:test";

import {
  cycleFormulaReferenceAtCaret,
  evaluateSpreadsheetCell,
  extractFormulaReferences,
  formatFormulaReference,
  FORMULA_ERRORS,
  insertFormulaReference,
  rewriteFormulaForStructure,
  translateFormula,
} from "../src/lib/spreadsheet-formula";
import {
  addSpreadsheetSheet,
  changeSpreadsheetStructure,
  createSpreadsheetTemplate,
  deleteSpreadsheetSheet,
  fillSpreadsheetRange,
  getActiveSpreadsheetSheet,
  getTimelineLabels,
  normalizeSpreadsheetData,
  renameSpreadsheetSheet,
  setActiveSpreadsheetSheet,
  type SpreadsheetData,
  type SpreadsheetWorkbook,
  updateSpreadsheetCell,
  updateWorkbookTimeline,
} from "../src/lib/spreadsheet-workbook";

function update(
  workbook: SpreadsheetWorkbook,
  sheetId: string,
  r: number,
  c: number,
  raw: string,
) {
  return updateSpreadsheetCell(workbook, { r, c }, raw, sheetId) as SpreadsheetWorkbook;
}

test("legacy single-sheet JSON upgrades without losing cells", () => {
  const legacy: SpreadsheetData = {
    rows: 2,
    cols: 2,
    cells: { "0,0": { raw: "legacy" }, "1,1": { raw: "=A1" } },
  };
  const workbook = normalizeSpreadsheetData(legacy);
  assert.equal(workbook.version, 2);
  assert.equal(workbook.sheets.length, 1);
  assert.equal(workbook.sheets[0].cells["0,0"].raw, "legacy");
  assert.equal(workbook.sheets[0].cells["1,1"].raw, "=A1");
  assert.deepEqual(normalizeSpreadsheetData(workbook), workbook);
});

test("row and column structure edits move data and rewrite local and cross-sheet references", () => {
  let workbook = createSpreadsheetTemplate("blank");
  const mainId = workbook.activeSheetId;
  workbook = renameSpreadsheetSheet(workbook, mainId, "Main");
  workbook = addSpreadsheetSheet(workbook, "Inputs");
  const inputsId = workbook.activeSheetId;
  workbook = update(workbook, inputsId, 0, 0, "10");
  workbook = setActiveSpreadsheetSheet(workbook, mainId);
  workbook = update(workbook, mainId, 0, 0, "='Inputs'!A1*2");

  workbook = changeSpreadsheetStructure(workbook, inputsId, "column", "insert", 0);
  assert.equal(workbook.sheets.find((sheet) => sheet.id === inputsId)?.cells["0,1"].raw, "10");
  assert.equal(workbook.sheets.find((sheet) => sheet.id === mainId)?.cells["0,0"].raw, "='Inputs'!B1*2");
  assert.equal(evaluateSpreadsheetCell(workbook, mainId, { r: 0, c: 0 }), 20);

  workbook = changeSpreadsheetStructure(workbook, inputsId, "column", "delete", 1);
  assert.equal(workbook.sheets.find((sheet) => sheet.id === mainId)?.cells["0,0"].raw, "=#REF!*2");
  assert.equal(evaluateSpreadsheetCell(workbook, mainId, { r: 0, c: 0 }), FORMULA_ERRORS.ref);
});

test("deleting a row contracts range edges and invalidates direct references", () => {
  const change = { axis: "row" as const, index: 2, kind: "delete" as const, sheetName: "Main" };
  assert.equal(rewriteFormulaForStructure("=SUM(A1:A3)", "Main", change), "=SUM(A1:A2)");
  assert.equal(rewriteFormulaForStructure("=SUM(A3:A5)", "Main", change), "=SUM(A3:A4)");
  assert.equal(rewriteFormulaForStructure("=SUM(A1:A5)", "Main", change), "=SUM(A1:A4)");
  assert.equal(rewriteFormulaForStructure("=A3*2", "Main", change), "=#REF!*2");
  assert.equal(rewriteFormulaForStructure("='Other'!A3", "Main", change), "='Other'!A3");
});

test("fill translates relative, absolute, and mixed A1 references in every direction", () => {
  assert.equal(translateFormula("=B2*C2", 1, 0), "=B3*C3");
  assert.equal(translateFormula("=B2*C2", 0, 1), "=C2*D2");
  assert.equal(translateFormula("=B2*C2", -1, 0), "=B1*C1");
  assert.equal(translateFormula("=B2*C2", 0, -1), "=A2*B2");
  assert.equal(translateFormula("=A1+$A$1+A$1+$A1", 1, 1), "=B2+$A$1+B$1+$A2");

  let workbook = createSpreadsheetTemplate("blank");
  const sheetId = workbook.activeSheetId;
  workbook = update(workbook, sheetId, 0, 0, "2026E");
  workbook = fillSpreadsheetRange(
    workbook,
    sheetId,
    { start: { r: 0, c: 0 }, end: { r: 0, c: 0 } },
    { start: { r: 0, c: 0 }, end: { r: 0, c: 2 } },
  );
  const sheet = getActiveSpreadsheetSheet(workbook);
  assert.equal(sheet.cells["0,1"].raw, "2027E");
  assert.equal(sheet.cells["0,2"].raw, "2028E");

  workbook = update(workbook, sheetId, 2, 2, "=B2*$A$1");
  workbook = fillSpreadsheetRange(
    workbook,
    sheetId,
    { start: { r: 2, c: 2 }, end: { r: 2, c: 2 } },
    { start: { r: 1, c: 1 }, end: { r: 3, c: 3 } },
  );
  const filled = getActiveSpreadsheetSheet(workbook);
  assert.equal(filled.cells["3,2"].raw, "=B3*$A$1");
  assert.equal(filled.cells["2,3"].raw, "=C2*$A$1");
  assert.equal(filled.cells["1,2"].raw, "=B1*$A$1");
  assert.equal(filled.cells["2,1"].raw, "=A2*$A$1");

  workbook = update(workbook, sheetId, 5, 0, "2026-12-31");
  workbook = fillSpreadsheetRange(
    workbook,
    sheetId,
    { start: { r: 5, c: 0 }, end: { r: 5, c: 0 } },
    { start: { r: 5, c: 0 }, end: { r: 5, c: 2 } },
  );
  assert.equal(getActiveSpreadsheetSheet(workbook).cells["5,1"].raw, "2027-01-01");

  workbook = update(workbook, sheetId, 7, 0, "A");
  workbook = update(workbook, sheetId, 7, 1, "B");
  workbook = fillSpreadsheetRange(
    workbook,
    sheetId,
    { start: { r: 7, c: 0 }, end: { r: 7, c: 1 } },
    { start: { r: 7, c: 0 }, end: { r: 7, c: 5 } },
  );
  assert.deepEqual(
    [0, 1, 2, 3, 4, 5].map((c) => getActiveSpreadsheetSheet(workbook).cells[`7,${c}`].raw),
    ["A", "B", "A", "B", "A", "B"],
  );
});

test("point-and-click references insert at the caret and preserve sheet quoting", () => {
  let workbook = createSpreadsheetTemplate("blank");
  const sourceSheetId = workbook.activeSheetId;
  workbook = renameSpreadsheetSheet(workbook, sourceSheetId, "Model");
  workbook = addSpreadsheetSheet(workbook, "Income Statement");
  const incomeSheetId = workbook.activeSheetId;

  assert.equal(
    formatFormulaReference(workbook, sourceSheetId, sourceSheetId, { r: 1, c: 1 }),
    "B2",
  );
  assert.equal(
    formatFormulaReference(workbook, sourceSheetId, incomeSheetId, { r: 1, c: 1 }),
    "'Income Statement'!B2",
  );
  assert.equal(
    formatFormulaReference(workbook, sourceSheetId, incomeSheetId, { r: 1, c: 1 }, { r: 9, c: 1 }),
    "'Income Statement'!B2:B10",
  );

  const middle = insertFormulaReference("=SUM()+1", 5, "B2:B10");
  assert.deepEqual(middle, { formula: "=SUM(B2:B10)+1", caretPosition: 11 });
  const product = insertFormulaReference(insertFormulaReference("=", 1, "B2").formula, 3, "*C2");
  assert.equal(product.formula, "=B2*C2");

  const references = extractFormulaReferences(
    workbook,
    sourceSheetId,
    "=B2*'Income Statement'!C3+SUM('Income Statement'!B2:B10)+IF(B2>0,\"A99\",0)",
  );
  assert.deepEqual(references.map(({ sheetId, start, end }) => ({ sheetId, start, end })), [
    { sheetId: sourceSheetId, start: { r: 1, c: 1 }, end: { r: 1, c: 1 } },
    { sheetId: incomeSheetId, start: { r: 2, c: 2 }, end: { r: 2, c: 2 } },
    { sheetId: incomeSheetId, start: { r: 1, c: 1 }, end: { r: 9, c: 1 } },
    { sheetId: sourceSheetId, start: { r: 1, c: 1 }, end: { r: 1, c: 1 } },
  ]);
});

test("F4 cycles relative and absolute formula references without changing Fill translation", () => {
  let formula = "=B2";
  for (const expected of ["=$B$2", "=B$2", "=$B2", "=B2"]) {
    const next = cycleFormulaReferenceAtCaret(formula, formula.length);
    formula = next.formula;
    assert.equal(formula, expected);
  }
  assert.equal(translateFormula("=$B$2+B$2+$B2+B2", 1, 1), "=$B$2+C$2+$B3+C3");
});

test("cell updates preserve custom backgrounds and change formula semantics", () => {
  const workbook = createSpreadsheetTemplate("blank");
  const sheet = getActiveSpreadsheetSheet(workbook);
  sheet.cells["0,0"] = { raw: "10", kind: "input", backgroundColor: "#123456" };
  const formula = updateSpreadsheetCell(workbook, { r: 0, c: 0 }, "=B2", sheet.id) as SpreadsheetWorkbook;
  assert.deepEqual(getActiveSpreadsheetSheet(formula).cells["0,0"], {
    raw: "=B2",
    kind: "formula",
    backgroundColor: "#123456",
  });
  const input = updateSpreadsheetCell(formula, { r: 0, c: 0 }, "12", sheet.id) as SpreadsheetWorkbook;
  assert.deepEqual(getActiveSpreadsheetSheet(input).cells["0,0"], {
    raw: "12",
    kind: "input",
    backgroundColor: "#123456",
  });
});

test("sheet add, rename, switch, and delete preserve or invalidate cross-sheet formulas", () => {
  let workbook = createSpreadsheetTemplate("blank");
  const mainId = workbook.activeSheetId;
  workbook = renameSpreadsheetSheet(workbook, mainId, "Main");
  workbook = addSpreadsheetSheet(workbook, "Inputs");
  const inputsId = workbook.activeSheetId;
  workbook = update(workbook, inputsId, 0, 0, "12");
  workbook = update(workbook, mainId, 0, 0, "='Inputs'!A1");
  workbook = renameSpreadsheetSheet(workbook, inputsId, "Drivers");
  assert.equal(workbook.sheets.find((sheet) => sheet.id === mainId)?.cells["0,0"].raw, "='Drivers'!A1");
  assert.equal(evaluateSpreadsheetCell(workbook, mainId, { r: 0, c: 0 }), 12);
  workbook = deleteSpreadsheetSheet(workbook, inputsId);
  assert.match(workbook.sheets.find((sheet) => sheet.id === mainId)?.cells["0,0"].raw ?? "", /#REF!/);
  assert.equal(evaluateSpreadsheetCell(workbook, mainId, { r: 0, c: 0 }), FORMULA_ERRORS.ref);
});

test("safe formula engine evaluates aggregation, errors, cycles, NPV, IRR, XNPV, and XIRR", () => {
  let workbook = createSpreadsheetTemplate("blank");
  const sheetId = workbook.activeSheetId;
  ["-100", "60", "60"].forEach((raw, row) => { workbook = update(workbook, sheetId, row, 0, raw); });
  ["2025-01-01", "2026-01-01", "2027-01-01"].forEach((raw, row) => { workbook = update(workbook, sheetId, row, 1, raw); });
  workbook = update(workbook, sheetId, 0, 2, "=SUM(A1:A3)");
  workbook = update(workbook, sheetId, 1, 2, "=NPV(10%,A2:A3)");
  workbook = update(workbook, sheetId, 2, 2, "=IRR(A1:A3)");
  workbook = update(workbook, sheetId, 3, 2, "=XNPV(10%,A1:A3,B1:B3)");
  workbook = update(workbook, sheetId, 4, 2, "=XIRR(A1:A3,B1:B3,10%)");
  workbook = update(workbook, sheetId, 5, 2, "=1/0");
  workbook = update(workbook, sheetId, 6, 2, "=C7");

  assert.equal(evaluateSpreadsheetCell(workbook, sheetId, { r: 0, c: 2 }), 20);
  assert.ok(Math.abs(Number(evaluateSpreadsheetCell(workbook, sheetId, { r: 1, c: 2 })) - 104.132231) < 1e-5);
  assert.ok(Math.abs(Number(evaluateSpreadsheetCell(workbook, sheetId, { r: 2, c: 2 })) - 0.130662) < 1e-5);
  assert.ok(Math.abs(Number(evaluateSpreadsheetCell(workbook, sheetId, { r: 3, c: 2 })) - 4.132231) < 1e-4);
  assert.ok(Math.abs(Number(evaluateSpreadsheetCell(workbook, sheetId, { r: 4, c: 2 })) - 0.130662) < 1e-4);
  assert.equal(evaluateSpreadsheetCell(workbook, sheetId, { r: 5, c: 2 }), FORMULA_ERRORS.div0);
  assert.equal(evaluateSpreadsheetCell(workbook, sheetId, { r: 6, c: 2 }), FORMULA_ERRORS.cycle);
});

test("financial templates create shared timelines and traceable workbook formulas", () => {
  const threeStatement = createSpreadsheetTemplate("three_statement");
  assert.deepEqual(threeStatement.sheets.map((sheet) => sheet.name), [
    "Income Statement",
    "Balance Sheet",
    "Cash Flow Statement",
  ]);
  assert.deepEqual(getTimelineLabels(threeStatement.metadata!.timeline!), [
    "2023A", "2024A", "2025A", "2026E", "2027E", "2028E", "2029E", "2030E",
  ]);
  const changedTimeline = updateWorkbookTimeline(threeStatement, 2026);
  assert.equal(changedTimeline.sheets[0].cells["0,1"].raw, "2024A");
  assert.equal(changedTimeline.sheets[1].cells["0,8"].raw, "2031E");
  assert.match(changedTimeline.sheets[2].cells["1,4"].raw, /Income Statement/);

  let statements = threeStatement;
  const income = statements.sheets.find((sheet) => sheet.name === "Income Statement")!;
  const balance = statements.sheets.find((sheet) => sheet.name === "Balance Sheet")!;
  const cashFlow = statements.sheets.find((sheet) => sheet.name === "Cash Flow Statement")!;
  [[1, "1000"], [2, "600"], [4, "200"], [6, "20"], [7, "45"]]
    .forEach(([row, raw]) => { statements = update(statements, income.id, Number(row), 1, String(raw)); });
  [[1, "100"], [2, "50"], [3, "25"], [4, "325"], [6, "50"], [7, "100"], [9, "350"]]
    .forEach(([row, raw]) => { statements = update(statements, balance.id, Number(row), 1, String(raw)); });
  assert.equal(evaluateSpreadsheetCell(statements, income.id, { r: 8, c: 1 }), 135);
  assert.equal(evaluateSpreadsheetCell(statements, cashFlow.id, { r: 1, c: 1 }), 135);
  assert.equal(evaluateSpreadsheetCell(statements, balance.id, { r: 11, c: 1 }), 0);

  const dcf = createSpreadsheetTemplate("dcf");
  assert.deepEqual(dcf.sheets.map((sheet) => sheet.name), [
    "Assumptions",
    "Financial Forecast",
    "WACC",
    "DCF Valuation",
    "Comparables",
    "Sensitivity",
  ]);
  const wacc = dcf.sheets.find((sheet) => sheet.name === "WACC")!;
  const assumptions = dcf.sheets.find((sheet) => sheet.name === "Assumptions")!;
  let modeled = dcf;
  const inputs: Array<[number, number, string]> = [
    [10, 1, "0.04"], [11, 1, "1.2"], [12, 1, "0.05"], [13, 1, "0.06"],
    [3, 1, "0.25"], [14, 1, "800"], [15, 1, "200"],
  ];
  inputs.forEach(([r, c, raw]) => { modeled = update(modeled, assumptions.id, r, c, raw); });
  assert.ok(Math.abs(Number(evaluateSpreadsheetCell(modeled, wacc.id, { r: 10, c: 1 })) - 0.089) < 1e-9);

  const changedDcfTimeline = updateWorkbookTimeline(modeled, 2026);
  assert.equal(changedDcfTimeline.sheets.find((sheet) => sheet.name === "Assumptions")!.cells["0,1"].raw, "2027E");
});

test("DCF valuation, comparable multiples, and sensitivity produce auditable numeric outputs", () => {
  let workbook = createSpreadsheetTemplate("dcf");
  const assumptions = workbook.sheets.find((sheet) => sheet.name === "Assumptions")!;
  const wacc = workbook.sheets.find((sheet) => sheet.name === "WACC")!;
  const valuation = workbook.sheets.find((sheet) => sheet.name === "DCF Valuation")!;
  const comparables = workbook.sheets.find((sheet) => sheet.name === "Comparables")!;
  const sensitivity = workbook.sheets.find((sheet) => sheet.name === "Sensitivity")!;

  const assumptionInputs: Array<[number, string]> = [
    [3, "0.25"], [7, "0.02"], [8, "10"], [9, "100"], [10, "0.04"],
    [11, "1.2"], [12, "0.05"], [13, "0.06"], [14, "800"], [15, "200"],
  ];
  assumptionInputs.forEach(([row, raw]) => { workbook = update(workbook, assumptions.id, row, 1, raw); });
  for (let col = 1; col <= 5; col++) workbook = update(workbook, valuation.id, 1, col, "100");

  const calculatedWacc = Number(evaluateSpreadsheetCell(workbook, wacc.id, { r: 10, c: 1 }));
  const expectedEnterpriseValue = [1, 2, 3, 4, 5]
    .reduce((total, period) => total + 100 / (1 + calculatedWacc) ** period, 0)
    + (100 * 1.02 / (calculatedWacc - 0.02)) / (1 + calculatedWacc) ** 5;
  assert.ok(Math.abs(Number(evaluateSpreadsheetCell(workbook, valuation.id, { r: 6, c: 1 })) - expectedEnterpriseValue) < 1e-8);
  assert.ok(Math.abs(Number(evaluateSpreadsheetCell(workbook, valuation.id, { r: 8, c: 1 })) - (expectedEnterpriseValue - 100)) < 1e-8);
  assert.ok(Math.abs(Number(evaluateSpreadsheetCell(workbook, valuation.id, { r: 10, c: 1 })) - (expectedEnterpriseValue - 100) / 10) < 1e-8);

  for (let row = 1; row <= 8; row++) {
    [[0, `Example ${row}`], [1, "10"], [2, "100"], [4, "200"], [6, "300"], [7, "100"], [8, "50"]]
      .forEach(([col, raw]) => { workbook = update(workbook, comparables.id, row, Number(col), String(raw)); });
  }
  assert.equal(evaluateSpreadsheetCell(workbook, comparables.id, { r: 1, c: 3 }), 1000);
  assert.equal(evaluateSpreadsheetCell(workbook, comparables.id, { r: 1, c: 5 }), 1200);
  assert.equal(evaluateSpreadsheetCell(workbook, comparables.id, { r: 1, c: 9 }), 20);
  assert.equal(evaluateSpreadsheetCell(workbook, comparables.id, { r: 1, c: 10 }), 12);
  assert.equal(evaluateSpreadsheetCell(workbook, comparables.id, { r: 1, c: 11 }), 4);
  [[3, "300"], [5, "100"], [7, "50"], [9, "200"], [11, "100"]]
    .forEach(([col, raw]) => { workbook = update(workbook, comparables.id, 15, Number(col), String(raw)); });
  assert.equal(evaluateSpreadsheetCell(workbook, comparables.id, { r: 16, c: 1 }), 1000);
  assert.equal(evaluateSpreadsheetCell(workbook, comparables.id, { r: 16, c: 2 }), 10);
  assert.equal(evaluateSpreadsheetCell(workbook, comparables.id, { r: 17, c: 1 }), 1200);
  assert.equal(evaluateSpreadsheetCell(workbook, comparables.id, { r: 17, c: 2 }), 1000);
  assert.equal(evaluateSpreadsheetCell(workbook, comparables.id, { r: 17, c: 3 }), 10);

  assert.equal(typeof evaluateSpreadsheetCell(workbook, sensitivity.id, { r: 1, c: 1 }), "number");
  workbook = update(workbook, sensitivity.id, 1, 0, "0.01");
  workbook = update(workbook, sensitivity.id, 0, 1, "0.02");
  assert.equal(evaluateSpreadsheetCell(workbook, sensitivity.id, { r: 1, c: 1 }), FORMULA_ERRORS.value);
});

test("market sizing template calculates TAM, SAM, and SOM from editable assumptions", () => {
  let workbook = createSpreadsheetTemplate("market_sizing");
  const assumptions = workbook.sheets.find((sheet) => sheet.name === "Assumptions")!;
  const sizing = workbook.sheets.find((sheet) => sheet.name === "TAM SAM SOM")!;
  const values = ["1000000", "0.2", "0.1", "4", "50", "0.5", "0.05"];
  values.forEach((raw, index) => { workbook = update(workbook, assumptions.id, index + 1, 1, raw); });
  assert.equal(evaluateSpreadsheetCell(workbook, sizing.id, { r: 1, c: 2 }), 40_000_000);
  assert.equal(evaluateSpreadsheetCell(workbook, sizing.id, { r: 2, c: 2 }), 20_000_000);
  assert.equal(evaluateSpreadsheetCell(workbook, sizing.id, { r: 3, c: 2 }), 1_000_000);
  const topDown = workbook.sheets.find((sheet) => sheet.name === "Top-down Estimation")!;
  const bottomUp = workbook.sheets.find((sheet) => sheet.name === "Bottom-up Estimation")!;
  const scenarios = workbook.sheets.find((sheet) => sheet.name === "Scenario Analysis")!;
  assert.equal(evaluateSpreadsheetCell(workbook, topDown.id, { r: 4, c: 2 }), 40_000_000);
  assert.equal(evaluateSpreadsheetCell(workbook, topDown.id, { r: 5, c: 2 }), 20_000_000);
  assert.equal(evaluateSpreadsheetCell(workbook, bottomUp.id, { r: 4, c: 2 }), 4_000_000);
  assert.equal(evaluateSpreadsheetCell(workbook, bottomUp.id, { r: 5, c: 2 }), 200_000);
  assert.equal(evaluateSpreadsheetCell(workbook, scenarios.id, { r: 2, c: 3 }), 1_000_000);
  assert.ok(Number(evaluateSpreadsheetCell(workbook, scenarios.id, { r: 1, c: 3 })) < 1_000_000);
  assert.ok(Number(evaluateSpreadsheetCell(workbook, scenarios.id, { r: 3, c: 3 })) > 1_000_000);
});
