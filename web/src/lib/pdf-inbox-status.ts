export function deriveIngestionStatus(assetStatus: string, jobStatus: string | null) {
  if (jobStatus === "completed" || assetStatus === "ready") return "completed" as const;
  if (jobStatus === "running" || assetStatus === "processing") return "processing" as const;
  if (
    ["failed", "cancelled"].includes(jobStatus ?? "")
    || ["failed", "unsupported"].includes(assetStatus)
  ) return "failed" as const;
  return "queued" as const;
}

export function safeIngestionError(code: string | null) {
  const messages: Record<string, string> = {
    attempts_exhausted: "處理重試次數已用盡。",
    blank_pdf: "PDF 沒有可擷取的文字。",
    corrupt_pdf: "PDF 已損毀或無法讀取。",
    encrypted_pdf: "目前不支援加密 PDF。",
    resource_limit_exceeded: "PDF 超出處理資源限制。",
    scanned_pdf: "掃描版 PDF 目前不支援 OCR。",
    unsupported_pdf: "PDF 使用目前不支援的格式。",
  };
  return code ? (messages[code] ?? "PDF 處理失敗，請稍後再試。") : null;
}
