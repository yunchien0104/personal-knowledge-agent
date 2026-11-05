import { Node, mergeAttributes, type Editor } from "@tiptap/core";
import { isHistoryTransaction } from "@tiptap/pm/history";
import { Fragment, type Node as ProseMirrorNode } from "@tiptap/pm/model";
import { AllSelection, NodeSelection, Plugin, PluginKey, TextSelection } from "@tiptap/pm/state";
import { ReactNodeViewRenderer, NodeViewWrapper } from "@tiptap/react";
import type { NodeViewProps } from "@tiptap/react";
import { ArrowLeft, Maximize2, PanelRightOpen, Trash2 } from "lucide-react";
import { useEffect, useReducer, useRef } from "react";
import { createPortal } from "react-dom";

import {
  createSpreadsheetTemplate,
  INITIAL_SPREADSHEET_WORKSPACE_STATE,
  normalizeSpreadsheetData,
  SPREADSHEET_WORKSPACE_LAYER_Z_INDEX,
  spreadsheetWorkspaceReducer,
  type SpreadsheetData,
} from "@/lib/spreadsheet-workspace";
import { SpreadsheetComponent } from "./SpreadsheetComponent";

export const DEFAULT_SPREADSHEET_DATA: SpreadsheetData = createSpreadsheetTemplate("blank");

declare module "@tiptap/core" {
  interface Commands<ReturnType> {
    spreadsheetBlock: {
      insertSpreadsheet: (data?: SpreadsheetData) => ReturnType;
      deleteSpreadsheetAt: (position: number) => ReturnType;
    };
  }
}

const SPREADSHEET_DATA_UPDATE_META = "spreadsheet:data-update";
const SPREADSHEET_EXPLICIT_DELETE_META = "spreadsheet:explicit-delete";
const spreadsheetProtectionPluginKey = new PluginKey("spreadsheetProtection");

function spreadsheetIdentity(data: unknown) {
  if (data && typeof data === "object" && "workbookId" in data && typeof data.workbookId === "string") {
    return `workbook:${data.workbookId}`;
  }
  return `legacy:${JSON.stringify(data)}`;
}

function spreadsheetIdentityCounts(doc: ProseMirrorNode) {
  const counts = new Map<string, number>();
  doc.descendants((node) => {
    if (node.type.name !== "spreadsheetBlock") return;
    const identity = spreadsheetIdentity(node.attrs.data);
    counts.set(identity, (counts.get(identity) ?? 0) + 1);
  });
  return counts;
}

export function updateSpreadsheetNodeData(editor: Editor, position: number, data: SpreadsheetData) {
  const normalized = normalizeSpreadsheetData(data);
  return editor.commands.command(({ state, tr }) => {
    const node = state.doc.nodeAt(position);
    if (node?.type.name !== "spreadsheetBlock") return false;
    tr.setNodeMarkup(position, undefined, { ...node.attrs, data: normalized });
    tr.setMeta(SPREADSHEET_DATA_UPDATE_META, true);
    return true;
  });
}

export function requestSpreadsheetDeletion(
  editor: Editor,
  position: number,
  confirmDelete: (message: string) => boolean = (message) => window.confirm(message),
) {
  const node = editor.state.doc.nodeAt(position);
  if (node?.type.name !== "spreadsheetBlock") return false;
  if (!confirmDelete("確定刪除這個完整 Workbook？此操作可使用 Undo 復原。")) return false;
  return editor.commands.deleteSpreadsheetAt(position);
}

function selectionTouchesSpreadsheet(editor: Editor) {
  const { selection, doc } = editor.state;
  if (selection instanceof NodeSelection && selection.node.type.name === "spreadsheetBlock") return true;
  let touchesSpreadsheet = false;
  doc.nodesBetween(selection.from, selection.to, (node) => {
    if (node.type.name === "spreadsheetBlock") touchesSpreadsheet = true;
    return !touchesSpreadsheet;
  });
  return touchesSpreadsheet;
}

function adjacentSpreadsheet(editor: Editor, direction: "backward" | "forward") {
  const { selection, doc } = editor.state;
  if (!selection.empty || !(selection instanceof TextSelection)) return false;
  const { $from } = selection;
  if (!$from.parent.isTextblock) return false;
  if (direction === "backward" && $from.parentOffset === 0) {
    return doc.resolve($from.before($from.depth)).nodeBefore?.type.name === "spreadsheetBlock";
  }
  if (direction === "forward" && $from.parentOffset === $from.parent.content.size) {
    return doc.resolve($from.after($from.depth)).nodeAfter?.type.name === "spreadsheetBlock";
  }
  return false;
}

export function handleProtectedSpreadsheetDeletion(editor: Editor, direction: "backward" | "forward") {
  const { selection, doc, schema } = editor.state;
  if (selection instanceof AllSelection) {
    const content: ProseMirrorNode[] = [];
    doc.forEach((node) => {
      if (node.type.name !== "spreadsheetBlock") return;
      if (content.length === 0 || content.at(-1)?.type.name !== "paragraph") {
        content.push(schema.nodes.paragraph.create());
      }
      content.push(node);
      content.push(schema.nodes.paragraph.create());
    });
    if (content.length === 0) return false;
    return editor.commands.command(({ tr }) => {
      tr.replaceWith(0, tr.doc.content.size, Fragment.fromArray(content));
      tr.setSelection(TextSelection.create(tr.doc, 1));
      return true;
    });
  }
  if (selectionTouchesSpreadsheet(editor) || adjacentSpreadsheet(editor, direction)) return true;
  return false;
}

export function createSpreadsheetProtectionPlugin() {
  return new Plugin({
    key: spreadsheetProtectionPluginKey,
    filterTransaction(transaction, state) {
      if (transaction.getMeta(SPREADSHEET_EXPLICIT_DELETE_META)) return true;
      if (transaction.getMeta(SPREADSHEET_DATA_UPDATE_META)) return true;
      const before = spreadsheetIdentityCounts(state.doc);
      const after = spreadsheetIdentityCounts(transaction.doc);
      const beforeCount = Array.from(before.values()).reduce((total, count) => total + count, 0);
      const afterCount = Array.from(after.values()).reduce((total, count) => total + count, 0);
      if (isHistoryTransaction(transaction) && afterCount >= beforeCount) return true;
      for (const [identity, count] of before) {
        if ((after.get(identity) ?? 0) < count) {
          if (isHistoryTransaction(transaction)) {
            const historyMeta = transaction.getMeta("history$") as { redo?: boolean } | undefined;
            return historyMeta?.redo === true;
          }
          return false;
        }
      }
      return true;
    },
  });
}

export function insertParagraphAdjacentToSpreadsheet(
  editor: Editor,
  position: number,
  nodeSize: number,
  direction: "before" | "after",
) {
  const paragraph = editor.schema.nodes.paragraph?.create();
  if (!paragraph) return false;
  const insertAt = direction === "before" ? position : position + nodeSize;
  const transaction = editor.state.tr.insert(insertAt, paragraph);
  transaction.setSelection(TextSelection.create(transaction.doc, insertAt + 1));
  editor.view.dispatch(transaction.scrollIntoView());
  editor.commands.focus();
  return true;
}

function SpreadsheetNodeView(props: NodeViewProps) {
  const data = props.node.attrs.data as SpreadsheetData;
  const [workspaceState, dispatchWorkspace] = useReducer(
    spreadsheetWorkspaceReducer,
    INITIAL_SPREADSHEET_WORKSPACE_STATE,
  );
  const dialogRef = useRef<HTMLDivElement | null>(null);
  const expandButtonRef = useRef<HTMLButtonElement | null>(null);
  const workspaceOpen = workspaceState.mode !== "inline";

  const updateData = (newData: SpreadsheetData) => {
    const position = props.getPos();
    if (typeof position === "number") updateSpreadsheetNodeData(props.editor, position, newData);
  };

  const deleteWorkbook = () => {
    const position = props.getPos();
    if (typeof position === "number") requestSpreadsheetDeletion(props.editor, position);
  };

  useEffect(() => {
    if (!workspaceOpen) return;
    const previousActiveElement = document.activeElement as HTMLElement | null;
    const returnFocusElement = expandButtonRef.current ?? previousActiveElement;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";

    const dialog = dialogRef.current;
    dialog?.querySelector<HTMLElement>("button")?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        dispatchWorkspace({ type: "closeWorkspace" });
        return;
      }
      if (event.key !== "Tab" || !dialog) return;
      const focusable = Array.from(
        dialog.querySelectorAll<HTMLElement>(
          'button:not([disabled]), input:not([disabled]), [tabindex]:not([tabindex="-1"])',
        ),
      );
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable.at(-1);
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      returnFocusElement?.focus();
    };
  }, [workspaceOpen]);

  const workspaceDialog = workspaceOpen ? createPortal(
    <div
      className="spreadsheet-workspace-layer print:hidden"
      style={{ zIndex: SPREADSHEET_WORKSPACE_LAYER_Z_INDEX }}
      data-workspace-mode={workspaceState.mode}
    >
      <button
        type="button"
        className="spreadsheet-workspace-backdrop"
        onClick={() => dispatchWorkspace({ type: "closeWorkspace" })}
        aria-label="關閉計算表格並返回筆記"
        tabIndex={-1}
      />
      <div
        ref={dialogRef}
        role="dialog"
        aria-modal="true"
        aria-label={`${data.metadata?.templateLabel || "計算表格"}工作區`}
        data-mode={workspaceState.mode}
        className="spreadsheet-workspace-panel flex flex-col border-l border-zinc-200 bg-zinc-50 shadow-2xl dark:border-zinc-700 dark:bg-zinc-950"
      >
        <header className="sticky top-0 z-20 flex shrink-0 flex-wrap items-center justify-between gap-2 border-b border-zinc-200 bg-white px-3 py-3 dark:border-zinc-800 dark:bg-zinc-900 sm:px-4">
          <div className="min-w-0">
            <h2 className="truncate text-sm font-semibold text-zinc-900 dark:text-zinc-100">{data.metadata?.templateLabel || "計算表格"}工作區</h2>
            <p className="hidden text-[11px] text-zinc-500 dark:text-zinc-400 sm:block">與筆記內嵌表格共用同一份資料</p>
          </div>
          <div className="flex shrink-0 items-center gap-1.5" role="group" aria-label="工作區顯示模式">
            {workspaceState.mode === "drawer" ? (
              <button
                type="button"
                onClick={() => dispatchWorkspace({ type: "enterFullscreen" })}
                className="inline-flex min-h-10 items-center gap-1.5 rounded-md border border-zinc-200 px-2.5 text-xs font-medium text-zinc-700 hover:bg-zinc-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-500 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
              ><Maximize2 className="h-4 w-4" />全螢幕</button>
            ) : (
              <button
                type="button"
                onClick={() => dispatchWorkspace({ type: "exitFullscreen" })}
                className="inline-flex min-h-10 items-center gap-1.5 rounded-md border border-zinc-200 px-2.5 text-xs font-medium text-zinc-700 hover:bg-zinc-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-500 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
              ><PanelRightOpen className="h-4 w-4" />縮回 Drawer</button>
            )}
            <button
              type="button"
              onClick={() => dispatchWorkspace({ type: "closeWorkspace" })}
              className="inline-flex min-h-10 items-center gap-1.5 rounded-md bg-zinc-900 px-2.5 text-xs font-semibold text-white hover:bg-zinc-700 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-500 dark:bg-zinc-100 dark:text-zinc-900 dark:hover:bg-white"
              aria-label="關閉計算表格並返回筆記"
            ><ArrowLeft className="h-4 w-4" />返回筆記</button>
            <button
              type="button"
              onClick={deleteWorkbook}
              className="inline-flex min-h-10 items-center gap-1.5 rounded-md border border-rose-200 px-2.5 text-xs font-medium text-rose-600 hover:bg-rose-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-rose-500 dark:border-rose-900 dark:text-rose-300 dark:hover:bg-rose-950/40"
              aria-label="刪除這個完整 Workbook"
            ><Trash2 className="h-4 w-4" />刪除 Workbook</button>
          </div>
        </header>
        <div className="flex-1 overflow-auto p-3 sm:p-5">
          <SpreadsheetComponent
            data={data}
            onChange={updateData}
            workspaceState={workspaceState}
            dispatchWorkspace={dispatchWorkspace}
            readOnly={!props.editor.isEditable}
            active
          />
        </div>
      </div>
    </div>,
    document.body,
  ) : null;

  return (
    <NodeViewWrapper className="spreadsheet-node-view my-4" contentEditable={false}>
      <div className="mb-1 flex justify-end gap-1 print:hidden" aria-label="計算表格段落操作">
        <button
          type="button"
          className="rounded px-2 py-1 text-[11px] text-neutral-500 hover:bg-neutral-100 dark:text-neutral-400 dark:hover:bg-neutral-800"
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => {
            const position = props.getPos();
            if (typeof position === "number") {
              insertParagraphAdjacentToSpreadsheet(props.editor, position, props.node.nodeSize, "before");
            }
          }}
        >
          上方新增段落
        </button>
        <button
          type="button"
          className="rounded px-2 py-1 text-[11px] text-neutral-500 hover:bg-neutral-100 dark:text-neutral-400 dark:hover:bg-neutral-800"
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => {
            const position = props.getPos();
            if (typeof position === "number") {
              insertParagraphAdjacentToSpreadsheet(props.editor, position, props.node.nodeSize, "after");
            }
          }}
        >
          下方新增段落
        </button>
        <button
          type="button"
          className="rounded px-2 py-1 text-[11px] text-rose-600 hover:bg-rose-50 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-rose-500 dark:text-rose-300 dark:hover:bg-rose-950/40"
          onMouseDown={(event) => event.preventDefault()}
          onClick={deleteWorkbook}
          aria-label="刪除這個完整 Workbook"
        >
          <Trash2 className="mr-1 inline h-3 w-3" />刪除 Workbook
        </button>
      </div>
      <SpreadsheetComponent
        data={data}
        onChange={updateData}
        workspaceState={workspaceState}
        dispatchWorkspace={dispatchWorkspace}
        readOnly={!props.editor.isEditable}
        active={!workspaceOpen}
        onExpand={() => dispatchWorkspace({ type: "openDrawer" })}
        expandButtonRef={expandButtonRef}
      />
      {workspaceDialog}
    </NodeViewWrapper>
  );
}

export const SpreadsheetNode = Node.create({
  name: "spreadsheetBlock",
  group: "block",
  atom: true,
  isolating: true,

  addAttributes() {
    return {
      data: {
        default: {
          rows: 3,
          cols: 3,
          cells: {},
        },
      },
    };
  },

  parseHTML() {
    return [
      {
        tag: "div[data-type=\"spreadsheet-block\"]",
      },
    ];
  },

  renderHTML({ HTMLAttributes }) {
    return ["div", mergeAttributes(HTMLAttributes, { "data-type": "spreadsheet-block" })];
  },

  addCommands() {
    return {
      insertSpreadsheet: (data = DEFAULT_SPREADSHEET_DATA) => ({ commands, dispatch, state, tr }) => {
        const { $from, empty } = state.selection;
        const spreadsheet = this.type.create({ data });
        const paragraph = state.schema.nodes.paragraph.create();

        // Keep the initial empty paragraph as a real cursor target before the
        // spreadsheet instead of replacing the document with a single atom.
        if (empty && $from.depth === 1 && $from.parent.isTextblock && $from.parent.content.size === 0) {
          const insertAt = $from.after(1);
          if (dispatch) {
            tr.insert(insertAt, Fragment.fromArray([spreadsheet, paragraph]));
            tr.setSelection(TextSelection.create(tr.doc, insertAt + spreadsheet.nodeSize + 1));
          }
          return true;
        }

        // Tiptap splits a populated textblock at the current selection. The
        // explicit trailing paragraph guarantees a writable cursor target.
        return commands.insertContent([
          { type: this.name, attrs: { data } },
          { type: "paragraph" },
        ]);
      },
      deleteSpreadsheetAt: (position: number) => ({ state, tr, dispatch }) => {
        const node = state.doc.nodeAt(position);
        if (node?.type !== this.type) return false;
        if (dispatch) {
          tr.delete(position, position + node.nodeSize);
          tr.setMeta(SPREADSHEET_EXPLICIT_DELETE_META, true);
        }
        return true;
      },
    };
  },

  addKeyboardShortcuts() {
    return {
      Backspace: () => handleProtectedSpreadsheetDeletion(this.editor, "backward"),
      Delete: () => handleProtectedSpreadsheetDeletion(this.editor, "forward"),
    };
  },

  addProseMirrorPlugins() {
    return [createSpreadsheetProtectionPlugin()];
  },
});

export const SpreadsheetExtension = SpreadsheetNode.extend({
  addNodeView() {
    return ReactNodeViewRenderer(SpreadsheetNodeView);
  },
});
