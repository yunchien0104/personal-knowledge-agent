import type { SpreadsheetCellPosition, SpreadsheetRange } from "./spreadsheet-workbook.ts";

export * from "./spreadsheet-workbook.ts";

export type SpreadsheetWorkspaceMode = "inline" | "drawer" | "fullscreen";

export interface SpreadsheetFormulaEditingState {
  workbookId: string;
  sourceSheetId: string;
  sourceCell: SpreadsheetCellPosition;
  originalValue: string;
  draft: string;
  caretPosition: number;
  referencedRanges: Array<{ sheetId: string; range: SpreadsheetRange }>;
  selectingRange: { sheetId: string; range: SpreadsheetRange } | null;
  status: "editing";
}

export interface SpreadsheetWorkspaceState {
  mode: SpreadsheetWorkspaceMode;
  activeSheetId: string | null;
  selectedCell: SpreadsheetCellPosition | null;
  selectedCellsBySheet: Record<string, SpreadsheetCellPosition | null>;
  selectedRangesBySheet: Record<string, SpreadsheetRange | null>;
  editingCell: SpreadsheetCellPosition | null;
  editingSheetId: string | null;
  editingWorkbookId: string | null;
  editValue: string;
  formulaEditing: SpreadsheetFormulaEditingState | null;
  fillSource: SpreadsheetRange | null;
  fillTarget: SpreadsheetRange | null;
}

export type SpreadsheetWorkspaceAction =
  | { type: "openDrawer" }
  | { type: "enterFullscreen" }
  | { type: "exitFullscreen" }
  | { type: "closeWorkspace" }
  | { type: "switchSheet"; sheetId: string }
  | { type: "selectCell"; cell: SpreadsheetCellPosition; sheetId?: string }
  | { type: "selectRange"; range: SpreadsheetRange; sheetId: string }
  | { type: "startEdit"; cell: SpreadsheetCellPosition; value: string; sheetId: string; workbookId: string }
  | { type: "setEditValue"; value: string; caretPosition?: number }
  | { type: "setFormulaCaret"; caretPosition: number }
  | { type: "insertFormulaReference"; value: string; caretPosition: number; sheetId: string; range: SpreadsheetRange }
  | { type: "startFormulaRange"; sheetId: string; range: SpreadsheetRange }
  | { type: "updateFormulaRange"; sheetId: string; range: SpreadsheetRange }
  | { type: "finishFormulaRange" }
  | { type: "finishEdit" }
  | { type: "cancelEdit" }
  | { type: "startFill"; range: SpreadsheetRange }
  | { type: "updateFill"; range: SpreadsheetRange }
  | { type: "finishFill" };

export const INITIAL_SPREADSHEET_WORKSPACE_STATE: SpreadsheetWorkspaceState = {
  mode: "inline",
  activeSheetId: null,
  selectedCell: null,
  selectedCellsBySheet: {},
  selectedRangesBySheet: {},
  editingCell: null,
  editingSheetId: null,
  editingWorkbookId: null,
  editValue: "",
  formulaEditing: null,
  fillSource: null,
  fillTarget: null,
};

export const SPREADSHEET_WORKSPACE_LAYER_Z_INDEX = 2_147_483_000;

export function spreadsheetWorkspaceReducer(
  state: SpreadsheetWorkspaceState,
  action: SpreadsheetWorkspaceAction,
): SpreadsheetWorkspaceState {
  switch (action.type) {
    case "openDrawer":
      return { ...state, mode: "drawer" };
    case "enterFullscreen":
      return state.mode === "drawer" ? { ...state, mode: "fullscreen" } : state;
    case "exitFullscreen":
      return state.mode === "fullscreen" ? { ...state, mode: "drawer" } : state;
    case "closeWorkspace":
      return { ...state, mode: "inline" };
    case "switchSheet":
      return {
        ...state,
        activeSheetId: action.sheetId,
        selectedCell: state.selectedCellsBySheet[action.sheetId] ?? null,
        editingCell: state.formulaEditing ? state.editingCell : null,
        editingSheetId: state.formulaEditing ? state.editingSheetId : null,
        editingWorkbookId: state.formulaEditing ? state.editingWorkbookId : null,
        editValue: state.formulaEditing ? state.editValue : "",
        fillSource: null,
        fillTarget: null,
      };
    case "selectCell": {
      const sheetId = action.sheetId ?? state.activeSheetId;
      return {
        ...state,
        activeSheetId: sheetId ?? state.activeSheetId,
        selectedCell: action.cell,
        selectedCellsBySheet: sheetId
          ? { ...state.selectedCellsBySheet, [sheetId]: action.cell }
          : state.selectedCellsBySheet,
        selectedRangesBySheet: sheetId
          ? { ...state.selectedRangesBySheet, [sheetId]: { start: action.cell, end: action.cell } }
          : state.selectedRangesBySheet,
      };
    }
    case "selectRange":
      return {
        ...state,
        activeSheetId: action.sheetId,
        selectedCell: action.range.end,
        selectedCellsBySheet: { ...state.selectedCellsBySheet, [action.sheetId]: action.range.end },
        selectedRangesBySheet: { ...state.selectedRangesBySheet, [action.sheetId]: action.range },
      };
    case "startEdit": {
      const sheetId = action.sheetId;
      const formulaEditing = action.value.startsWith("=")
        ? {
          workbookId: action.workbookId,
          sourceSheetId: sheetId,
          sourceCell: action.cell,
          originalValue: action.value,
          draft: action.value,
          caretPosition: action.value.length,
          referencedRanges: [],
          selectingRange: null,
          status: "editing" as const,
        }
        : null;
      return {
        ...state,
        activeSheetId: sheetId ?? state.activeSheetId,
        selectedCell: action.cell,
        selectedCellsBySheet: sheetId
          ? { ...state.selectedCellsBySheet, [sheetId]: action.cell }
          : state.selectedCellsBySheet,
        selectedRangesBySheet: sheetId
          ? { ...state.selectedRangesBySheet, [sheetId]: { start: action.cell, end: action.cell } }
          : state.selectedRangesBySheet,
        editingCell: action.cell,
        editingSheetId: sheetId,
        editingWorkbookId: action.workbookId,
        editValue: action.value,
        formulaEditing,
      };
    }
    case "setEditValue": {
      const caretPosition = action.caretPosition ?? action.value.length;
      const formulaEditing = action.value.startsWith("=")
        ? state.formulaEditing
          ? { ...state.formulaEditing, draft: action.value, caretPosition }
          : state.editingCell && state.editingSheetId && state.editingWorkbookId
            ? {
              workbookId: state.editingWorkbookId,
              sourceSheetId: state.editingSheetId,
              sourceCell: state.editingCell,
              originalValue: state.editValue,
              draft: action.value,
              caretPosition,
              referencedRanges: [],
              selectingRange: null,
              status: "editing" as const,
            }
            : null
        : null;
      return { ...state, editValue: action.value, formulaEditing };
    }
    case "setFormulaCaret":
      return state.formulaEditing
        ? { ...state, formulaEditing: { ...state.formulaEditing, caretPosition: action.caretPosition } }
        : state;
    case "insertFormulaReference":
      return state.formulaEditing
        ? {
          ...state,
          editValue: action.value,
          formulaEditing: {
            ...state.formulaEditing,
            draft: action.value,
            caretPosition: action.caretPosition,
            referencedRanges: [
              ...state.formulaEditing.referencedRanges,
              { sheetId: action.sheetId, range: action.range },
            ],
            selectingRange: null,
          },
        }
        : state;
    case "startFormulaRange":
    case "updateFormulaRange":
      return state.formulaEditing
        ? {
          ...state,
          formulaEditing: {
            ...state.formulaEditing,
            selectingRange: { sheetId: action.sheetId, range: action.range },
          },
        }
        : state;
    case "finishFormulaRange":
      return state.formulaEditing
        ? { ...state, formulaEditing: { ...state.formulaEditing, selectingRange: null } }
        : state;
    case "finishEdit":
      return {
        ...state,
        activeSheetId: state.formulaEditing?.sourceSheetId ?? state.activeSheetId,
        selectedCell: state.formulaEditing?.sourceCell ?? state.selectedCell,
        editingCell: null,
        editingSheetId: null,
        editingWorkbookId: null,
        editValue: "",
        formulaEditing: null,
      };
    case "cancelEdit":
      return {
        ...state,
        activeSheetId: state.formulaEditing?.sourceSheetId ?? state.activeSheetId,
        selectedCell: state.formulaEditing?.sourceCell ?? state.selectedCell,
        editingCell: null,
        editingSheetId: null,
        editingWorkbookId: null,
        editValue: "",
        formulaEditing: null,
      };
    case "startFill":
      return { ...state, fillSource: action.range, fillTarget: action.range };
    case "updateFill":
      return { ...state, fillTarget: action.range };
    case "finishFill":
      return { ...state, fillSource: null, fillTarget: null };
  }
}
