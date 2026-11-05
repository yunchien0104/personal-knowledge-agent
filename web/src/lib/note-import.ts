import type { JSONContent } from "@tiptap/core";

export type NoteImportResponseData = {
  note_id: string;
  import_id: string;
  operation: "reference" | "partial" | "full";
  snapshot_title: string;
  snapshot_text: string;
};

export function parseNoteImportResponse(value: unknown): NoteImportResponseData {
  const data = value && typeof value === "object" && "data" in value
    ? (value as { data?: unknown }).data
    : null;
  if (!data || typeof data !== "object") {
    throw new Error("匯入完成，但伺服器沒有回傳可插入的內容");
  }

  const candidate = data as Partial<NoteImportResponseData>;
  if (
    typeof candidate.note_id !== "string"
    || typeof candidate.import_id !== "string"
    || !["reference", "partial", "full"].includes(candidate.operation ?? "")
    || typeof candidate.snapshot_title !== "string"
    || typeof candidate.snapshot_text !== "string"
  ) {
    throw new Error("匯入完成，但伺服器回傳格式不完整");
  }

  return candidate as NoteImportResponseData;
}

export function plainTextToParagraphs(text: string): JSONContent[] {
  return text.replace(/\r\n?/g, "\n").split("\n").map((line) => (
    line.length > 0
      ? { type: "paragraph", content: [{ type: "text", text: line }] }
      : { type: "paragraph" }
  ));
}

export function appendImportedText(
  currentContent: JSONContent | null,
  text: string,
): JSONContent {
  return {
    type: "doc",
    content: [
      ...(currentContent?.content ?? []),
      ...plainTextToParagraphs(text),
    ],
  };
}
