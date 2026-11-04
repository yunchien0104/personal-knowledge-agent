# Personal Brain - Notes Workspace 交接說明與技術總結

## 1. 模組交付狀態總覽
本模組已按照《功能開發交接說明書 v1.0》各階段規範完成全端整合：

- [x] Phase 1: Notes 首頁（三種建立入口、Grid/List 視圖切換、即時搜尋）
- [x] Phase 2 & 3: 統一編輯器與資料雙向保護（Tiptap 工具列、Slash Command、Debounce 600ms 自動存檔、LocalStorage + FastAPI 雙重同步、Markdown 匯出）
- [x] Phase 4: 內嵌 Excel 式計算表格（Tiptap 自訂節點、A1 座標參照、動態重新計算 SUM/AVERAGE、Markdown 快照轉換）
- [x] Phase 5: 筆記側欄檢索與 AI 編輯助手（歷史筆記預覽與插入、Inline Copilot 語氣修飾/擴寫/摘要、Diff 對比與 Accept/Reject 決策）
- [x] Phase 6: A4 PDF 匯出與列印引擎（Print CSS 自動優化排版）

## 2. 啟動與維運指南
- 前端伺服器 (Next.js):
  cd web && npm run dev
  訪問 http://localhost:3000/notes

- 後端 API 伺服器 (FastAPI):
  cd personal-brain-integrations
  source .venv/bin/activate
  python3 -m uvicorn server:app --port 8000 --reload
