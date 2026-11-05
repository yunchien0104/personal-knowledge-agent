import assert from "node:assert/strict";
import test from "node:test";

import {
  buildWordDiff,
  clampPopoverPosition,
  requestAiSuggestion,
} from "../src/components/notes/aiAssistant.ts";

test("popover stays inside the viewport near every edge", () => {
  const viewport = { width: 1024, height: 768 };
  const popover = { width: 360, height: 600 };
  assert.deepEqual(
    clampPopoverPosition({ x: 1000, y: 740 }, viewport, popover),
    { x: 652, y: 128 },
  );
  assert.deepEqual(
    clampPopoverPosition({ x: -20, y: 0 }, viewport, popover),
    { x: 12, y: 12 },
  );
});

test("word diff marks additions and removals and bounds large input", () => {
  const parts = buildWordDiff("原本 的句子", "改寫 的句子");
  assert.equal(parts.map((part) => part.value).join(""), "原本改寫 的句子");
  assert.deepEqual(parts.map((part) => part.type), ["removed", "added", "same"]);

  const original = Array.from({ length: 300 }, (_, index) => `a${index}`).join(" ");
  const suggested = Array.from({ length: 300 }, (_, index) => `b${index}`).join(" ");
  assert.deepEqual(
    buildWordDiff(original, suggested).map((part) => part.type),
    ["removed", "added"],
  );
});

test("mock request preserves the public API contract", async () => {
  let requestBody = "";
  const generatedText = await requestAiSuggestion(
    {
      operation: "rewrite",
      instruction: "更精簡",
      selectedText: "原始內容",
      context: "筆記上下文",
      noteTitle: "測試筆記",
    },
    undefined,
    async (input, init) => {
      assert.equal(input, "/api/personal-notes/ai");
      assert.equal(init?.method, "POST");
      requestBody = String(init?.body);
      return new Response(JSON.stringify({ generatedText: "精簡內容" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    },
  );
  assert.equal(generatedText, "精簡內容");
  assert.equal(JSON.parse(requestBody).operation, "rewrite");
});

test("request parser supports the repository error envelope", async () => {
  await assert.rejects(
    () => requestAiSuggestion(
      {
        operation: "improve",
        instruction: "",
        selectedText: "文字",
        context: "",
        noteTitle: "",
      },
      undefined,
      async () => new Response(JSON.stringify({
        data: null,
        error: { code: "UNAUTHENTICATED", message: "Authentication is required." },
      }), { status: 401 }),
    ),
    /Authentication is required/,
  );
});
