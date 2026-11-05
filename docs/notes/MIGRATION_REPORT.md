# Phase 0: Notes Repository Migration Report

## 1. 來源與目標資訊
- **來源 Repository**: `yunchien0104/personal-brain-integrations`
- **目標 Repository**: `yunchien0104/personal-brain-notes-dev`
- **開發基準分支**: `main`
- **作業分支**: `feat/notes-workspace-refactor`
- **原開發者 Commits 基準**: `57cec3c`, `d9c2089`, `ba6be61` (author: ricky-9394)

## 2. 成果移植清單 (Ported Items)
| 模組 / 元件 | 原 Commit SHA | 目標路徑 | 狀態說明 |
| :--- | :--- | :--- | :--- |
| Notes Dashboard (Grid/List UI) | `57cec3c` | `web/src/app/(workspace)/notes/page.tsx` | 已移植，支援搜尋與版面切換 |
| Tiptap Editor & Word Toolbar | `57cec3c` | `web/src/components/notes/TiptapEditor.tsx` | 已移植，含 Slash Command 與排版工具 |
| Spreadsheet Block (計算表格) | `57cec3c` | `web/src/components/notes/SpreadsheetComponent.tsx` | 已移植純前端 A1/SUM/AVERAGE 計算引擎 |
| Notes Sidebar (筆記檢索引用) | `57cec3c` | `web/src/components/notes/NotesSidebar.tsx` | 已移植抽屜 UI |
| AI Assistant Modal (Diff 預覽) | `57cec3c` | `web/src/components/notes/AiAssistantModal.tsx` | 已移植 UI，標記待後續對接真實模型 |
| 編輯器主頁面 & A4 PDF 列印 | `d9c2089` | `web/src/app/(workspace)/notes/[noteId]/page.tsx` | 已移植 Print CSS 與匯出排版 |
| 完整交接與規格文檔 | `57cec3c` | `docs/notes/*.md` | 已移植 5 份完整 Specification |

## 3. 架構重構與排除項目 (Refactoring & Deliberate Exclusions)
- **廢除獨立 FastAPI**: 刪除 `server:app` 與 Python CRUD 依賴，改為建立標準 Next.js Route Handlers (`web/src/app/api/personal-notes/`)。
- **無關內容完全隔離**: 嚴格排除 `adapters/`（Instagram / Threads）與其他無關變更，維持 Repo 純淨度。
- **未實作項目明確標記**:
  - AI Assistant 目前為前端 Mock 預覽，未完成正式後端 LLM 串接。
  - Knowledge / Research 匯入目前為前端模擬介面，尚未連接生產資料庫。
