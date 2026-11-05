import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import { Editor, type JSONContent } from "@tiptap/core";
import { redo, undo, undoDepth } from "@tiptap/pm/history";
import { EditorState } from "@tiptap/pm/state";

import { createCoreNotesEditorExtensions } from "../src/components/notes/editor-extensions.ts";
import {
  createNoteAutosaveQueue,
  type NoteDraft,
  type NoteSaveRequest,
  type NoteSaveResult,
  type NoteSavingStatus,
} from "../src/lib/note-autosave.ts";

const paragraph = (text: string): JSONContent => ({
  type: "paragraph",
  content: [{ type: "text", text }],
});

function createEditor(content: JSONContent = { type: "doc", content: [paragraph("hello")] }) {
  return new Editor({ extensions: createCoreNotesEditorExtensions(), content });
}

function marks(editor: Editor) {
  return editor.getJSON().content?.[0]?.content?.[0]?.marks?.map((mark) => mark.type) ?? [];
}

test("heading levels, bold, italic, underline and strike execute through Tiptap", () => {
  const editor = createEditor({ type: "doc", content: [paragraph("format me")] });
  try {
    assert.equal(editor.commands.setTextSelection({ from: 1, to: 10 }), true);
    assert.equal(editor.commands.toggleBold(), true);
    assert.equal(editor.commands.toggleItalic(), true);
    assert.equal(editor.commands.toggleUnderline(), true);
    assert.equal(editor.commands.toggleStrike(), true);
    assert.equal(editor.commands.toggleHeading({ level: 1 }), true);

    assert.equal(editor.getJSON().content?.[0]?.type, "heading");
    assert.equal(editor.getJSON().content?.[0]?.attrs?.level, 1);
    assert.deepEqual(new Set(marks(editor)), new Set(["bold", "italic", "underline", "strike"]));

    assert.equal(editor.commands.toggleHeading({ level: 2 }), true);
    assert.equal(editor.getJSON().content?.[0]?.attrs?.level, 2);
    assert.equal(editor.commands.toggleHeading({ level: 3 }), true);
    assert.equal(editor.getJSON().content?.[0]?.attrs?.level, 3);
    assert.equal(editor.commands.toggleHeading({ level: 4 }), true);
    assert.equal(editor.getJSON().content?.[0]?.attrs?.level, 4);
  } finally {
    editor.destroy();
  }
});

test("italic preserves English and Chinese text through JSON restoration", () => {
  const editor = createEditor({ type: "doc", content: [paragraph("English 中文斜體")] });
  try {
    assert.equal(editor.commands.setTextSelection({ from: 1, to: 13 }), true);
    assert.equal(editor.commands.toggleItalic(), true);
    const json = editor.getJSON();
    assert.match(JSON.stringify(json), /English 中文斜體/);
    assert.ok(marks(editor).includes("italic"));

    const restored = createEditor(json);
    try {
      assert.deepEqual(restored.getJSON(), json);
      assert.ok(marks(restored).includes("italic"));
    } finally {
      restored.destroy();
    }
  } finally {
    editor.destroy();
  }
});

test("color, highlight, font family and font size coexist and restore", () => {
  const editor = createEditor({ type: "doc", content: [paragraph("Styled 中文")] });
  try {
    assert.equal(editor.commands.setTextSelection({ from: 1, to: 10 }), true);
    assert.equal(editor.commands.setColor("#2563eb"), true);
    assert.equal(editor.commands.setHighlight({ color: "rgba(250, 204, 21, 0.38)" }), true);
    assert.equal(editor.commands.setFontFamily("var(--font-mono)"), true);
    assert.equal(editor.commands.setFontSize("20px"), true);
    assert.equal(editor.commands.toggleBold(), true);
    assert.equal(editor.commands.toggleItalic(), true);
    assert.equal(editor.commands.toggleUnderline(), true);

    const json = editor.getJSON();
    const textMarks = json.content?.[0]?.content?.[0]?.marks ?? [];
    const textStyle = textMarks.find((mark) => mark.type === "textStyle");
    const highlight = textMarks.find((mark) => mark.type === "highlight");
    assert.deepEqual({ ...textStyle?.attrs }, {
      color: "#2563eb",
      fontFamily: "var(--font-mono)",
      fontSize: "20px",
    });
    assert.equal(highlight?.attrs?.color, "rgba(250, 204, 21, 0.38)");
    assert.deepEqual(
      new Set(textMarks.map((mark) => mark.type)),
      new Set(["textStyle", "bold", "italic", "highlight", "underline"])
    );

    const restored = createEditor(json);
    try {
      assert.deepEqual(restored.getJSON(), json);
    } finally {
      restored.destroy();
    }
  } finally {
    editor.destroy();
  }
});

test("collapsed selection stores styles for subsequent input", () => {
  const editor = createEditor({ type: "doc", content: [paragraph("A")] });
  try {
    assert.equal(editor.commands.setTextSelection(2), true);
    assert.equal(editor.commands.setColor("#7c3aed"), true);
    assert.equal(editor.commands.setFontSize("24px"), true);
    assert.equal(editor.commands.toggleUnderline(), true);
    assert.equal(editor.commands.insertContent({ type: "text", text: "B" }), true);

    const inserted = editor.getJSON().content?.[0]?.content?.find(
      (node) => "text" in node && node.text === "B"
    );
    assert.ok(inserted);
    assert.ok(inserted.marks?.some((mark) => mark.type === "underline"));
    assert.deepEqual({ ...inserted.marks?.find((mark) => mark.type === "textStyle")?.attrs }, {
      color: "#7c3aed",
      fontFamily: null,
      fontSize: "24px",
    });
  } finally {
    editor.destroy();
  }
});

test("clear formatting removes inline marks and resets headings", () => {
  const editor = createEditor({ type: "doc", content: [paragraph("clear me")] });
  try {
    editor.commands.setTextSelection({ from: 1, to: 9 });
    editor.commands.toggleHeading({ level: 3 });
    editor.commands.toggleBold();
    editor.commands.toggleItalic();
    editor.commands.setColor("#dc2626");
    editor.commands.setHighlight({ color: "rgba(74, 222, 128, 0.32)" });

    assert.equal(editor.chain().unsetAllMarks().clearNodes().run(), true);
    const json = editor.getJSON();
    assert.equal(json.content?.[0]?.type, "paragraph");
    assert.equal(json.content?.[0]?.content?.[0]?.marks, undefined);
  } finally {
    editor.destroy();
  }
});

test("configured Tiptap history undoes and redoes formatting", () => {
  const editor = createEditor({ type: "doc", content: [paragraph("x")] });
  try {
    let state = EditorState.create({
      doc: editor.state.doc,
      schema: editor.schema,
      plugins: editor.extensionManager.plugins,
    });
    const italic = state.schema.marks.italic.create();
    state = state.apply(state.tr.addMark(1, 2, italic));
    assert.equal(state.doc.firstChild?.firstChild?.marks[0]?.type.name, "italic");
    assert.equal(undoDepth(state), 1);

    const dispatch = (transaction: Parameters<typeof state.apply>[0]) => {
      state = state.apply(transaction);
    };
    assert.equal(undo(state, dispatch), true);
    assert.equal(state.doc.firstChild?.firstChild?.marks.length, 0);
    assert.equal(redo(state, dispatch), true);
    assert.equal(state.doc.firstChild?.firstChild?.marks[0]?.type.name, "italic");
  } finally {
    editor.destroy();
  }
});

test("bullet, ordered and task lists serialize as distinct nodes", () => {
  const cases = [
    ["toggleBulletList", "bulletList"],
    ["toggleOrderedList", "orderedList"],
    ["toggleTaskList", "taskList"],
  ] as const;

  for (const [command, nodeType] of cases) {
    const editor = createEditor();
    try {
      assert.equal(editor.commands[command](), true);
      assert.equal(editor.getJSON().content?.[0]?.type, nodeType);
    } finally {
      editor.destroy();
    }
  }
});

test("table insertion, cell editing, row/column changes and JSON restoration work", () => {
  const editor = createEditor({
    type: "doc",
    content: [paragraph("before"), paragraph("after")],
  });
  try {
    assert.equal(editor.commands.setTextSelection(7), true);
    assert.equal(editor.commands.insertTable({ rows: 2, cols: 2, withHeaderRow: true }), true);
    assert.equal(editor.commands.insertContent({ type: "text", text: "A1" }), true);
    assert.equal(editor.commands.addRowAfter(), true);
    assert.equal(editor.commands.addColumnAfter(), true);

    const json = editor.getJSON();
    const table = json.content?.find((node) => node.type === "table") as
      | { content?: Array<{ content?: unknown[] }> }
      | undefined;
    assert.ok(table, "table should be inserted without removing surrounding paragraphs");
    assert.equal(table.content?.length, 3);
    assert.equal(table.content?.[0]?.content?.length, 3);
    assert.match(JSON.stringify(table), /A1/);
    assert.match(editor.getText(), /before/);
    assert.match(editor.getText(), /after/);

    const restored = createEditor(json);
    try {
      assert.deepEqual(restored.getJSON(), json);
      assert.match(restored.getText(), /A1/);
    } finally {
      restored.destroy();
    }
  } finally {
    editor.destroy();
  }
});

test("serialization and parent-state observation do not move the cursor", () => {
  const editor = createEditor({ type: "doc", content: [paragraph("cursor stays here")] });
  try {
    assert.equal(editor.commands.setTextSelection(7), true);
    const selectionBefore = { from: editor.state.selection.from, to: editor.state.selection.to };
    const serialized = editor.getJSON();
    structuredClone(serialized);
    editor.getText({ blockSeparator: "\n" });
    assert.deepEqual(
      { from: editor.state.selection.from, to: editor.state.selection.to },
      selectionBefore
    );
  } finally {
    editor.destroy();
  }
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

const draft = (title: string): NoteDraft => ({
  title,
  contentJson: { type: "doc", content: [paragraph(title)] },
  plainText: title,
});

test("autosave serializes PATCH requests and advances OCC revisions", async () => {
  const first = deferred<NoteSaveResult>();
  const second = deferred<NoteSaveResult>();
  const calls: NoteSaveRequest[] = [];
  const statuses: NoteSavingStatus[] = [];
  const savedLatest: boolean[] = [];
  const queue = createNoteAutosaveQueue({
    initialDraft: draft("initial"),
    initialRevision: 4,
    onConflict() {},
    onRevision() {},
    onStatus(status) { statuses.push(status); },
    onSaved(_request, _revision, isLatest) { savedLatest.push(isLatest); },
    save(request) {
      calls.push(request);
      return calls.length === 1 ? first.promise : second.promise;
    },
  });

  queue.update(draft("first edit"));
  const saving = queue.requestSave();
  queue.update(draft("second edit"));
  void queue.requestSave();
  assert.equal(calls.length, 1, "only one PATCH may be in flight");
  assert.equal(calls[0].expectedRevision, 4);
  assert.equal(calls[0].plainText, "first edit");

  first.resolve({ status: "saved", bodyRevision: 5 });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(calls.length, 2);
  assert.equal(calls[1].expectedRevision, 5);
  assert.equal(calls[1].plainText, "second edit");

  second.resolve({ status: "saved", bodyRevision: 6 });
  await saving;
  assert.equal(queue.getRevision(), 6);
  assert.equal(queue.hasUnsavedChanges(), false);
  assert.deepEqual(savedLatest, [false, true]);
  assert.equal(statuses.at(-1), "saved");
});

test("conflict stops stale writes and preserves the newest unsaved draft", async () => {
  let saveCalls = 0;
  let conflicted = false;
  const statuses: NoteSavingStatus[] = [];
  const queue = createNoteAutosaveQueue({
    initialDraft: draft("server"),
    initialRevision: 8,
    onConflict() { conflicted = true; },
    onRevision() {},
    onStatus(status) { statuses.push(status); },
    async save() {
      saveCalls += 1;
      return { status: "conflict" };
    },
  });

  queue.update(draft("local unsaved"));
  await queue.requestSave();
  queue.update(draft("still local"));
  await queue.requestSave();

  assert.equal(conflicted, true);
  assert.equal(saveCalls, 1, "a conflicted queue must stop sending stale revisions");
  assert.equal(queue.getDraft().title, "still local");
  assert.equal(queue.hasUnsavedChanges(), true);
  assert.ok(statuses.includes("conflict"));
});

test("a recovered backup with a stale base revision can be blocked before PATCH", async () => {
  let saveCalls = 0;
  const queue = createNoteAutosaveQueue({
    initialDraft: draft("server"),
    initialRevision: 9,
    onConflict() {},
    onRevision() {},
    onStatus() {},
    async save() {
      saveCalls += 1;
      return { status: "saved", bodyRevision: 10 };
    },
  });

  queue.update(draft("recovered local edit"));
  queue.markConflict();
  await queue.requestSave("manual");
  assert.equal(saveCalls, 0);
  assert.equal(queue.hasUnsavedChanges(), true);
});

test("failed saves remain retryable without discarding the draft", async () => {
  let saveCalls = 0;
  const statuses: NoteSavingStatus[] = [];
  const queue = createNoteAutosaveQueue({
    initialDraft: draft("server"),
    initialRevision: 2,
    onConflict() {},
    onRevision() {},
    onStatus(status) { statuses.push(status); },
    async save(request) {
      saveCalls += 1;
      if (saveCalls === 1) throw new Error("offline");
      return { status: "saved", bodyRevision: request.expectedRevision + 1 };
    },
  });

  queue.update(draft("offline edit"));
  await queue.requestSave();
  assert.equal(queue.getDraft().plainText, "offline edit");
  assert.equal(queue.hasUnsavedChanges(), true);
  assert.equal(statuses.at(-1), "failed");

  await queue.requestSave("manual");
  assert.equal(saveCalls, 2);
  assert.equal(queue.hasUnsavedChanges(), false);
  assert.equal(statuses.at(-1), "saved");
});

test("autosave carries formatted JSON without altering marks", async () => {
  const editor = createEditor({ type: "doc", content: [paragraph("autosave style")] });
  try {
    editor.commands.setTextSelection({ from: 1, to: 15 });
    editor.commands.toggleItalic();
    editor.commands.setColor("#2563eb");
    editor.commands.setFontSize("18px");
    const formatted = editor.getJSON();
    let savedContent: JSONContent | null = null;
    const queue = createNoteAutosaveQueue({
      initialDraft: draft("server"),
      initialRevision: 3,
      onConflict() {},
      onRevision() {},
      onStatus() {},
      async save(request) {
        savedContent = request.contentJson;
        return { status: "saved", bodyRevision: 4 };
      },
    });

    queue.update({ title: "formatted", contentJson: formatted, plainText: editor.getText() });
    await queue.requestSave();
    assert.deepEqual(savedContent, formatted);

    const restored = createEditor(savedContent as JSONContent);
    try {
      assert.deepEqual(restored.getJSON(), formatted);
    } finally {
      restored.destroy();
    }
  } finally {
    editor.destroy();
  }
});

test("editor CSS retains keyboard focus visibility and dark table styling", async () => {
  const css = await readFile(new URL("../src/app/globals.css", import.meta.url), "utf8");
  assert.match(css, /\*:focus-visible\s*\{[^}]*outline:/);
  assert.match(css, /\.notes-editor:has\(\.notes-editor-content:focus-visible\)/);
  assert.match(css, /\.dark \.notes-editor \.notes-editor-content th/);
  assert.match(css, /\.notes-editor \.notes-editor-content table/);
  assert.match(css, /:root\s*\{[^}]*font-synthesis:\s*none/);
  assert.match(css, /\.notes-editor \.notes-editor-content\s*\{[^}]*font-synthesis:\s*style/);
  assert.match(css, /\.notes-editor \.notes-editor-content em\s*\{\s*font-style:\s*italic/);
});

test("notes layout is responsive, viewport-aware and avoids nested vertical scrolling", async () => {
  const page = await readFile(
    new URL("../src/app/(workspace)/notes/[noteId]/page.tsx", import.meta.url),
    "utf8"
  );
  const editor = await readFile(
    new URL("../src/components/notes/TiptapEditor.tsx", import.meta.url),
    "utf8"
  );
  const formattingControls = await readFile(
    new URL("../src/components/notes/TextFormattingControls.tsx", import.meta.url),
    "utf8"
  );
  const css = await readFile(new URL("../src/app/globals.css", import.meta.url), "utf8");

  assert.match(page, /max-w-\[90rem\]/);
  assert.match(page, /px-3 py-4 sm:px-5 sm:py-5 lg:px-8/);
  assert.match(page, /min-w-0 flex-wrap/);
  assert.match(page, /w-full flex-wrap[^\n]*lg:w-auto/);
  assert.match(page, /className="mb-3 w-full/);
  assert.match(editor, /px-5 py-6 sm:px-8 sm:py-8 lg:px-10/);
  assert.doesNotMatch(editor, /overflow-y-(?:auto|scroll)/);
  assert.match(formattingControls, /absolute right-0[^\n]*sm:left-0 sm:right-auto/);
  assert.match(
    css,
    /\.notes-editor \.notes-editor-content\s*\{[^}]*margin-inline:\s*0;[^}]*max-width:\s*none;/,
  );
  assert.match(
    css,
    /\.notes-editor \.notes-editor-content > p,[\s\S]*?margin-left:\s*0;[\s\S]*?margin-right:\s*0;[\s\S]*?max-width:\s*none;/,
  );
  assert.doesNotMatch(css, /max-width:\s*82ch/);
  assert.doesNotMatch(css, /text-indent\s*:/);
  assert.match(css, /min-height:\s*420px/);
  assert.match(css, /@media \(min-width:\s*768px\)[\s\S]*min-height:\s*520px/);
  assert.match(
    css,
    /@media \(min-width:\s*1024px\)[\s\S]*min-height:\s*max\(600px, calc\(100dvh - 250px\)\)/
  );
});
