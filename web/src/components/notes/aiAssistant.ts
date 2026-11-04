export type AiOperation =
  | "improve"
  | "rewrite"
  | "summarize"
  | "expand"
  | "translate_en"
  | "translate_zh"
  | "change_tone"
  | "custom";

export type AiApplyAction = "replace" | "insert_below" | "insert_cursor";

export interface AiSelectionSnapshot {
  from: number;
  to: number;
  anchor: number;
  head: number;
  text: string;
  selectionType: "text";
  documentFingerprint: string;
}

export interface PopoverAnchor {
  x: number;
  y: number;
}

export interface DiffPart {
  value: string;
  type: "same" | "added" | "removed";
}

export interface AiRequestPayload {
  operation: AiOperation;
  instruction: string;
  selectedText: string;
  context: string;
  noteTitle: string;
}

function getErrorMessage(data: unknown, status: number) {
  if (!data || typeof data !== "object") return `請求失敗 (${status})`;
  const error = "error" in data ? data.error : undefined;
  if (typeof error === "string") return error;
  if (error && typeof error === "object" && "message" in error && typeof error.message === "string") {
    return error.message;
  }
  return `請求失敗 (${status})`;
}

export async function requestAiSuggestion(
  payload: AiRequestPayload,
  signal?: AbortSignal,
  fetcher: typeof fetch = fetch,
) {
  const response = await fetcher("/api/personal-notes/ai", {
    method: "POST",
    signal,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = (await response.json().catch(() => ({}))) as Record<string, unknown>;
  if (!response.ok) throw new Error(getErrorMessage(data, response.status));
  const generatedText = data.generatedText;
  if (typeof generatedText !== "string" || !generatedText.trim()) {
    throw new Error("AI Provider 未回傳有效內容。");
  }
  return generatedText;
}

const clamp = (value: number, min: number, max: number) =>
  Math.min(Math.max(value, min), max);

export function clampPopoverPosition(
  anchor: PopoverAnchor,
  viewport: { width: number; height: number },
  popover: { width: number; height: number },
  margin = 12,
): PopoverAnchor {
  const maxX = Math.max(margin, viewport.width - popover.width - margin);
  const maxY = Math.max(margin, viewport.height - popover.height - margin);
  const preferredY = anchor.y + margin;
  const y =
    preferredY + popover.height <= viewport.height - margin
      ? preferredY
      : anchor.y - popover.height - margin;

  return {
    x: clamp(anchor.x + margin, margin, maxX),
    y: clamp(y, margin, maxY),
  };
}

function tokenize(value: string) {
  return value.match(/\s+|[^\s]+/g) ?? [];
}

function appendPart(parts: DiffPart[], type: DiffPart["type"], value: string) {
  if (!value) return;
  const previous = parts.at(-1);
  if (previous?.type === type) previous.value += value;
  else parts.push({ type, value });
}

/** Bounded word diff so long notes cannot allocate an unbounded LCS matrix. */
export function buildWordDiff(original: string, suggested: string): DiffPart[] {
  if (original === suggested) return [{ type: "same", value: original }];

  const before = tokenize(original);
  const after = tokenize(suggested);
  if (before.length * after.length > 40_000) {
    return [
      { type: "removed", value: original },
      { type: "added", value: suggested },
    ];
  }

  const matrix = Array.from(
    { length: before.length + 1 },
    () => new Uint16Array(after.length + 1),
  );
  for (let i = before.length - 1; i >= 0; i -= 1) {
    for (let j = after.length - 1; j >= 0; j -= 1) {
      matrix[i][j] = before[i] === after[j]
        ? matrix[i + 1][j + 1] + 1
        : Math.max(matrix[i + 1][j], matrix[i][j + 1]);
    }
  }

  const parts: DiffPart[] = [];
  let i = 0;
  let j = 0;
  while (i < before.length && j < after.length) {
    if (before[i] === after[j]) {
      appendPart(parts, "same", before[i]);
      i += 1;
      j += 1;
    } else if (matrix[i + 1][j] >= matrix[i][j + 1]) {
      appendPart(parts, "removed", before[i]);
      i += 1;
    } else {
      appendPart(parts, "added", after[j]);
      j += 1;
    }
  }
  while (i < before.length) appendPart(parts, "removed", before[i++]);
  while (j < after.length) appendPart(parts, "added", after[j++]);
  return parts;
}
