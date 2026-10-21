export const PDF_BUCKET = "asset-private";
export const MAX_PDF_BYTES = 10 * 1024 * 1024;
export const PDF_MIME_TYPE = "application/pdf";

const PDF_PREFIX = new Uint8Array([0x25, 0x50, 0x44, 0x46, 0x2d]);
const PDF_METADATA_PATTERN = /^pdf:([0-9a-f-]{36}):(\d+):(.+)$/;

export function pdfObjectPath(userId: string, assetId: string) {
  return `${userId}/${assetId}/original/source.pdf`;
}

export function encodePdfMetadata(assetId: string, fileSize: number, fileName: string) {
  return `pdf:${assetId}:${fileSize}:${encodeURIComponent(fileName)}`;
}

export function parsePdfMetadata(value: string | null) {
  const match = value?.match(PDF_METADATA_PATTERN);
  if (!match) return null;
  try {
    return {
      assetId: match[1],
      fileSize: Number(match[2]),
      fileName: decodeURIComponent(match[3]),
    };
  } catch {
    return null;
  }
}

export function hasPdfSignature(bytes: Uint8Array) {
  return PDF_PREFIX.every((value, index) => bytes[index] === value);
}

export async function validatePdfFile(file: File) {
  if (!file.name.toLowerCase().endsWith(".pdf")) return "檔名必須以 .pdf 結尾。";
  if (file.type !== PDF_MIME_TYPE) return "檔案類型必須是 PDF。";
  if (file.size < PDF_PREFIX.length) return "PDF 檔案內容不完整。";
  if (file.size > MAX_PDF_BYTES) return "PDF 不得超過 10 MB。";
  const header = new Uint8Array(await file.slice(0, PDF_PREFIX.length).arrayBuffer());
  return hasPdfSignature(header) ? null : "檔案內容不是有效的 PDF。";
}

export type PdfUploadResult = {
  asset: {
    id: string;
    source_external_id: string;
    status: string;
    created_at: string;
  };
  stored: { size: number };
};
