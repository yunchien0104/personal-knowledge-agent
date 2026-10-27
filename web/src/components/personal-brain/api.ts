import type { Asset, KnowledgeItem, KnowledgeStatus, Note, SourceKind } from "./types";

export type ApiEnvelope<T> = {
  data: T | null;
  error?: { message?: string };
  meta?: { total?: number };
};

export async function getApiEnvelope<T>(url: string, init?: RequestInit): Promise<ApiEnvelope<T>> {
  const response = await fetch(url, { cache: "no-store", ...init });
  const payload = await response.json() as ApiEnvelope<T>;
  if (!response.ok || payload.data === null) {
    throw new Error(payload.error?.message ?? "資料讀取失敗，請稍後再試。");
  }
  return payload;
}

export async function getApi<T>(url: string, init?: RequestInit): Promise<T> {
  return (await getApiEnvelope<T>(url, init)).data as T;
}

export function sourceKind(type: Asset["source_type"]): SourceKind {
  if (type === "pdf") return "PDF";
  if (type === "instagram_reel") return "IG";
  return "TH";
}

export function supportsSourceNote(type: Asset["source_type"]) {
  return type === "pdf" || type === "threads";
}

function statusLabel(status: string): KnowledgeStatus {
  if (status === "deleting") return "刪除中";
  if (status === "deletion_failed") return "刪除失敗";
  if (status === "source_removed") return "原檔已移除";
  if (["ready", "completed"].includes(status)) return "已完成";
  if (["processing", "running"].includes(status)) return "處理中";
  if (["failed", "unsupported", "cancelled"].includes(status)) return "失敗";
  return "待檢視";
}

function ingestionMessage(asset: Asset) {
  const code = asset.ingestion_job?.last_error_code;
  const messages: Record<string, string> = {
    threads_content_incomplete: "Threads 只提供截斷預覽，因此未發布不完整內容。",
    threads_post_inaccessible: "貼文需要登入、已刪除或無法公開讀取；可改用文字或匯出內容。",
    threads_fetch_timeout: "Threads 暫時逾時，系統會依重試規則再次處理。",
    threads_fetch_failed: "Threads 暫時無法載入，系統會依重試規則再次處理。",
    browser_runtime_missing: "處理服務尚未安裝 Threads 瀏覽器執行環境。",
  };
  return code ? messages[code] ?? "來源處理失敗；未發布任何不完整版本。" : null;
}

function pdfName(value: string | null) {
  const match = value?.match(/^pdf:[0-9a-f-]{36}:\d+:(.+)$/);
  if (!match) return null;
  try {
    return decodeURIComponent(match[1]);
  } catch {
    return null;
  }
}

export function assetTitle(asset: Asset) {
  if (asset.source_type === "pdf") return pdfName(asset.source_external_id) ?? `PDF ${asset.id.slice(0, 8)}`;
  if (asset.source_url) {
    try {
      return new URL(asset.source_url).hostname.replace(/^www\./, "");
    } catch {
      return asset.source_url;
    }
  }
  return asset.source_type === "instagram_reel" ? "Instagram Reel" : "Threads 收藏";
}

export function toKnowledgeItems(assets: Asset[], notes: Note[]): KnowledgeItem[] {
  const sources = assets.map((asset): KnowledgeItem => ({
    id: asset.id,
    kind: sourceKind(asset.source_type),
    category: "source",
    title: assetTitle(asset),
    meta: `${new Date(asset.created_at).toLocaleDateString("zh-TW")} · ${asset.status}`,
    status: statusLabel(asset.status),
    excerpt: ingestionMessage(asset)
      ?? (asset.active_revision_id ? "來源已建立可追溯版本。" : "來源已保存，尚未建立可閱讀摘要。"),
    updatedAt: asset.updated_at,
    sourceStatus: asset.status,
    activeRevisionId: asset.active_revision_id,
    sourceUrl: asset.source_url,
  }));
  const noteItems = notes.map((note): KnowledgeItem => ({
    id: note.id,
    kind: "NOTE",
    category: "note",
    title: note.title || "未命名筆記",
    meta: `${new Date(note.updated_at).toLocaleDateString("zh-TW")} · ${note.note_type}`,
    status: note.publication_state === "draft" ? "待檢視" : "已完成",
    excerpt: note.body.trim() || "這則筆記目前沒有內容。",
    updatedAt: note.updated_at,
  }));
  return [...sources, ...noteItems].toSorted((a, b) => b.updatedAt.localeCompare(a.updatedAt));
}
