import Link from "next/link";
import { redirect } from "next/navigation";
import { connection } from "next/server";

import { loadPdfInbox } from "@/lib/pdf-inbox";
import { createServerSupabaseClient } from "@/lib/supabase";

import { InboxDeleteControl } from "./inbox-delete-control";

export const instant = false;

const STATUS_LABELS: Record<string, string> = {
  queued: "等待處理",
  processing: "處理中",
  completed: "完成",
  failed: "失敗",
};

function formatBytes(bytes: number | null) {
  if (bytes === null) return "大小未知";
  return `${(bytes / 1024 / 1024).toFixed(2)} MB`;
}

export default async function InboxPage() {
  await connection();
  const supabase = await createServerSupabaseClient();
  const { data: { user }, error } = await supabase.auth.getUser();
  if (error || !user) redirect("/auth/login");
  const items = await loadPdfInbox(supabase, user.id);

  return (
    <main style={{ padding: "3rem", fontFamily: "Arial", maxWidth: 900 }}>
      <nav style={{ display: "flex", gap: "1rem", marginBottom: "2rem" }}>
        <Link href="/upload">上傳 PDF</Link>
        <Link href="/search">知識搜尋</Link>
        <Link href="/protected">帳號</Link>
      </nav>
      <h1>Asset Inbox</h1>
      {items.length === 0 ? (
        <p>尚未上傳 PDF。</p>
      ) : (
        <ul style={{ display: "grid", gap: "1rem", padding: 0, listStyle: "none" }}>
          {items.map((item) => (
            <li key={item.id} style={{ border: "1px solid currentColor", padding: "1rem" }}>
              <strong>{item.fileName}</strong>
              <p>{STATUS_LABELS[item.ingestionStatus]} · {formatBytes(item.fileSize)}</p>
              {item.ingestionStatus === "failed" && item.errorMessage ? (
                <p role="alert">{item.errorMessage}</p>
              ) : null}
              <p>建立時間：{new Date(item.createdAt).toLocaleString("zh-TW")}</p>
              {item.stored && item.downloadUrl ? (
                <p>檔案已保存 · <a href={item.downloadUrl}>下載確認</a></p>
              ) : (
                <p>檔案尚未完成保存。</p>
              )}
              {item.ingestionStatus === "completed" ? (
                <p><Link href={`/assets/${item.id}/source-note`}>Source Note</Link></p>
              ) : null}
              <InboxDeleteControl
                assetId={item.id}
                fileName={item.fileName}
                sourceStatus={item.sourceStatus}
                initialDeletion={item.deletion}
              />
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}
