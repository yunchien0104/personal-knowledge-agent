import type { AiOperation } from "@/components/notes/aiAssistant";

export interface NotesAiRequest {
  operation: AiOperation;
  instruction: string;
  selectedText: string;
  context: string;
  noteTitle: string;
}

export interface NotesAiResult {
  generatedText: string;
  model: string;
  provider: "openai" | "gemini";
}

export interface NotesAiProvider {
  readonly name: NotesAiResult["provider"];
  generate(request: NotesAiRequest): Promise<NotesAiResult>;
}

export class NotesAiProviderError extends Error {
  constructor(
    message: string,
    readonly code: "CONFIGURATION" | "TIMEOUT" | "UPSTREAM" | "EMPTY_RESPONSE",
    readonly status = 502,
  ) {
    super(message);
    this.name = "NotesAiProviderError";
  }
}
