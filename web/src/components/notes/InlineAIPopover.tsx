"use client";

import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import {
  Check,
  ChevronDown,
  Copy,
  CornerDownLeft,
  Loader2,
  RefreshCw,
  SlidersHorizontal,
  X,
} from "lucide-react";

import {
  buildWordDiff,
  clampPopoverPosition,
  requestAiSuggestion,
  type AiApplyAction,
  type AiOperation,
  type AiSelectionSnapshot,
  type PopoverAnchor,
} from "./aiAssistant";

const OPERATION_TAGS: Array<{ key: AiOperation; label: string }> = [
  { key: "improve", label: "Improve" },
  { key: "rewrite", label: "Rewrite" },
  { key: "summarize", label: "Summarize" },
  { key: "expand", label: "Expand" },
  { key: "translate_en", label: "Translate EN" },
  { key: "translate_zh", label: "Translate ZH" },
];

interface InlineAIPopoverProps {
  anchor: PopoverAnchor;
  selection: AiSelectionSnapshot;
  context: string;
  noteTitle: string;
  selectionIsValid: boolean;
  externalNotice?: string;
  onApply: (text: string, action: AiApplyAction) => void;
  onReject: () => void;
  onResultStateChange: (hasUnappliedResult: boolean) => void;
}

export function InlineAIPopover({
  anchor,
  selection,
  context,
  noteTitle,
  selectionIsValid,
  externalNotice,
  onApply,
  onReject,
  onResultStateChange,
}: InlineAIPopoverProps) {
  const popoverRef = useRef<HTMLDivElement>(null);
  const requestSequence = useRef(0);
  const abortControllerRef = useRef<AbortController | null>(null);
  const [position, setPosition] = useState(anchor);
  const [activeOp, setActiveOp] = useState<AiOperation>("improve");
  const [customPrompt, setCustomPrompt] = useState("");
  const [refinePrompt, setRefinePrompt] = useState("");
  const [suggestedText, setSuggestedText] = useState("");
  const [lastRequest, setLastRequest] = useState<{
    operation: AiOperation;
    instruction: string;
    baseText?: string;
  } | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");
  const [copied, setCopied] = useState(false);
  const [showOriginal, setShowOriginal] = useState(false);

  const diff = useMemo(
    () => buildWordDiff(selection.text, suggestedText),
    [selection.text, suggestedText],
  );

  useEffect(() => {
    onResultStateChange(Boolean(suggestedText));
    return () => onResultStateChange(false);
  }, [onResultStateChange, suggestedText]);

  useEffect(() => {
    const updatePosition = () => {
      const element = popoverRef.current;
      setPosition(clampPopoverPosition(
        anchor,
        { width: window.innerWidth, height: window.innerHeight },
        { width: element?.offsetWidth ?? 360, height: element?.offsetHeight ?? 560 },
      ));
    };
    updatePosition();
    const frame = requestAnimationFrame(updatePosition);
    const observer = new ResizeObserver(updatePosition);
    if (popoverRef.current) observer.observe(popoverRef.current);
    window.addEventListener("resize", updatePosition);
    return () => {
      cancelAnimationFrame(frame);
      observer.disconnect();
      window.removeEventListener("resize", updatePosition);
    };
  }, [anchor]);

  useEffect(() => () => {
    abortControllerRef.current?.abort();
    requestSequence.current += 1;
  }, []);

  async function triggerAiCall(
    operation: AiOperation,
    instruction: string,
    baseText?: string,
  ) {
    abortControllerRef.current?.abort();
    const abortController = new AbortController();
    abortControllerRef.current = abortController;
    const sequence = ++requestSequence.current;
    setIsLoading(true);
    setErrorMessage("");
    setLastRequest({ operation, instruction, baseText });
    try {
      const generatedText = await requestAiSuggestion(
        {
          operation,
          instruction,
          selectedText: baseText ?? selection.text,
          context,
          noteTitle,
        },
        abortController.signal,
      );
      if (sequence === requestSequence.current) {
        setSuggestedText(generatedText);
        setActiveOp(operation);
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === "AbortError") return;
      if (sequence === requestSequence.current) {
        setErrorMessage(error instanceof Error ? error.message : "生成失敗，請稍後再試。");
      }
    } finally {
      if (sequence === requestSequence.current) setIsLoading(false);
    }
  }

  const handleCustomSubmit = (event: FormEvent) => {
    event.preventDefault();
    const instruction = customPrompt.trim();
    if (instruction) void triggerAiCall("custom", instruction);
  };

  const handleRefineSubmit = (event: FormEvent) => {
    event.preventDefault();
    const instruction = refinePrompt.trim();
    if (!instruction || !suggestedText) return;
    void triggerAiCall("custom", `請基於前一版內容進行微調：${instruction}`, suggestedText);
    setRefinePrompt("");
  };

  const handleCopy = async () => {
    if (!suggestedText) return;
    try {
      await navigator.clipboard.writeText(suggestedText);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2_000);
    } catch {
      setErrorMessage("無法存取剪貼簿，請檢查瀏覽器權限。");
    }
  };

  return (
    <div
      ref={popoverRef}
      data-inline-ai-popover
      role="dialog"
      aria-label="Inline AI 助理"
      className="fixed z-[70] flex max-h-[min(680px,calc(100vh-24px))] w-[min(360px,calc(100vw-24px))] flex-col overflow-hidden rounded-2xl border border-sky-200 bg-white shadow-2xl dark:border-sky-900 dark:bg-neutral-900"
      style={{ left: position.x, top: position.y }}
      onMouseDown={(event) => event.stopPropagation()}
    >
      <div className="flex items-center justify-between border-b border-neutral-200 px-3 py-2.5 dark:border-neutral-800">
        <div>
          <p className="text-sm font-semibold text-neutral-900 dark:text-neutral-100">Inline AI</p>
          <p className="text-[11px] text-neutral-500">已鎖定 {selection.text.length} 個字元</p>
        </div>
        <button type="button" onClick={onReject} className="rounded-lg p-1.5 text-neutral-400 hover:bg-neutral-100 dark:hover:bg-neutral-800" aria-label="拒絕建議並關閉">
          <X className="h-4 w-4" />
        </button>
      </div>

      <div className="flex-1 space-y-3 overflow-y-auto p-3">
        <div className="flex flex-wrap gap-1.5" aria-label="AI 快捷操作">
          {OPERATION_TAGS.map((tag) => (
            <button
              key={tag.key}
              type="button"
              onClick={() => void triggerAiCall(tag.key, "")}
              disabled={isLoading}
              className={`rounded-full border px-2.5 py-1 text-[11px] font-medium transition disabled:opacity-50 ${activeOp === tag.key ? "border-sky-600 bg-sky-600 text-white" : "border-neutral-200 bg-white text-neutral-700 hover:border-sky-300 hover:bg-sky-50 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-200"}`}
            >
              {tag.label}
            </button>
          ))}
        </div>

        <form onSubmit={handleCustomSubmit} className="flex gap-2">
          <textarea
            value={customPrompt}
            onChange={(event) => setCustomPrompt(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Enter" && !event.shiftKey) {
                event.preventDefault();
                handleCustomSubmit(event);
              }
            }}
            placeholder="直接輸入指令…"
            aria-label="自訂 AI 指令"
            rows={2}
            className="min-w-0 flex-1 resize-none rounded-xl border border-neutral-200 bg-white px-3 py-2 text-xs text-neutral-900 outline-none focus:border-sky-500 dark:border-neutral-700 dark:bg-neutral-800 dark:text-neutral-100"
          />
          <button type="submit" disabled={isLoading || !customPrompt.trim()} className="self-stretch rounded-xl bg-sky-600 px-3 text-white hover:bg-sky-700 disabled:opacity-40" aria-label="送出自訂指令">
            <CornerDownLeft className="h-4 w-4" />
          </button>
        </form>

        {errorMessage ? <div role="alert" className="rounded-lg bg-red-50 px-3 py-2 text-xs text-red-700 dark:bg-red-950/30 dark:text-red-300">{errorMessage}</div> : null}
        {externalNotice ? <div role="status" className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">{externalNotice}</div> : null}
        {!selectionIsValid ? <div role="alert" className="rounded-lg bg-amber-50 px-3 py-2 text-xs text-amber-800 dark:bg-amber-950/30 dark:text-amber-300">原選取內容或文件結構已變更。請拒絕此結果並重新選取文字。</div> : null}

        <div className="rounded-xl border border-sky-100 bg-sky-50/40 p-3 dark:border-sky-900/60 dark:bg-sky-950/20">
          <div className="mb-2 flex items-center justify-between">
            <span className="text-xs font-semibold text-sky-800 dark:text-sky-200">AI 建議</span>
            {isLoading ? <span className="flex items-center gap-1 text-[11px] text-sky-700 dark:text-sky-300"><Loader2 className="h-3 w-3 animate-spin" />生成中</span> : null}
          </div>
          <div className="max-h-40 overflow-y-auto whitespace-pre-wrap text-xs leading-5 text-neutral-800 dark:text-neutral-100">
            {suggestedText || (isLoading ? "正在產生建議…" : "選擇快捷操作或輸入自訂指令。")}
          </div>
        </div>

        {suggestedText ? (
          <details className="rounded-xl border border-neutral-200 bg-neutral-50 dark:border-neutral-800 dark:bg-neutral-950/30" open>
            <summary className="cursor-pointer select-none px-3 py-2 text-xs font-medium text-neutral-700 dark:text-neutral-300">修改差異預覽</summary>
            <div className="max-h-32 overflow-y-auto border-t border-neutral-200 px-3 py-2 text-xs leading-5 dark:border-neutral-800">
              {diff.map((part, index) => (
                <span key={`${part.type}-${index}`} className={part.type === "added" ? "bg-emerald-100 text-emerald-900 dark:bg-emerald-950 dark:text-emerald-200" : part.type === "removed" ? "bg-red-100 text-red-800 line-through dark:bg-red-950 dark:text-red-300" : "text-neutral-700 dark:text-neutral-300"}>{part.value}</span>
              ))}
            </div>
          </details>
        ) : null}

        <button type="button" onClick={() => setShowOriginal((value) => !value)} className="flex w-full items-center justify-between rounded-lg px-1 text-left text-xs font-medium text-neutral-600 dark:text-neutral-400" aria-expanded={showOriginal}>
          原始文字
          <ChevronDown className={`h-3.5 w-3.5 transition ${showOriginal ? "rotate-180" : ""}`} />
        </button>
        {showOriginal ? <div className="max-h-28 overflow-y-auto whitespace-pre-wrap rounded-lg bg-neutral-100 p-2.5 text-xs leading-5 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300">{selection.text}</div> : null}

        {suggestedText ? (
          <form onSubmit={handleRefineSubmit} className="rounded-xl border border-neutral-200 p-2.5 dark:border-neutral-800">
            <label htmlFor="ai-refine-prompt" className="mb-1.5 flex items-center gap-1.5 text-[11px] font-medium text-neutral-600 dark:text-neutral-300"><SlidersHorizontal className="h-3.5 w-3.5 text-sky-600" />繼續微調</label>
            <div className="flex gap-2">
              <input id="ai-refine-prompt" value={refinePrompt} onChange={(event) => setRefinePrompt(event.target.value)} placeholder="例如：再精簡一點" className="min-w-0 flex-1 rounded-lg border border-neutral-200 px-2.5 py-1.5 text-xs outline-none focus:border-sky-500 dark:border-neutral-700 dark:bg-neutral-800" />
              <button type="submit" disabled={isLoading || !refinePrompt.trim()} className="rounded-lg bg-neutral-800 px-2.5 py-1.5 text-[11px] text-white disabled:opacity-40 dark:bg-neutral-100 dark:text-neutral-900">微調</button>
            </div>
          </form>
        ) : null}
      </div>

      <div className="space-y-2 border-t border-neutral-200 p-3 dark:border-neutral-800">
        <div className="flex flex-wrap gap-1.5">
          <button type="button" onClick={() => void handleCopy()} disabled={!suggestedText} className="inline-flex items-center gap-1 rounded-lg border border-neutral-200 px-2 py-1 text-[11px] disabled:opacity-40 dark:border-neutral-700">{copied ? <Check className="h-3 w-3 text-emerald-600" /> : <Copy className="h-3 w-3" />}{copied ? "已複製" : "複製"}</button>
          <button type="button" onClick={() => lastRequest && void triggerAiCall(lastRequest.operation, lastRequest.instruction, lastRequest.baseText)} disabled={isLoading || !lastRequest} className="inline-flex items-center gap-1 rounded-lg border border-neutral-200 px-2 py-1 text-[11px] disabled:opacity-40 dark:border-neutral-700"><RefreshCw className={`h-3 w-3 ${isLoading ? "animate-spin" : ""}`} />重新生成</button>
          <button type="button" onClick={onReject} className="ml-auto rounded-lg px-2 py-1 text-[11px] text-neutral-600 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-800">拒絕</button>
        </div>
        <div className="grid grid-cols-3 gap-1.5">
          <button type="button" onClick={() => onApply(suggestedText, "insert_cursor")} disabled={!suggestedText || !selectionIsValid} className="rounded-lg border border-neutral-200 px-2 py-1.5 text-[11px] font-medium disabled:opacity-40 dark:border-neutral-700">游標插入</button>
          <button type="button" onClick={() => onApply(suggestedText, "insert_below")} disabled={!suggestedText || !selectionIsValid} className="rounded-lg border border-neutral-200 px-2 py-1.5 text-[11px] font-medium disabled:opacity-40 dark:border-neutral-700">插入下方</button>
          <button type="button" onClick={() => onApply(suggestedText, "replace")} disabled={!suggestedText || !selectionIsValid} className="rounded-lg bg-sky-600 px-2 py-1.5 text-[11px] font-semibold text-white hover:bg-sky-700 disabled:opacity-40">接受取代</button>
        </div>
      </div>
    </div>
  );
}
