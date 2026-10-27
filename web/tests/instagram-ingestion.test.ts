import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import test from "node:test";

import {
  attachInstagramMedia,
  hasInstagramImageSignature,
  instagramCitation,
  normalizeInstagramPostUrl,
  validateInstagramPostContract,
} from "../src/lib/instagram.ts";


function hash(value: string) {
  return createHash("sha256").update(value).digest("hex");
}

function contract() {
  const caption = "Caption 😀";
  const ocr = "圖片文字";
  const text = `${caption}\n\n${ocr}`;
  const sourceHash = hash(text);
  return {
    schema_version: 1,
    status: "ok",
    source_type: "instagram_post",
    source_url: "https://www.instagram.com/p/C0dExPost1/",
    source_external_id: "C0dExPost1",
    author: null,
    published_at: null,
    source_hash: sourceHash,
    text,
    media: [{
      id: "image-001", kind: "image", mime: "image/jpeg",
      sha256: "a".repeat(64), size_bytes: 100,
    }],
    segments: [
      {
        id: "caption", text: caption, content_hash: hash(caption),
        evidence_locator: {
          kind: "instagram_post_caption", url: "https://www.instagram.com/p/C0dExPost1/",
          post_id: "C0dExPost1", start_offset: 0, end_offset: Array.from(caption).length,
          source_hash: sourceHash,
        },
      },
      {
        id: "image-001-ocr", text: ocr, content_hash: hash(ocr), artifact_refs: ["image-001"],
        evidence_locator: {
          kind: "instagram_image", url: "https://www.instagram.com/p/C0dExPost1/",
          post_id: "C0dExPost1", image_index: 1, media_id: "image-001",
          start_offset: Array.from(caption).length + 2,
          end_offset: Array.from(text).length, source_hash: sourceHash,
        },
      },
    ],
    metadata: { post_shortcode: "C0dExPost1", image_count: 1 },
    provenance: {
      retrieved_at: "2026-10-27T00:00:00Z",
      processor_version: "instagram-post-unified-v1",
      retrieval_method: "ig_transcript_mac_app",
    },
    warnings: [],
  };
}

test("validates the merged Instagram Contract using Unicode code-point offsets", async () => {
  const value = contract();
  assert.deepEqual(await validateInstagramPostContract(value), value);
});

test("enriches the merged adapter output with the selected image bytes", async () => {
  const value = contract();
  const adapterOutput = {
    ...value,
    media: value.media.map((media) => ({
      id: media.id,
      kind: media.kind,
      mime: media.mime,
    })),
  };
  const jpeg = new File(
    [new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0xff, 0xd9])],
    "image-001.jpg",
    { type: "image/jpeg" },
  );

  const enriched = await attachInstagramMedia(adapterOutput, [jpeg]);

  assert.equal(enriched.media[0].size_bytes, jpeg.size);
  assert.equal(
    enriched.media[0].sha256,
    createHash("sha256").update(Buffer.from(await jpeg.arrayBuffer())).digest("hex"),
  );
});

test("fails closed on identity, locator, and hash changes", async () => {
  const invalid = contract();
  invalid.segments[1].evidence_locator.media_id = "other";
  await assert.rejects(validateInstagramPostContract(invalid));

  assert.equal(normalizeInstagramPostUrl("https://instagram.com.evil.test/p/C0dExPost1/"), null);
});

test("resolves only canonical Instagram image citations", () => {
  const locator = contract().segments[1].evidence_locator;
  assert.deepEqual(instagramCitation(locator), {
    sourceUrl: "https://www.instagram.com/p/C0dExPost1/",
    imageIndex: 1,
    mediaId: "image-001",
  });
  assert.equal(instagramCitation({ ...locator, url: "https://example.test/p/C0dExPost1/" }), null);
  assert.equal(instagramCitation({ kind: "pdf_page", page_number: 1 }), null);
});

test("accepts only JPEG or PNG file signatures declared by the Contract", () => {
  assert.equal(
    hasInstagramImageSignature(
      new Uint8Array([0xff, 0xd8, 0xff, 0xe0, 0xff, 0xd9]),
      "image/jpeg",
    ),
    true,
  );
  assert.equal(
    hasInstagramImageSignature(
      new Uint8Array([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
      "image/png",
    ),
    true,
  );
  assert.equal(
    hasInstagramImageSignature(new TextEncoder().encode("not-an-image"), "image/jpeg"),
    false,
  );
});
