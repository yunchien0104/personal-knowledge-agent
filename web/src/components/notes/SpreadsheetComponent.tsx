"use client";

import {
  useEffect,
  useRef,
  type CSSProperties,
  type Dispatch,
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent as ReactPointerEvent,
  type Ref,
} from "react";
import { Calculator, Columns3, Maximize2, Plus, Rows3, Trash2 } from "lucide-react";

import {
  columnIndexToName,
  cycleFormulaReferenceAtCaret,
  evaluateSpreadsheetCell,
  extractFormulaReferences,
  formatFormulaReference,
  insertFormulaReference,
} from "@/lib/spreadsheet-formula";
import {
  addSpreadsheetSheet,
  changeSpreadsheetStructure,
  deleteSpreadsheetSheet,
  fillSpreadsheetRange,
  getActiveSpreadsheetSheet,
  normalizeSpreadsheetData,
  renameSpreadsheetSheet,
  setActiveSpreadsheetSheet,
  spreadsheetSheetReferenceCount,
  type SpreadsheetData,
  type SpreadsheetCellPosition,
  type SpreadsheetRange,
  type SpreadsheetWorkspaceAction,
  type SpreadsheetWorkspaceState,
  updateSpreadsheetCell,
  updateWorkbookTimeline,
} from "@/lib/spreadsheet-workspace";

export type { SpreadsheetData } from "@/lib/spreadsheet-workspace";

interface SpreadsheetProps {
  data: SpreadsheetData;
  onChange: (data: SpreadsheetData) => void;
  workspaceState: SpreadsheetWorkspaceState;
  dispatchWorkspace: Dispatch<SpreadsheetWorkspaceAction>;
  readOnly?: boolean;
  active?: boolean;
  onExpand?: () => void;
  expandButtonRef?: Ref<HTMLButtonElement>;
}

function sameCell(a: SpreadsheetCellPosition | null, r: number, c: number) {
  return a?.r === r && a.c === c;
}

function cellInRange(cell: SpreadsheetCellPosition, range: SpreadsheetRange | null) {
  if (!range) return false;
  return cell.r >= Math.min(range.start.r, range.end.r)
    && cell.r <= Math.max(range.start.r, range.end.r)
    && cell.c >= Math.min(range.start.c, range.end.c)
    && cell.c <= Math.max(range.start.c, range.end.c);
}

const FORMULA_REFERENCE_COLORS = ["#2563eb", "#db2777", "#7c3aed", "#059669", "#d97706"] as const;

function semanticCellClass(
  cell: ReturnType<typeof getActiveSpreadsheetSheet>["cells"][string] | undefined,
  timelineLabel: string | undefined,
) {
  if (cell?.kind === "formula") return "spreadsheet-cell-formula";
  if (cell?.kind === "not_applicable") return "spreadsheet-cell-not-applicable";
  if (cell?.kind === "input") {
    return timelineLabel?.endsWith("A")
      ? "spreadsheet-cell-historical"
      : "spreadsheet-cell-assumption";
  }
  return "spreadsheet-cell-neutral";
}

export function SpreadsheetComponent({
  data,
  onChange,
  workspaceState,
  dispatchWorkspace,
  readOnly = false,
  active = true,
  onExpand,
  expandButtonRef,
}: SpreadsheetProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const formulaBarRef = useRef<HTMLInputElement | null>(null);
  const gridRef = useRef<HTMLTableElement | null>(null);
  const formulaDragStartRef = useRef<{ sheetId: string; cell: SpreadsheetCellPosition } | null>(null);
  const skipNextBlurCommitRef = useRef(false);
  const moveAfterBlurRef = useRef<SpreadsheetCellPosition | null>(null);
  const workbook = normalizeSpreadsheetData(data);
  const sheet = getActiveSpreadsheetSheet(workbook);
  const { editingCell, editingSheetId, editValue, fillSource, fillTarget, formulaEditing } = workspaceState;
  const selectedCell = workspaceState.selectedCellsBySheet[sheet.id] ?? null;
  const selectedRange = workspaceState.selectedRangesBySheet[sheet.id]
    ?? (selectedCell ? { start: selectedCell, end: selectedCell } : null);
  const formulaReferences = formulaEditing
    ? extractFormulaReferences(workbook, formulaEditing.sourceSheetId, formulaEditing.draft)
    : [];
  const formulaSourceSheet = formulaEditing
    ? workbook.sheets.find((candidate) => candidate.id === formulaEditing.sourceSheetId)
    : undefined;

  useEffect(() => {
    if (!active || !editingCell) return;
    const editor = editingSheetId === sheet.id ? inputRef.current : formulaBarRef.current;
    editor?.focus();
    const caretPosition = formulaEditing?.caretPosition ?? editValue.length;
    editor?.setSelectionRange(caretPosition, caretPosition);
  }, [active, editValue.length, editingCell, editingSheetId, formulaEditing?.caretPosition, sheet.id]);

  useEffect(() => {
    if (!active || !fillSource) return;
    const handlePointerMove = (event: PointerEvent) => {
      const element = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>("[data-cell]");
      if (!element || !gridRef.current?.contains(element)) return;
      const [r, c] = (element.dataset.cell ?? "").split(",").map(Number);
      if (!Number.isInteger(r) || !Number.isInteger(c)) return;
      dispatchWorkspace({ type: "updateFill", range: { start: fillSource.start, end: { r, c } } });
    };
    const handlePointerUp = (event: PointerEvent) => {
      const element = document.elementFromPoint(event.clientX, event.clientY)?.closest<HTMLElement>("[data-cell]");
      const [r, c] = (element?.dataset.cell ?? "").split(",").map(Number);
      const finalTarget = element && gridRef.current?.contains(element) && Number.isInteger(r) && Number.isInteger(c)
        ? { start: fillSource.start, end: { r, c } }
        : fillTarget;
      if (finalTarget) onChange(fillSpreadsheetRange(workbook, sheet.id, fillSource, finalTarget));
      dispatchWorkspace({ type: "finishFill" });
    };
    window.addEventListener("pointermove", handlePointerMove);
    window.addEventListener("pointerup", handlePointerUp, { once: true });
    return () => {
      window.removeEventListener("pointermove", handlePointerMove);
      window.removeEventListener("pointerup", handlePointerUp);
    };
  }, [active, dispatchWorkspace, fillSource, fillTarget, onChange, sheet.id, workbook]);

  const startEdit = (r: number, c: number, initialValue?: string) => {
    if (readOnly) return;
    skipNextBlurCommitRef.current = false;
    moveAfterBlurRef.current = null;
    dispatchWorkspace({
      type: "startEdit",
      cell: { r, c },
      sheetId: sheet.id,
      workbookId: workbook.workbookId,
      value: initialValue ?? sheet.cells[`${r},${c}`]?.raw ?? "",
    });
  };

  const focusCell = (cell: SpreadsheetCellPosition) => {
    requestAnimationFrame(() => {
      gridRef.current?.querySelector<HTMLElement>(`[data-cell="${cell.r},${cell.c}"]`)?.focus();
    });
  };

  const moveSelection = (r: number, c: number) => {
    const cell = {
      r: Math.min(Math.max(r, 0), sheet.rows - 1),
      c: Math.min(Math.max(c, 0), sheet.cols - 1),
    };
    dispatchWorkspace({ type: "selectCell", cell, sheetId: sheet.id });
    focusCell(cell);
  };

  const commitEdit = (moveTo?: SpreadsheetCellPosition) => {
    if (!editingCell || !editingSheetId) return;
    const sourceSheetId = formulaEditing?.sourceSheetId ?? editingSheetId;
    const sourceCell = formulaEditing?.sourceCell ?? editingCell;
    let next = updateSpreadsheetCell(workbook, sourceCell, editValue, sourceSheetId);
    if (formulaEditing && workbook.activeSheetId !== sourceSheetId) {
      next = setActiveSpreadsheetSheet(next, sourceSheetId);
    }
    onChange(next);
    dispatchWorkspace({ type: "finishEdit" });
    if (moveTo) {
      requestAnimationFrame(() => {
        gridRef.current?.querySelector<HTMLElement>(`[data-cell="${moveTo.r},${moveTo.c}"]`)?.focus();
      });
    }
  };

  const cancelEdit = () => {
    if (formulaEditing && workbook.activeSheetId !== formulaEditing.sourceSheetId) {
      onChange(setActiveSpreadsheetSheet(workbook, formulaEditing.sourceSheetId));
    }
    const sourceCell = formulaEditing?.sourceCell ?? editingCell;
    dispatchWorkspace({ type: "cancelEdit" });
    if (sourceCell) focusCell(sourceCell);
  };

  const updateEditValue = (value: string, caretPosition: number | null) => {
    dispatchWorkspace({
      type: "setEditValue",
      value,
      caretPosition: caretPosition ?? value.length,
    });
  };

  const insertReference = (targetSheetId: string, start: SpreadsheetCellPosition, end = start) => {
    if (!formulaEditing) return;
    const reference = formatFormulaReference(
      workbook,
      formulaEditing.sourceSheetId,
      targetSheetId,
      start,
      end,
    );
    if (!reference) return;
    const next = insertFormulaReference(
      formulaEditing.draft,
      formulaEditing.caretPosition,
      reference,
    );
    const range = {
      start: { r: Math.min(start.r, end.r), c: Math.min(start.c, end.c) },
      end: { r: Math.max(start.r, end.r), c: Math.max(start.c, end.c) },
    };
    dispatchWorkspace({
      type: "insertFormulaReference",
      value: next.formula,
      caretPosition: next.caretPosition,
      sheetId: targetSheetId,
      range,
    });
  };

  const handleFormulaPointerDown = (
    event: ReactPointerEvent<HTMLTableCellElement>,
    cell: SpreadsheetCellPosition,
  ) => {
    if (!formulaEditing || readOnly) return false;
    event.preventDefault();
    event.stopPropagation();
    formulaDragStartRef.current = { sheetId: sheet.id, cell };
    dispatchWorkspace({
      type: "startFormulaRange",
      sheetId: sheet.id,
      range: { start: cell, end: cell },
    });
    return true;
  };

  const handleFormulaPointerEnter = (
    event: ReactPointerEvent<HTMLTableCellElement>,
    cell: SpreadsheetCellPosition,
  ) => {
    const start = formulaDragStartRef.current;
    if (!formulaEditing || !start || start.sheetId !== sheet.id || event.buttons !== 1) return;
    dispatchWorkspace({
      type: "updateFormulaRange",
      sheetId: sheet.id,
      range: { start: start.cell, end: cell },
    });
  };

  const handleFormulaPointerUp = (
    event: ReactPointerEvent<HTMLTableCellElement>,
    cell: SpreadsheetCellPosition,
  ) => {
    const start = formulaDragStartRef.current;
    if (!formulaEditing || !start || start.sheetId !== sheet.id) return;
    event.preventDefault();
    event.stopPropagation();
    formulaDragStartRef.current = null;
    insertReference(sheet.id, start.cell, cell);
  };

  const handleCellKeyDown = (event: KeyboardEvent<HTMLTableCellElement>, r: number, c: number) => {
    if (event.key === "Enter" || event.key === "F2") {
      event.preventDefault();
      event.stopPropagation();
      startEdit(r, c);
      return;
    }
    if (!readOnly && event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      event.stopPropagation();
      startEdit(r, c, event.key);
      return;
    }
    if (!readOnly && (event.key === "Backspace" || event.key === "Delete")) {
      event.preventDefault();
      event.stopPropagation();
      onChange(updateSpreadsheetCell(workbook, { r, c }, "", sheet.id));
      return;
    }
    const target = {
      ArrowUp: { r: r - 1, c },
      ArrowDown: { r: r + 1, c },
      ArrowLeft: { r, c: c - 1 },
      ArrowRight: { r, c: c + 1 },
    }[event.key];
    if (target) {
      event.preventDefault();
      event.stopPropagation();
      moveSelection(target.r, target.c);
    }
  };

  const handleEditingKeyDown = (
    event: KeyboardEvent<HTMLInputElement>,
    cell: SpreadsheetCellPosition,
  ) => {
    event.stopPropagation();
    if (event.key === "F4" && formulaEditing) {
      event.preventDefault();
      const next = cycleFormulaReferenceAtCaret(
        formulaEditing.draft,
        event.currentTarget.selectionStart ?? formulaEditing.caretPosition,
      );
      dispatchWorkspace({
        type: "setEditValue",
        value: next.formula,
        caretPosition: next.caretPosition,
      });
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const editSheetId = formulaEditing?.sourceSheetId ?? editingSheetId;
      const editSheet = workbook.sheets.find((candidate) => candidate.id === editSheetId);
      commitEdit({ r: Math.min(cell.r + 1, (editSheet?.rows ?? sheet.rows) - 1), c: cell.c });
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      skipNextBlurCommitRef.current = true;
      cancelEdit();
    }
  };

  const selectWithPointer = (event: MouseEvent<HTMLTableCellElement>, r: number, c: number) => {
    if (formulaEditing) return;
    const cell = { r, c };
    if (event.shiftKey && selectedRange) {
      dispatchWorkspace({ type: "selectRange", range: { start: selectedRange.start, end: cell }, sheetId: sheet.id });
      return;
    }
    dispatchWorkspace({ type: "selectCell", cell, sheetId: sheet.id });
  };

  const changeStructure = (axis: "row" | "column", kind: "insert" | "delete", index: number) => {
    onChange(changeSpreadsheetStructure(workbook, sheet.id, axis, kind, index));
    if (!selectedCell) return;
    const coordinate = axis === "row" ? selectedCell.r : selectedCell.c;
    let nextCoordinate = coordinate;
    if (kind === "insert" && index <= coordinate) nextCoordinate++;
    if (kind === "delete" && index <= coordinate) nextCoordinate = Math.max(0, coordinate - 1);
    const nextCell = axis === "row"
      ? { ...selectedCell, r: nextCoordinate }
      : { ...selectedCell, c: nextCoordinate };
    dispatchWorkspace({ type: "selectCell", cell: nextCell, sheetId: sheet.id });
  };

  const switchSheet = (sheetId: string) => {
    onChange(setActiveSpreadsheetSheet(workbook, sheetId));
    dispatchWorkspace({ type: "switchSheet", sheetId });
  };

  const addSheet = () => {
    const next = addSpreadsheetSheet(workbook);
    onChange(next);
    dispatchWorkspace({ type: "switchSheet", sheetId: next.activeSheetId });
  };

  const renameSheet = () => {
    const nextName = window.prompt("重新命名工作表", sheet.name);
    if (nextName) onChange(renameSpreadsheetSheet(workbook, sheet.id, nextName));
  };

  const deleteSheet = () => {
    if (workbook.sheets.length <= 1) return;
    const references = spreadsheetSheetReferenceCount(workbook, sheet.id);
    const message = references > 0
      ? `此工作表仍被 ${references} 個公式引用。刪除後引用將顯示 #REF!。確定刪除？`
      : `確定刪除「${sheet.name}」？`;
    if (window.confirm(message)) {
      const next = deleteSpreadsheetSheet(workbook, sheet.id);
      onChange(next);
      dispatchWorkspace({ type: "switchSheet", sheetId: next.activeSheetId });
    }
  };

  const displayCell = (r: number, c: number) => {
    const raw = sheet.cells[`${r},${c}`]?.raw ?? "";
    if (!raw.startsWith("=")) return raw;
    const value = evaluateSpreadsheetCell(workbook, sheet.id, { r, c });
    if (typeof value !== "number") return String(value);
    return Number.isInteger(value) ? String(value) : Number(value.toFixed(6)).toString();
  };

  return (
    <div className="rounded-xl border border-neutral-200 bg-white p-3 shadow-sm dark:border-neutral-700 dark:bg-neutral-900 not-prose">
      <div className="mb-2 flex flex-wrap items-center justify-between gap-2 border-b border-neutral-100 pb-2 dark:border-neutral-800">
        <div className="min-w-0 text-xs text-neutral-700 dark:text-neutral-300">
          <div className="flex items-center gap-2 font-semibold">
            <Calculator className="h-4 w-4 shrink-0 text-emerald-500" />
            <span className="truncate">{workbook.metadata?.templateLabel || "Workbook"}</span>
            <span className="whitespace-nowrap font-mono text-[10px] font-normal text-neutral-400">{sheet.name} · {sheet.rows} × {sheet.cols}</span>
          </div>
          <div className="mt-1 text-[10px] text-neutral-400">安全公式：跨 Sheet、SUM、AVERAGE、MEDIAN、MIN、MAX、IF、NPV、IRR、XNPV、XIRR</div>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {workbook.metadata?.timeline ? (
            <label className="flex items-center gap-1 text-[10px] text-neutral-500 dark:text-neutral-400">
              最後歷史年
              <input
                type="number"
                value={workbook.metadata.timeline.lastHistoricalYear}
                onChange={(event) => onChange(updateWorkbookTimeline(workbook, Number(event.target.value)))}
                disabled={readOnly}
                className="w-16 rounded border border-neutral-200 bg-transparent px-1.5 py-1 text-xs dark:border-neutral-700"
              />
            </label>
          ) : null}
          {onExpand ? (
            <button
              ref={expandButtonRef}
              type="button"
              onMouseDown={(event) => {
                if (formulaEditing) event.preventDefault();
              }}
              onClick={onExpand}
              className="flex items-center gap-1 rounded bg-indigo-50 px-2 py-1 text-[11px] font-medium text-indigo-700 hover:bg-indigo-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-500 dark:bg-indigo-950/60 dark:text-indigo-300 dark:hover:bg-indigo-900/60"
              aria-label="展開計算表格工作區"
            ><Maximize2 className="h-3 w-3" /> 展開</button>
          ) : null}
        </div>
      </div>

      {active && formulaEditing ? (
        <div className="mb-2 flex items-center gap-2 rounded-md border border-blue-200 bg-blue-50/70 px-2 py-1.5 dark:border-blue-800 dark:bg-blue-950/30">
          <span className="shrink-0 font-mono text-[10px] font-semibold text-blue-700 dark:text-blue-300">
            {formulaSourceSheet?.name ?? "Sheet"}!{columnIndexToName(formulaEditing.sourceCell.c)}{formulaEditing.sourceCell.r + 1}
          </span>
          {sheet.id === formulaEditing.sourceSheetId ? (
            <span className="min-w-0 flex-1 truncate font-mono text-xs text-zinc-700 dark:text-zinc-200">
              {formulaEditing.draft}
            </span>
          ) : (
            <input
              ref={formulaBarRef}
              type="text"
              value={formulaEditing.draft}
              onChange={(event) => updateEditValue(event.target.value, event.target.selectionStart)}
              onSelect={(event) => dispatchWorkspace({
                type: "setFormulaCaret",
                caretPosition: event.currentTarget.selectionStart ?? formulaEditing.draft.length,
              })}
              onKeyDown={(event) => handleEditingKeyDown(event, formulaEditing.sourceCell)}
              aria-label="公式編輯列"
              className="min-w-0 flex-1 bg-transparent font-mono text-xs text-zinc-900 outline-none dark:text-zinc-100"
            />
          )}
          <span className="hidden shrink-0 text-[10px] text-blue-600 dark:text-blue-300 sm:inline">點選儲存格或拖曳範圍</span>
        </div>
      ) : null}

      {!readOnly && selectedCell ? (
        <div className="mb-2 flex flex-wrap items-center gap-1 rounded-md bg-neutral-50 p-1 dark:bg-neutral-800/50" role="toolbar" aria-label="列欄結構操作">
          <Rows3 className="mx-1 h-3.5 w-3.5 text-neutral-400" />
          <button type="button" onClick={() => changeStructure("row", "insert", selectedCell.r)} className="rounded px-2 py-1 text-[10px] hover:bg-white dark:hover:bg-neutral-700">上方插入列</button>
          <button type="button" onClick={() => changeStructure("row", "insert", selectedCell.r + 1)} className="rounded px-2 py-1 text-[10px] hover:bg-white dark:hover:bg-neutral-700">下方插入列</button>
          <button type="button" onClick={() => changeStructure("row", "delete", selectedCell.r)} className="rounded px-2 py-1 text-[10px] text-rose-600 hover:bg-rose-50 dark:text-rose-300 dark:hover:bg-rose-950/30">刪除列</button>
          <Columns3 className="mx-1 h-3.5 w-3.5 text-neutral-400" />
          <button type="button" onClick={() => changeStructure("column", "insert", selectedCell.c)} className="rounded px-2 py-1 text-[10px] hover:bg-white dark:hover:bg-neutral-700">左側插入欄</button>
          <button type="button" onClick={() => changeStructure("column", "insert", selectedCell.c + 1)} className="rounded px-2 py-1 text-[10px] hover:bg-white dark:hover:bg-neutral-700">右側插入欄</button>
          <button type="button" onClick={() => changeStructure("column", "delete", selectedCell.c)} className="rounded px-2 py-1 text-[10px] text-rose-600 hover:bg-rose-50 dark:text-rose-300 dark:hover:bg-rose-950/30">刪除欄</button>
        </div>
      ) : null}

      <div className="overflow-x-auto">
        <table ref={gridRef} className="spreadsheet-grid w-full border-collapse text-xs" role="grid" aria-label={`${sheet.name} 工作表`} aria-rowcount={sheet.rows} aria-colcount={sheet.cols}>
          <thead>
            <tr>
              <th className="w-8 border border-neutral-200 bg-neutral-100 p-1 text-center font-mono text-[10px] text-neutral-400 dark:border-neutral-700 dark:bg-neutral-800">#</th>
              {Array.from({ length: sheet.cols }).map((_, c) => (
                <th key={c} className="min-w-[90px] border border-neutral-200 bg-neutral-50 p-1 text-center font-mono font-semibold text-neutral-600 dark:border-neutral-700 dark:bg-neutral-800/60 dark:text-neutral-300">{columnIndexToName(c)}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {Array.from({ length: sheet.rows }).map((_, r) => (
              <tr key={`${sheet.id}:${r}`}>
                <th scope="row" className="border border-neutral-200 bg-neutral-50 p-1 text-center font-mono text-[10px] font-normal text-neutral-400 dark:border-neutral-700 dark:bg-neutral-800/40">{r + 1}</th>
                {Array.from({ length: sheet.cols }).map((_, c) => {
                  const isEditing = editingSheetId === sheet.id && sameCell(editingCell, r, c);
                  const showEditor = active && isEditing;
                  const isSelected = cellInRange({ r, c }, selectedRange) || (!selectedCell && r === 0 && c === 0);
                  const isFillHandleCell = selectedRange
                    ? r === Math.max(selectedRange.start.r, selectedRange.end.r)
                      && c === Math.max(selectedRange.start.c, selectedRange.end.c)
                    : r === 0 && c === 0;
                  const isFillTarget = cellInRange({ r, c }, fillTarget);
                  const cell = sheet.cells[`${r},${c}`];
                  const timelineLabel = sheet.cells[`0,${c}`]?.raw;
                  const formulaReferenceIndex = formulaReferences.findIndex((reference) => (
                    reference.sheetId === sheet.id && cellInRange({ r, c }, reference)
                  ));
                  const formulaSelection = formulaEditing?.selectingRange;
                  const isFormulaSelection = formulaSelection?.sheetId === sheet.id
                    && cellInRange({ r, c }, formulaSelection.range);
                  const referenceColor = formulaReferenceIndex >= 0
                    ? FORMULA_REFERENCE_COLORS[formulaReferenceIndex % FORMULA_REFERENCE_COLORS.length]
                    : isFormulaSelection
                      ? FORMULA_REFERENCE_COLORS[formulaReferences.length % FORMULA_REFERENCE_COLORS.length]
                      : undefined;
                  const cellStyle = {
                    ...(cell?.backgroundColor ? { backgroundColor: cell.backgroundColor } : {}),
                    ...(referenceColor ? { "--formula-reference-color": referenceColor } : {}),
                  } as CSSProperties;
                  return (
                    <td
                      key={`${sheet.id}:${r}:${c}`}
                      data-cell={`${r},${c}`}
                      data-cell-kind={cell?.kind ?? "empty"}
                      data-formula-reference={referenceColor ? "true" : undefined}
                      role="gridcell"
                      aria-selected={isSelected}
                      tabIndex={isSelected ? 0 : -1}
                      style={cellStyle}
                      onMouseDown={(event) => {
                        if (event.shiftKey || formulaEditing) event.preventDefault();
                      }}
                      onPointerDown={(event) => handleFormulaPointerDown(event, { r, c })}
                      onPointerEnter={(event) => handleFormulaPointerEnter(event, { r, c })}
                      onPointerUp={(event) => handleFormulaPointerUp(event, { r, c })}
                      onFocus={() => {
                        if (!formulaEditing && !cellInRange({ r, c }, selectedRange)) {
                          dispatchWorkspace({ type: "selectCell", cell: { r, c }, sheetId: sheet.id });
                        }
                      }}
                      onClick={(event) => selectWithPointer(event, r, c)}
                      onDoubleClick={(event) => {
                        if ((event.target as HTMLElement).closest(".spreadsheet-cell-editor")) return;
                        event.preventDefault();
                        startEdit(r, c);
                      }}
                      onKeyDown={(event) => handleCellKeyDown(event, r, c)}
                      className={`spreadsheet-cell relative min-w-[90px] select-none border p-1.5 font-mono text-neutral-800 transition-colors dark:text-neutral-200 ${semanticCellClass(cell, timelineLabel)} ${isSelected && !formulaEditing ? "spreadsheet-cell-selected" : ""} ${showEditor ? "spreadsheet-cell-editing" : "cursor-cell"} ${isFillTarget ? "spreadsheet-cell-fill-target" : ""} ${referenceColor ? "spreadsheet-cell-formula-reference" : ""}`}
                    >
                      {showEditor ? (
                        <input
                          ref={inputRef}
                          type="text"
                          value={editValue}
                          onChange={(event) => updateEditValue(event.target.value, event.target.selectionStart)}
                          onSelect={(event) => {
                            if (formulaEditing) {
                              dispatchWorkspace({
                                type: "setFormulaCaret",
                                caretPosition: event.currentTarget.selectionStart ?? editValue.length,
                              });
                            }
                          }}
                          onBlur={() => {
                            if (formulaEditing) return;
                            if (skipNextBlurCommitRef.current) {
                              skipNextBlurCommitRef.current = false;
                              return;
                            }
                            const moveTo = moveAfterBlurRef.current;
                            moveAfterBlurRef.current = null;
                            commitEdit(moveTo ?? undefined);
                          }}
                          onKeyDown={(event) => handleEditingKeyDown(event, { r, c })}
                          aria-label={`第 ${r + 1} 列第 ${c + 1} 欄`}
                          className="spreadsheet-cell-editor w-full appearance-none select-text bg-transparent font-mono text-xs text-neutral-900 outline-none dark:text-white"
                        />
                      ) : <div className="truncate">{isEditing ? editValue : displayCell(r, c)}</div>}
                      {isFillHandleCell && !isEditing && !readOnly && !formulaEditing ? (
                        <button
                          type="button"
                          className="absolute -bottom-1 -right-1 z-20 h-3 w-3 cursor-crosshair rounded-sm border border-white bg-blue-600 shadow-sm dark:border-neutral-900"
                          aria-label="拖曳填滿"
                          onClick={(event) => event.stopPropagation()}
                          onPointerDown={(event) => {
                            event.preventDefault();
                            event.stopPropagation();
                            dispatchWorkspace({
                              type: "startFill",
                              range: selectedRange ?? { start: { r, c }, end: { r, c } },
                            });
                          }}
                        />
                      ) : null}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="mt-2 flex items-center gap-1 overflow-x-auto border-t border-neutral-100 pt-2 dark:border-neutral-800" role="tablist" aria-label="工作表">
        {workbook.sheets.map((candidate) => {
          const isActiveSheet = candidate.id === sheet.id;
          return (
            <button
              key={candidate.id}
              type="button"
              role="tab"
              aria-selected={isActiveSheet}
              aria-label={candidate.name}
              data-state={isActiveSheet ? "active" : "inactive"}
              style={{
                backgroundColor: isActiveSheet ? "#171717" : "#FFFFFF",
                color: isActiveSheet ? "#FFFFFF" : "#000000",
              }}
              onMouseDown={(event) => {
                if (formulaEditing) event.preventDefault();
              }}
              onClick={() => switchSheet(candidate.id)}
              onDoubleClick={() => isActiveSheet && renameSheet()}
              className={`spreadsheet-sheet-tab shrink-0 rounded-md px-3 py-1.5 text-[11px] font-medium ${isActiveSheet ? "spreadsheet-sheet-tab--active bg-[#171717] text-white" : "spreadsheet-sheet-tab--inactive bg-white text-black"}`}
            >
              <span
                className="spreadsheet-sheet-tab-label"
                style={{
                  color: isActiveSheet ? "#FFFFFF" : "#000000",
                  WebkitTextFillColor: isActiveSheet ? "#FFFFFF" : "#000000",
                }}
              >
                {candidate.name}
              </span>
            </button>
          );
        })}
        {!readOnly ? (
          <>
            <button type="button" onClick={addSheet} className="shrink-0 rounded p-1.5 text-neutral-500 hover:bg-neutral-100 dark:hover:bg-neutral-800" aria-label="新增工作表"><Plus className="h-3.5 w-3.5" /></button>
            <button type="button" onClick={renameSheet} className="shrink-0 rounded px-2 py-1 text-[10px] text-neutral-500 hover:bg-neutral-100 dark:hover:bg-neutral-800">重新命名</button>
            <button type="button" onClick={deleteSheet} disabled={workbook.sheets.length <= 1} className="inline-flex shrink-0 items-center gap-1 rounded px-2 py-1.5 text-[11px] font-medium text-rose-500 hover:bg-rose-50 disabled:opacity-30 dark:hover:bg-rose-950/30" aria-label="刪除目前工作表"><Trash2 className="h-3.5 w-3.5" />刪除分頁</button>
          </>
        ) : null}
      </div>
    </div>
  );
}
