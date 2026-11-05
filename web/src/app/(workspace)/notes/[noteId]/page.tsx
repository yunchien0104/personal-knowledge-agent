"use client";

import type { JSONContent } from "@tiptap/core";
import {
  AlertTriangle,
  ArrowLeft,
  BookOpen,
  Check,
  Download,
  FileDown,
  Loader2,
  RefreshCw,
  Save,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useCallback, useEffect, useRef, useState } from "react";

import { NotesSidebar } from "@/components/notes/NotesSidebar";
import { TiptapEditor } from "@/components/notes/TiptapEditor";
import {
  createNoteAutosaveQueue,
  type NoteAutosaveQueue,
  type NoteDraft,
  type NoteSavingStatus,
} from "@/lib/note-autosave";
import { appendImportedText } from "@/lib/note-import";
import { createNotePdfSnapshot, notePdfFilename, type NotePdfCitation } from "@/lib/note-pdf";

type LocalNoteBackup = {
  baseRevision: number;
  content: JSONContent | null;
  dirty: boolean;
  plainText: string;
  title: string;
  updatedAt: string;
};

export default function NoteDetailPage({ params }: { params: Promise<{ noteId: string }> }) {
  const { noteId } = use(params);
  const router = useRouter();
  const [title, setTitle] = useState("未命名筆記");
  const [content, setContent] = useState<JSONContent | null>(null);
  const [loading, setLoading] = useState(true);
  const [revision, setRevision] = useState(1);
  const [savingStatus, setSavingStatus] = useState<NoteSavingStatus>("saved");
  const [conflictModalOpen, setConflictModalOpen] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [editorResetKey, setEditorResetKey] = useState(0);
  const saveTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const autosaveRef = useRef<NoteAutosaveQueue | null>(null);
  const titleRef = useRef(title);
  const contentRef = useRef<JSONContent | null>(content);
  const plainTextRef = useRef("");
  const citationsRef = useRef<NotePdfCitation[] | null>(null);

  // 側欄與匯出狀態
  const [sidebarOpen, setSidebarOpen] = useState(false);
  const [exportingPdf, setExportingPdf] = useState(false);
  const [pdfExportMessage, setPdfExportMessage] = useState("");

  const localBackupKey = `pb_note_${noteId}`;

  const buildAutosaveQueue = useCallback((draft: NoteDraft, initialRevision: number) => {
    return createNoteAutosaveQueue({
      initialDraft: draft,
      initialRevision,
      onConflict: () => setConflictModalOpen(true),
      onError: (error) => console.warn("儲存失敗", error),
      onRevision: setRevision,
      onStatus: setSavingStatus,
      onSaved: (request, bodyRevision, isLatest) => {
        if (!isLatest) return;
        localStorage.setItem(localBackupKey, JSON.stringify({
          baseRevision: bodyRevision,
          content: request.contentJson,
          dirty: false,
          plainText: request.plainText,
          title: request.title,
          updatedAt: new Date().toISOString(),
        } satisfies LocalNoteBackup));
      },
      save: async (request) => {
        localStorage.setItem(localBackupKey, JSON.stringify({
          baseRevision: request.expectedRevision,
          content: request.contentJson,
          dirty: true,
          plainText: request.plainText,
          title: request.title,
          updatedAt: new Date().toISOString(),
        } satisfies LocalNoteBackup));

        const response = await fetch(`/api/personal-notes/${noteId}`, {
          method: "PATCH",
          headers: { "Content-Type": "application/json" },
          keepalive: true,
          body: JSON.stringify({
            title: request.title,
            content_json: request.contentJson,
            plain_text: request.plainText,
            expected_revision: request.expectedRevision,
            reason: request.reason,
          }),
        });

        if (response.status === 409) return { status: "conflict" as const };
        if (!response.ok) throw new Error(`儲存失敗 (${response.status})`);

        const json = (await response.json()) as { data?: { body_revision?: number } };
        const bodyRevision = json.data?.body_revision;
        if (!bodyRevision) throw new Error("伺服器未回傳有效版本");

        return { status: "saved" as const, bodyRevision };
      },
    });
  }, [localBackupKey, noteId]);

  useEffect(() => {
    let active = true;
    fetch(`/api/personal-notes/${noteId}`)
      .then((res) => {
        if (!res.ok) throw new Error("讀取失敗");
        return res.json();
      })
      .then((json: { data?: { title?: string; body_revision?: number; content_json?: JSONContent; plain_text?: string } }) => {
        if (!active) return;
        if (json?.data) {
          const serverRevision = json.data.body_revision ?? 1;
          const serverDraft: NoteDraft = {
            title: json.data.title || "未命名筆記",
            contentJson: json.data.content_json ?? { type: "doc", content: [] },
            plainText: json.data.plain_text ?? "",
          };
          let draft = serverDraft;
          let backup: LocalNoteBackup | null = null;
          try {
            backup = JSON.parse(localStorage.getItem(localBackupKey) ?? "null") as LocalNoteBackup | null;
          } catch {
            localStorage.removeItem(localBackupKey);
          }
          const recoverLocalDraft = Boolean(backup?.dirty && backup.content);
          if (recoverLocalDraft && backup) {
            draft = {
              title: backup.title,
              contentJson: backup.content,
              plainText: backup.plainText,
            };
          }

          titleRef.current = draft.title;
          contentRef.current = draft.contentJson;
          plainTextRef.current = draft.plainText;
          setTitle(draft.title);
          setContent(draft.contentJson);
          setRevision(serverRevision);
          autosaveRef.current = buildAutosaveQueue(draft, serverRevision);

          if (recoverLocalDraft) {
            autosaveRef.current.update(draft);
            if (backup?.baseRevision !== serverRevision) {
              autosaveRef.current.markConflict();
            } else {
              void autosaveRef.current.requestSave("autosave");
            }
          } else {
            setSavingStatus("saved");
            setConflictModalOpen(false);
          }
        }
      })
      .catch((err: unknown) => {
        if (!active) return;
        console.warn("後端載入失敗，使用緊急本機暫存", err);
        const local = localStorage.getItem(localBackupKey);
        if (local) {
          try {
            const parsed = JSON.parse(local) as LocalNoteBackup;
            titleRef.current = parsed.title;
            contentRef.current = parsed.content;
            plainTextRef.current = parsed.plainText;
            setTitle(parsed.title);
            setContent(parsed.content);
            setRevision(parsed.baseRevision);
            autosaveRef.current = buildAutosaveQueue({
              title: parsed.title,
              contentJson: parsed.content,
              plainText: parsed.plainText,
            }, parsed.baseRevision);
            autosaveRef.current.update(autosaveRef.current.getDraft());
            setSavingStatus("failed");
          } catch {
            localStorage.removeItem(localBackupKey);
          }
        } else {
          setSavingStatus("failed");
        }
      })
      .finally(() => {
        if (active) setLoading(false);
      });

    return () => {
      active = false;
    };
  }, [buildAutosaveQueue, localBackupKey, noteId, reloadKey]);

  useEffect(() => {
    let active = true;
    citationsRef.current = null;
    fetch(`/api/personal-notes/${noteId}/citations`, { cache: "no-store" })
      .then(async (response) => {
        if (!response.ok) throw new Error(`引用資料讀取失敗 (${response.status})`);
        return response.json() as Promise<{ data?: { citations?: NotePdfCitation[] } }>;
      })
      .then((body) => {
        if (active) citationsRef.current = body.data?.citations ?? [];
      })
      .catch((error: unknown) => console.warn("引用資料預載失敗", error));
    return () => {
      active = false;
    };
  }, [noteId]);

  useEffect(() => {
    const flushPendingSave = () => {
      if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
      if (autosaveRef.current?.hasUnsavedChanges()) {
        void autosaveRef.current.requestSave("autosave");
      }
    };
    window.addEventListener("pagehide", flushPendingSave);
    return () => {
      window.removeEventListener("pagehide", flushPendingSave);
      flushPendingSave();
    };
  }, []);

  function scheduleAutosave(draft: NoteDraft) {
    autosaveRef.current?.update(draft);
    localStorage.setItem(localBackupKey, JSON.stringify({
      baseRevision: autosaveRef.current?.getRevision() ?? revision,
      content: draft.contentJson,
      dirty: true,
      plainText: draft.plainText,
      title: draft.title,
      updatedAt: new Date().toISOString(),
    } satisfies LocalNoteBackup));
    if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
    saveTimeoutRef.current = setTimeout(() => {
      void autosaveRef.current?.requestSave("autosave");
    }, 600);
  }

  function handleContentChange(jsonContent: JSONContent, plainText: string) {
    contentRef.current = jsonContent;
    plainTextRef.current = plainText;
    setContent(jsonContent);
    scheduleAutosave({ title: titleRef.current, contentJson: jsonContent, plainText });
  }

  function handleTitleChange(e: React.ChangeEvent<HTMLInputElement>) {
    const newTitle = e.target.value;
    titleRef.current = newTitle;
    setTitle(newTitle);
    scheduleAutosave({ title: newTitle, contentJson: contentRef.current, plainText: plainTextRef.current });
  }

  async function handleInsertText(text: string) {
    const nextContent = appendImportedText(contentRef.current, text);
    const nextPlainText = [plainTextRef.current, text].filter(Boolean).join("\n");
    contentRef.current = nextContent;
    plainTextRef.current = nextPlainText;
    setContent(nextContent);
    setEditorResetKey((value) => value + 1);
    autosaveRef.current?.update({ title: titleRef.current, contentJson: nextContent, plainText: nextPlainText });
    await autosaveRef.current?.requestSave("manual");
    if (autosaveRef.current?.hasUnsavedChanges()) {
      throw new Error("內容已顯示在編輯器，但尚未成功儲存，請按「儲存」重試");
    }
  }

  async function handleDownloadPdf() {
    if (exportingPdf) return;
    setExportingPdf(true);
    setPdfExportMessage("");

    try {
      // Committing an active spreadsheet cell happens on blur. Wait one frame so
      // its onChange reaches the editor before taking the immutable PDF snapshot.
      if (document.activeElement instanceof HTMLElement) document.activeElement.blur();
      await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));

      let citations = citationsRef.current ?? [];
      let citationWarning = "";
      try {
        const response = await fetch(`/api/personal-notes/${noteId}/citations`, { cache: "no-store" });
        if (!response.ok) throw new Error(`引用資料讀取失敗 (${response.status})`);
        const body = (await response.json()) as { data?: { citations?: NotePdfCitation[] } };
        citations = body.data?.citations ?? [];
        citationsRef.current = citations;
      } catch (error) {
        console.warn("PDF 使用快取引用資料", error);
        citationWarning = citationsRef.current
          ? "引用資料更新失敗，已使用最近載入的引用快照。"
          : "引用資料目前無法載入，這份 PDF 未包含引用附錄。";
      }
      const currentDraft = autosaveRef.current?.getDraft();
      const currentStatus = autosaveRef.current?.hasUnsavedChanges() && savingStatus === "saved"
        ? "unsaved"
        : savingStatus;
      const snapshot = createNotePdfSnapshot({
        title: currentDraft?.title ?? titleRef.current,
        content: currentDraft?.contentJson ?? contentRef.current,
        citations,
        savingStatus: currentStatus,
      });

      const { generateNotePdfBlob } = await import("@/components/notes/NotePdfDocument");
      const blob = await generateNotePdfBlob(snapshot);
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = notePdfFilename(snapshot.title);
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1_000);

      const completionMessage = snapshot.includesLocalDraft
        ? "PDF 已包含目前畫面中的本機內容；未儲存或衝突狀態仍會保留。"
        : "PDF 下載完成。";
      setPdfExportMessage([completionMessage, citationWarning].filter(Boolean).join(" "));
    } catch (error) {
      console.warn("PDF 匯出失敗", error);
      setPdfExportMessage(error instanceof Error ? `PDF 匯出失敗：${error.message}` : "PDF 匯出失敗，請稍後再試。");
    } finally {
      setExportingPdf(false);
    }
  }

  function handleExportMarkdown() {
    let md = `# ${title}\n\n`;

    function parseNode(node: JSONContent): string {
      if (!node) return "";
      if (node.type === "text") return node.text || "";
      const inner = (node.content || []).map(parseNode).join("");

      switch (node.type) {
        case "heading":
          return `\n${"#".repeat(Number(node.attrs?.level) || 1)} ${inner}\n\n`;
        case "paragraph":
          return `${inner}\n\n`;
        case "bulletList":
          return `${inner}\n`;
        case "orderedList":
          return `${inner}\n`;
        case "listItem":
          return `- ${inner}`;
        case "codeBlock":
          return `\`\`\`\n${inner}\n\`\`\`\n\n`;
        case "spreadsheetBlock": {
          const spData = node.attrs?.data as { rows: number; cols: number; cells?: Record<string, { raw?: string }> } | undefined;
          if (!spData) return "> [Excel 計算表格]\n\n";
          let tableMd = "\n> [Excel 計算表格快照]\n\n";
          for (let r = 0; r < spData.rows; r++) {
            const rowVals: string[] = [];
            for (let c = 0; c < spData.cols; c++) {
              rowVals.push(spData.cells?.[r + "," + c]?.raw || "");
            }
            tableMd += `| ${rowVals.join(" | ")} |\n`;
            if (r === 0) {
              tableMd += `| ${rowVals.map(() => "---").join(" | ")} |\n`;
            }
          }
          return tableMd + "\n";
        }
        default:
          return inner;
      }
    }

    if (content?.content) {
      md += content.content.map(parseNode).join("");
    } else {
      md += "*(筆記內容空白)*";
    }

    const blob = new Blob([md], { type: "text/markdown;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = `${title.replace(/[/\\?%*:|"<>]/g, "_")}.md`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(url);
  }

  async function handleDelete() {
    if (!confirm("確定要刪除這篇筆記嗎？")) return;
    localStorage.removeItem(localBackupKey);

    try {
      await fetch(`/api/personal-notes/${noteId}`, { method: "DELETE" });
    } catch (err: unknown) {
      console.warn("後端刪除失敗", err);
    }
    router.push("/notes");
  }

  if (loading) {
    return <div className="p-12 text-center text-sm text-neutral-500">正在讀取筆記...</div>;
  }

  return (
    <div className="mx-auto w-full max-w-[90rem] px-3 py-4 sm:px-5 sm:py-5 lg:px-8 print:max-w-none print:p-0">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2 print:hidden">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <Link
            href="/notes"
            className="flex items-center gap-1.5 text-sm text-neutral-500 hover:text-neutral-900 dark:hover:text-neutral-100"
          >
            <ArrowLeft className="h-4 w-4" />
            返回列表
          </Link>

          <div className="flex items-center gap-1.5 text-xs text-neutral-400">
            {savingStatus === "saving" && (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin text-neutral-500" />
                <span>儲存中...</span>
              </>
            )}
            {savingStatus === "saved" && (
              <>
                <Check className="h-3.5 w-3.5 text-emerald-600" />
                <span className="text-emerald-600 dark:text-emerald-400">已儲存 (Rev {revision})</span>
              </>
            )}
            {savingStatus === "unsaved" && <span>未儲存變更...</span>}
            {savingStatus === "failed" && (
              <span className="text-red-500 font-medium">儲存失敗，請檢查網路</span>
            )}
            {savingStatus === "conflict" && (
              <span className="flex items-center gap-1 text-amber-600 font-medium">
                <AlertTriangle className="h-3.5 w-3.5" />
                版本衝突
              </span>
            )}
          </div>
        </div>

        <div className="flex w-full flex-wrap items-center gap-1.5 lg:w-auto lg:justify-end">
          <button
            onClick={() => setSidebarOpen(true)}
            className="flex items-center gap-1.5 rounded-lg border border-neutral-200 px-3 py-1.5 text-xs font-medium text-neutral-700 hover:bg-neutral-50 dark:border-neutral-800 dark:text-neutral-300 dark:hover:bg-neutral-800"
          >
            <BookOpen className="h-3.5 w-3.5" />
            插入引用
          </button>

          <button
            type="button"
            onClick={() => void handleDownloadPdf()}
            disabled={exportingPdf}
            className="flex items-center gap-1.5 rounded-lg border border-neutral-200 px-3 py-1.5 text-xs font-medium text-neutral-700 hover:bg-neutral-50 disabled:cursor-wait disabled:opacity-60 dark:border-neutral-800 dark:text-neutral-300 dark:hover:bg-neutral-800"
            title="下載只包含筆記內容與引用資訊的 A4 PDF"
          >
            {exportingPdf ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <FileDown className="h-3.5 w-3.5" />}
            {exportingPdf ? "產生中..." : "下載 PDF"}
          </button>

          <button
            onClick={handleExportMarkdown}
            className="flex items-center gap-1.5 rounded-lg border border-neutral-200 px-3 py-1.5 text-xs font-medium text-neutral-700 hover:bg-neutral-50 dark:border-neutral-800 dark:text-neutral-300 dark:hover:bg-neutral-800"
          >
            <Download className="h-3.5 w-3.5" />
            Markdown
          </button>

          <button
            onClick={() => {
              if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
              void autosaveRef.current?.requestSave("manual");
            }}
            className="flex items-center gap-1.5 rounded-lg border border-neutral-200 px-3 py-1.5 text-xs font-medium text-neutral-700 hover:bg-neutral-50 dark:border-neutral-800 dark:text-neutral-300 dark:hover:bg-neutral-800"
          >
            <Save className="h-3.5 w-3.5" />
            手動儲存
          </button>

          <button
            onClick={handleDelete}
            className="flex items-center gap-1.5 rounded-lg border border-red-200 px-3 py-1.5 text-xs font-medium text-red-600 hover:bg-red-50 dark:border-red-900 dark:text-red-400 dark:hover:bg-red-950/40"
          >
            <Trash2 className="h-3.5 w-3.5" />
            刪除
          </button>
        </div>
      </div>

      {pdfExportMessage ? (
        <p className="mb-2 text-right text-xs text-neutral-500 dark:text-neutral-400" role="status" aria-live="polite">
          {pdfExportMessage}
        </p>
      ) : null}

      <input
        type="text"
        value={title}
        onChange={handleTitleChange}
        placeholder="請輸入筆記標題..."
        className="mb-3 w-full border-b border-transparent bg-transparent text-3xl font-bold text-neutral-900 outline-none transition focus:border-neutral-300 dark:text-neutral-100 dark:focus:border-neutral-700 print:mb-4 print:border-none"
      />

      <TiptapEditor
        key={`${noteId}:${reloadKey}:${editorResetKey}`}
        initialContent={content}
        noteTitle={title}
        onChange={handleContentChange}
        savingStatus={savingStatus}
      />

      <NotesSidebar
        isOpen={sidebarOpen}
        onClose={() => setSidebarOpen(false)}
        currentNoteId={noteId}
        onInsertContent={handleInsertText}
      />

      {conflictModalOpen && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 px-4 backdrop-blur-sm">
          <div className="w-full max-w-md rounded-xl border border-amber-200 bg-white p-6 shadow-xl dark:border-amber-900 dark:bg-neutral-900">
            <div className="flex items-center gap-2.5 text-amber-600 dark:text-amber-400">
              <AlertTriangle className="h-5 w-5" />
              <h3 className="text-base font-semibold">偵測到版本衝突 (OCC Conflict)</h3>
            </div>
            <p className="mt-3 text-xs text-neutral-600 dark:text-neutral-400 leading-relaxed">
              此筆記已在其他視窗或連線中被修改。為避免未儲存的輸入遺失，系統已暫停自動覆蓋。
            </p>
            <div className="mt-6 flex flex-col gap-2 sm:flex-row sm:justify-end">
              <button
                onClick={() => {
                  if (saveTimeoutRef.current) clearTimeout(saveTimeoutRef.current);
                  localStorage.removeItem(localBackupKey);
                  setConflictModalOpen(false);
                  setLoading(true);
                  setReloadKey((prev) => prev + 1);
                }}
                className="inline-flex items-center justify-center gap-1.5 rounded-lg border border-neutral-200 px-3 py-1.5 text-xs font-medium text-neutral-700 hover:bg-neutral-50 dark:border-neutral-800 dark:text-neutral-300 dark:hover:bg-neutral-800"
              >
                <RefreshCw className="h-3.5 w-3.5" />
                放棄本機變更，重新載入最新版本
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
