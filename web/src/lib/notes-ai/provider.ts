import { createGeminiProvider } from "./gemini";
import { createOpenAiProvider } from "./openai";
import { NotesAiProviderError, type NotesAiProvider } from "./types";

const DEFAULT_TIMEOUT_MS = 30_000;

export function getNotesAiProvider(
  env: Readonly<Record<string, string | undefined>> = process.env,
): NotesAiProvider {
  const provider = env.NOTES_AI_PROVIDER?.trim().toLowerCase();
  const timeoutMs = Number(env.NOTES_AI_TIMEOUT_MS) || DEFAULT_TIMEOUT_MS;
  if (provider === "openai") {
    return createOpenAiProvider({
      apiKey: env.OPENAI_API_KEY ?? "",
      model: env.NOTES_AI_MODEL?.trim() ?? "",
      timeoutMs,
    });
  }
  if (provider === "gemini") {
    return createGeminiProvider({
      apiKey: env.GEMINI_API_KEY ?? "",
      timeoutMs,
      models: env.NOTES_AI_GEMINI_MODELS?.split(",").map((model) => model.trim()),
    });
  }
  throw new NotesAiProviderError(
    "NOTES_AI_PROVIDER 必須明確設定為 openai 或 gemini。",
    "CONFIGURATION",
    503,
  );
}
