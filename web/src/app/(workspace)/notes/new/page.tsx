"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Loader2 } from "lucide-react";

export default function NewNotePage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const requestedRef = useRef(false);

  useEffect(() => {
    if (requestedRef.current) return;
    requestedRef.current = true;

    async function createNewNote() {
      try {
        const res = await fetch("/api/personal-notes", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            title: "未命名筆記",
            content_json: {
              type: "doc",
              content: [
                {
                  type: "paragraph",
                  content: [{ type: "text", text: "" }],
                },
              ],
            },
            plain_text: "",
          }),
        });

        if (!res.ok) {
          throw new Error("無法建立新筆記");
        }

        const json = (await res.json()) as { data?: { id?: string } };
        const newId = json.data?.id;

        if (newId) {
          router.replace(`/notes/${newId}`);
        } else {
          throw new Error("伺服器未回傳筆記 ID");
        }
      } catch (err: unknown) {
        const message = err instanceof Error ? err.message : "建立筆記時發生未知錯誤";
        setError(message);
      }
    }

    createNewNote();
  }, [router]);

  if (error) {
    return (
      <div className="flex min-h-[60vh] flex-col items-center justify-center space-y-4">
        <p className="text-sm font-medium text-red-500">{error}</p>
        <button
          onClick={() => {
            requestedRef.current = false;
            setError(null);
          }}
          className="rounded-lg bg-zinc-900 px-4 py-2 text-xs font-semibold text-white hover:bg-zinc-800"
        >
          重新嘗試
        </button>
      </div>
    );
  }

  return (
    <div className="flex min-h-[60vh] flex-col items-center justify-center space-y-3">
      <Loader2 className="h-6 w-6 animate-spin text-indigo-600" />
      <p className="text-sm text-zinc-500">正在建立新筆記，請稍候...</p>
    </div>
  );
}
