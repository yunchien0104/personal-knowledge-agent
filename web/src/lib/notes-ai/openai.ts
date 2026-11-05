import OpenAI from "openai";

import { buildNotesAiPrompt } from "./prompt";
import { NotesAiProviderError, type NotesAiProvider } from "./types";

type ResponsesClient = Pick<OpenAI, "responses">;

export function createOpenAiProvider(
  config: { apiKey: string; model: string; timeoutMs: number },
  suppliedClient?: ResponsesClient,
): NotesAiProvider {
  if (!config.apiKey) {
    throw new NotesAiProviderError("OPENAI_API_KEY 尚未設定。", "CONFIGURATION", 503);
  }
  if (!config.model) {
    throw new NotesAiProviderError(
      "NOTES_AI_MODEL 尚未設定；為避免猜測不可用模型，本服務不會自動選擇模型。",
      "CONFIGURATION",
      503,
    );
  }
  const client = suppliedClient ?? new OpenAI({
    apiKey: config.apiKey,
    maxRetries: 0,
    timeout: config.timeoutMs,
  });

  return {
    name: "openai",
    async generate(request) {
      try {
        const response = await client.responses.create({
          model: config.model,
          input: buildNotesAiPrompt(request),
          max_output_tokens: 2_048,
          store: false,
        });
        const generatedText = response.output_text?.trim();
        if (!generatedText) {
          throw new NotesAiProviderError("OpenAI 未回傳文字內容。", "EMPTY_RESPONSE");
        }
        return { generatedText, model: config.model, provider: "openai" };
      } catch (error) {
        if (error instanceof NotesAiProviderError) throw error;
        if (error instanceof OpenAI.APIConnectionTimeoutError) {
          throw new NotesAiProviderError("OpenAI 請求逾時。", "TIMEOUT", 504);
        }
        throw new NotesAiProviderError("OpenAI 暫時無法完成請求。", "UPSTREAM");
      }
    },
  };
}
