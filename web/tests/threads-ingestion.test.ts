import assert from "node:assert/strict";
import test from "node:test";

import {
  normalizeThreadsUrl,
  submitThreadsUrl,
  threadsCitationUrl,
  threadsEnqueueResultSchema,
} from "../src/lib/threads.ts";

test("sends optional user-provided text as the public-fetch fallback", async () => {
  const originalFetch = globalThis.fetch;
  let requestBody: unknown;
  globalThis.fetch = async (_input, init) => {
    requestBody = JSON.parse(String(init?.body));
    return Response.json({ data: {
      duplicate: false,
      asset: {
        id: "73000000-0000-4000-8000-000000000003",
        source_type: "threads",
        source_url: "https://www.threads.net/t/DewcDm5n4FD",
        source_external_id: "DewcDm5n4FD",
        status: "pending",
        active_revision_id: null,
        created_at: "2026-10-22T00:00:00Z",
        updated_at: "2026-10-22T00:00:00Z",
      },
      job: {
        id: "71000000-0000-4000-8000-000000000001",
        status: "queued",
        idempotencyKey: "ingest_threads:73000000-0000-4000-8000-000000000003:source-v1",
      },
    } });
  };
  try {
    await submitThreadsUrl(
      "https://www.threads.net/t/DewcDm5n4FD",
      "  User-provided evidence.  ",
    );
  } finally {
    globalThis.fetch = originalFetch;
  }
  assert.deepEqual(requestBody, {
    source_type: "threads",
    source_url: "https://www.threads.net/t/DewcDm5n4FD",
    source_text: "User-provided evidence.",
  });
});

test("normalizes supported Threads post links to one canonical identity", () => {
  assert.deepEqual(
    normalizeThreadsUrl(
      "https://www.threads.com/@benjaminmarketlab/post/DewcDm5n4FD?xmt=tracking#share",
    ),
    {
      canonicalUrl: "https://www.threads.net/t/DewcDm5n4FD",
      postId: "DewcDm5n4FD",
    },
  );
  assert.deepEqual(
    normalizeThreadsUrl("https://threads.net/t/DewcDm5n4FD"),
    {
      canonicalUrl: "https://www.threads.net/t/DewcDm5n4FD",
      postId: "DewcDm5n4FD",
    },
  );
});

test("rejects non-post, credentialed, insecure, and lookalike URLs", () => {
  for (const value of [
    "https://threads.net/@account",
    "http://threads.net/t/DewcDm5n4FD",
    "https://user:pass@threads.net/t/DewcDm5n4FD",
    "https://threads.net.evil.test/t/DewcDm5n4FD",
    "https://example.test/t/DewcDm5n4FD",
  ]) assert.equal(normalizeThreadsUrl(value), null, value);
});

test("validates the typed Asset and Job enqueue response", () => {
  assert.equal(threadsEnqueueResultSchema.safeParse({
    duplicate: false,
    asset: {
      id: "73000000-0000-4000-8000-000000000003",
      source_type: "threads",
      source_url: "https://www.threads.net/t/DewcDm5n4FD",
      source_external_id: "DewcDm5n4FD",
      status: "pending",
      active_revision_id: null,
      created_at: "2026-10-22T00:00:00Z",
      updated_at: "2026-10-22T00:00:00Z",
    },
    job: {
      id: "71000000-0000-4000-8000-000000000001",
      status: "queued",
      idempotencyKey: "ingest_threads:73000000-0000-4000-8000-000000000003:source-v1",
    },
  }).success, true);
});

test("resolves citation URLs only from a matching immutable Threads locator", () => {
  const originalUrl = "https://www.threads.net/t/DewcDm5n4FD";
  const mutableAssetUrl = "https://www.threads.net/t/OtherPost1";
  const locator = {
    kind: "threads_post",
    url: originalUrl,
    post_id: "DewcDm5n4FD",
    start_offset: 0,
    end_offset: 8,
  };

  assert.equal(threadsCitationUrl(locator), originalUrl);
  assert.notEqual(threadsCitationUrl(locator), mutableAssetUrl);
  assert.equal(
    threadsCitationUrl({
      ...locator,
      source_url: "https://example.test/t/PoisonedPost",
      url: `${originalUrl}?url=https://example.test/t/PoisonedPost`,
    }),
    originalUrl,
  );
  assert.equal(
    threadsCitationUrl({ ...locator, url: "https://threads.net.evil.test/t/DewcDm5n4FD" }),
    null,
  );
  assert.equal(threadsCitationUrl({ ...locator, post_id: "OtherPost1" }), null);
  assert.equal(threadsCitationUrl({ kind: "pdf_page", page_number: 1 }), null);
});
