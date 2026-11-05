import type { Editor, JSONContent } from "@tiptap/core";
import type { Node as ProseMirrorNode } from "@tiptap/pm/model";
import { AllSelection, NodeSelection, TextSelection, type EditorState, type Transaction } from "@tiptap/pm/state";

import type { AiApplyAction, AiSelectionSnapshot } from "./aiAssistant";

export type AiSelectionResult =
  | { ok: true; snapshot: AiSelectionSnapshot }
  | { ok: false; reason: string };

export type AiApplyResult =
  | { ok: true }
  | { ok: false; reason: string };

const FORBIDDEN_NODE_NAMES = new Set([
  "spreadsheetBlock",
  "table",
  "tableRow",
  "tableCell",
  "tableHeader",
]);

export function fingerprintDocument(doc: ProseMirrorNode) {
  const value = JSON.stringify(doc.toJSON());
  let hash = 0x811c9dc5;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 0x01000193);
  }
  return (hash >>> 0).toString(16).padStart(8, "0");
}

function forbiddenAncestor(doc: ProseMirrorNode, position: number) {
  const resolved = doc.resolve(position);
  for (let depth = 0; depth <= resolved.depth; depth += 1) {
    const node = resolved.node(depth);
    if (FORBIDDEN_NODE_NAMES.has(node.type.name)) return node.type.name;
    if (depth > 0 && (node.type.spec.atom || node.type.spec.isolating)) return node.type.name;
  }
  return null;
}

function structuralGuard(doc: ProseMirrorNode, from: number, to: number) {
  if (from < 0 || to > doc.content.size || from >= to) return "選取位置已失效。";
  const ancestor = forbiddenAncestor(doc, from) ?? forbiddenAncestor(doc, to);
  if (ancestor) return `AI 第一版不會修改受保護節點（${ancestor}）。`;

  let forbiddenNode = "";
  doc.nodesBetween(from, to, (node) => {
    if (FORBIDDEN_NODE_NAMES.has(node.type.name)) forbiddenNode = node.type.name;
    else if (!node.isText && (node.type.spec.atom || node.type.spec.isolating)) {
      forbiddenNode = node.type.name;
    }
    return !forbiddenNode;
  });
  return forbiddenNode ? `選取範圍跨過受保護節點（${forbiddenNode}）。` : null;
}

function selectionText(doc: ProseMirrorNode, from: number, to: number) {
  return doc.textBetween(from, to, "\n", "\n");
}

export function captureAiSelection(state: EditorState): AiSelectionResult {
  const { selection, doc } = state;
  if (selection instanceof NodeSelection || selection instanceof AllSelection) {
    return { ok: false, reason: "AI 僅支援一般文字選取。" };
  }
  if (!(selection instanceof TextSelection) || selection.empty) {
    return { ok: false, reason: "請先選取一段一般文字。" };
  }
  if (selection.constructor.name === "CellSelection") {
    return { ok: false, reason: "AI 第一版不支援跨表格儲存格選取。" };
  }

  const unsafeReason = structuralGuard(doc, selection.from, selection.to);
  if (unsafeReason) return { ok: false, reason: unsafeReason };
  const text = selectionText(doc, selection.from, selection.to);
  if (!text.trim()) return { ok: false, reason: "選取內容不能只有空白。" };

  return {
    ok: true,
    snapshot: {
      from: selection.from,
      to: selection.to,
      anchor: selection.anchor,
      head: selection.head,
      text,
      selectionType: "text",
      documentFingerprint: fingerprintDocument(doc),
    },
  };
}

export function validateAiSelection(
  doc: ProseMirrorNode,
  snapshot: AiSelectionSnapshot,
): AiApplyResult {
  const unsafeReason = structuralGuard(doc, snapshot.from, snapshot.to);
  if (unsafeReason) return { ok: false, reason: unsafeReason };
  if (snapshot.anchor < snapshot.from || snapshot.anchor > snapshot.to) {
    return { ok: false, reason: "選取起點已失效。" };
  }
  if (snapshot.head < snapshot.from || snapshot.head > snapshot.to) {
    return { ok: false, reason: "選取游標位置已失效。" };
  }
  if (selectionText(doc, snapshot.from, snapshot.to) !== snapshot.text) {
    return { ok: false, reason: "原選取文字已被修改，請重新選取。" };
  }
  if (fingerprintDocument(doc) !== snapshot.documentFingerprint) {
    return { ok: false, reason: "文件結構已變更，請重新選取以避免錯位。" };
  }
  return { ok: true };
}

export function mapAiSelection(
  snapshot: AiSelectionSnapshot,
  transaction: Transaction,
): AiSelectionResult {
  if (!transaction.docChanged) return { ok: true, snapshot };
  const fromResult = transaction.mapping.mapResult(snapshot.from, 1);
  const toResult = transaction.mapping.mapResult(snapshot.to, -1);
  const anchorResult = transaction.mapping.mapResult(
    snapshot.anchor,
    snapshot.anchor === snapshot.from ? 1 : -1,
  );
  const headResult = transaction.mapping.mapResult(
    snapshot.head,
    snapshot.head === snapshot.from ? 1 : -1,
  );
  if (fromResult.deleted || toResult.deleted || anchorResult.deleted || headResult.deleted) {
    return { ok: false, reason: "選取範圍已被其他編輯刪除。" };
  }

  const mapped = {
    ...snapshot,
    from: fromResult.pos,
    to: toResult.pos,
    anchor: anchorResult.pos,
    head: headResult.pos,
    documentFingerprint: fingerprintDocument(transaction.doc),
  };
  const unsafeReason = structuralGuard(transaction.doc, mapped.from, mapped.to);
  if (unsafeReason) return { ok: false, reason: unsafeReason };
  if (selectionText(transaction.doc, mapped.from, mapped.to) !== snapshot.text) {
    return { ok: false, reason: "原選取文字已被修改，請重新選取。" };
  }
  return { ok: true, snapshot: mapped };
}

function inlineContent(text: string): JSONContent | JSONContent[] {
  if (!text.includes("\n")) return { type: "text", text };
  return text.split(/\n/).map((line) => ({
    type: "paragraph",
    content: line ? [{ type: "text", text: line }] : undefined,
  }));
}

function blockContent(text: string): JSONContent[] {
  return text.split(/\n/).map((line) => ({
    type: "paragraph",
    content: line ? [{ type: "text", text: line }] : undefined,
  }));
}

function topLevelBlockEnd(doc: ProseMirrorNode, position: number) {
  const resolved = doc.resolve(position);
  if (resolved.depth < 1) return null;
  return resolved.after(1);
}

export function applySafeAiEdit(
  editor: Editor,
  snapshot: AiSelectionSnapshot,
  text: string,
  action: AiApplyAction,
): AiApplyResult {
  if (!text) return { ok: false, reason: "AI 建議是空白內容。" };
  const validation = validateAiSelection(editor.state.doc, snapshot);
  if (!validation.ok) return validation;

  const before = fingerprintDocument(editor.state.doc);
  let chain = editor.chain().command(({ tr }) => {
    tr.setMeta("ai-assistant", action);
    return true;
  });

  if (action === "replace") {
    chain = chain.insertContentAt(
      { from: snapshot.from, to: snapshot.to },
      inlineContent(text),
      { updateSelection: true },
    );
  } else if (action === "insert_cursor") {
    chain = chain.insertContentAt(snapshot.head, inlineContent(text), { updateSelection: true });
  } else {
    const insertAt = topLevelBlockEnd(editor.state.doc, snapshot.head);
    if (insertAt === null) return { ok: false, reason: "找不到安全的插入區塊。" };
    chain = chain.insertContentAt(insertAt, blockContent(text), { updateSelection: true });
  }

  const commandAccepted = chain.run();
  const changed = fingerprintDocument(editor.state.doc) !== before;
  if (!commandAccepted || !changed) {
    return {
      ok: false,
      reason: "編輯器保護機制拒絕了這次修改；AI 建議已保留。",
    };
  }
  return { ok: true };
}
