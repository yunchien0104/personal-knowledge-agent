import assert from "node:assert/strict";
import test from "node:test";

import { Editor, type JSONContent } from "@tiptap/core";
import { EditorState, TextSelection } from "@tiptap/pm/state";

import {
  applySafeAiEdit,
  captureAiSelection,
  mapAiSelection,
  validateAiSelection,
} from "../src/components/notes/aiEditorTransaction";
import { createCoreNotesEditorExtensions } from "../src/components/notes/editor-extensions";
import { SpreadsheetNode } from "../src/components/notes/SpreadsheetExtension";
import { createSpreadsheetTemplate } from "../src/lib/spreadsheet-workspace";

const paragraph = (text: string): JSONContent => ({
  type: "paragraph",
  content: text ? [{ type: "text", text }] : undefined,
});

function createEditor(content: JSONContent, onUpdate?: () => void) {
  const editor = new Editor({
    extensions: [...createCoreNotesEditorExtensions(), SpreadsheetNode],
    content,
    onUpdate: () => onUpdate?.(),
  });
  editor.view.updateState(EditorState.create({
    doc: editor.state.doc,
    schema: editor.schema,
    plugins: editor.extensionManager.plugins,
  }));
  return editor;
}

function capture(editor: Editor) {
  const result = captureAiSelection(editor.state);
  assert.equal(result.ok, true, result.ok ? undefined : result.reason);
  return result.snapshot;
}

test("captures forward and backward text selections including head direction", () => {
  const editor = createEditor({ type: "doc", content: [paragraph("alpha beta")] });
  try {
    editor.view.dispatch(editor.state.tr.setSelection(TextSelection.create(editor.state.doc, 1, 6)));
    const forward = capture(editor);
    assert.deepEqual({ anchor: forward.anchor, head: forward.head, text: forward.text }, { anchor: 1, head: 6, text: "alpha" });

    editor.view.dispatch(editor.state.tr.setSelection(TextSelection.create(editor.state.doc, 6, 1)));
    const backward = capture(editor);
    assert.deepEqual({ anchor: backward.anchor, head: backward.head, text: backward.text }, { anchor: 6, head: 1, text: "alpha" });
  } finally {
    editor.destroy();
  }
});

test("maps a snapshot across edits before it and rejects edits inside it", () => {
  const editor = createEditor({ type: "doc", content: [paragraph("alpha beta")] });
  try {
    editor.commands.setTextSelection({ from: 1, to: 6 });
    const snapshot = capture(editor);
    const beforeTransaction = editor.state.tr.insertText("prefix ", 1);
    const mapped = mapAiSelection(snapshot, beforeTransaction);
    assert.equal(mapped.ok, true, mapped.ok ? undefined : mapped.reason);
    editor.view.dispatch(beforeTransaction);
    assert.equal(validateAiSelection(editor.state.doc, mapped.snapshot).ok, true);
    assert.equal(mapped.snapshot.from, 8);

    const insideTransaction = editor.state.tr.insertText("!", mapped.snapshot.from + 1);
    const invalid = mapAiSelection(mapped.snapshot, insideTransaction);
    assert.equal(invalid.ok, false);
  } finally {
    editor.destroy();
  }
});

test("replace is structured, emits one update, and is one undo step", () => {
  let updates = 0;
  const editor = createEditor(
    { type: "doc", content: [paragraph("alpha beta")] },
    () => { updates += 1; },
  );
  try {
    editor.commands.setTextSelection({ from: 1, to: 6 });
    const result = applySafeAiEdit(editor, capture(editor), "<safe & plain>", "replace");
    assert.deepEqual(result, { ok: true });
    assert.equal(editor.getText(), "<safe & plain> beta");
    assert.doesNotMatch(JSON.stringify(editor.getJSON()), /<safe & plain><\//);
    assert.equal(updates, 1);
    assert.equal(editor.commands.undo(), true);
    assert.equal(editor.getText(), "alpha beta");
  } finally {
    editor.destroy();
  }
});

test("insert at cursor uses mapped head for a backward selection", () => {
  const editor = createEditor({ type: "doc", content: [paragraph("alpha beta")] });
  try {
    editor.view.dispatch(editor.state.tr.setSelection(TextSelection.create(editor.state.doc, 6, 1)));
    const result = applySafeAiEdit(editor, capture(editor), "X", "insert_cursor");
    assert.deepEqual(result, { ok: true });
    assert.equal(editor.getText(), "Xalpha beta");
  } finally {
    editor.destroy();
  }
});

test("insert below uses a top-level block boundary", () => {
  const editor = createEditor({ type: "doc", content: [paragraph("alpha"), paragraph("omega")] });
  try {
    editor.commands.setTextSelection({ from: 1, to: 6 });
    const result = applySafeAiEdit(editor, capture(editor), "new line", "insert_below");
    assert.deepEqual(result, { ok: true });
    assert.deepEqual(editor.getJSON().content?.map((node) => node.type), ["paragraph", "paragraph", "paragraph"]);
    assert.equal(editor.getText({ blockSeparator: "|" }), "alpha|new line|omega");
  } finally {
    editor.destroy();
  }
});

test("guards spreadsheet atoms and table selections before AI apply", () => {
  const editor = createEditor({
    type: "doc",
    content: [
      paragraph("before"),
      { type: "spreadsheetBlock", attrs: { data: createSpreadsheetTemplate("blank") } },
      paragraph("after"),
    ],
  });
  try {
    editor.commands.setTextSelection({ from: 1, to: editor.state.doc.content.size - 1 });
    const spreadsheetResult = captureAiSelection(editor.state);
    assert.equal(spreadsheetResult.ok, false);
    assert.match(spreadsheetResult.reason, /受保護節點/);
  } finally {
    editor.destroy();
  }

  const tableEditor = createEditor({ type: "doc", content: [paragraph("before")] });
  try {
    tableEditor.commands.setTextSelection(1);
    tableEditor.commands.insertTable({ rows: 2, cols: 2, withHeaderRow: true });
    const tableResult = captureAiSelection(tableEditor.state);
    assert.equal(tableResult.ok, false);
    assert.match(tableResult.reason, /受保護節點|一般文字/);
  } finally {
    tableEditor.destroy();
  }
});
