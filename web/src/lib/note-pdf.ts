import type { JSONContent } from "@tiptap/core";
import { evaluateSpreadsheetCell } from "./spreadsheet-formula.ts";
import {
  getActiveSpreadsheetSheet,
  normalizeSpreadsheetData,
  type SpreadsheetData,
} from "./spreadsheet-workbook.ts";

export type { SpreadsheetData } from "./spreadsheet-workbook.ts";

export type NotePdfSavingStatus = "saved" | "saving" | "unsaved" | "failed" | "conflict";

export type NotePdfCitation = {
  id: string;
  source_label: string | null;
  source_url: string | null;
  supporting_quote: string | null;
  validity_status: string | null;
  locator_json: Record<string, unknown> | null;
  created_at: string;
};

export type NotePdfSnapshot = {
  title: string;
  content: JSONContent;
  citations: NotePdfCitation[];
  exportedAt: string;
  savingStatus: NotePdfSavingStatus;
  includesLocalDraft: boolean;
};

const CSS_COLOR_MAP: Record<string, string> = {
  "var(--note-color-red)": "#dc2626",
  "var(--note-color-orange)": "#ea580c",
  "var(--note-color-green)": "#16a34a",
  "var(--note-color-blue)": "#2563eb",
  "var(--note-color-purple)": "#9333ea",
};

export function resolvePdfColor(value: unknown, fallback: string): string {
  if (typeof value !== "string") return fallback;
  const normalized = value.trim();
  if (CSS_COLOR_MAP[normalized]) return CSS_COLOR_MAP[normalized];
  if (/^#[\da-f]{3,8}$/i.test(normalized)) return normalized;
  if (/^rgba?\([\d.,%\s]+\)$/i.test(normalized)) return normalized;
  return fallback;
}

export function notePdfFilename(title: string): string {
  const safeTitle = title
    .trim()
    .replace(/[\\/:*?"<>|\u0000-\u001f]/g, "_")
    .replace(/\.+$/g, "")
    .slice(0, 120);
  return `${safeTitle || "未命名筆記"}.pdf`;
}

export function createNotePdfSnapshot(input: {
  title: string;
  content: JSONContent | null;
  citations: NotePdfCitation[];
  savingStatus: NotePdfSavingStatus;
  exportedAt?: string;
}): NotePdfSnapshot {
  const snapshot = {
    title: input.title.trim() || "未命名筆記",
    content: input.content ?? { type: "doc", content: [] },
    citations: input.citations,
    exportedAt: input.exportedAt ?? new Date().toISOString(),
    savingStatus: input.savingStatus,
    includesLocalDraft: input.savingStatus !== "saved",
  } satisfies NotePdfSnapshot;

  // PDF generation is asynchronous. Clone the editor state so later typing cannot
  // mutate the export that is already in progress.
  return JSON.parse(JSON.stringify(snapshot)) as NotePdfSnapshot;
}

export function spreadsheetCellDisplay(
  data: SpreadsheetData,
  row: number,
  col: number,
): string {
  const workbook = normalizeSpreadsheetData(data);
  const sheet = getActiveSpreadsheetSheet(workbook);
  const raw = sheet.cells[`${row},${col}`]?.raw ?? "";
  if (!raw.startsWith("=")) return raw;
  const value = evaluateSpreadsheetCell(workbook, sheet.id, { r: row, c: col });
  if (typeof value !== "number") return String(value);
  return Number.isInteger(value) ? String(value) : value.toFixed(2);
}

export function spreadsheetRows(data: SpreadsheetData): string[][] {
  const sheet = getActiveSpreadsheetSheet(data);
  return Array.from({ length: Math.max(0, sheet.rows) }, (_, row) =>
    Array.from({ length: Math.max(0, sheet.cols) }, (_, col) => spreadsheetCellDisplay(data, row, col)),
  );
}
