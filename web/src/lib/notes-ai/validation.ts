import { z } from "zod";

export const NOTES_AI_MAX_REQUEST_BYTES = 140_000;

export const notesAiRequestSchema = z.object({
  operation: z.enum([
    "improve",
    "rewrite",
    "summarize",
    "expand",
    "translate_en",
    "translate_zh",
    "change_tone",
    "custom",
  ]),
  instruction: z.string().trim().max(4_000).default(""),
  selectedText: z.string().trim().min(1).max(40_000),
  context: z.string().trim().max(80_000).default(""),
  noteTitle: z.string().trim().max(500).default(""),
}).strict();
