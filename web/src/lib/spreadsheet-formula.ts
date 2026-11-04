import type {
  SpreadsheetCellPosition,
  SpreadsheetSheet,
  SpreadsheetWorkbook,
} from "./spreadsheet-workbook.ts";

export type SpreadsheetFormulaValue = number | string | boolean;

export const FORMULA_ERRORS = {
  cycle: "#CYCLE!",
  div0: "#DIV/0!",
  ref: "#REF!",
  value: "#VALUE!",
} as const;

const ERROR_VALUES = new Set<string>(Object.values(FORMULA_ERRORS));
const CELL_REFERENCE = /(?:(?:'((?:[^']|'')+)'|([A-Za-z_][A-Za-z0-9_ ]*))!)?(\$?)([A-Z]+)(\$?)([1-9][0-9]*)/g;
const FORMULA_REFERENCE_TOKEN = /(?:(?:'((?:[^']|'')+)'|([A-Za-z_][A-Za-z0-9_ ]*))!)?(\$?)([A-Z]+)(\$?)([1-9][0-9]*)(?:\s*:\s*(?:(?:'((?:[^']|'')+)'|([A-Za-z_][A-Za-z0-9_ ]*))!)?(\$?)([A-Z]+)(\$?)([1-9][0-9]*))?/g;

export function columnNameToIndex(name: string) {
  let result = 0;
  for (const character of name.toUpperCase()) result = result * 26 + character.charCodeAt(0) - 64;
  return result - 1;
}

export function columnIndexToName(index: number) {
  let value = index + 1;
  let result = "";
  while (value > 0) {
    const remainder = (value - 1) % 26;
    result = String.fromCharCode(65 + remainder) + result;
    value = Math.floor((value - 1) / 26);
  }
  return result;
}

function quotedSheetName(name: string) {
  return `'${name.replaceAll("'", "''")}'!`;
}

export interface FormulaReferenceRange {
  sheetId: string;
  start: SpreadsheetCellPosition;
  end: SpreadsheetCellPosition;
  tokenStart: number;
  tokenEnd: number;
}

function cellAddress(cell: SpreadsheetCellPosition) {
  return `${columnIndexToName(cell.c)}${cell.r + 1}`;
}

export function formatFormulaReference(
  workbook: SpreadsheetWorkbook,
  sourceSheetId: string,
  targetSheetId: string,
  start: SpreadsheetCellPosition,
  end: SpreadsheetCellPosition = start,
) {
  const sheet = workbook.sheets.find((candidate) => candidate.id === targetSheetId);
  if (!sheet) return "";
  const prefix = sourceSheetId === targetSheetId ? "" : quotedSheetName(sheet.name);
  const normalizedStart = {
    r: Math.min(start.r, end.r),
    c: Math.min(start.c, end.c),
  };
  const normalizedEnd = {
    r: Math.max(start.r, end.r),
    c: Math.max(start.c, end.c),
  };
  const rangeSuffix = normalizedStart.r === normalizedEnd.r && normalizedStart.c === normalizedEnd.c
    ? ""
    : `:${cellAddress(normalizedEnd)}`;
  return `${prefix}${cellAddress(normalizedStart)}${rangeSuffix}`;
}

export function insertFormulaReference(formula: string, caretPosition: number, reference: string) {
  const safeCaret = Math.min(Math.max(caretPosition, 0), formula.length);
  return {
    formula: `${formula.slice(0, safeCaret)}${reference}${formula.slice(safeCaret)}`,
    caretPosition: safeCaret + reference.length,
  };
}

function resolveSheetId(workbook: SpreadsheetWorkbook, sheetName: string | undefined, sourceSheetId: string) {
  if (!sheetName) return sourceSheetId;
  return workbook.sheets.find((sheet) => sheet.name === sheetName.replaceAll("''", "'"))?.id;
}

function isInsideFormulaString(formula: string, index: number) {
  let insideString = false;
  for (let cursor = 0; cursor < index; cursor++) {
    if (formula[cursor] !== '"') continue;
    if (insideString && formula[cursor + 1] === '"') {
      cursor++;
      continue;
    }
    insideString = !insideString;
  }
  return insideString;
}

export function extractFormulaReferences(
  workbook: SpreadsheetWorkbook,
  sourceSheetId: string,
  formula: string,
): FormulaReferenceRange[] {
  if (!formula.startsWith("=")) return [];
  const references: FormulaReferenceRange[] = [];
  for (const match of formula.matchAll(FORMULA_REFERENCE_TOKEN)) {
    if (match.index === undefined || isInsideFormulaString(formula, match.index)) continue;
    const sheetName = match[1] ?? match[2];
    const sheetId = resolveSheetId(workbook, sheetName, sourceSheetId);
    if (!sheetId) continue;
    const rangeSheetName = match[7] ?? match[8];
    const rangeSheetId = resolveSheetId(workbook, rangeSheetName ?? sheetName, sourceSheetId);
    if (rangeSheetId && rangeSheetId !== sheetId) continue;
    const start = {
      r: Number.parseInt(match[6], 10) - 1,
      c: columnNameToIndex(match[4]),
    };
    const end = match[10] && match[12]
      ? { r: Number.parseInt(match[12], 10) - 1, c: columnNameToIndex(match[10]) }
      : start;
    references.push({
      sheetId,
      start: { r: Math.min(start.r, end.r), c: Math.min(start.c, end.c) },
      end: { r: Math.max(start.r, end.r), c: Math.max(start.c, end.c) },
      tokenStart: match.index,
      tokenEnd: match.index + match[0].length,
    });
  }
  return references;
}

function cycleAddressMode(address: string) {
  const match = address.match(/^(\$?)([A-Z]+)(\$?)([1-9][0-9]*)$/);
  if (!match) return address;
  const [, absoluteColumn, column, absoluteRow, row] = match;
  if (!absoluteColumn && !absoluteRow) return `$${column}$${row}`;
  if (absoluteColumn && absoluteRow) return `${column}$${row}`;
  if (!absoluteColumn && absoluteRow) return `$${column}${row}`;
  return `${column}${row}`;
}

export function cycleFormulaReferenceAtCaret(formula: string, caretPosition: number) {
  const matches = Array.from(formula.matchAll(FORMULA_REFERENCE_TOKEN))
    .filter((candidate) => candidate.index !== undefined && !isInsideFormulaString(formula, candidate.index));
  const match = matches.find((candidate) => {
    const start = candidate.index ?? -1;
    return caretPosition >= start && caretPosition <= start + candidate[0].length;
  }) ?? matches.findLast((candidate) => (candidate.index ?? -1) + candidate[0].length <= caretPosition);
  if (!match || match.index === undefined) return { formula, caretPosition };
  const nextToken = match[0].replace(/\$?[A-Z]+\$?[1-9][0-9]*/g, cycleAddressMode);
  return {
    formula: `${formula.slice(0, match.index)}${nextToken}${formula.slice(match.index + match[0].length)}`,
    caretPosition: match.index + nextToken.length,
  };
}

export function translateFormula(formula: string, rowDelta: number, colDelta: number) {
  if (!formula.startsWith("=")) return formula;
  return formula.replace(
    CELL_REFERENCE,
    (match, quotedSheet: string | undefined, plainSheet: string | undefined, absoluteColumn: string, column: string, absoluteRow: string, rowText: string) => {
      const nextColumn = columnNameToIndex(column) + (absoluteColumn ? 0 : colDelta);
      const nextRow = Number.parseInt(rowText, 10) - 1 + (absoluteRow ? 0 : rowDelta);
      if (nextColumn < 0 || nextRow < 0) return FORMULA_ERRORS.ref;
      const sheetPrefix = quotedSheet
        ? quotedSheetName(quotedSheet.replaceAll("''", "'"))
        : plainSheet
          ? `${plainSheet}!`
          : "";
      return `${sheetPrefix}${absoluteColumn}${columnIndexToName(nextColumn)}${absoluteRow}${nextRow + 1}`;
    },
  );
}

export interface FormulaStructureChange {
  axis: "row" | "column";
  index: number;
  kind: "insert" | "delete";
  sheetName: string;
}

export function rewriteFormulaForStructure(
  formula: string,
  formulaSheetName: string,
  change: FormulaStructureChange,
) {
  if (!formula.startsWith("=")) return formula;
  const references = Array.from(formula.matchAll(CELL_REFERENCE));
  let cursor = 0;
  let rewritten = "";

  references.forEach((reference, referenceIndex) => {
    const offset = reference.index;
    rewritten += formula.slice(cursor, offset);
    cursor = offset + reference[0].length;

    const quotedSheet = reference[1];
    const plainSheet = reference[2];
    const absoluteColumn = reference[3];
    const column = reference[4];
    const absoluteRow = reference[5];
    const rowText = reference[6];
    const referencedSheet = quotedSheet?.replaceAll("''", "'") ?? plainSheet ?? formulaSheetName;
    if (referencedSheet !== change.sheetName) {
      rewritten += reference[0];
      return;
    }

    let row = Number.parseInt(rowText, 10) - 1;
    let col = columnNameToIndex(column);
    const coordinate = change.axis === "row" ? row : col;
    const previous = references[referenceIndex - 1];
    const next = references[referenceIndex + 1];
    const previousIsRangePartner = previous
      ? formula.slice((previous.index ?? 0) + previous[0].length, offset).trim() === ":"
      : false;
    const nextIsRangePartner = next
      ? formula.slice(cursor, next.index).trim() === ":"
      : false;
    const rangePartner = previousIsRangePartner ? previous : nextIsRangePartner ? next : undefined;

    if (change.kind === "delete" && coordinate === change.index) {
      if (!rangePartner) {
        rewritten += FORMULA_ERRORS.ref;
        return;
      }
      const partnerSheet = rangePartner[1]?.replaceAll("''", "'") ?? rangePartner[2] ?? formulaSheetName;
      const partnerCoordinate = change.axis === "row"
        ? Number.parseInt(rangePartner[6], 10) - 1
        : columnNameToIndex(rangePartner[4]);
      if (partnerSheet !== change.sheetName || partnerCoordinate === change.index) {
        rewritten += FORMULA_ERRORS.ref;
        return;
      }
      // Contract a range at the deleted edge. The endpoint after the removed
      // coordinate keeps its index; an endpoint before it shifts back once.
      if (partnerCoordinate < change.index) {
        if (change.axis === "row") row--;
        else col--;
      }
    } else if (change.kind === "insert" && coordinate >= change.index) {
      if (change.axis === "row") row++;
      else col++;
    } else if (change.kind === "delete" && coordinate > change.index) {
      if (change.axis === "row") row--;
      else col--;
    }

    const sheetPrefix = quotedSheet
      ? quotedSheetName(referencedSheet)
      : plainSheet
        ? `${plainSheet}!`
        : "";
    rewritten += `${sheetPrefix}${absoluteColumn}${columnIndexToName(col)}${absoluteRow}${row + 1}`;
  });

  return rewritten + formula.slice(cursor);
}

export function renameSheetReferences(formula: string, oldName: string, newName: string) {
  if (!formula.startsWith("=")) return formula;
  return formula
    .replaceAll(quotedSheetName(oldName), quotedSheetName(newName))
    .replaceAll(`${oldName}!`, quotedSheetName(newName));
}

export function deleteSheetReferences(formula: string, deletedName: string) {
  if (!formula.startsWith("=")) return formula;
  const escapedName = deletedName.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const escapedQuotedName = deletedName.replaceAll("'", "''").replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const qualifiedReference = new RegExp(
    `(?:'${escapedQuotedName}'|${escapedName})!\\$?[A-Z]+\\$?[1-9][0-9]*`,
    "gi",
  );
  return formula.replace(qualifiedReference, FORMULA_ERRORS.ref);
}

function parseLiteral(raw: string): SpreadsheetFormulaValue {
  const trimmed = raw.trim();
  if (trimmed === "") return 0;
  if (ERROR_VALUES.has(trimmed)) return trimmed;
  if (/^-?(?:\d+\.?\d*|\.\d+)%$/.test(trimmed)) return Number.parseFloat(trimmed) / 100;
  if (/^-?(?:\d+\.?\d*|\.\d+)$/.test(trimmed)) return Number.parseFloat(trimmed);
  if (trimmed.toUpperCase() === "TRUE") return true;
  if (trimmed.toUpperCase() === "FALSE") return false;
  return raw;
}

function asNumber(value: SpreadsheetFormulaValue): number | string {
  if (typeof value === "number") return value;
  if (typeof value === "boolean") return value ? 1 : 0;
  if (ERROR_VALUES.has(value)) return value;
  const parsed = Number.parseFloat(value);
  return Number.isFinite(parsed) ? parsed : FORMULA_ERRORS.value;
}

function flatten(values: Array<SpreadsheetFormulaValue | SpreadsheetFormulaValue[]>) {
  return values.flatMap((value) => Array.isArray(value) ? value : [value]);
}

function irr(values: number[], guess = 0.1) {
  let rate = guess;
  for (let iteration = 0; iteration < 100; iteration++) {
    let value = 0;
    let derivative = 0;
    values.forEach((cashFlow, index) => {
      value += cashFlow / (1 + rate) ** index;
      if (index > 0) derivative -= index * cashFlow / (1 + rate) ** (index + 1);
    });
    if (Math.abs(value) < 1e-9) return rate;
    if (!Number.isFinite(derivative) || Math.abs(derivative) < 1e-12) break;
    const next = rate - value / derivative;
    if (!Number.isFinite(next) || next <= -0.999999) break;
    rate = next;
  }
  return FORMULA_ERRORS.value;
}

function xnpv(rate: number, values: number[], dates: Array<string | number>) {
  if (rate <= -1 || values.length !== dates.length || values.length === 0) return FORMULA_ERRORS.value;
  const timestamps = dates.map((date) => new Date(date).getTime());
  if (timestamps.some((value) => Number.isNaN(value))) return FORMULA_ERRORS.value;
  const start = timestamps[0];
  return values.reduce((total, value, index) => total + value / (1 + rate) ** ((timestamps[index] - start) / 86_400_000 / 365), 0);
}

function xirr(values: number[], dates: Array<string | number>, guess = 0.1) {
  let rate = guess;
  for (let iteration = 0; iteration < 100; iteration++) {
    const value = xnpv(rate, values, dates);
    if (typeof value !== "number") return value;
    if (Math.abs(value) < 1e-9) return rate;
    const epsilon = 1e-6;
    const upper = xnpv(rate + epsilon, values, dates);
    const lower = xnpv(rate - epsilon, values, dates);
    if (typeof upper !== "number" || typeof lower !== "number") return FORMULA_ERRORS.value;
    const derivative = (upper - lower) / (2 * epsilon);
    if (!Number.isFinite(derivative) || Math.abs(derivative) < 1e-12) return FORMULA_ERRORS.value;
    rate -= value / derivative;
    if (!Number.isFinite(rate) || rate <= -0.999999) return FORMULA_ERRORS.value;
  }
  return FORMULA_ERRORS.value;
}

interface ReferenceValue {
  kind: "reference";
  sheet: SpreadsheetSheet;
  cell: SpreadsheetCellPosition;
}

type ParserValue = SpreadsheetFormulaValue | SpreadsheetFormulaValue[] | ReferenceValue;

class FormulaParser {
  private index = 0;
  private readonly expression: string;
  private readonly workbook: SpreadsheetWorkbook;
  private readonly currentSheet: SpreadsheetSheet;
  private readonly visited: Set<string>;

  constructor(
    expression: string,
    workbook: SpreadsheetWorkbook,
    currentSheet: SpreadsheetSheet,
    visited: Set<string>,
  ) {
    this.expression = expression;
    this.workbook = workbook;
    this.currentSheet = currentSheet;
    this.visited = visited;
  }

  parse(): SpreadsheetFormulaValue {
    const value = this.parseComparison();
    this.skipSpaces();
    if (this.index < this.expression.length) return FORMULA_ERRORS.value;
    return this.resolveScalar(value);
  }

  private parseComparison(): ParserValue {
    let left = this.parseAddSubtract();
    while (true) {
      this.skipSpaces();
      const operator = this.matchOperator([">=", "<=", "<>", "=", ">", "<"]);
      if (!operator) return left;
      const right = this.parseAddSubtract();
      const leftValue = this.resolveScalar(left);
      const rightValue = this.resolveScalar(right);
      if (ERROR_VALUES.has(String(leftValue))) return leftValue;
      if (ERROR_VALUES.has(String(rightValue))) return rightValue;
      if (operator === "=") left = leftValue === rightValue;
      else if (operator === "<>") left = leftValue !== rightValue;
      else if (operator === ">") left = leftValue > rightValue;
      else if (operator === "<") left = leftValue < rightValue;
      else if (operator === ">=") left = leftValue >= rightValue;
      else left = leftValue <= rightValue;
    }
  }

  private parseAddSubtract(): ParserValue {
    let left = this.parseMultiplyDivide();
    while (true) {
      this.skipSpaces();
      const operator = this.matchOperator(["+", "-"]);
      if (!operator) return left;
      const right = this.parseMultiplyDivide();
      left = this.numericOperation(left, right, operator);
    }
  }

  private parseMultiplyDivide(): ParserValue {
    let left = this.parsePower();
    while (true) {
      this.skipSpaces();
      const operator = this.matchOperator(["*", "/"]);
      if (!operator) return left;
      const right = this.parsePower();
      left = this.numericOperation(left, right, operator);
    }
  }

  private parsePower(): ParserValue {
    let left = this.parseUnary();
    this.skipSpaces();
    if (this.matchOperator(["^"])) left = this.numericOperation(left, this.parsePower(), "^");
    return left;
  }

  private parseUnary(): ParserValue {
    this.skipSpaces();
    if (this.matchOperator(["+"])) return this.parseUnary();
    if (this.matchOperator(["-"])) return this.numericOperation(0, this.parseUnary(), "-");
    return this.parsePrimary();
  }

  private parsePrimary(): ParserValue {
    this.skipSpaces();
    if (this.expression[this.index] === "(") {
      this.index++;
      const value = this.parseComparison();
      this.skipSpaces();
      if (this.expression[this.index] !== ")") return FORMULA_ERRORS.value;
      this.index++;
      return value;
    }
    if (this.expression[this.index] === '"') return this.parseString();
    const number = this.parseNumber();
    if (number !== null) return number;
    const reference = this.parseReference();
    if (reference) {
      this.skipSpaces();
      if (this.expression[this.index] === ":") {
        this.index++;
        const end = this.parseReference(reference.sheet);
        if (!end || end.sheet.id !== reference.sheet.id) return FORMULA_ERRORS.ref;
        return this.rangeValues(reference, end);
      }
      return reference;
    }
    const identifier = this.parseIdentifier();
    if (!identifier) return FORMULA_ERRORS.value;
    if (identifier === "TRUE") return true;
    if (identifier === "FALSE") return false;
    this.skipSpaces();
    if (this.expression[this.index] !== "(") return FORMULA_ERRORS.value;
    this.index++;
    const args: ParserValue[] = [];
    this.skipSpaces();
    while (this.expression[this.index] !== ")") {
      args.push(this.parseComparison());
      this.skipSpaces();
      if (this.expression[this.index] === ",") {
        this.index++;
        continue;
      }
      if (this.expression[this.index] !== ")") return FORMULA_ERRORS.value;
    }
    this.index++;
    return this.callFunction(identifier, args);
  }

  private parseString() {
    this.index++;
    let value = "";
    while (this.index < this.expression.length && this.expression[this.index] !== '"') value += this.expression[this.index++];
    if (this.expression[this.index] === '"') this.index++;
    return value as SpreadsheetFormulaValue;
  }

  private parseNumber() {
    const match = this.expression.slice(this.index).match(/^(?:\d+\.?\d*|\.\d+)(?:[Ee][+-]?\d+)?%?/);
    if (!match) return null;
    this.index += match[0].length;
    return match[0].endsWith("%") ? Number.parseFloat(match[0]) / 100 : Number.parseFloat(match[0]);
  }

  private parseReference(forcedSheet?: SpreadsheetSheet): ReferenceValue | null {
    const remaining = this.expression.slice(this.index);
    const match = remaining.match(/^(?:(?:'((?:[^']|'')+)'|([A-Za-z_][A-Za-z0-9_ ]*))!)?\$?([A-Z]+)\$?([1-9][0-9]*)/i);
    if (!match) return null;
    const sheetName = match[1]?.replaceAll("''", "'") ?? match[2];
    const sheet = forcedSheet ?? (sheetName
      ? this.workbook.sheets.find((candidate) => candidate.name.toLowerCase() === sheetName.toLowerCase())
      : this.currentSheet);
    if (!sheet) {
      this.index += match[0].length;
      return { kind: "reference", sheet: this.currentSheet, cell: { r: -1, c: -1 } };
    }
    this.index += match[0].length;
    return {
      kind: "reference",
      sheet,
      cell: { r: Number.parseInt(match[4], 10) - 1, c: columnNameToIndex(match[3]) },
    };
  }

  private parseIdentifier() {
    const match = this.expression.slice(this.index).match(/^[A-Za-z][A-Za-z0-9.]*/);
    if (!match) return null;
    this.index += match[0].length;
    return match[0].toUpperCase();
  }

  private rangeValues(start: ReferenceValue, end: ReferenceValue) {
    const values: SpreadsheetFormulaValue[] = [];
    for (let row = Math.min(start.cell.r, end.cell.r); row <= Math.max(start.cell.r, end.cell.r); row++) {
      for (let col = Math.min(start.cell.c, end.cell.c); col <= Math.max(start.cell.c, end.cell.c); col++) {
        values.push(evaluateSpreadsheetCell(this.workbook, start.sheet.id, { r: row, c: col }, new Set(this.visited)));
      }
    }
    return values;
  }

  private callFunction(name: string, args: ParserValue[]): ParserValue {
    if (name === "IF") return Boolean(this.resolveScalar(args[0])) ? this.resolveScalar(args[1]) : this.resolveScalar(args[2]);
    if (name === "XNPV") {
      const rate = asNumber(this.resolveScalar(args[0]));
      const valueArg = Array.isArray(args[1]) ? args[1].map(asNumber) : [asNumber(this.resolveScalar(args[1]))];
      const dateArg = Array.isArray(args[2]) ? args[2] : [this.resolveScalar(args[2])];
      if (typeof rate === "string" || valueArg.some((value) => typeof value === "string")) return FORMULA_ERRORS.value;
      return xnpv(rate, valueArg as number[], dateArg as Array<string | number>);
    }
    if (name === "XIRR") {
      const valueArg = Array.isArray(args[0]) ? args[0].map(asNumber) : [asNumber(this.resolveScalar(args[0]))];
      const dateArg = Array.isArray(args[1]) ? args[1] : [this.resolveScalar(args[1])];
      const guess = args[2] === undefined ? 0.1 : asNumber(this.resolveScalar(args[2]));
      if (typeof guess === "string" || valueArg.some((value) => typeof value === "string")) return FORMULA_ERRORS.value;
      return xirr(valueArg as number[], dateArg as Array<string | number>, guess);
    }
    const values = flatten(args.map((arg) => Array.isArray(arg) ? arg : this.resolveScalar(arg)));
    if (values.some((value) => ERROR_VALUES.has(String(value)))) return values.find((value) => ERROR_VALUES.has(String(value))) as string;
    const numbers = values.map(asNumber);
    if (numbers.some((value) => typeof value === "string")) return FORMULA_ERRORS.value;
    const numeric = numbers as number[];
    if (name === "SUM") return numeric.reduce((sum, value) => sum + value, 0);
    if (name === "AVERAGE") return numeric.length ? numeric.reduce((sum, value) => sum + value, 0) / numeric.length : FORMULA_ERRORS.div0;
    if (name === "MIN") return numeric.length ? Math.min(...numeric) : 0;
    if (name === "MAX") return numeric.length ? Math.max(...numeric) : 0;
    if (name === "MEDIAN") {
      if (!numeric.length) return 0;
      const sorted = [...numeric].sort((a, b) => a - b);
      const middle = Math.floor(sorted.length / 2);
      return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
    }
    if (name === "NPV") {
      const [rate, ...cashFlows] = numeric;
      return cashFlows.reduce((sum, value, index) => sum + value / (1 + rate) ** (index + 1), 0);
    }
    if (name === "IRR") return irr(numeric);
    return FORMULA_ERRORS.value;
  }

  private resolveScalar(value: ParserValue): SpreadsheetFormulaValue {
    if (Array.isArray(value)) return value[0] ?? 0;
    if (typeof value === "object" && value.kind === "reference") {
      if (value.cell.r < 0 || value.cell.c < 0) return FORMULA_ERRORS.ref;
      return evaluateSpreadsheetCell(this.workbook, value.sheet.id, value.cell, new Set(this.visited));
    }
    return value as SpreadsheetFormulaValue;
  }

  private numericOperation(left: ParserValue, right: ParserValue, operator: string): SpreadsheetFormulaValue {
    const leftNumber = asNumber(this.resolveScalar(left));
    const rightNumber = asNumber(this.resolveScalar(right));
    if (typeof leftNumber === "string") return leftNumber;
    if (typeof rightNumber === "string") return rightNumber;
    if (operator === "+") return leftNumber + rightNumber;
    if (operator === "-") return leftNumber - rightNumber;
    if (operator === "*") return leftNumber * rightNumber;
    if (operator === "/") return rightNumber === 0 ? FORMULA_ERRORS.div0 : leftNumber / rightNumber;
    return leftNumber ** rightNumber;
  }

  private matchOperator(operators: string[]) {
    this.skipSpaces();
    const operator = operators.find((candidate) => this.expression.startsWith(candidate, this.index));
    if (operator) this.index += operator.length;
    return operator;
  }

  private skipSpaces() {
    while (/\s/.test(this.expression[this.index] ?? "")) this.index++;
  }
}

export function evaluateSpreadsheetCell(
  workbook: SpreadsheetWorkbook,
  sheetId: string,
  cell: SpreadsheetCellPosition,
  visited = new Set<string>(),
): SpreadsheetFormulaValue {
  const sheet = workbook.sheets.find((candidate) => candidate.id === sheetId);
  if (!sheet || cell.r < 0 || cell.c < 0 || cell.r >= sheet.rows || cell.c >= sheet.cols) return FORMULA_ERRORS.ref;
  const visitKey = `${sheet.id}:${cell.r},${cell.c}`;
  if (visited.has(visitKey)) return FORMULA_ERRORS.cycle;
  visited.add(visitKey);
  const raw = sheet.cells[`${cell.r},${cell.c}`]?.raw ?? "";
  if (!raw.startsWith("=")) return parseLiteral(raw);
  if (raw.includes(FORMULA_ERRORS.ref)) return FORMULA_ERRORS.ref;
  return new FormulaParser(raw.slice(1), workbook, sheet, visited).parse();
}
