import assert from "node:assert/strict";
import test from "node:test";

import { createOpenAiProvider } from "../src/lib/notes-ai/openai";
import { buildNotesAiPrompt } from "../src/lib/notes-ai/prompt";
import { getNotesAiProvider } from "../src/lib/notes-ai/provider";
import { checkNotesAiRateLimit, resetNotesAiRateLimitForTests } from "../src/lib/notes-ai/rate-limit";
import { notesAiRequestSchema } from "../src/lib/notes-ai/validation";

const request = {
  operation: "rewrite" as const,
  instruction: "更精簡",
  selectedText: "原始內容",
  context: "筆記上下文",
  noteTitle: "測試筆記",
};

test("validation accepts every supported operation and rejects oversized input", () => {
  for (const operation of ["improve", "rewrite", "summarize", "expand", "translate_en", "translate_zh", "change_tone", "custom"]) {
    assert.equal(notesAiRequestSchema.safeParse({ ...request, operation }).success, true);
  }
  assert.equal(notesAiRequestSchema.safeParse({ ...request, selectedText: "x".repeat(40_001) }).success, false);
});

test("prompt keeps custom instruction, title, context and selected text", () => {
  const prompt = buildNotesAiPrompt(request);
  assert.match(prompt, /更精簡/);
  assert.match(prompt, /測試筆記/);
  assert.match(prompt, /筆記上下文/);
  assert.match(prompt, /原始內容/);
});

test("OpenAI adapter uses Responses API output text without exposing provider details to UI", async () => {
  let createInput: unknown;
  const fakeClient = {
    responses: {
      create: async (input: unknown) => {
        createInput = input;
        return { output_text: "精簡內容" };
      },
    },
  } as unknown as Parameters<typeof createOpenAiProvider>[1];
  const provider = createOpenAiProvider(
    { apiKey: "test-only", model: "configured-test-model", timeoutMs: 1_000 },
    fakeClient,
  );
  const result = await provider.generate(request);
  assert.deepEqual(result, {
    generatedText: "精簡內容",
    model: "configured-test-model",
    provider: "openai",
  });
  assert.equal((createInput as { model: string }).model, "configured-test-model");
  assert.equal((createInput as { store: boolean }).store, false);
});

test("provider selection requires explicit configuration and never falls back", () => {
  assert.throws(() => getNotesAiProvider({}), /NOTES_AI_PROVIDER/);
  assert.equal(
    getNotesAiProvider({
      NOTES_AI_PROVIDER: "openai",
      OPENAI_API_KEY: "test-only",
      NOTES_AI_MODEL: "configured-test-model",
    }).name,
    "openai",
  );
  assert.throws(
    () => getNotesAiProvider({ NOTES_AI_PROVIDER: "openai", OPENAI_API_KEY: "key" }),
    /NOTES_AI_MODEL/,
  );
  assert.throws(
    () => getNotesAiProvider({ NOTES_AI_PROVIDER: "gemini", LLM_API_KEY: "legacy-must-not-be-used" }),
    /GEMINI_API_KEY/,
  );
});

test("per-user rate limit rejects the eleventh request in a minute", () => {
  resetNotesAiRateLimitForTests();
  for (let index = 0; index < 10; index += 1) {
    assert.equal(checkNotesAiRateLimit("user", 1_000).allowed, true);
  }
  assert.equal(checkNotesAiRateLimit("user", 1_000).allowed, false);
});
