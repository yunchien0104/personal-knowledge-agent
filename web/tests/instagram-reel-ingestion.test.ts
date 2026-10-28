import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  attachInstagramReelKeyframes,
  instagramReelCitation,
  normalizeInstagramReelUrl,
  validateInstagramReelContract,
} from "../src/lib/instagram-reel.ts";

const hash = (value: string) => createHash("sha256").update(value).digest("hex");

function contract() {
  const caption = "Reel caption 😀";
  const transcript = "第一段逐字稿";
  const text = `${caption}\n\n${transcript}`;
  const sourceHash = hash(text);
  return {
    schema_version: 1, status: "ok", source_type: "instagram_reel",
    source_url: "https://www.instagram.com/reel/C0dExReel1/", source_external_id: "C0dExReel1",
    author: null, published_at: null, source_hash: sourceHash, text,
    media: [{ id: "frame-001", kind: "keyframe", mime: "image/jpeg", sha256: "a".repeat(64), size_bytes: 6 }],
    segments: [
      { id: "caption", text: caption, content_hash: hash(caption), evidence_locator: {
        kind: "instagram_caption", url: "https://www.instagram.com/reel/C0dExReel1/", reel_id: "C0dExReel1",
        start_offset: 0, end_offset: Array.from(caption).length, source_hash: sourceHash,
      } },
      { id: "transcript-1", text: transcript, content_hash: hash(transcript), evidence_locator: {
        kind: "video_segment", start_seconds: 0, end_seconds: 4.2,
        start_offset: Array.from(caption).length + 2, end_offset: Array.from(text).length,
        source_hash: sourceHash, frame_ids: ["frame-001"],
      } },
    ],
    metadata: { reel_shortcode: "C0dExReel1", transcript_language: "zh" },
    provenance: { retrieved_at: "2026-10-27T00:00:00Z", processor_version: "instagram-reel-unified-v1", retrieval_method: "ig_transcript_mac_app" },
    warnings: [],
  };
}

test("validates caption plus transcript Reel Contract", async () => {
  const value = contract();
  assert.deepEqual(await validateInstagramReelContract(value), value);
});

test("accepts Contract v2 with explicit transcript provenance", async () => {
  const value = contract();
  const v2 = {
    ...value,
    schema_version: 2,
    metadata: { ...value.metadata, duration_seconds: 4.2 },
    segments: [value.segments[0], {
      ...value.segments[1],
      evidence_locator: {
        ...value.segments[1].evidence_locator,
        content_origin: "source_transcript",
      },
    }],
  };
  assert.equal((await validateInstagramReelContract(v2)).schema_version, 2);
});

test("accepts caption-only Reel without keyframes", async () => {
  const value = contract();
  const text = value.segments[0].text;
  const sourceHash = hash(text);
  const captionOnly = {
    ...value, text, source_hash: sourceHash, media: [],
    segments: [{ ...value.segments[0], evidence_locator: {
      ...value.segments[0].evidence_locator, source_hash: sourceHash,
    } }],
  };
  assert.equal((await validateInstagramReelContract(captionOnly)).segments.length, 1);
});

test("attaches keyframe bytes and fails closed on missing references", async () => {
  const value = contract();
  const jpeg = new File([new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0xff, 0xd9])], "frame.jpg", { type: "image/jpeg" });
  const enriched = await attachInstagramReelKeyframes(value, [jpeg]);
  assert.equal(enriched.media[0].size_bytes, 6);
  const invalid = contract();
  invalid.segments[1].evidence_locator.frame_ids = ["missing"];
  await assert.rejects(validateInstagramReelContract(invalid));
});

test("normalizes URLs and resolves timestamp/keyframe citations", () => {
  assert.equal(normalizeInstagramReelUrl("https://instagram.com.evil.test/reel/C0dExReel1/"), null);
  const locator = contract().segments[1].evidence_locator;
  assert.deepEqual(instagramReelCitation(locator, "https://www.instagram.com/reel/C0dExReel1/"), {
    sourceUrl: "https://www.instagram.com/reel/C0dExReel1/", startSeconds: 0, endSeconds: 4.2, mediaId: "frame-001",
  });
});
