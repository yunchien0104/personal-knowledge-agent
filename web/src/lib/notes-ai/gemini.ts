import { buildNotesAiPrompt } from "./prompt";
import { NotesAiProviderError, type NotesAiProvider } from "./types";

const LEGACY_MODELS = [
  "gemini-flash-lite-latest",
  "gemini-3.5-flash-lite",
  "gemini-3.1-flash-lite",
  "gemini-flash-latest",
];

export function createGeminiProvider(config: {
  apiKey: string;
  timeoutMs: number;
  models?: string[];
}): NotesAiProvider {
  if (!config.apiKey) {
    throw new NotesAiProviderError("GEMINI_API_KEY 尚未設定。", "CONFIGURATION", 503);
  }
  const models = config.models?.filter(Boolean).length ? config.models : LEGACY_MODELS;
  return {
    name: "gemini",
    async generate(request) {
      const prompt = buildNotesAiPrompt(request);
      for (const model of models) {
        try {
          const response = await fetch(
            `https://generativelanguage.googleapis.com/v1beta/models/${encodeURIComponent(model)}:generateContent?key=${encodeURIComponent(config.apiKey)}`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              signal: AbortSignal.timeout(config.timeoutMs),
              body: JSON.stringify({
                contents: [{ parts: [{ text: prompt }] }],
                generationConfig: { temperature: 0.3, maxOutputTokens: 2_048 },
              }),
            },
          );
          if (!response.ok) continue;
          const body = await response.json() as {
            candidates?: Array<{ content?: { parts?: Array<{ text?: string }> } }>;
          };
          const generatedText = body.candidates?.[0]?.content?.parts?.[0]?.text?.trim();
          if (generatedText) return { generatedText, model, provider: "gemini" };
        } catch (error) {
          if (error instanceof DOMException && error.name === "TimeoutError") {
            throw new NotesAiProviderError("Gemini 請求逾時。", "TIMEOUT", 504);
          }
        }
      }
      throw new NotesAiProviderError("所有已設定的 Gemini 模型皆無法完成請求。", "UPSTREAM");
    },
  };
}
