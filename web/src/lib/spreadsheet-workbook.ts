import {
  deleteSheetReferences,
  renameSheetReferences,
  rewriteFormulaForStructure,
  translateFormula,
} from "./spreadsheet-formula.ts";

export interface SpreadsheetCellData {
  raw: string;
  kind?: "input" | "formula" | "label" | "timeline" | "not_applicable";
  backgroundColor?: string;
}

export interface SpreadsheetCellPosition {
  r: number;
  c: number;
}

export interface SpreadsheetRange {
  start: SpreadsheetCellPosition;
  end: SpreadsheetCellPosition;
}

export type SpreadsheetTemplateId =
  | "blank"
  | "three_statement"
  | "dcf"
  | "sensitivity"
  | "market_sizing";

export interface FinancialTimeline {
  lastHistoricalYear: number;
  historicalYears: number;
  forecastYears: number;
}

export interface SpreadsheetMetadata {
  templateId?: SpreadsheetTemplateId;
  templateLabel?: string;
  timeline?: FinancialTimeline;
}

export interface LegacySpreadsheetData {
  rows: number;
  cols: number;
  cells: Record<string, SpreadsheetCellData>;
  metadata?: SpreadsheetMetadata & { unsupportedFeatures?: string[] };
}

export interface SpreadsheetSheet {
  id: string;
  name: string;
  rows: number;
  cols: number;
  cells: Record<string, SpreadsheetCellData>;
}

export interface SpreadsheetWorkbook {
  version: 2;
  workbookId: string;
  activeSheetId: string;
  sheets: SpreadsheetSheet[];
  metadata?: SpreadsheetMetadata;
}

export type SpreadsheetData = LegacySpreadsheetData | SpreadsheetWorkbook;

export interface SpreadsheetTemplateDefinition {
  id: SpreadsheetTemplateId;
  label: string;
  description: string;
}

export const DEFAULT_FINANCIAL_TIMELINE: FinancialTimeline = {
  lastHistoricalYear: 2025,
  historicalYears: 3,
  forecastYears: 5,
};

export const SPREADSHEET_TEMPLATES: readonly SpreadsheetTemplateDefinition[] = [
  { id: "blank", label: "Blank Workbook", description: "空白的多工作表 Workbook" },
  { id: "three_statement", label: "Three-Statement Financial Model", description: "損益、資產負債與現金流量三表" },
  { id: "dcf", label: "DCF Valuation", description: "六工作表 DCF、WACC、可比公司與敏感度模型" },
  { id: "sensitivity", label: "Sensitivity Analysis", description: "可設定列、欄變數與基準輸出的敏感度模型" },
  { id: "market_sizing", label: "Market Sizing", description: "Top-down、Bottom-up 與情境分析" },
];

function makeId(prefix: string) {
  return `${prefix}-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
}

function cellKey(r: number, c: number) {
  return `${r},${c}`;
}

function cloneWorkbook(workbook: SpreadsheetWorkbook): SpreadsheetWorkbook {
  return {
    ...workbook,
    sheets: workbook.sheets.map((sheet) => ({
      ...sheet,
      cells: Object.fromEntries(Object.entries(sheet.cells).map(([key, cell]) => [key, { ...cell }])),
    })),
    metadata: workbook.metadata
      ? { ...workbook.metadata, timeline: workbook.metadata.timeline ? { ...workbook.metadata.timeline } : undefined }
      : undefined,
  };
}

export function isSpreadsheetWorkbook(data: SpreadsheetData): data is SpreadsheetWorkbook {
  return "version" in data && data.version === 2 && Array.isArray(data.sheets);
}

export function normalizeSpreadsheetData(data: SpreadsheetData): SpreadsheetWorkbook {
  if (isSpreadsheetWorkbook(data)) return data;
  const sheetId = "legacy-sheet-1";
  return {
    version: 2,
    workbookId: "legacy-workbook",
    activeSheetId: sheetId,
    sheets: [{ id: sheetId, name: "Sheet 1", rows: data.rows, cols: data.cols, cells: { ...data.cells } }],
    metadata: data.metadata
      ? { templateId: data.metadata.templateId, templateLabel: data.metadata.templateLabel }
      : { templateLabel: "Imported Spreadsheet" },
  };
}

export function getActiveSpreadsheetSheet(data: SpreadsheetData) {
  const workbook = normalizeSpreadsheetData(data);
  return workbook.sheets.find((sheet) => sheet.id === workbook.activeSheetId) ?? workbook.sheets[0];
}

export function getTimelineLabels(timeline: FinancialTimeline) {
  const historical = Array.from(
    { length: timeline.historicalYears },
    (_, index) => `${timeline.lastHistoricalYear - timeline.historicalYears + index + 1}A`,
  );
  const forecast = Array.from(
    { length: timeline.forecastYears },
    (_, index) => `${timeline.lastHistoricalYear + index + 1}E`,
  );
  return [...historical, ...forecast];
}

function createSheet(name: string, rows: number, cols: number): SpreadsheetSheet {
  return { id: makeId("sheet"), name, rows, cols, cells: {} };
}

function setCell(
  sheet: SpreadsheetSheet,
  r: number,
  c: number,
  raw: string,
  kind?: SpreadsheetCellData["kind"],
) {
  sheet.cells[cellKey(r, c)] = { raw, kind };
}

function applyTimelineHeader(sheet: SpreadsheetSheet, timeline: FinancialTimeline) {
  setCell(sheet, 0, 0, "Line Item", "label");
  getTimelineLabels(timeline).forEach((label, index) => setCell(sheet, 0, index + 1, label, "timeline"));
}

function createWorkbook(
  templateId: SpreadsheetTemplateId,
  sheets: SpreadsheetSheet[],
  timeline?: FinancialTimeline,
): SpreadsheetWorkbook {
  const definition = SPREADSHEET_TEMPLATES.find((template) => template.id === templateId);
  return {
    version: 2,
    workbookId: makeId("workbook"),
    activeSheetId: sheets[0].id,
    sheets,
    metadata: {
      templateId,
      templateLabel: definition?.label,
      timeline: timeline ? { ...timeline } : undefined,
    },
  };
}

function createThreeStatementWorkbook() {
  const timeline = { ...DEFAULT_FINANCIAL_TIMELINE };
  const labels = getTimelineLabels(timeline);
  const cols = labels.length + 1;
  const income = createSheet("Income Statement", 9, cols);
  const balance = createSheet("Balance Sheet", 12, cols);
  const cashFlow = createSheet("Cash Flow Statement", 9, cols);
  [income, balance, cashFlow].forEach((sheet) => applyTimelineHeader(sheet, timeline));

  ["Revenue", "COGS", "Gross Profit", "Operating Expenses", "EBIT", "Interest Expense", "Taxes", "Net Income"]
    .forEach((label, index) => setCell(income, index + 1, 0, label, "label"));
  ["Cash", "Accounts Receivable", "Inventory", "PP&E", "Total Assets", "Accounts Payable", "Debt", "Total Liabilities", "Shareholders' Equity", "Retained Earnings", "Balance Check"]
    .forEach((label, index) => setCell(balance, index + 1, 0, label, "label"));
  ["Net Income", "D&A", "Changes in Working Capital", "Operating Cash Flow", "Capital Expenditures", "Investing Cash Flow", "Financing Cash Flow", "Net Change in Cash"]
    .forEach((label, index) => setCell(cashFlow, index + 1, 0, label, "label"));

  for (let col = 1; col < cols; col++) {
    const column = String.fromCharCode(65 + col);
    [1, 2, 4, 6, 7].forEach((row) => setCell(income, row, col, "", "input"));
    setCell(income, 3, col, `=${column}2-${column}3`, "formula");
    setCell(income, 5, col, `=${column}4-${column}5`, "formula");
    setCell(income, 8, col, `=${column}6-${column}7-${column}8`, "formula");

    [1, 2, 3, 4, 6, 7].forEach((row) => setCell(balance, row, col, "", "input"));
    setCell(balance, 5, col, `=SUM(${column}2:${column}5)`, "formula");
    setCell(balance, 8, col, `=${column}7+${column}8`, "formula");
    setCell(balance, 9, col, "", "input");
    setCell(balance, 10, col, col === 1 ? "" : `=${String.fromCharCode(64 + col)}11+'Income Statement'!${column}9`, col === 1 ? "input" : "formula");
    setCell(balance, 11, col, `=${column}6-${column}9-${column}10`, "formula");

    setCell(cashFlow, 1, col, `='Income Statement'!${column}9`, "formula");
    [2, 3, 5, 7].forEach((row) => setCell(cashFlow, row, col, "", "input"));
    setCell(cashFlow, 4, col, `=SUM(${column}2:${column}4)`, "formula");
    setCell(cashFlow, 6, col, `=-${column}6`, "formula");
    setCell(cashFlow, 8, col, `=${column}5+${column}7+${column}8`, "formula");
    if (col >= timeline.historicalYears + 1) {
      const previousColumn = String.fromCharCode(64 + col);
      setCell(balance, 1, col, `=${previousColumn}2+'Cash Flow Statement'!${column}9`, "formula");
    }
  }
  return createWorkbook("three_statement", [income, balance, cashFlow], timeline);
}

function createDcfWorkbook() {
  const timeline = { ...DEFAULT_FINANCIAL_TIMELINE };
  const allYears = getTimelineLabels(timeline);
  const forecastYears = allYears.slice(timeline.historicalYears);
  const assumptions = createSheet("Assumptions", 18, forecastYears.length + 1);
  setCell(assumptions, 0, 0, "Assumption", "label");
  forecastYears.forEach((year, index) => setCell(assumptions, 0, index + 1, year, "timeline"));
  ["Revenue Growth", "Operating Margin", "Tax Rate", "D&A % Revenue", "CapEx % Revenue", "NWC % Revenue"]
    .forEach((label, index) => setCell(assumptions, index + 1, 0, label, "label"));
  ["Terminal Growth Rate", "Shares Outstanding", "Net Debt", "Risk-Free Rate", "Beta", "Equity Risk Premium", "Pre-tax Cost of Debt", "Market Value of Equity", "Market Value of Debt"]
    .forEach((label, index) => setCell(assumptions, index + 7, 0, label, "label"));
  setCell(assumptions, 16, 0, "Valuation Date", "label");
  setCell(assumptions, 17, 0, "Discount Convention", "label");
  setCell(assumptions, 16, 1, "", "input");
  setCell(assumptions, 17, 1, "Year-end periods (1-5)", "label");
  for (let col = 1; col <= forecastYears.length; col++) {
    for (let row = 1; row <= 6; row++) setCell(assumptions, row, col, "", "input");
  }
  for (let row = 7; row <= 15; row++) setCell(assumptions, row, 1, "", "input");

  const forecast = createSheet("Financial Forecast", 9, allYears.length + 1);
  applyTimelineHeader(forecast, timeline);
  ["Revenue", "EBIT", "NOPAT", "D&A", "CapEx", "Change in NWC", "FCFF", "Discount Period"]
    .forEach((label, index) => setCell(forecast, index + 1, 0, label, "label"));
  for (let col = 1; col <= allYears.length; col++) {
    const column = String.fromCharCode(65 + col);
    if (col <= timeline.historicalYears) {
      for (let row = 1; row <= 7; row++) setCell(forecast, row, col, "", "input");
      setCell(forecast, 8, col, "0", "input");
    } else {
      const assumptionColumn = String.fromCharCode(66 + col - timeline.historicalYears - 1);
      const previousColumn = String.fromCharCode(64 + col);
      setCell(forecast, 1, col, `=${previousColumn}2*(1+'Assumptions'!${assumptionColumn}2)`, "formula");
      setCell(forecast, 2, col, `=${column}2*'Assumptions'!${assumptionColumn}3`, "formula");
      setCell(forecast, 3, col, `=${column}3*(1-'Assumptions'!${assumptionColumn}4)`, "formula");
      setCell(forecast, 4, col, `=${column}2*'Assumptions'!${assumptionColumn}5`, "formula");
      setCell(forecast, 5, col, `=${column}2*'Assumptions'!${assumptionColumn}6`, "formula");
      setCell(forecast, 6, col, `=${column}2*'Assumptions'!${assumptionColumn}7-${previousColumn}2*'Assumptions'!${assumptionColumn}7`, "formula");
      setCell(forecast, 7, col, `=${column}4+${column}5-${column}6-${column}7`, "formula");
      setCell(forecast, 8, col, String(col - timeline.historicalYears), "formula");
    }
  }

  const wacc = createSheet("WACC", 11, 2);
  ["WACC Component", "Risk-Free Rate", "Beta", "Equity Risk Premium", "Cost of Equity", "Pre-tax Cost of Debt", "Effective Tax Rate", "Market Value of Equity", "Market Value of Debt", "Equity Weight", "Calculated WACC"]
    .forEach((label, row) => setCell(wacc, row, 0, label, "label"));
  ["='Assumptions'!B11", "='Assumptions'!B12", "='Assumptions'!B13", "=B2+B3*B4", "='Assumptions'!B14", "='Assumptions'!B4", "='Assumptions'!B15", "='Assumptions'!B16", "=B8/(B8+B9)", "=B5*B10+B6*(1-B7)*(1-B10)"]
    .forEach((formula, index) => setCell(wacc, index + 1, 1, formula, "formula"));

  const valuation = createSheet("DCF Valuation", 11, forecastYears.length + 1);
  setCell(valuation, 0, 0, "DCF Valuation", "label");
  forecastYears.forEach((year, index) => setCell(valuation, 0, index + 1, year, "timeline"));
  ["Forecast FCFF", "Discount Factor", "Present Value of FCFF", "Terminal Value", "PV of Terminal Value", "Enterprise Value", "Net Debt", "Equity Value", "Shares Outstanding", "Implied Share Price"]
    .forEach((label, index) => setCell(valuation, index + 1, 0, label, "label"));
  for (let col = 1; col <= forecastYears.length; col++) {
    const column = String.fromCharCode(65 + col);
    const forecastColumn = String.fromCharCode(65 + col + timeline.historicalYears);
    setCell(valuation, 1, col, `='Financial Forecast'!${forecastColumn}8`, "formula");
    setCell(valuation, 2, col, `=1/(1+'WACC'!B11)^${col}`, "formula");
    setCell(valuation, 3, col, `=${column}2*${column}3`, "formula");
  }
  const finalColumn = String.fromCharCode(65 + forecastYears.length);
  setCell(valuation, 4, 1, `=${finalColumn}2*(1+'Assumptions'!B8)/('WACC'!B11-'Assumptions'!B8)`, "formula");
  setCell(valuation, 5, 1, `=B5*${finalColumn}3`, "formula");
  setCell(valuation, 6, 1, "=SUM(B4:F4)+B6", "formula");
  setCell(valuation, 7, 1, "='Assumptions'!B10", "formula");
  setCell(valuation, 8, 1, "=B7-B8", "formula");
  setCell(valuation, 9, 1, "='Assumptions'!B9", "formula");
  setCell(valuation, 10, 1, "=B9/B10", "formula");

  const comparables = createSheet("Comparables", 19, 12);
  ["Company", "Share Price", "Shares Outstanding", "Market Capitalization", "Net Debt", "Enterprise Value", "Revenue", "EBITDA", "Net Income", "P/E", "EV/EBITDA", "EV/Revenue"]
    .forEach((label, col) => setCell(comparables, 0, col, label, "label"));
  for (let row = 1; row <= 8; row++) {
    for (let col = 0; col <= 8; col++) setCell(comparables, row, col, "", "input");
    const excelRow = row + 1;
    setCell(comparables, row, 3, `=B${excelRow}*C${excelRow}`, "formula");
    setCell(comparables, row, 5, `=D${excelRow}+E${excelRow}`, "formula");
    setCell(comparables, row, 9, `=D${excelRow}/I${excelRow}`, "formula");
    setCell(comparables, row, 10, `=F${excelRow}/H${excelRow}`, "formula");
    setCell(comparables, row, 11, `=F${excelRow}/G${excelRow}`, "formula");
  }
  ["Mean", "Median", "Min", "Max"].forEach((label, index) => setCell(comparables, index + 10, 0, label, "label"));
  for (let col = 9; col <= 11; col++) {
    const column = String.fromCharCode(65 + col);
    setCell(comparables, 10, col, `=AVERAGE(${column}2:${column}9)`, "formula");
    setCell(comparables, 11, col, `=MEDIAN(${column}2:${column}9)`, "formula");
    setCell(comparables, 12, col, `=MIN(${column}2:${column}9)`, "formula");
    setCell(comparables, 13, col, `=MAX(${column}2:${column}9)`, "formula");
  }
  [["Target Inputs", ""], ["Revenue", ""], ["EBITDA", ""], ["Net Income", ""], ["Net Debt", ""], ["Shares", ""]]
    .forEach(([label, value], index) => {
      setCell(comparables, 15, index * 2, label, "label");
      setCell(comparables, 15, index * 2 + 1, value, "input");
    });
  setCell(comparables, 16, 0, "P/E Implied Equity / Share Price", "label");
  setCell(comparables, 16, 1, "=H16*J12", "formula");
  setCell(comparables, 16, 2, "=B17/L16", "formula");
  setCell(comparables, 17, 0, "EV/EBITDA Implied EV / Equity / Share Price", "label");
  setCell(comparables, 17, 1, "=F16*K12", "formula");
  setCell(comparables, 17, 2, "=B18-J16", "formula");
  setCell(comparables, 17, 3, "=C18/L16", "formula");
  setCell(comparables, 18, 0, "EV/Revenue Implied EV / Equity / Share Price", "label");
  setCell(comparables, 18, 1, "=D16*L12", "formula");
  setCell(comparables, 18, 2, "=B19-J16", "formula");
  setCell(comparables, 18, 3, "=C19/L16", "formula");

  const sensitivity = createSheet("Sensitivity", 7, 7);
  setCell(sensitivity, 0, 0, "WACC / Terminal Growth", "label");
  [0.01, 0.015, 0.02, 0.025, 0.03, 0.035].forEach((value, index) => setCell(sensitivity, 0, index + 1, String(value), "input"));
  [0.07, 0.08, 0.09, 0.1, 0.11, 0.12].forEach((value, index) => setCell(sensitivity, index + 1, 0, String(value), "input"));
  for (let row = 1; row <= 6; row++) {
    for (let col = 1; col <= 6; col++) {
      const waccCell = `$A${row + 1}`;
      const growthCell = `${String.fromCharCode(65 + col)}$1`;
      setCell(
        sensitivity,
        row,
        col,
        `=IF(${waccCell}<=${growthCell},"#VALUE!",('DCF Valuation'!F2*(1+${growthCell})/(${waccCell}-${growthCell})/(1+${waccCell})^5+SUM('DCF Valuation'!B4:F4)-'Assumptions'!B10)/'Assumptions'!B9)`,
        "formula",
      );
    }
  }
  return createWorkbook("dcf", [assumptions, forecast, wacc, valuation, comparables, sensitivity], timeline);
}

function createSensitivityWorkbook() {
  const sheet = createSheet("Sensitivity", 10, 10);
  ["Configuration", "Row Variable", "Column Variable", "Base Formula / Output", "Scenario Step Size", "Scenario Range"]
    .forEach((label, row) => setCell(sheet, row, 0, label, "label"));
  for (let row = 1; row <= 5; row++) setCell(sheet, row, 1, "", "input");
  setCell(sheet, 7, 0, "Set the base formula and scenario inputs above; matrix values remain blank until a model formula is provided.", "label");
  return createWorkbook("sensitivity", [sheet]);
}

function createMarketSizingWorkbook() {
  const assumptions = createSheet("Assumptions", 8, 3);
  [["Driver", "Base", "Source / Notes"], ["Population", "", ""], ["Target Segment %", "", ""], ["Penetration %", "", ""], ["Annual Purchase Frequency", "", ""], ["Average Order Value", "", ""], ["Serviceable Market %", "", ""], ["Target Share %", "", ""]]
    .forEach((row, r) => row.forEach((raw, c) => setCell(assumptions, r, c, raw, r === 0 || c === 0 ? "label" : "input")));
  const sizing = createSheet("TAM SAM SOM", 4, 3);
  [["Market", "Formula", "Value"], ["TAM", "Population × Target % × Frequency × AOV", "='Assumptions'!B2*'Assumptions'!B3*'Assumptions'!B5*'Assumptions'!B6"], ["SAM", "TAM × Serviceable %", "=C2*'Assumptions'!B7"], ["SOM", "SAM × Target Share %", "=C3*'Assumptions'!B8"]]
    .forEach((row, r) => row.forEach((raw, c) => setCell(sizing, r, c, raw, raw.startsWith("=") ? "formula" : r === 0 || c < 2 ? "label" : "input")));
  const topDown = createSheet("Top-down Estimation", 6, 3);
  const bottomUp = createSheet("Bottom-up Estimation", 6, 3);
  const drivers = createSheet("Key Drivers", 8, 4);
  const scenarios = createSheet("Scenario Analysis", 6, 5);
  [["Top-down Driver", "Value", "Output"], ["Population", "='Assumptions'!B2", ""], ["Target Segment %", "='Assumptions'!B3", ""], ["Annual Spend", "='Assumptions'!B5*'Assumptions'!B6", ""], ["TAM", "", "=B2*B3*B4"], ["SAM", "", "=C5*'Assumptions'!B7"]]
    .forEach((row, r) => row.forEach((raw, c) => setCell(topDown, r, c, raw, raw.startsWith("=") ? "formula" : r === 0 || c === 0 ? "label" : "input")));
  [["Bottom-up Driver", "Value", "Output"], ["Target Customers", "='Assumptions'!B2*'Assumptions'!B3", ""], ["Penetration %", "='Assumptions'!B4", ""], ["Annual Revenue / Customer", "='Assumptions'!B5*'Assumptions'!B6", ""], ["Addressable Revenue", "", "=B2*B3*B4"], ["SOM", "", "=C5*'Assumptions'!B8"]]
    .forEach((row, r) => row.forEach((raw, c) => setCell(bottomUp, r, c, raw, raw.startsWith("=") ? "formula" : r === 0 || c === 0 ? "label" : "input")));
  [["Driver", "Base", "Bear Multiplier", "Bull Multiplier"], ["Population", "='Assumptions'!B2", "0.9", "1.1"], ["Target Segment %", "='Assumptions'!B3", "0.8", "1.2"], ["Penetration %", "='Assumptions'!B4", "0.75", "1.25"], ["Purchase Frequency", "='Assumptions'!B5", "0.9", "1.1"], ["Average Order Value", "='Assumptions'!B6", "0.9", "1.1"], ["Serviceable %", "='Assumptions'!B7", "0.85", "1.15"], ["Target Share %", "='Assumptions'!B8", "0.75", "1.25"]]
    .forEach((row, r) => row.forEach((raw, c) => setCell(drivers, r, c, raw, raw.startsWith("=") ? "formula" : r === 0 || c === 0 ? "label" : "input")));
  [["Scenario", "TAM", "SAM", "SOM", "Method"], ["Bear", "='TAM SAM SOM'!C2*'Key Drivers'!C2*'Key Drivers'!C3*'Key Drivers'!C5*'Key Drivers'!C6", "=B2*'Assumptions'!B7*'Key Drivers'!C7", "=C2*'Assumptions'!B8*'Key Drivers'!C8", "Driver-based"], ["Base", "='TAM SAM SOM'!C2", "='TAM SAM SOM'!C3", "='TAM SAM SOM'!C4", "Driver-based"], ["Bull", "='TAM SAM SOM'!C2*'Key Drivers'!D2*'Key Drivers'!D3*'Key Drivers'!D5*'Key Drivers'!D6", "=B4*'Assumptions'!B7*'Key Drivers'!D7", "=C4*'Assumptions'!B8*'Key Drivers'!D8", "Driver-based"]]
    .forEach((row, r) => row.forEach((raw, c) => setCell(scenarios, r, c, raw, raw.startsWith("=") ? "formula" : r === 0 || c === 0 ? "label" : "input")));
  return createWorkbook("market_sizing", [assumptions, sizing, topDown, bottomUp, drivers, scenarios]);
}

export function createSpreadsheetTemplate(id: SpreadsheetTemplateId): SpreadsheetWorkbook {
  if (id === "three_statement") return createThreeStatementWorkbook();
  if (id === "dcf") return createDcfWorkbook();
  if (id === "sensitivity") return createSensitivityWorkbook();
  if (id === "market_sizing") return createMarketSizingWorkbook();
  const sheet = createSheet("Sheet 1", 20, 10);
  return createWorkbook("blank", [sheet]);
}

export function updateSpreadsheetCell(
  data: SpreadsheetData,
  cell: SpreadsheetCellPosition,
  raw: string,
  sheetId?: string,
): SpreadsheetData {
  if (!isSpreadsheetWorkbook(data)) {
    const previous = data.cells[cellKey(cell.r, cell.c)];
    return {
      ...data,
      cells: {
        ...data.cells,
        [cellKey(cell.r, cell.c)]: {
          ...previous,
          raw,
          kind: raw.startsWith("=") ? "formula" : previous?.kind ?? "input",
        },
      },
    };
  }
  const workbook = cloneWorkbook(data);
  const sheet = workbook.sheets.find((candidate) => candidate.id === (sheetId ?? workbook.activeSheetId));
  if (!sheet) return data;
  const previous = sheet.cells[cellKey(cell.r, cell.c)];
  sheet.cells[cellKey(cell.r, cell.c)] = {
    ...previous,
    raw,
    kind: raw.startsWith("=") ? "formula" : previous?.kind === "formula" ? "input" : previous?.kind ?? "input",
  };
  return workbook;
}

export function setActiveSpreadsheetSheet(data: SpreadsheetData, sheetId: string) {
  const workbook = cloneWorkbook(normalizeSpreadsheetData(data));
  if (workbook.sheets.some((sheet) => sheet.id === sheetId)) workbook.activeSheetId = sheetId;
  return workbook;
}

export function addSpreadsheetSheet(data: SpreadsheetData, name?: string) {
  const workbook = cloneWorkbook(normalizeSpreadsheetData(data));
  const usedNames = new Set(workbook.sheets.map((sheet) => sheet.name));
  let index = workbook.sheets.length + 1;
  let nextName = name?.trim() || `Sheet ${index}`;
  while (usedNames.has(nextName)) nextName = `Sheet ${++index}`;
  const sheet = createSheet(nextName, 20, 10);
  workbook.sheets.push(sheet);
  workbook.activeSheetId = sheet.id;
  return workbook;
}

export function renameSpreadsheetSheet(data: SpreadsheetData, sheetId: string, name: string) {
  const workbook = cloneWorkbook(normalizeSpreadsheetData(data));
  const sheet = workbook.sheets.find((candidate) => candidate.id === sheetId);
  const nextName = name.trim();
  if (!sheet || !nextName || workbook.sheets.some((candidate) => candidate.id !== sheetId && candidate.name === nextName)) return workbook;
  const oldName = sheet.name;
  sheet.name = nextName;
  workbook.sheets.forEach((candidate) => {
    Object.values(candidate.cells).forEach((cell) => {
      if (cell.raw.startsWith("=")) cell.raw = renameSheetReferences(cell.raw, oldName, nextName);
    });
  });
  return workbook;
}

export function spreadsheetSheetReferenceCount(data: SpreadsheetData, sheetId: string) {
  const workbook = normalizeSpreadsheetData(data);
  const sheet = workbook.sheets.find((candidate) => candidate.id === sheetId);
  if (!sheet) return 0;
  const quoted = `'${sheet.name.replaceAll("'", "''")}'!`;
  return workbook.sheets.reduce((count, candidate) => count + Object.values(candidate.cells).filter((cell) => cell.raw.includes(quoted) || cell.raw.includes(`${sheet.name}!`)).length, 0);
}

export function deleteSpreadsheetSheet(data: SpreadsheetData, sheetId: string) {
  const workbook = cloneWorkbook(normalizeSpreadsheetData(data));
  if (workbook.sheets.length <= 1) return workbook;
  const sheet = workbook.sheets.find((candidate) => candidate.id === sheetId);
  if (!sheet) return workbook;
  workbook.sheets = workbook.sheets.filter((candidate) => candidate.id !== sheetId);
  workbook.sheets.forEach((candidate) => {
    Object.values(candidate.cells).forEach((cell) => {
      if (cell.raw.startsWith("=")) cell.raw = deleteSheetReferences(cell.raw, sheet.name);
    });
  });
  if (workbook.activeSheetId === sheetId) workbook.activeSheetId = workbook.sheets[0].id;
  return workbook;
}

export function changeSpreadsheetStructure(
  data: SpreadsheetData,
  sheetId: string,
  axis: "row" | "column",
  kind: "insert" | "delete",
  index: number,
) {
  const workbook = cloneWorkbook(normalizeSpreadsheetData(data));
  const sheet = workbook.sheets.find((candidate) => candidate.id === sheetId);
  if (!sheet) return workbook;
  const limit = axis === "row" ? sheet.rows : sheet.cols;
  const safeIndex = Math.min(Math.max(index, 0), Math.max(limit - (kind === "delete" ? 1 : 0), 0));
  if (kind === "delete" && limit <= 1) return workbook;
  const moved: Record<string, SpreadsheetCellData> = {};
  Object.entries(sheet.cells).forEach(([key, cell]) => {
    const [r, c] = key.split(",").map(Number);
    const coordinate = axis === "row" ? r : c;
    if (kind === "delete" && coordinate === safeIndex) return;
    let nextRow = r;
    let nextCol = c;
    if (kind === "insert" && coordinate >= safeIndex) {
      if (axis === "row") nextRow++;
      else nextCol++;
    }
    if (kind === "delete" && coordinate > safeIndex) {
      if (axis === "row") nextRow--;
      else nextCol--;
    }
    moved[cellKey(nextRow, nextCol)] = cell;
  });
  sheet.cells = moved;
  if (axis === "row") sheet.rows += kind === "insert" ? 1 : -1;
  else sheet.cols += kind === "insert" ? 1 : -1;
  workbook.sheets.forEach((formulaSheet) => {
    Object.values(formulaSheet.cells).forEach((cell) => {
      if (cell.raw.startsWith("=")) {
        cell.raw = rewriteFormulaForStructure(cell.raw, formulaSheet.name, {
          axis,
          index: safeIndex,
          kind,
          sheetName: sheet.name,
        });
      }
    });
  });
  return workbook;
}

function normalizedRange(range: SpreadsheetRange) {
  return {
    start: { r: Math.min(range.start.r, range.end.r), c: Math.min(range.start.c, range.end.c) },
    end: { r: Math.max(range.start.r, range.end.r), c: Math.max(range.start.c, range.end.c) },
  };
}

export function fillSpreadsheetRange(
  data: SpreadsheetData,
  sheetId: string,
  sourceRange: SpreadsheetRange,
  targetRange: SpreadsheetRange,
) {
  const workbook = cloneWorkbook(normalizeSpreadsheetData(data));
  const sheet = workbook.sheets.find((candidate) => candidate.id === sheetId);
  if (!sheet) return workbook;
  const source = normalizedRange(sourceRange);
  const target = normalizedRange(targetRange);
  if (target.start.r < 0 || target.start.c < 0 || target.end.r >= sheet.rows || target.end.c >= sheet.cols) return workbook;
  const sourceRows = source.end.r - source.start.r + 1;
  const sourceCols = source.end.c - source.start.c + 1;
  for (let r = target.start.r; r <= target.end.r; r++) {
    for (let c = target.start.c; c <= target.end.c; c++) {
      if (r >= source.start.r && r <= source.end.r && c >= source.start.c && c <= source.end.c) continue;
      const sourceRow = source.start.r + ((r - source.start.r) % sourceRows + sourceRows) % sourceRows;
      const sourceCol = source.start.c + ((c - source.start.c) % sourceCols + sourceCols) % sourceCols;
      const sourceCell = sheet.cells[cellKey(sourceRow, sourceCol)] ?? { raw: "" };
      let raw = sourceCell.raw;
      const yearMatch = raw.match(/^(\d{4})([AE])$/);
      const dateMatch = raw.match(/^(\d{4})-(\d{2})-(\d{2})$/);
      if (yearMatch && sourceRows === 1 && sourceCols === 1) {
        const distance = Math.abs(r - sourceRow) >= Math.abs(c - sourceCol) ? r - sourceRow : c - sourceCol;
        raw = `${Number.parseInt(yearMatch[1], 10) + distance}${yearMatch[2]}`;
      } else if (dateMatch && sourceRows === 1 && sourceCols === 1) {
        const distance = Math.abs(r - sourceRow) >= Math.abs(c - sourceCol) ? r - sourceRow : c - sourceCol;
        const date = new Date(`${raw}T00:00:00Z`);
        if (!Number.isNaN(date.getTime())) {
          date.setUTCDate(date.getUTCDate() + distance);
          raw = date.toISOString().slice(0, 10);
        }
      } else if (raw.startsWith("=")) {
        raw = translateFormula(raw, r - sourceRow, c - sourceCol);
      }
      sheet.cells[cellKey(r, c)] = { ...sourceCell, raw };
    }
  }
  return workbook;
}

export function updateWorkbookTimeline(data: SpreadsheetData, lastHistoricalYear: number) {
  const workbook = cloneWorkbook(normalizeSpreadsheetData(data));
  const timeline = workbook.metadata?.timeline;
  if (!timeline || !Number.isInteger(lastHistoricalYear)) return workbook;
  timeline.lastHistoricalYear = lastHistoricalYear;
  const labels = getTimelineLabels(timeline);
  const historicalLabels = labels.slice(0, timeline.historicalYears);
  const forecastLabels = labels.slice(timeline.historicalYears);
  workbook.sheets.forEach((sheet) => {
    const timelineCells = Object.entries(sheet.cells).filter(([, cell]) => cell.kind === "timeline");
    const currentLabels = timelineCells.map(([, cell]) => cell.raw);
    const replacement = currentLabels.every((label) => label.endsWith("E"))
      ? forecastLabels
      : currentLabels.every((label) => label.endsWith("A"))
        ? historicalLabels
        : labels;
    timelineCells.forEach(([, cell], index) => {
      cell.raw = replacement[index] ?? cell.raw;
    });
  });
  return workbook;
}
