import { BookOpen, FilePlus2, FileText, Sparkles } from "lucide-react";

import { Card, PageTitle } from "@/components/personal-brain/ui";

const entryPoints = [
  { title: "建立空白筆記", detail: "TODO：建立 manual_note、rich document 與初始版本。", icon: FilePlus2 },
  { title: "匯入 Knowledge 或 Research", detail: "TODO：複製內容與 citation snapshot，不回寫原始知識。", icon: BookOpen },
  { title: "匯入 DOCX 或 Markdown", detail: "TODO：解析為結構化內容；首版不匯入圖片。", icon: FileText },
];

export function NotesWorkspaceHandoff() {
  return (
    <div>
      <PageTitle title="筆記" />
      <div className="mb-5 rounded-lg border border-amber-200 bg-amber-50 p-4 text-sm text-amber-900">
        <p className="font-semibold">交接開發 Stub</p>
        <p className="mt-1 text-xs leading-5">導覽、Auth、schema baseline、synthetic fixtures 與測試骨架已建立；富文字編輯器、Autosave、匯入與匯出尚未實作。</p>
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        {entryPoints.map(({ title, detail, icon: Icon }) => (
          <Card key={title} className="p-5">
            <Icon className="h-5 w-5 text-indigo-600" />
            <h2 className="mt-4 text-sm font-semibold">{title}</h2>
            <p className="mt-2 text-xs leading-5 text-muted">{detail}</p>
            <button type="button" disabled className="mt-5 rounded-md border border-line px-3 py-2 text-xs text-muted disabled:cursor-not-allowed disabled:opacity-60">尚未實作</button>
          </Card>
        ))}
      </div>
      <Card className="mt-5 p-5">
        <div className="flex items-center gap-2"><Sparkles className="h-4 w-4 text-indigo-600" /><h2 className="text-sm font-semibold">AI 編輯助手</h2></div>
        <p className="mt-2 text-xs leading-5 text-muted">TODO：只操作使用者明確選取的內容，顯示 diff，套用前需要確認；預設使用 Mock adapter。</p>
      </Card>
    </div>
  );
}
