"use client";

import type { Editor } from "@tiptap/core";
import { useEditorState } from "@tiptap/react";
import { Bold, Eraser, Italic, Palette, Strikethrough, Underline as UnderlineIcon } from "lucide-react";

const TEXT_COLORS = [
  { label: "紅色", value: "var(--note-color-red)" },
  { label: "橙色", value: "var(--note-color-orange)" },
  { label: "綠色", value: "var(--note-color-green)" },
  { label: "藍色", value: "var(--note-color-blue)" },
  { label: "紫色", value: "var(--note-color-purple)" },
] as const;

const HIGHLIGHT_COLORS = [
  { label: "黃色螢光筆", value: "rgba(250, 204, 21, 0.38)" },
  { label: "綠色螢光筆", value: "rgba(74, 222, 128, 0.32)" },
  { label: "粉色螢光筆", value: "rgba(244, 114, 182, 0.32)" },
  { label: "藍色螢光筆", value: "rgba(96, 165, 250, 0.32)" },
  { label: "紫色螢光筆", value: "rgba(192, 132, 252, 0.32)" },
] as const;

const FONT_SIZES = ["12", "14", "16", "18", "20", "24", "28", "32"] as const;

const buttonClass =
  "rounded p-1.5 text-zinc-600 hover:bg-zinc-100 dark:text-zinc-400 dark:hover:bg-zinc-800";
const activeButtonClass = "bg-zinc-100 text-indigo-600 dark:bg-zinc-800 dark:text-indigo-400";

export function TextFormattingControls({ editor }: { editor: Editor }) {
  const state = useEditorState({
    editor,
    selector: ({ editor: currentEditor }) => {
      if (!currentEditor) {
        return {
          block: "paragraph",
          color: "",
          fontFamily: "",
          fontSize: "",
          highlight: "",
          isBold: false,
          isItalic: false,
          isStrike: false,
          isUnderline: false,
        };
      }
      const textStyle = currentEditor.getAttributes("textStyle");
      const highlight = currentEditor.getAttributes("highlight");
      const headingLevel = [1, 2, 3, 4].find((level) =>
        currentEditor.isActive("heading", { level })
      );
      return {
        block: headingLevel ? `heading-${headingLevel}` : "paragraph",
        color: typeof textStyle.color === "string" ? textStyle.color : "",
        fontFamily: typeof textStyle.fontFamily === "string" ? textStyle.fontFamily : "",
        fontSize: typeof textStyle.fontSize === "string" ? textStyle.fontSize : "",
        highlight: typeof highlight.color === "string" ? highlight.color : "",
        isBold: currentEditor.isActive("bold"),
        isItalic: currentEditor.isActive("italic"),
        isStrike: currentEditor.isActive("strike"),
        isUnderline: currentEditor.isActive("underline"),
      };
    },
  });

  function setBlock(value: string) {
    if (value === "paragraph") {
      editor.chain().focus().setParagraph().run();
      return;
    }
    const level = Number(value.replace("heading-", "")) as 1 | 2 | 3 | 4;
    editor.chain().focus().setHeading({ level }).run();
  }

  function setFontFamily(value: string) {
    const chain = editor.chain().focus();
    if (value) chain.setFontFamily(value).run();
    else chain.unsetFontFamily().run();
  }

  function setFontSize(value: string) {
    const chain = editor.chain().focus();
    if (value) chain.setFontSize(`${value}px`).run();
    else chain.unsetFontSize().run();
  }

  function clearFormatting() {
    editor.chain().focus().unsetAllMarks().clearNodes().run();
  }

  return (
    <div className="flex items-center gap-1" role="group" aria-label="文字格式">
      <button
        type="button"
        onClick={() => editor.chain().focus().toggleBold().run()}
        aria-label="粗體"
        aria-pressed={state?.isBold ?? false}
        className={`${buttonClass} ${state?.isBold ? activeButtonClass : ""}`}
        title="粗體"
      >
        <Bold className="h-4 w-4" />
      </button>
      <button
        type="button"
        onClick={() => editor.chain().focus().toggleItalic().run()}
        aria-label="斜體"
        aria-pressed={state?.isItalic ?? false}
        className={`${buttonClass} ${state?.isItalic ? activeButtonClass : ""}`}
        title="斜體"
      >
        <Italic className="h-4 w-4" />
      </button>
      <button
        type="button"
        onClick={() => editor.chain().focus().toggleUnderline().run()}
        aria-label="底線"
        aria-pressed={state?.isUnderline ?? false}
        className={`${buttonClass} ${state?.isUnderline ? activeButtonClass : ""}`}
        title="底線"
      >
        <UnderlineIcon className="h-4 w-4" />
      </button>
      <button
        type="button"
        onClick={() => editor.chain().focus().toggleStrike().run()}
        aria-label="刪除線"
        aria-pressed={state?.isStrike ?? false}
        className={`${buttonClass} ${state?.isStrike ? activeButtonClass : ""}`}
        title="刪除線"
      >
        <Strikethrough className="h-4 w-4" />
      </button>

      <select
        aria-label="段落樣式"
        value={state?.block ?? "paragraph"}
        onChange={(event) => setBlock(event.target.value)}
        className="h-7 rounded border border-zinc-200 bg-white px-1.5 text-xs text-zinc-700 outline-none focus-visible:ring-2 focus-visible:ring-zinc-400 dark:border-zinc-700 dark:bg-zinc-900 dark:text-zinc-300"
      >
        <option value="paragraph">本文</option>
        <option value="heading-1">H1</option>
        <option value="heading-2">H2</option>
        <option value="heading-3">H3</option>
        <option value="heading-4">H4</option>
      </select>

      <details className="group relative">
        <summary className="flex h-7 list-none cursor-pointer items-center gap-1 rounded border border-zinc-200 px-2 text-xs text-zinc-600 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800">
          <Palette className="h-3.5 w-3.5" />
          樣式
        </summary>
        <div className="absolute right-0 top-9 z-30 w-64 space-y-3 rounded-lg border border-zinc-200 bg-white p-3 shadow-xl sm:left-0 sm:right-auto dark:border-zinc-700 dark:bg-zinc-900">
          <label className="grid gap-1 text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
            字型
            <select
              value={state?.fontFamily ?? ""}
              onChange={(event) => setFontFamily(event.target.value)}
              className="rounded border border-zinc-200 bg-white px-2 py-1.5 text-xs text-zinc-700 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-300"
            >
              <option value="">預設黑體</option>
              <option value={'"Noto Serif TC", "Songti TC", "PMingLiU", serif'}>宋體</option>
              <option value="var(--font-mono)">等寬字體</option>
            </select>
          </label>

          <label className="grid gap-1 text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
            字體大小
            <select
              value={(state?.fontSize ?? "").replace("px", "")}
              onChange={(event) => setFontSize(event.target.value)}
              className="rounded border border-zinc-200 bg-white px-2 py-1.5 text-xs text-zinc-700 dark:border-zinc-700 dark:bg-zinc-950 dark:text-zinc-300"
            >
              <option value="">預設大小</option>
              {FONT_SIZES.map((size) => <option key={size} value={size}>{size}px</option>)}
            </select>
          </label>

          <div>
            <div className="mb-1 text-[11px] font-medium text-zinc-500 dark:text-zinc-400">字體顏色</div>
            <div className="flex items-center gap-1.5">
              <button type="button" onClick={() => editor.chain().focus().unsetColor().run()} className="h-6 rounded border border-zinc-200 px-1.5 text-[10px] text-zinc-500 dark:border-zinc-700" aria-label="使用預設字體顏色">預設</button>
              {TEXT_COLORS.map((color) => (
                <button
                  key={color.label}
                  type="button"
                  onClick={() => editor.chain().focus().setColor(color.value).run()}
                  aria-label={color.label}
                  aria-pressed={state?.color === color.value}
                  className="h-6 w-6 rounded-full border border-black/10 ring-offset-2 aria-pressed:ring-2 aria-pressed:ring-zinc-500 dark:border-white/20 dark:ring-offset-zinc-900"
                  style={{ backgroundColor: color.value }}
                />
              ))}
              <label className="relative h-6 w-6 cursor-pointer overflow-hidden rounded-full border border-zinc-300 bg-gradient-to-br from-red-400 via-green-400 to-blue-500 dark:border-zinc-600" title="自訂字體顏色">
                <span className="sr-only">自訂字體顏色</span>
                <input
                  type="color"
                  aria-label="自訂字體顏色"
                  value={state?.color?.startsWith("#") ? state.color : "#3b82f6"}
                  onChange={(event) => editor.chain().focus().setColor(event.target.value).run()}
                  className="absolute inset-0 h-full w-full cursor-pointer opacity-0"
                />
              </label>
            </div>
          </div>

          <div>
            <div className="mb-1 text-[11px] font-medium text-zinc-500 dark:text-zinc-400">螢光筆</div>
            <div className="flex items-center gap-1.5">
              <button type="button" onClick={() => editor.chain().focus().unsetHighlight().run()} className="h-6 rounded border border-zinc-200 px-1.5 text-[10px] text-zinc-500 dark:border-zinc-700" aria-label="移除螢光筆">無</button>
              {HIGHLIGHT_COLORS.map((color) => (
                <button
                  key={color.label}
                  type="button"
                  onClick={() => editor.chain().focus().toggleHighlight({ color: color.value }).run()}
                  aria-label={color.label}
                  aria-pressed={state?.highlight === color.value}
                  className="h-6 w-7 rounded border border-black/10 ring-offset-2 aria-pressed:ring-2 aria-pressed:ring-zinc-500 dark:border-white/20 dark:ring-offset-zinc-900"
                  style={{ backgroundColor: color.value }}
                />
              ))}
            </div>
          </div>

          <button
            type="button"
            onClick={clearFormatting}
            className="flex w-full items-center justify-center gap-1.5 rounded border border-zinc-200 px-2 py-1.5 text-xs text-zinc-600 hover:bg-zinc-50 dark:border-zinc-700 dark:text-zinc-300 dark:hover:bg-zinc-800"
          >
            <Eraser className="h-3.5 w-3.5" />
            清除格式
          </button>
        </div>
      </details>
    </div>
  );
}
