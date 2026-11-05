import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

import { Editor, type JSONContent } from "@tiptap/core";
import { closeHistory } from "@tiptap/pm/history";
import { AllSelection, EditorState, NodeSelection } from "@tiptap/pm/state";

import { createCoreNotesEditorExtensions } from "../src/components/notes/editor-extensions";
import {
  handleProtectedSpreadsheetDeletion,
  insertParagraphAdjacentToSpreadsheet,
  requestSpreadsheetDeletion,
  SpreadsheetNode,
  updateSpreadsheetNodeData,
} from "../src/components/notes/SpreadsheetExtension";
import { createNoteAutosaveQueue, type NoteSaveRequest } from "../src/lib/note-autosave";
import {
  createSpreadsheetTemplate,
  getActiveSpreadsheetSheet,
  INITIAL_SPREADSHEET_WORKSPACE_STATE,
  SPREADSHEET_WORKSPACE_LAYER_Z_INDEX,
  spreadsheetWorkspaceReducer,
  SPREADSHEET_TEMPLATES,
  type SpreadsheetData,
  updateSpreadsheetCell,
} from "../src/lib/spreadsheet-workspace";

const spreadsheetData: SpreadsheetData = {
  rows: 2,
  cols: 2,
  cells: {
    "0,0": { raw: "項目" },
    "0,1": { raw: "數值" },
    "1,0": { raw: "測試" },
    "1,1": { raw: "42" },
  },
};

function createSpreadsheetEditor(content?: JSONContent, onUpdate?: (json: JSONContent) => void) {
  const editor = new Editor({
    extensions: [...createCoreNotesEditorExtensions(), SpreadsheetNode],
    content: content ?? { type: "doc", content: [{ type: "paragraph" }] },
    onUpdate: ({ editor }) => onUpdate?.(editor.getJSON()),
  });
  // Core-only tests do not mount EditorContent, so install the same extension
  // plugins that the browser view receives before exercising transactions.
  editor.view.updateState(EditorState.create({
    doc: editor.state.doc,
    schema: editor.schema,
    plugins: editor.extensionManager.plugins,
  }));
  return editor;
}

function spreadsheetPositions(editor: Editor) {
  const positions: Array<{ position: number; nodeSize: number }> = [];
  editor.state.doc.descendants((node, position) => {
    if (node.type.name === "spreadsheetBlock") positions.push({ position, nodeSize: node.nodeSize });
  });
  return positions;
}

function spreadsheetNodes(editor: Editor) {
  return spreadsheetPositions(editor).map(({ position }) => editor.state.doc.nodeAt(position)!);
}

test("spreadsheet insertion keeps writable paragraphs before and after the atom", () => {
  const updates: JSONContent[] = [];
  const editor = createSpreadsheetEditor(undefined, (json) => updates.push(json));
  try {
    assert.equal(editor.commands.insertSpreadsheet(spreadsheetData), true);
    assert.deepEqual(editor.getJSON().content?.map((node) => node.type), [
      "paragraph",
      "spreadsheetBlock",
      "paragraph",
    ]);

    const [spreadsheet] = spreadsheetPositions(editor);
    assert.ok(spreadsheet);
    assert.equal(editor.commands.setNodeSelection(spreadsheet.position), true);
    assert.ok(editor.state.selection instanceof NodeSelection, "spreadsheet remains selectable");
    assert.equal(updateSpreadsheetNodeData(editor, spreadsheet.position, {
      ...spreadsheetData,
      cells: { ...spreadsheetData.cells, "1,1": { raw: "84" } },
    }), true);
    assert.match(JSON.stringify(editor.getJSON()), /84/);

    assert.equal(editor.commands.setTextSelection(1), true);
    assert.equal(editor.commands.insertContent({ type: "text", text: "上方文字" }), true);
    const trailingParagraphPosition = spreadsheetPositions(editor)[0].position + spreadsheet.nodeSize + 1;
    assert.equal(editor.commands.setTextSelection(trailingParagraphPosition), true);
    assert.equal(editor.commands.insertContent({ type: "text", text: "下方文字" }), true);
    assert.match(editor.getText({ blockSeparator: "\n" }), /上方文字[\s\S]*下方文字/);
    assert.ok(updates.length >= 3, "text and spreadsheet changes emit editor updates for autosave");
  } finally {
    editor.destroy();
  }
});

test("cell value and formula updates use exact-node transactions without removing the workbook", () => {
  for (const template of SPREADSHEET_TEMPLATES) {
    const updates: JSONContent[] = [];
    const editor = createSpreadsheetEditor(undefined, (json) => updates.push(json));
    try {
      assert.equal(editor.commands.insertSpreadsheet(createSpreadsheetTemplate(template.id)), true);
      const [{ position }] = spreadsheetPositions(editor);
      const initial = spreadsheetNodes(editor)[0].attrs.data as SpreadsheetData;
      const activeSheet = getActiveSpreadsheetSheet(initial);
      const withValue = updateSpreadsheetCell(initial, { r: 1, c: 1 }, "123", activeSheet.id);
      assert.equal(updateSpreadsheetNodeData(editor, position, withValue), true);
      assert.equal(spreadsheetPositions(editor).length, 1, `${template.id} value update must retain its node`);
      assert.equal(getActiveSpreadsheetSheet(spreadsheetNodes(editor)[0].attrs.data as SpreadsheetData).cells["1,1"].raw, "123");

      const withFormula = updateSpreadsheetCell(
        spreadsheetNodes(editor)[0].attrs.data as SpreadsheetData,
        { r: 1, c: 2 },
        "=B2*2",
        activeSheet.id,
      );
      assert.equal(updateSpreadsheetNodeData(editor, position, withFormula), true);
      assert.equal(spreadsheetPositions(editor).length, 1, `${template.id} formula update must retain its node`);
      assert.equal(getActiveSpreadsheetSheet(spreadsheetNodes(editor)[0].attrs.data as SpreadsheetData).cells["1,2"].raw, "=B2*2");
      assert.ok(updates.length >= 3, "insert and both cell transactions must reach autosave onUpdate");
    } finally {
      editor.destroy();
    }
  }
});

test("transaction protection blocks Backspace, Delete, node deletion, cut, and paste replacement", () => {
  const editor = createSpreadsheetEditor({
    type: "doc",
    content: [
      { type: "paragraph", content: [{ type: "text", text: "before" }] },
      { type: "spreadsheetBlock", attrs: { data: createSpreadsheetTemplate("blank") } },
      { type: "paragraph", content: [{ type: "text", text: "after" }] },
    ],
  });
  try {
    const [{ position, nodeSize }] = spreadsheetPositions(editor);
    const original = editor.getJSON();

    editor.view.dispatch(editor.state.tr.delete(position, position + nodeSize));
    assert.deepEqual(editor.getJSON(), original, "raw delete transaction must be rejected");

    assert.equal(editor.commands.setNodeSelection(position), true);
    editor.commands.deleteSelection();
    assert.equal(spreadsheetPositions(editor).length, 1, "selected workbook must survive Delete");

    assert.equal(editor.commands.setTextSelection(position + nodeSize + 1), true);
    assert.equal(handleProtectedSpreadsheetDeletion(editor, "backward"), true);
    assert.equal(spreadsheetPositions(editor).length, 1, "Backspace at the trailing boundary must be blocked");

    assert.equal(editor.commands.setTextSelection(position - 1), true);
    assert.equal(handleProtectedSpreadsheetDeletion(editor, "forward"), true);
    assert.equal(spreadsheetPositions(editor).length, 1, "Delete at the leading boundary must be blocked");

    editor.view.dispatch(editor.state.tr.delete(0, editor.state.doc.content.size));
    assert.equal(spreadsheetPositions(editor).length, 1, "cut-style range deletion must be rejected");

    const replacement = editor.schema.nodes.paragraph.create(undefined, editor.schema.text("replacement"));
    editor.view.dispatch(editor.state.tr.replaceWith(0, editor.state.doc.content.size, replacement));
    assert.equal(spreadsheetPositions(editor).length, 1, "paste replacement must not overwrite a workbook");
  } finally {
    editor.destroy();
  }
});

test("select-all deletion removes text but preserves every workbook", () => {
  const editor = createSpreadsheetEditor({
    type: "doc",
    content: [
      { type: "paragraph", content: [{ type: "text", text: "remove me" }] },
      { type: "spreadsheetBlock", attrs: { data: createSpreadsheetTemplate("blank") } },
      { type: "paragraph", content: [{ type: "text", text: "remove me too" }] },
      { type: "spreadsheetBlock", attrs: { data: createSpreadsheetTemplate("dcf") } },
    ],
  });
  try {
    editor.view.dispatch(editor.state.tr.setSelection(new AllSelection(editor.state.doc)));
    assert.equal(handleProtectedSpreadsheetDeletion(editor, "backward"), true);
    assert.equal(spreadsheetPositions(editor).length, 2);
    assert.doesNotMatch(editor.getText(), /remove me/);
  } finally {
    editor.destroy();
  }
});

test("only confirmed explicit deletion removes the targeted workbook and remains undoable", () => {
  const editor = createSpreadsheetEditor();
  try {
    assert.equal(editor.commands.insertSpreadsheet(createSpreadsheetTemplate("blank")), true);
    assert.equal(editor.commands.insertSpreadsheet(createSpreadsheetTemplate("market_sizing")), true);
    const [first, second] = spreadsheetPositions(editor);
    const secondId = (editor.state.doc.nodeAt(second.position)?.attrs.data as SpreadsheetData & { workbookId?: string }).workbookId;
    const secondData = editor.state.doc.nodeAt(second.position)?.attrs.data as SpreadsheetData;
    const secondSheet = getActiveSpreadsheetSheet(secondData);
    assert.equal(updateSpreadsheetNodeData(
      editor,
      second.position,
      updateSpreadsheetCell(secondData, { r: 1, c: 1 }, "isolated", secondSheet.id),
    ), true);

    const beforeCancel = editor.getJSON();
    assert.equal(requestSpreadsheetDeletion(editor, first.position, () => false), false);
    assert.equal(spreadsheetPositions(editor).length, 2, "cancel must not dispatch a transaction");
    assert.deepEqual(editor.getJSON(), beforeCancel);

    editor.view.dispatch(closeHistory(editor.state.tr));
    assert.equal(requestSpreadsheetDeletion(editor, first.position, () => true), true);
    assert.equal(spreadsheetPositions(editor).length, 1);
    assert.equal((spreadsheetNodes(editor)[0].attrs.data as SpreadsheetData & { workbookId?: string }).workbookId, secondId);
    assert.equal(getActiveSpreadsheetSheet(spreadsheetNodes(editor)[0].attrs.data as SpreadsheetData).cells["1,1"].raw, "isolated");

    assert.equal(editor.commands.undo(), true);
    assert.equal(spreadsheetPositions(editor).length, 2, "explicit deletion must be undoable");
    assert.equal(editor.commands.redo(), true);
    assert.equal(spreadsheetPositions(editor).length, 1, "explicit deletion must be redoable");
  } finally {
    editor.destroy();
  }
});

test("undo cannot bypass protected deletion, while cell edit history remains available", () => {
  const editor = createSpreadsheetEditor();
  try {
    assert.equal(editor.commands.insertSpreadsheet(createSpreadsheetTemplate("blank")), true);
    assert.equal(editor.commands.undo(), true);
    assert.equal(spreadsheetPositions(editor).length, 1, "undoing insertion must not bypass explicit Workbook deletion");

    const [{ position }] = spreadsheetPositions(editor);
    const data = editor.state.doc.nodeAt(position)?.attrs.data as SpreadsheetData;
    const sheet = getActiveSpreadsheetSheet(data);
    editor.view.dispatch(closeHistory(editor.state.tr));
    assert.equal(updateSpreadsheetNodeData(
      editor,
      position,
      updateSpreadsheetCell(data, { r: 1, c: 1 }, "history-value", sheet.id),
    ), true);
    assert.equal(editor.commands.undo(), true);
    assert.notEqual(
      getActiveSpreadsheetSheet(spreadsheetNodes(editor)[0].attrs.data as SpreadsheetData).cells["1,1"]?.raw,
      "history-value",
    );
  } finally {
    editor.destroy();
  }
});

test("plain paragraphs around a spreadsheet remain unindented paragraph nodes", () => {
  const editor = createSpreadsheetEditor();
  try {
    assert.equal(editor.commands.insertSpreadsheet(spreadsheetData), true);
    const content = editor.getJSON().content ?? [];
    const surroundingParagraphs = [content[0], content[2]];
    for (const node of surroundingParagraphs) {
      assert.equal(node.type, "paragraph");
      assert.equal(node.attrs, undefined, "plain paragraphs must not carry hidden indent attributes");
    }
    assert.equal(editor.isActive("bulletList"), false);
    assert.equal(editor.isActive("orderedList"), false);
    assert.equal(editor.isActive("taskList"), false);
    assert.equal(
      editor.extensionManager.extensions.some((extension) => extension.name.toLowerCase().includes("indent")),
      false,
      "the editor must not inject a paragraph indentation extension",
    );
  } finally {
    editor.destroy();
  }
});

test("spreadsheet JSON passes through autosave without losing neighboring text", async () => {
  const editor = createSpreadsheetEditor({
    type: "doc",
    content: [{ type: "paragraph", content: [{ type: "text", text: "before" }] }],
  });
  try {
    editor.commands.setTextSelection(7);
    editor.commands.insertSpreadsheet(spreadsheetData);
    editor.commands.insertContent({ type: "text", text: "after" });
    const contentJson = editor.getJSON();
    const calls: NoteSaveRequest[] = [];
    const queue = createNoteAutosaveQueue({
      initialDraft: { title: "Spreadsheet", contentJson: null, plainText: "" },
      initialRevision: 7,
      onConflict() {},
      onError() {},
      onRevision() {},
      onStatus() {},
      async save(request) {
        calls.push(request);
        return { status: "saved", bodyRevision: 8 };
      },
    });
    queue.update({
      title: "Spreadsheet",
      contentJson,
      plainText: editor.getText({ blockSeparator: "\n" }),
    });
    await queue.requestSave("autosave");

    assert.equal(calls.length, 1);
    assert.deepEqual(calls[0].contentJson, contentJson);
    assert.match(JSON.stringify(calls[0].contentJson), /spreadsheetBlock/);
    assert.match(calls[0].plainText, /before[\s\S]*after/);
  } finally {
    editor.destroy();
  }
});

test("multiple spreadsheets serialize, restore, and participate in undo/redo", () => {
  const editor = createSpreadsheetEditor({
    type: "doc",
    content: [{ type: "paragraph", content: [{ type: "text", text: "開始" }] }],
  });
  try {
    editor.commands.setTextSelection(3);
    assert.equal(editor.commands.insertSpreadsheet(spreadsheetData), true);
    assert.equal(editor.commands.insertSpreadsheet({ ...spreadsheetData, cells: { "0,0": { raw: "第二個" } } }), true);
    assert.equal(spreadsheetPositions(editor).length, 2);

    const serialized = editor.getJSON();
    assert.match(JSON.stringify(serialized), /第二個/);

    editor.view.dispatch(closeHistory(editor.state.tr));
    const second = spreadsheetPositions(editor)[1];
    const secondData = editor.state.doc.nodeAt(second.position)?.attrs.data as SpreadsheetData;
    const secondSheet = getActiveSpreadsheetSheet(secondData);
    assert.equal(updateSpreadsheetNodeData(
      editor,
      second.position,
      updateSpreadsheetCell(secondData, { r: 1, c: 1 }, "undo-marker", secondSheet.id),
    ), true);
    assert.equal(editor.commands.undo(), true);
    assert.equal(spreadsheetPositions(editor).length, 2, "undoing a cell edit must not delete a workbook");
    assert.notEqual(
      getActiveSpreadsheetSheet(spreadsheetNodes(editor)[1].attrs.data as SpreadsheetData).cells["1,1"]?.raw,
      "undo-marker",
    );
    assert.equal(editor.commands.redo(), true);
    assert.equal(getActiveSpreadsheetSheet(spreadsheetNodes(editor)[1].attrs.data as SpreadsheetData).cells["1,1"].raw, "undo-marker");

    const restored = createSpreadsheetEditor(editor.getJSON());
    try {
      assert.deepEqual(restored.getJSON(), editor.getJSON());
      assert.equal(spreadsheetPositions(restored).length, 2);
    } finally {
      restored.destroy();
    }
  } finally {
    editor.destroy();
  }
});

test("legacy adjacent spreadsheets can create independent paragraphs on either side", () => {
  const editor = createSpreadsheetEditor({
    type: "doc",
    content: [
      { type: "spreadsheetBlock", attrs: { data: spreadsheetData } },
      { type: "spreadsheetBlock", attrs: { data: spreadsheetData } },
    ],
  });
  try {
    const first = spreadsheetPositions(editor)[0];
    assert.equal(insertParagraphAdjacentToSpreadsheet(editor, first.position, first.nodeSize, "before"), true);
    const last = spreadsheetPositions(editor).at(-1);
    assert.ok(last);
    assert.equal(insertParagraphAdjacentToSpreadsheet(editor, last.position, last.nodeSize, "after"), true);
    const nodeTypes = editor.getJSON().content?.map((node) => node.type) ?? [];
    assert.deepEqual(nodeTypes.slice(0, 4), ["paragraph", "spreadsheetBlock", "spreadsheetBlock", "paragraph"]);
    assert.equal(nodeTypes.filter((type) => type === "spreadsheetBlock").length, 2);
  } finally {
    editor.destroy();
  }
});

test("all workspace templates create independent editable workbook data", () => {
  assert.deepEqual(SPREADSHEET_TEMPLATES.map((template) => template.id), [
    "blank",
    "three_statement",
    "dcf",
    "sensitivity",
    "market_sizing",
  ]);

  for (const template of SPREADSHEET_TEMPLATES) {
    const first = createSpreadsheetTemplate(template.id);
    const second = createSpreadsheetTemplate(template.id);
    assert.notEqual(first, second, `${template.label} must return fresh node data`);
    assert.notEqual(first.sheets, second.sheets, `${template.label} sheets must not be shared`);
    assert.notEqual(first.sheets[0].cells, second.sheets[0].cells, `${template.label} cells must not be shared`);
    assert.ok(first.sheets[0].rows > 0 && first.sheets[0].cols > 0);
    assert.equal(first.metadata?.templateId, template.id);
    assert.equal(first.metadata?.templateLabel, template.label);
  }
});

test("drawer and fullscreen preserve the same node data, selection, and uncommitted edit state", () => {
  let workspace = spreadsheetWorkspaceReducer(INITIAL_SPREADSHEET_WORKSPACE_STATE, {
    type: "startEdit",
    cell: { r: 2, c: 3 },
    value: "=B3*C3",
    sheetId: "sheet-1",
    workbookId: "workbook-1",
  });
  workspace = spreadsheetWorkspaceReducer(workspace, { type: "setEditValue", value: "=B3*C3+10" });
  workspace = spreadsheetWorkspaceReducer(workspace, {
    type: "selectRange",
    sheetId: "sheet-1",
    range: { start: { r: 2, c: 3 }, end: { r: 4, c: 5 } },
  });
  workspace = spreadsheetWorkspaceReducer(workspace, { type: "openDrawer" });
  assert.equal(workspace.mode, "drawer", "inline expand must open the drawer first");
  assert.deepEqual(workspace.selectedCell, { r: 4, c: 5 });
  assert.deepEqual(workspace.selectedRangesBySheet["sheet-1"], {
    start: { r: 2, c: 3 },
    end: { r: 4, c: 5 },
  });
  assert.deepEqual(workspace.editingCell, { r: 2, c: 3 });
  assert.equal(workspace.editValue, "=B3*C3+10");
  assert.equal(workspace.formulaEditing?.sourceSheetId, "sheet-1");
  assert.equal(workspace.formulaEditing?.workbookId, "workbook-1");
  workspace = spreadsheetWorkspaceReducer(workspace, { type: "enterFullscreen" });
  assert.equal(workspace.mode, "fullscreen");
  assert.equal(workspace.editValue, "=B3*C3+10");

  const editor = createSpreadsheetEditor();
  try {
    assert.equal(editor.commands.insertSpreadsheet(createSpreadsheetTemplate("market_sizing")), true);
    const [spreadsheet] = spreadsheetPositions(editor);
    assert.equal(editor.commands.setNodeSelection(spreadsheet.position), true);

    const inlineData = (editor.state.selection as NodeSelection).node.attrs.data as SpreadsheetData;
    const drawerData = updateSpreadsheetCell(inlineData, { r: 1, c: 1 }, "1000");
    assert.equal(editor.commands.updateAttributes("spreadsheetBlock", { data: drawerData }), true);
    const fullscreenData = updateSpreadsheetCell(
      (editor.state.selection as NodeSelection).node.attrs.data as SpreadsheetData,
      { r: 1, c: 2 },
      "25",
    );
    assert.equal(editor.commands.updateAttributes("spreadsheetBlock", { data: fullscreenData }), true);

    const spreadsheetNodes = editor.getJSON().content?.filter((node) => node.type === "spreadsheetBlock") ?? [];
    assert.equal(spreadsheetNodes.length, 1, "workspace modes must not create duplicate spreadsheet nodes");
    const savedSheet = getActiveSpreadsheetSheet(spreadsheetNodes[0].attrs?.data as SpreadsheetData);
    assert.equal(savedSheet.cells["1,1"].raw, "1000");
    assert.equal(savedSheet.cells["1,2"].raw, "25");

    workspace = spreadsheetWorkspaceReducer(workspace, { type: "exitFullscreen" });
    assert.equal(workspace.mode, "drawer", "fullscreen must shrink back to drawer");
    workspace = spreadsheetWorkspaceReducer(workspace, { type: "closeWorkspace" });
    assert.equal(workspace.mode, "inline");
    assert.deepEqual(workspace.selectedCell, { r: 4, c: 5 });
    assert.equal(workspace.editValue, "=B3*C3+10");
  } finally {
    editor.destroy();
  }
});

test("workspace mode contract prevents inline from skipping directly to fullscreen", () => {
  const invalidFullscreen = spreadsheetWorkspaceReducer(INITIAL_SPREADSHEET_WORKSPACE_STATE, {
    type: "enterFullscreen",
  });
  assert.equal(invalidFullscreen.mode, "inline");

  const drawer = spreadsheetWorkspaceReducer(invalidFullscreen, { type: "openDrawer" });
  assert.equal(drawer.mode, "drawer");
  const fullscreen = spreadsheetWorkspaceReducer(drawer, { type: "enterFullscreen" });
  assert.equal(fullscreen.mode, "fullscreen");
  const closed = spreadsheetWorkspaceReducer(fullscreen, { type: "closeWorkspace" });
  assert.equal(closed.mode, "inline");
});

test("formula editing survives sheet and workspace changes and returns to its source", () => {
  let state = spreadsheetWorkspaceReducer(INITIAL_SPREADSHEET_WORKSPACE_STATE, {
    type: "startEdit",
    cell: { r: 3, c: 3 },
    value: "=",
    sheetId: "source-sheet",
    workbookId: "workbook-1",
  });
  state = spreadsheetWorkspaceReducer(state, { type: "switchSheet", sheetId: "inputs-sheet" });
  assert.equal(state.activeSheetId, "inputs-sheet");
  assert.deepEqual(state.editingCell, { r: 3, c: 3 });
  assert.equal(state.formulaEditing?.sourceSheetId, "source-sheet");
  state = spreadsheetWorkspaceReducer(state, {
    type: "insertFormulaReference",
    value: "='Inputs'!B2",
    caretPosition: 12,
    sheetId: "inputs-sheet",
    range: { start: { r: 1, c: 1 }, end: { r: 1, c: 1 } },
  });
  state = spreadsheetWorkspaceReducer(state, { type: "openDrawer" });
  state = spreadsheetWorkspaceReducer(state, { type: "enterFullscreen" });
  assert.equal(state.editValue, "='Inputs'!B2");
  assert.equal(state.formulaEditing?.referencedRanges.length, 1);
  state = spreadsheetWorkspaceReducer(state, { type: "finishEdit" });
  assert.equal(state.activeSheetId, "source-sheet");
  assert.deepEqual(state.selectedCell, { r: 3, c: 3 });
  assert.equal(state.formulaEditing, null);
  assert.equal(state.editingCell, null);
});

test("formula range selection is distinct from fill state and clears after insertion", () => {
  let state = spreadsheetWorkspaceReducer(INITIAL_SPREADSHEET_WORKSPACE_STATE, {
    type: "startEdit",
    cell: { r: 0, c: 0 },
    value: "=SUM(",
    sheetId: "sheet-1",
    workbookId: "workbook-1",
  });
  state = spreadsheetWorkspaceReducer(state, {
    type: "startFormulaRange",
    sheetId: "sheet-1",
    range: { start: { r: 1, c: 1 }, end: { r: 1, c: 1 } },
  });
  state = spreadsheetWorkspaceReducer(state, {
    type: "updateFormulaRange",
    sheetId: "sheet-1",
    range: { start: { r: 1, c: 1 }, end: { r: 9, c: 1 } },
  });
  assert.deepEqual(state.formulaEditing?.selectingRange?.range.end, { r: 9, c: 1 });
  assert.equal(state.fillSource, null);
  assert.equal(state.fillTarget, null);
  state = spreadsheetWorkspaceReducer(state, {
    type: "insertFormulaReference",
    value: "=SUM(B2:B10",
    caretPosition: 11,
    sheetId: "sheet-1",
    range: { start: { r: 1, c: 1 }, end: { r: 9, c: 1 } },
  });
  assert.equal(state.formulaEditing?.selectingRange, null);
  assert.equal(state.formulaEditing?.referencedRanges.length, 1);
});

test("workspace shell fixes drawer width, layer order, controls, Escape, and focus restoration", () => {
  const css = readFileSync(new URL("../src/app/globals.css", import.meta.url), "utf8");
  const nodeView = readFileSync(
    new URL("../src/components/notes/SpreadsheetExtension.tsx", import.meta.url),
    "utf8",
  );

  assert.ok(SPREADSHEET_WORKSPACE_LAYER_Z_INDEX > 50, "workspace must sit above AppShell z-30/50 layers");
  assert.match(css, /\.spreadsheet-workspace-panel\[data-mode="drawer"\][\s\S]*?width:\s*65vw/);
  assert.match(css, /\.spreadsheet-workspace-panel\[data-mode="fullscreen"\][\s\S]*?width:\s*100vw/);
  assert.match(nodeView, /createPortal\([\s\S]*?document\.body/);
  assert.match(nodeView, /全螢幕/);
  assert.match(nodeView, /縮回 Drawer/);
  assert.match(nodeView, /返回筆記/);
  assert.match(nodeView, /event\.key === "Escape"[\s\S]*?closeWorkspace/);
  assert.match(nodeView, /returnFocusElement\?\.focus\(\)/);
});

test("spreadsheet semantic backgrounds and transient overlays remain separate", () => {
  const css = readFileSync(new URL("../src/app/globals.css", import.meta.url), "utf8");
  const component = readFileSync(
    new URL("../src/components/notes/SpreadsheetComponent.tsx", import.meta.url),
    "utf8",
  );

  assert.match(css, /\.spreadsheet-cell-historical\s*\{[\s\S]*?background:\s*rgb\(254 249 195\)/);
  assert.match(css, /\.spreadsheet-cell-assumption\s*\{[\s\S]*?background:\s*rgb\(219 234 254\)/);
  assert.match(css, /\.spreadsheet-cell-formula\s*\{[\s\S]*?background:\s*rgb\(220 252 231\)/);
  assert.match(css, /\.spreadsheet-cell-not-applicable\s*\{[\s\S]*?background:\s*rgb\(228 228 231\)/);
  assert.match(css, /\.spreadsheet-cell-formula-reference\s*\{[\s\S]*?box-shadow:[\s\S]*?var\(--formula-reference-color\)/);
  assert.match(css, /\.dark \.spreadsheet-grid \.spreadsheet-cell-formula/);
  assert.match(css, /\.spreadsheet-cell-editor\s*\{[\s\S]*?appearance:\s*none/);
  assert.match(css, /\.spreadsheet-cell-editing \.spreadsheet-cell-editor:focus-visible\s*\{[\s\S]*?outline:\s*none/);
  assert.match(css, /\.spreadsheet-cell-editor::selection\s*\{[\s\S]*?background:\s*rgb\(37 99 235 \/ \.22\)/);
  assert.match(css, /\.spreadsheet-sheet-tab\s*\{[\s\S]*?-webkit-appearance:\s*none;[\s\S]*?appearance:\s*none;/);
  assert.match(css, /\.spreadsheet-sheet-tab\.spreadsheet-sheet-tab--inactive,[\s\S]*?\.spreadsheet-sheet-tab\[aria-selected="false"\][\s\S]*?background:\s*#ffffff;[\s\S]*?color:\s*#000000;/);
  assert.match(css, /\.spreadsheet-sheet-tab\.spreadsheet-sheet-tab--active,[\s\S]*?\.spreadsheet-sheet-tab\[aria-selected="true"\],[\s\S]*?\.spreadsheet-sheet-tab\.spreadsheet-sheet-tab--active:hover,[\s\S]*?\.spreadsheet-sheet-tab\.spreadsheet-sheet-tab--active:focus-visible,[\s\S]*?\.spreadsheet-sheet-tab\.spreadsheet-sheet-tab--active:active[\s\S]*?background:\s*#171717;[\s\S]*?color:\s*#ffffff;/);
  assert.match(css, /\.dark \.spreadsheet-sheet-tab\[data-state="inactive"\][\s\S]*?background:\s*#ffffff;[\s\S]*?color:\s*#000000;/);
  assert.match(css, /\.dark \.spreadsheet-sheet-tab\[data-state="active"\][\s\S]*?background:\s*#171717;[\s\S]*?color:\s*#ffffff;/);
  assert.match(css, /\.spreadsheet-sheet-tab-label\s*\{[\s\S]*?color:\s*inherit;[\s\S]*?user-select:\s*none;/);
  assert.match(component, /select-none[\s\S]*?spreadsheet-cell-selected/);
  assert.match(component, /className="spreadsheet-cell-editor[^"]*appearance-none[^"]*select-text/);
  assert.match(component, /data-state=\{isActiveSheet \? "active" : "inactive"\}/);
  assert.match(component, /isActiveSheet \? "spreadsheet-sheet-tab--active bg-\[#171717\] text-white" : "spreadsheet-sheet-tab--inactive bg-white text-black"/);
  assert.match(component, /backgroundColor: isActiveSheet \? "#171717" : "#FFFFFF"/);
  assert.match(component, /color: isActiveSheet \? "#FFFFFF" : "#000000"/);
  assert.match(component, /aria-label=\{candidate\.name\}/);
  assert.match(component, /aria-label="刪除目前工作表"[\s\S]*?刪除分頁/);
  assert.match(component, /className="spreadsheet-sheet-tab-label"[\s\S]*?color: isActiveSheet \? "#FFFFFF" : "#000000"[\s\S]*?WebkitTextFillColor: isActiveSheet \? "#FFFFFF" : "#000000"/);
  assert.match(component, /event\.target as HTMLElement[\s\S]*?closest\("\.spreadsheet-cell-editor"\)[\s\S]*?return/);
  assert.match(component, /cell\?\.backgroundColor[\s\S]*?backgroundColor: cell\.backgroundColor/);
  assert.match(component, /!formulaEditing \? \([\s\S]*?aria-label="拖曳填滿"/);
});

test("template metadata and formulas survive JSON serialization and restoration", () => {
  const editor = createSpreadsheetEditor();
  try {
    for (const template of SPREADSHEET_TEMPLATES) {
      assert.equal(editor.commands.insertSpreadsheet(createSpreadsheetTemplate(template.id)), true);
    }
    const serialized = editor.getJSON();
    const restored = createSpreadsheetEditor(serialized);
    try {
      assert.deepEqual(restored.getJSON(), serialized);
      assert.equal(spreadsheetPositions(restored).length, SPREADSHEET_TEMPLATES.length);
      assert.match(JSON.stringify(serialized), /market_sizing/);
      assert.match(JSON.stringify(serialized), /=B2\*C2/);
    } finally {
      restored.destroy();
    }
  } finally {
    editor.destroy();
  }
});
