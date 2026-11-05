"use client";

import type { JSONContent } from "@tiptap/core";
import { useState } from "react";

import { TiptapEditor } from "./TiptapEditor";

const INITIAL_CONTENT: JSONContent = {
  type: "doc",
  content: [
    {
      type: "paragraph",
      content: [{ type: "text", text: "Alpha paragraph for AI cursor browser testing." }],
    },
    {
      type: "paragraph",
      content: [{ type: "text", text: "Second paragraph remains available for insert tests." }],
    },
    {
      type: "spreadsheetBlock",
      attrs: {
        data: {
          version: 2,
          workbookId: "e2e-workbook",
          activeSheetId: "e2e-sheet",
          sheets: [{ id: "e2e-sheet", name: "Sheet 1", rows: 4, cols: 4, cells: {} }],
          metadata: { templateId: "blank", templateLabel: "E2E Workbook" },
        },
      },
    },
    {
      type: "paragraph",
      content: [{ type: "text", text: "Trailing safe paragraph." }],
    },
  ],
};

export function AiEditorE2EHarness() {
  const [updateCount, setUpdateCount] = useState(0);
  const [plainText, setPlainText] = useState("");
  return (
    <main className="mx-auto max-w-5xl p-6">
      <h1 className="mb-4 text-xl font-semibold">AI Editor E2E Harness</h1>
      <TiptapEditor
        initialContent={INITIAL_CONTENT}
        noteTitle="E2E 測試筆記"
        savingStatus="saved"
        onChange={(_json, nextPlainText) => {
          setUpdateCount((count) => count + 1);
          setPlainText(nextPlainText);
        }}
      />
      <output data-testid="update-count" className="mt-3 block text-xs">{updateCount}</output>
      <output data-testid="plain-text" className="sr-only">{plainText}</output>
    </main>
  );
}
