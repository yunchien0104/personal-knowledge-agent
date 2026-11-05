import type { NotesAiRequest } from "./types";

const TASK_INSTRUCTIONS: Record<NotesAiRequest["operation"], string> = {
  summarize: "請為以下內容提煉清晰、具備層次與洞見的條列式重點摘要。",
  rewrite: "請重新組織以下段落的架構與文句，使其敘事更加流暢精練，並保留核心原意。",
  expand: "請針對以下內容進行深入論述與觀點擴寫，補充實務脈絡與背後邏輯。",
  translate_en: "請將以下內容翻譯為地道、專業的英文（Professional English）。",
  translate_zh: "請將以下內容轉換為精準流暢的繁體中文（台灣標準書面語彙）。",
  improve: "請優化以下文字的文筆、用詞精確度與論證邏輯。",
  change_tone: "請依照使用者指定的語氣改寫以下內容，並保留核心原意。",
  custom: "請依據專業水準優化並潤飾以下內容。",
};

export function buildNotesAiPrompt(request: NotesAiRequest) {
  const taskInstruction = request.instruction || TASK_INSTRUCTIONS[request.operation];
  return `你是一個內嵌於個人知識庫系統的專業 AI 知識助手。你的職責是處理各類筆記（如財務分析、市場研究、會議記錄、論文與程式草稿）。
任務規範：
1. 直接輸出處理後的文字內容，嚴禁包含任何額外的問候語、開場白、結語、引言或格式說明。
2. 保持專業、精確與嚴謹度。
3. 若有使用者特定指示，以該指示為最高優先權。

【使用者指示】：${taskInstruction}
【筆記標題】：${request.noteTitle || "未命名筆記"}
${request.context ? `【筆記上下文】：${request.context}\n` : ""}【欲處理文本】：
"""
${request.selectedText}
"""`;
}
