import type { JSONContent } from "@tiptap/core";

export type NoteSavingStatus =
  | "saved"
  | "saving"
  | "unsaved"
  | "failed"
  | "conflict";

export type NoteSaveReason = "autosave" | "manual";

export type NoteDraft = {
  title: string;
  contentJson: JSONContent | null;
  plainText: string;
};

export type NoteSaveRequest = NoteDraft & {
  expectedRevision: number;
  reason: NoteSaveReason;
};

export type NoteSaveResult =
  | { status: "saved"; bodyRevision: number }
  | { status: "conflict" };

type NoteAutosaveOptions = {
  initialDraft: NoteDraft;
  initialRevision: number;
  save: (request: NoteSaveRequest) => Promise<NoteSaveResult>;
  onStatus: (status: NoteSavingStatus) => void;
  onRevision: (revision: number) => void;
  onConflict: () => void;
  onSaved?: (request: NoteSaveRequest, bodyRevision: number, isLatest: boolean) => void;
  onError?: (error: unknown) => void;
};

export type NoteAutosaveQueue = {
  update: (draft: NoteDraft) => void;
  requestSave: (reason?: NoteSaveReason) => Promise<void>;
  getDraft: () => NoteDraft;
  getRevision: () => number;
  hasUnsavedChanges: () => boolean;
  markConflict: () => void;
};

/**
 * Serializes note writes so every PATCH uses the revision returned by the
 * previous PATCH. New edits made while a request is running stay dirty and are
 * never marked saved by an older response.
 */
export function createNoteAutosaveQueue(options: NoteAutosaveOptions): NoteAutosaveQueue {
  let latestDraft = options.initialDraft;
  let revision = options.initialRevision;
  let changeVersion = 0;
  let savedVersion = 0;
  let queuedReason: NoteSaveReason | null = null;
  let drainPromise: Promise<void> | null = null;
  let conflicted = false;

  async function drain() {
    while (queuedReason && !conflicted) {
      const reason = queuedReason;
      queuedReason = null;
      const requestVersion = changeVersion;
      const requestDraft = latestDraft;

      if (reason === "autosave" && requestVersion === savedVersion) continue;

      options.onStatus("saving");
      try {
        const saveRequest: NoteSaveRequest = {
          ...requestDraft,
          expectedRevision: revision,
          reason,
        };
        const result = await options.save(saveRequest);

        if (result.status === "conflict") {
          conflicted = true;
          options.onStatus("conflict");
          options.onConflict();
          break;
        }

        revision = result.bodyRevision;
        savedVersion = requestVersion;
        options.onSaved?.(saveRequest, result.bodyRevision, changeVersion === requestVersion);
        options.onRevision(revision);
        options.onStatus(changeVersion === savedVersion ? "saved" : "unsaved");
      } catch (error) {
        options.onStatus("failed");
        options.onError?.(error);
        break;
      }
    }
  }

  function requestSave(reason: NoteSaveReason = "autosave") {
    if (conflicted) return drainPromise ?? Promise.resolve();
    if (reason === "manual" || queuedReason === null) queuedReason = reason;
    if (!drainPromise) {
      drainPromise = drain().finally(() => {
        drainPromise = null;
        if (queuedReason && !conflicted) void requestSave(queuedReason);
      });
    }
    return drainPromise;
  }

  return {
    update(draft) {
      latestDraft = draft;
      changeVersion += 1;
      options.onStatus("unsaved");
    },
    requestSave,
    getDraft: () => latestDraft,
    getRevision: () => revision,
    hasUnsavedChanges: () => changeVersion !== savedVersion,
    markConflict() {
      conflicted = true;
      options.onStatus("conflict");
      options.onConflict();
    },
  };
}
