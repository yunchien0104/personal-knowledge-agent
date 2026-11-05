"use client";

import type { JSONContent } from "@tiptap/core";
import { TextSelection } from "@tiptap/pm/state";
import { EditorContent, useEditor, useEditorState } from "@tiptap/react";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  Calculator,
  CheckCircle2,
  Clipboard,
  Code,
  Columns3,
  Copy,
  List,
  ListOrdered,
  LogOut,
  MousePointer2,
  Redo,
  Rows3,
  Scissors,
  Table as TableIcon,
  Trash2,
  Undo,
} from "lucide-react";

import { createCoreNotesEditorExtensions } from "./editor-extensions";
import { InlineAIPopover } from "./InlineAIPopover";
import { SpreadsheetExtension } from "./SpreadsheetExtension";
import { TextFormattingControls } from "./TextFormattingControls";
import {
  applySafeAiEdit,
  captureAiSelection,
  mapAiSelection,
} from "./aiEditorTransaction";
import type {
  AiApplyAction,
  AiSelectionSnapshot,
  PopoverAnchor,
} from "./aiAssistant";
import {
  createSpreadsheetTemplate,
  SPREADSHEET_TEMPLATES,
  type SpreadsheetTemplateId,
} from "@/lib/spreadsheet-workspace";

interface TiptapEditorProps {
  initialContent?: JSONContent | null;
  noteTitle?: string;
  onChange?: (json: JSONContent, plainText: string) => void;
  savingStatus?: "saved" | "saving" | "unsaved" | "failed" | "conflict";
}

export function TiptapEditor({
  initialContent,
  noteTitle = "",
  onChange,
  savingStatus = "saved",
}: TiptapEditorProps) {
  const [templatePickerOpen, setTemplatePickerOpen] = useState(false);
  const [isAiMode, setIsAiMode] = useState(false);
  const [contextMenu, setContextMenu] = useState<PopoverAnchor | null>(null);
  const [contextMenuError, setContextMenuError] = useState("");
  const [aiSession, setAiSession] = useState<{
    anchor: PopoverAnchor;
    selection: AiSelectionSnapshot;
    context: string;
  } | null>(null);
  const [selectionIsValid, setSelectionIsValid] = useState(true);
  const [sessionNotice, setSessionNotice] = useState("");
  const templatePickerRef = useRef<HTMLDivElement | null>(null);
  const editorAreaRef = useRef<HTMLDivElement | null>(null);
  const selectionSnapshotRef = useRef<AiSelectionSnapshot | null>(null);
  const hasUnappliedResultRef = useRef(false);
  const editor = useEditor({
    extensions: [...createCoreNotesEditorExtensions(), SpreadsheetExtension],
    content: initialContent || {
      type: "doc",
      content: [
        {
          type: "paragraph",
          content: [{ type: "text", text: "歡迎使用 Personal Brain 筆記工作區！在此自由記錄想法或計算數據。" }],
        },
      ],
    },
    immediatelyRender: false,
    editorProps: {
      attributes: {
        "aria-label": "筆記內容",
        class: "notes-editor-content",
      },
    },
    onUpdate: ({ editor }) => {
      onChange?.(editor.getJSON(), editor.getText({ blockSeparator: "\n" }));
    },
    onTransaction: ({ transaction }) => {
      const snapshot = selectionSnapshotRef.current;
      if (!snapshot || !transaction.docChanged) return;
      const mapped = mapAiSelection(snapshot, transaction);
      if (!mapped.ok) {
        setSelectionIsValid(false);
        setSessionNotice(mapped.reason);
        return;
      }
      selectionSnapshotRef.current = mapped.snapshot;
      setSelectionIsValid(true);
      setAiSession((session) => session
        ? { ...session, selection: mapped.snapshot }
        : session);
    },
  });

  const toolbarState = useEditorState({
    editor,
    selector: ({ editor: currentEditor }) => {
      if (!currentEditor) {
        return {
          canRedo: false,
          canUndo: false,
          characters: 0,
          isBulletList: false,
          isCodeBlock: false,
          isOrderedList: false,
          isTable: false,
          isTaskList: false,
        };
      }
      return {
        canRedo: currentEditor.can().redo(),
        canUndo: currentEditor.can().undo(),
        characters: currentEditor.getText().length,
        isBulletList: currentEditor.isActive("bulletList"),
        isCodeBlock: currentEditor.isActive("codeBlock"),
        isOrderedList: currentEditor.isActive("orderedList"),
        isTable: currentEditor.isActive("table"),
        isTaskList: currentEditor.isActive("taskList"),
      };
    },
  });

  useEffect(() => {
    if (!templatePickerOpen) return;
    const closePicker = (event: MouseEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent && event.key === "Escape") {
        setTemplatePickerOpen(false);
        return;
      }
      if (event instanceof MouseEvent && !templatePickerRef.current?.contains(event.target as Node)) {
        setTemplatePickerOpen(false);
      }
    };
    document.addEventListener("mousedown", closePicker);
    document.addEventListener("keydown", closePicker);
    return () => {
      document.removeEventListener("mousedown", closePicker);
      document.removeEventListener("keydown", closePicker);
    };
  }, [templatePickerOpen]);

  const closeAiSession = useCallback(() => {
    selectionSnapshotRef.current = null;
    hasUnappliedResultRef.current = false;
    setAiSession(null);
    setSelectionIsValid(true);
    setSessionNotice("");
  }, []);

  const exitAiMode = useCallback(() => {
    closeAiSession();
    setContextMenu(null);
    setIsAiMode(false);
  }, [closeAiSession]);

  const handleResultStateChange = useCallback((hasResult: boolean) => {
    hasUnappliedResultRef.current = hasResult;
  }, []);

  useEffect(() => {
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || !isAiMode) return;
      event.preventDefault();
      exitAiMode();
    };
    window.addEventListener("keydown", handleEscape);
    return () => window.removeEventListener("keydown", handleEscape);
  }, [exitAiMode, isAiMode]);

  useEffect(() => {
    if (!contextMenu) return;
    const dismiss = () => setContextMenu(null);
    window.addEventListener("mousedown", dismiss);
    window.addEventListener("blur", dismiss);
    return () => {
      window.removeEventListener("mousedown", dismiss);
      window.removeEventListener("blur", dismiss);
    };
  }, [contextMenu]);

  const hasAiSession = aiSession !== null;
  useEffect(() => {
    if (!editor || !hasAiSession) return;
    const updateAnchor = () => {
      const snapshot = selectionSnapshotRef.current;
      if (!snapshot) return;
      try {
        const coordinates = editor.view.coordsAtPos(snapshot.head);
        setAiSession((session) => session
          ? { ...session, anchor: { x: coordinates.left, y: coordinates.bottom } }
          : session);
      } catch {
        setSelectionIsValid(false);
        setSessionNotice("無法重新定位原選取範圍，請重新選取。");
      }
    };
    const scrollContainer = editorAreaRef.current;
    window.addEventListener("resize", updateAnchor);
    window.addEventListener("scroll", updateAnchor, { passive: true, capture: true });
    scrollContainer?.addEventListener("scroll", updateAnchor, { passive: true });
    return () => {
      window.removeEventListener("resize", updateAnchor);
      window.removeEventListener("scroll", updateAnchor, { capture: true });
      scrollContainer?.removeEventListener("scroll", updateAnchor);
    };
  }, [editor, hasAiSession]);

  if (!editor) {
    return <div className="h-64 animate-pulse rounded-xl bg-zinc-50 dark:bg-zinc-900" />;
  }

  const insertSpreadsheet = (templateId: SpreadsheetTemplateId) => {
    editor
      .chain()
      .focus()
      .insertSpreadsheet(createSpreadsheetTemplate(templateId))
      .run();
    setTemplatePickerOpen(false);
  };

  const handleApplyAi = (text: string, action: AiApplyAction) => {
    const snapshot = selectionSnapshotRef.current;
    if (!snapshot) return;
    if (savingStatus === "conflict") {
      setSessionNotice("目前存在 OCC 版本衝突。請先重新載入或解決衝突，再套用 AI 修改。");
      return;
    }
    const result = applySafeAiEdit(editor, snapshot, text, action);
    if (!result.ok) {
      setSelectionIsValid(false);
      setSessionNotice(result.reason);
      return;
    }
    editor.commands.focus();
    closeAiSession();
  };

  const handleEditorContextMenu = (event: React.MouseEvent<HTMLDivElement>) => {
    const target = event.target as HTMLElement;
    if (target.closest(".spreadsheet-node-view, .spreadsheet-workspace-layer, input, textarea, button, [contenteditable='false']")) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    setContextMenuError("");
    setContextMenu({
      x: Math.max(8, Math.min(event.clientX, window.innerWidth - 228)),
      y: Math.max(8, Math.min(event.clientY, window.innerHeight - 258)),
    });
  };

  const handleEditorMouseUp = (event: React.MouseEvent<HTMLDivElement>) => {
    if (!isAiMode || event.button !== 0) return;
    requestAnimationFrame(() => {
      const captured = captureAiSelection(editor.state);
      if (!captured.ok) {
        if (!hasUnappliedResultRef.current) closeAiSession();
        return;
      }
      const current = selectionSnapshotRef.current;
      const sameSelection = current?.from === captured.snapshot.from
        && current.to === captured.snapshot.to
        && current.text === captured.snapshot.text;
      if (hasUnappliedResultRef.current && !sameSelection) {
        setSessionNotice("目前仍有未套用的 AI 結果。請先接受或拒絕，再選取新的文字。");
        if (current) {
          editor.view.dispatch(editor.state.tr.setSelection(
            TextSelection.create(editor.state.doc, current.anchor, current.head),
          ));
        }
        return;
      }
      if (sameSelection) return;
      selectionSnapshotRef.current = captured.snapshot;
      setSelectionIsValid(true);
      setSessionNotice("");
      setAiSession({
        anchor: { x: event.clientX, y: event.clientY },
        selection: captured.snapshot,
        context: editor.getText({ blockSeparator: "\n" }),
      });
    });
  };

  const copySelection = async (cut: boolean) => {
    const { from, to } = editor.state.selection;
    if (from === to) return;
    const text = editor.state.doc.textBetween(from, to, "\n", "\n");
    try {
      await navigator.clipboard.writeText(text);
      if (cut) editor.chain().focus().deleteSelection().run();
      setContextMenu(null);
    } catch {
      setContextMenuError("瀏覽器未允許剪貼簿存取，請使用鍵盤快捷鍵。");
    }
  };

  const pasteAtSelection = async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text) editor.chain().focus().insertContent({ type: "text", text }).run();
      setContextMenu(null);
    } catch {
      setContextMenuError("瀏覽器未允許剪貼簿存取，請使用鍵盤快捷鍵。");
    }
  };

  return (
    <div className="notes-editor relative flex flex-col rounded-xl border border-zinc-200 bg-white shadow-sm transition-shadow dark:border-zinc-800 dark:bg-zinc-950">
      {/* 編輯器頂部工具列 */}
      <div className="sticky top-0 z-10 flex flex-wrap items-center gap-1 border-b border-zinc-200 bg-white/95 px-3 py-2 backdrop-blur-sm dark:border-zinc-800 dark:bg-zinc-950/95 print:hidden">
        <TextFormattingControls editor={editor} />

        <div className="mx-1 h-4 w-px bg-zinc-200 dark:bg-zinc-800" />

        <button
          type="button"
          onClick={() => editor.chain().focus().toggleBulletList().run()}
          aria-label="項目清單"
          aria-pressed={toolbarState?.isBulletList ?? false}
          className={`rounded p-1.5 hover:bg-zinc-100 dark:hover:bg-zinc-800 ${toolbarState?.isBulletList ? "bg-zinc-100 text-indigo-600 dark:bg-zinc-800 dark:text-indigo-400" : "text-zinc-600 dark:text-zinc-400"}`}
          title="無序清單"
        >
          <List className="h-4 w-4" />
        </button>
        <button
          type="button"
          onClick={() => editor.chain().focus().toggleOrderedList().run()}
          aria-label="編號清單"
          aria-pressed={toolbarState?.isOrderedList ?? false}
          className={`rounded p-1.5 hover:bg-zinc-100 dark:hover:bg-zinc-800 ${toolbarState?.isOrderedList ? "bg-zinc-100 text-indigo-600 dark:bg-zinc-800 dark:text-indigo-400" : "text-zinc-600 dark:text-zinc-400"}`}
          title="有序清單"
        >
          <ListOrdered className="h-4 w-4" />
        </button>
        <button
          type="button"
          onClick={() => editor.chain().focus().toggleTaskList().run()}
          aria-label="待辦清單"
          aria-pressed={toolbarState?.isTaskList ?? false}
          className={`rounded p-1.5 hover:bg-zinc-100 dark:hover:bg-zinc-800 ${toolbarState?.isTaskList ? "bg-zinc-100 text-indigo-600 dark:bg-zinc-800 dark:text-indigo-400" : "text-zinc-600 dark:text-zinc-400"}`}
          title="待辦清單"
        >
          <CheckCircle2 className="h-4 w-4" />
        </button>
        <button
          type="button"
          onClick={() => editor.chain().focus().toggleCodeBlock().run()}
          aria-label="程式碼區塊"
          aria-pressed={toolbarState?.isCodeBlock ?? false}
          className={`rounded p-1.5 hover:bg-zinc-100 dark:hover:bg-zinc-800 ${toolbarState?.isCodeBlock ? "bg-zinc-100 text-indigo-600 dark:bg-zinc-800 dark:text-indigo-400" : "text-zinc-600 dark:text-zinc-400"}`}
          title="程式碼區塊"
        >
          <Code className="h-4 w-4" />
        </button>

        <div className="mx-1 h-4 w-px bg-zinc-200 dark:bg-zinc-800" />

        <button
          type="button"
          onClick={() => editor.chain().focus().insertTable({ rows: 3, cols: 3, withHeaderRow: true }).run()}
          aria-label="插入一般表格"
          className="rounded p-1.5 text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800"
          title="插入一般表格"
        >
          <TableIcon className="h-4 w-4" />
        </button>

        {/* 插入動態計算表格 (Excel-like) */}
        <div ref={templatePickerRef} className="relative">
          <button
            type="button"
            onClick={() => setTemplatePickerOpen((open) => !open)}
            aria-label="選擇計算表格模板"
            aria-haspopup="menu"
            aria-expanded={templatePickerOpen}
            className="flex items-center gap-1 rounded bg-indigo-50 px-2 py-1 text-xs font-medium text-indigo-600 hover:bg-indigo-100 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-500 dark:bg-indigo-950/60 dark:text-indigo-400 dark:hover:bg-indigo-900/60"
            title="插入計算表格"
          >
            <Calculator className="h-3.5 w-3.5" />
            計算表格
          </button>
          {templatePickerOpen ? (
            <div
              role="menu"
              aria-label="計算表格模板"
              className="absolute left-0 top-full z-30 mt-2 w-[min(20rem,calc(100vw-2rem))] rounded-xl border border-zinc-200 bg-white p-2 shadow-xl dark:border-zinc-700 dark:bg-zinc-900"
            >
              <p className="px-2 pb-1 text-[11px] font-semibold text-zinc-500 dark:text-zinc-400">選擇模板</p>
              {SPREADSHEET_TEMPLATES.map((template) => (
                <button
                  key={template.id}
                  type="button"
                  role="menuitem"
                  onClick={() => insertSpreadsheet(template.id)}
                  className="block w-full rounded-lg px-2 py-2 text-left hover:bg-zinc-100 focus-visible:outline-2 focus-visible:outline-offset-[-2px] focus-visible:outline-indigo-500 dark:hover:bg-zinc-800"
                >
                  <span className="flex items-center justify-between gap-2 text-xs font-medium text-zinc-800 dark:text-zinc-100">
                    {template.label}
                  </span>
                  <span className="mt-0.5 block text-[10px] leading-4 text-zinc-500 dark:text-zinc-400">{template.description}</span>
                </button>
              ))}
            </div>
          ) : null}
        </div>

        {toolbarState?.isTable ? (
          <div className="flex items-center gap-0.5 border-l border-zinc-200 pl-1 dark:border-zinc-800" role="group" aria-label="表格操作">
            <button type="button" onClick={() => editor.chain().focus().addRowBefore().run()} className="rounded p-1.5 text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800" aria-label="在上方新增列" title="在上方新增列"><Rows3 className="h-4 w-4" /></button>
            <button type="button" onClick={() => editor.chain().focus().addRowAfter().run()} className="rounded px-2 py-1 text-[11px] text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800" aria-label="在下方新增列" title="在下方新增列">+列</button>
            <button type="button" onClick={() => editor.chain().focus().deleteRow().run()} className="rounded px-2 py-1 text-[11px] text-rose-600 hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-950/30" aria-label="刪除目前列" title="刪除目前列">−列</button>
            <button type="button" onClick={() => editor.chain().focus().addColumnBefore().run()} className="rounded p-1.5 text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800" aria-label="在左側新增欄" title="在左側新增欄"><Columns3 className="h-4 w-4" /></button>
            <button type="button" onClick={() => editor.chain().focus().addColumnAfter().run()} className="rounded px-2 py-1 text-[11px] text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800" aria-label="在右側新增欄" title="在右側新增欄">+欄</button>
            <button type="button" onClick={() => editor.chain().focus().deleteColumn().run()} className="rounded px-2 py-1 text-[11px] text-rose-600 hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-950/30" aria-label="刪除目前欄" title="刪除目前欄">−欄</button>
            <button type="button" onClick={() => editor.chain().focus().deleteTable().run()} className="rounded p-1.5 text-rose-600 hover:bg-rose-50 dark:text-rose-400 dark:hover:bg-rose-950/30" aria-label="刪除表格" title="刪除表格"><Trash2 className="h-4 w-4" /></button>
          </div>
        ) : null}

        <div className="ml-auto flex items-center gap-1">
          {isAiMode ? (
            <span className="mr-1 hidden items-center gap-1 rounded-full bg-sky-50 px-2 py-1 text-[11px] font-medium text-sky-700 sm:flex dark:bg-sky-950/40 dark:text-sky-300">
              <MousePointer2 className="h-3 w-3" />AI 模式 · Esc 退出
            </span>
          ) : null}
          <button
            type="button"
            onClick={() => editor.chain().focus().undo().run()}
            disabled={!toolbarState?.canUndo}
            aria-label="復原"
            className="rounded p-1.5 text-zinc-400 hover:bg-zinc-100 disabled:opacity-30 dark:hover:bg-zinc-800"
            title="復原"
          >
            <Undo className="h-4 w-4" />
          </button>
          <button
            type="button"
            onClick={() => editor.chain().focus().redo().run()}
            disabled={!toolbarState?.canRedo}
            aria-label="重做"
            className="rounded p-1.5 text-zinc-400 hover:bg-zinc-100 disabled:opacity-30 dark:hover:bg-zinc-800"
            title="重做"
          >
            <Redo className="h-4 w-4" />
          </button>
        </div>
      </div>

      {/* 編輯器主要內容區 */}
      <div
        ref={editorAreaRef}
        className={`flex flex-1 px-5 py-6 sm:px-8 sm:py-8 lg:px-10 ${isAiMode ? "ai-cursor-mode" : ""}`}
        onContextMenu={handleEditorContextMenu}
        onMouseUp={handleEditorMouseUp}
        onMouseDown={() => setContextMenu(null)}
      >
        <EditorContent editor={editor} className="w-full" />
      </div>

      {contextMenu ? (
        <div
          role="menu"
          aria-label="筆記編輯選單"
          className="fixed z-[80] w-[220px] overflow-hidden rounded-xl border border-neutral-200 bg-white p-1.5 shadow-xl dark:border-neutral-700 dark:bg-neutral-900"
          style={{ left: contextMenu.x, top: contextMenu.y }}
          onMouseDown={(event) => event.stopPropagation()}
        >
          <button
            type="button"
            role="menuitem"
            onClick={() => {
              if (isAiMode) exitAiMode();
              else setIsAiMode(true);
              setContextMenu(null);
            }}
            className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-left text-sm font-medium text-sky-700 hover:bg-sky-50 dark:text-sky-300 dark:hover:bg-sky-950/40"
          >
            {isAiMode ? <LogOut className="h-4 w-4" /> : <MousePointer2 className="h-4 w-4" />}
            {isAiMode ? "退出 AI 助理" : "啟用 AI 助理"}
          </button>
          <div className="my-1 h-px bg-neutral-100 dark:bg-neutral-800" />
          <button type="button" role="menuitem" onClick={() => void copySelection(false)} className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs text-neutral-700 hover:bg-neutral-100 dark:text-neutral-200 dark:hover:bg-neutral-800"><Copy className="h-3.5 w-3.5" />複製<span className="ml-auto text-neutral-400">⌘C</span></button>
          <button type="button" role="menuitem" onClick={() => void copySelection(true)} className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs text-neutral-700 hover:bg-neutral-100 dark:text-neutral-200 dark:hover:bg-neutral-800"><Scissors className="h-3.5 w-3.5" />剪下<span className="ml-auto text-neutral-400">⌘X</span></button>
          <button type="button" role="menuitem" onClick={() => void pasteAtSelection()} className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs text-neutral-700 hover:bg-neutral-100 dark:text-neutral-200 dark:hover:bg-neutral-800"><Clipboard className="h-3.5 w-3.5" />貼上<span className="ml-auto text-neutral-400">⌘V</span></button>
          <button type="button" role="menuitem" onClick={() => { editor.chain().focus().selectAll().run(); setContextMenu(null); }} className="flex w-full items-center gap-2 rounded-lg px-2.5 py-1.5 text-left text-xs text-neutral-700 hover:bg-neutral-100 dark:text-neutral-200 dark:hover:bg-neutral-800"><MousePointer2 className="h-3.5 w-3.5" />全選<span className="ml-auto text-neutral-400">⌘A</span></button>
          {contextMenuError ? <p role="alert" className="mx-1 mt-1 rounded-md bg-amber-50 px-2 py-1.5 text-[10px] leading-4 text-amber-800 dark:bg-amber-950/40 dark:text-amber-300">{contextMenuError}</p> : null}
        </div>
      ) : null}

      {aiSession ? (
        <InlineAIPopover
          key={`${aiSession.selection.from}-${aiSession.selection.to}-${aiSession.selection.documentFingerprint}`}
          anchor={aiSession.anchor}
          selection={aiSession.selection}
          context={aiSession.context}
          noteTitle={noteTitle}
          selectionIsValid={selectionIsValid}
          externalNotice={sessionNotice}
          onApply={handleApplyAi}
          onReject={closeAiSession}
          onResultStateChange={handleResultStateChange}
        />
      ) : null}

      {/* 底部狀態列 */}
      <div className="flex items-center justify-between border-t border-zinc-100 px-4 py-2 text-xs text-zinc-400 dark:border-zinc-900 print:hidden">
        <div>
          {toolbarState?.characters ?? 0} 字
        </div>
        <div className="capitalize">
          {savingStatus === "saving" && "儲存中..."}
          {savingStatus === "saved" && "已儲存"}
          {savingStatus === "unsaved" && "未儲存"}
          {savingStatus === "failed" && "儲存失敗"}
          {savingStatus === "conflict" && "版本衝突"}
        </div>
      </div>
    </div>
  );
}
