import Link from "next/link";
import { redirect } from "next/navigation";
import { connection } from "next/server";

import { UploadForm } from "@/app/upload/upload-form";
import { createServerSupabaseClient } from "@/lib/supabase";

export const instant = false;

export default async function UploadPage() {
  await connection();
  const supabase = await createServerSupabaseClient();
  const { data: { user }, error } = await supabase.auth.getUser();
  if (error || !user) redirect("/auth/login");

  return (
    <main style={{ padding: "3rem", fontFamily: "Arial", maxWidth: 760 }}>
      <nav style={{ display: "flex", gap: "1rem", marginBottom: "2rem" }}>
        <Link href="/inbox">Inbox</Link>
        <Link href="/protected">帳號</Link>
      </nav>
      <h1>上傳 PDF</h1>
      <p>檔案會儲存在你的私人空間；目前只建立來源紀錄，不會解析內容。</p>
      <UploadForm />
    </main>
  );
}
