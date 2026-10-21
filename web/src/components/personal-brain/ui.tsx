import type { ButtonHTMLAttributes, PropsWithChildren, ReactNode } from "react";
import type { KnowledgeStatus, SourceKind } from "./types";

export function Card({ children, className = "" }: PropsWithChildren<{ className?: string }>) {
  return <section className={`rounded-lg border border-line bg-surface ${className}`}>{children}</section>;
}

export function PageTitle({ title, action }: { title: string; action?: ReactNode }) {
  return (
    <header className="mb-7 flex items-center justify-between gap-4">
      <h1 className="text-[26px] font-semibold leading-tight tracking-[-0.025em] text-ink sm:text-[30px]">{title}</h1>
      {action}
    </header>
  );
}

const kindStyle: Record<SourceKind, string> = {
  PDF: "bg-[#f5f1ef] text-[#76564b]",
  IG: "bg-[#f4f1f4] text-[#675867]",
  TH: "bg-elevated text-muted",
  NOTE: "bg-[#f1f1ef] text-[#555552]",
};

const kindLabel: Record<SourceKind, string> = { PDF: "PDF", IG: "IG", TH: "TH", NOTE: "筆記" };

export function SourceIcon({ kind, size = "md" }: { kind: SourceKind; size?: "sm" | "md" | "lg" }) {
  const sizes = { sm: "h-8 w-8 text-[9px]", md: "h-10 w-10 text-[10px]", lg: "h-11 w-11 text-[10px]" };
  return <span className={`grid shrink-0 place-items-center rounded-md font-semibold ${kindStyle[kind]} ${sizes[size]}`}>{kindLabel[kind]}</span>;
}

const statusStyle: Record<KnowledgeStatus, string> = {
  已完成: "bg-emerald-50 text-emerald-700",
  處理中: "bg-indigo-50 text-indigo-700",
  待檢視: "bg-amber-50 text-amber-700",
  失敗: "bg-rose-50 text-rose-700",
  刪除中: "bg-amber-50 text-amber-700",
  刪除失敗: "bg-rose-50 text-rose-700",
  原檔已移除: "bg-slate-100 text-slate-600",
};

export function StatusBadge({ status }: { status: KnowledgeStatus }) {
  return <span className={`rounded px-2 py-1 text-[10px] font-medium ${statusStyle[status]}`}>{status}</span>;
}

export function PrimaryButton({ className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button className={`inline-flex items-center justify-center gap-2 rounded-md bg-indigo-500 px-4 py-2.5 text-sm font-semibold text-white transition duration-150 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-indigo-500/20 disabled:cursor-not-allowed disabled:opacity-50 ${className}`} {...props} />;
}

export function SoftButton({ className = "", ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return <button className={`inline-flex items-center justify-center gap-2 rounded-md border border-line bg-surface px-3.5 py-2.5 text-sm font-medium text-ink transition duration-150 hover:border-indigo-200 hover:bg-subtle disabled:cursor-not-allowed disabled:opacity-50 ${className}`} {...props} />;
}

export function PendingState({ title, description }: { title: string; description: string }) {
  return (
    <div className="grid min-h-44 place-items-center rounded-lg border border-dashed border-line bg-subtle p-6 text-center">
      <div><p className="text-sm font-semibold text-ink">{title}</p><p className="mt-2 max-w-md text-xs leading-5 text-muted">{description}</p></div>
    </div>
  );
}
