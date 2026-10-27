import { z } from "zod";


export const INSTAGRAM_POST_MAX_IMAGES = 20;
export const INSTAGRAM_IMAGE_MAX_BYTES = 10 * 1024 * 1024;
export const INSTAGRAM_TOTAL_MAX_BYTES = 50 * 1024 * 1024;
export const INSTAGRAM_CONTRACT_MAX_BYTES = 2 * 1024 * 1024;
export const INSTAGRAM_IMAGE_MIME_TYPES = ["image/jpeg", "image/png"] as const;

const hashSchema = z.string().regex(/^[0-9a-f]{64}$/);
const mediaIdSchema = z.string().regex(/^[A-Za-z0-9_-]{1,128}$/);
const shortcodeSchema = z.string().regex(/^[A-Za-z0-9_-]{5,64}$/);

const authorSchema = z.object({
  external_id: z.string().min(1).max(512).nullable(),
  handle: z.string().min(1).max(512).nullable(),
  display_name: z.string().min(1).max(512).nullable(),
}).strict().nullable();

const mediaSchema = z.object({
  id: mediaIdSchema,
  kind: z.literal("image"),
  mime: z.enum(INSTAGRAM_IMAGE_MIME_TYPES),
  sha256: hashSchema,
  size_bytes: z.number().int().min(1).max(INSTAGRAM_IMAGE_MAX_BYTES),
  source_url: z.url().max(2048).nullable().optional(),
}).strict();

const captionLocatorSchema = z.object({
  kind: z.literal("instagram_post_caption"),
  url: z.url().max(2048),
  post_id: shortcodeSchema,
  start_offset: z.number().int().min(0),
  end_offset: z.number().int().positive(),
  source_hash: hashSchema,
}).strict();

const imageLocatorSchema = z.object({
  kind: z.literal("instagram_image"),
  url: z.url().max(2048),
  post_id: shortcodeSchema,
  image_index: z.number().int().min(1).max(INSTAGRAM_POST_MAX_IMAGES),
  media_id: mediaIdSchema,
  start_offset: z.number().int().min(0),
  end_offset: z.number().int().positive(),
  source_hash: hashSchema,
}).strict();

const segmentSchema = z.object({
  id: z.string().min(1).max(128),
  text: z.string().min(1).max(100_000),
  content_hash: hashSchema,
  artifact_refs: z.array(mediaIdSchema).max(1).optional(),
  evidence_locator: z.discriminatedUnion("kind", [
    captionLocatorSchema,
    imageLocatorSchema,
  ]),
}).strict();

const instagramPostContractSchema = z.object({
  schema_version: z.literal(1),
  status: z.literal("ok"),
  source_type: z.literal("instagram_post"),
  source_url: z.url().max(2048),
  source_external_id: shortcodeSchema,
  author: authorSchema,
  published_at: z.iso.datetime({ offset: true }).nullable(),
  source_hash: hashSchema,
  text: z.string().min(1).max(2_000_000),
  media: z.array(mediaSchema).min(1).max(INSTAGRAM_POST_MAX_IMAGES),
  segments: z.array(segmentSchema).min(1).max(21),
  metadata: z.object({
    post_shortcode: shortcodeSchema,
    image_count: z.number().int().min(1).max(INSTAGRAM_POST_MAX_IMAGES),
    ocr_languages: z.array(z.string().min(1)).optional(),
  }).strict(),
  provenance: z.object({
    retrieved_at: z.iso.datetime({ offset: true }),
    processor_version: z.string().regex(/^[a-z0-9][a-z0-9._-]{0,63}$/),
    retrieval_method: z.string().min(1).max(128),
  }).strict(),
  warnings: z.array(z.string().min(1)).max(100),
}).strict();

export type InstagramPostContract = z.infer<typeof instagramPostContractSchema>;

function codePoints(value: string) {
  return Array.from(value);
}

async function sha256Hex(value: string | ArrayBuffer) {
  const bytes = typeof value === "string" ? new TextEncoder().encode(value) : value;
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function normalizeInstagramPostUrl(value: string) {
  try {
    const url = new URL(value.trim());
    const match = url.pathname.match(/^\/p\/([A-Za-z0-9_-]{5,64})\/?$/);
    if (
      url.protocol !== "https:"
      || !["instagram.com", "www.instagram.com"].includes(url.hostname.toLowerCase())
      || url.username || url.password || url.port || !match
    ) return null;
    return {
      canonicalUrl: `https://www.instagram.com/p/${match[1]}/`,
      shortcode: match[1],
    };
  } catch {
    return null;
  }
}

export async function validateInstagramPostContract(input: unknown) {
  const parsed = instagramPostContractSchema.safeParse(input);
  if (!parsed.success) throw new Error("Instagram Contract 格式不正確。");
  const contract = parsed.data;
  const identity = normalizeInstagramPostUrl(contract.source_url);
  if (!identity || identity.shortcode !== contract.source_external_id) {
    throw new Error("Instagram Contract 的來源網址不一致。");
  }
  if (await sha256Hex(contract.text) !== contract.source_hash) {
    throw new Error("Instagram Contract 的全文 hash 不一致。");
  }
  if (new Set(contract.media.map((item) => item.id)).size !== contract.media.length) {
    throw new Error("Instagram Contract 的圖片 ID 重複。");
  }
  const totalBytes = contract.media.reduce((total, item) => total + item.size_bytes, 0);
  if (totalBytes > INSTAGRAM_TOTAL_MAX_BYTES) throw new Error("Instagram 圖片總大小不得超過 50 MB。");

  const text = codePoints(contract.text);
  let previousEnd = 0;
  let previousImageIndex = 0;
  const seenSegments = new Set<string>();
  const seenImages = new Set<string>();
  const pieces: string[] = [];
  for (const [index, segment] of contract.segments.entries()) {
    if (seenSegments.has(segment.id)) throw new Error("Instagram Contract 的 segment ID 重複。");
    seenSegments.add(segment.id);
    if (await sha256Hex(segment.text) !== segment.content_hash) {
      throw new Error("Instagram Contract 的 segment hash 不一致。");
    }
    const locator = segment.evidence_locator;
    const segmentLength = codePoints(segment.text).length;
    const expectedStart = index === 0 ? 0 : previousEnd + 2;
    if (
      locator.url !== identity.canonicalUrl
      || locator.post_id !== identity.shortcode
      || locator.source_hash !== contract.source_hash
      || locator.start_offset !== expectedStart
      || locator.end_offset !== expectedStart + segmentLength
      || text.slice(locator.start_offset, locator.end_offset).join("") !== segment.text
    ) throw new Error("Instagram Contract 的 locator 不一致。");

    if (locator.kind === "instagram_post_caption") {
      if (index !== 0 || segment.artifact_refs !== undefined) {
        throw new Error("Instagram caption locator 不正確。");
      }
    } else {
      const media = contract.media[locator.image_index - 1];
      if (
        locator.image_index <= previousImageIndex
        || !media
        || media.id !== locator.media_id
        || seenImages.has(locator.media_id)
        || segment.artifact_refs?.length !== 1
        || segment.artifact_refs[0] !== locator.media_id
      ) throw new Error("Instagram image locator 不正確。");
      previousImageIndex = locator.image_index;
      seenImages.add(locator.media_id);
    }
    previousEnd = locator.end_offset;
    pieces.push(segment.text);
  }
  if (pieces.join("\n\n") !== contract.text) throw new Error("Instagram segments 無法重建全文。");
  if (
    contract.metadata.post_shortcode !== identity.shortcode
    || contract.metadata.image_count !== contract.media.length
  ) throw new Error("Instagram metadata 不一致。");
  return contract;
}

export async function attachInstagramMedia(
  rawContract: unknown,
  files: File[],
): Promise<InstagramPostContract> {
  if (!rawContract || typeof rawContract !== "object") throw new Error("Instagram Contract 格式不正確。");
  const draft = structuredClone(rawContract) as Record<string, unknown>;
  if (!Array.isArray(draft.media) || draft.media.length !== files.length) {
    throw new Error("選取的圖片數量必須和 Contract media 完全相同。");
  }
  draft.media = await Promise.all(draft.media.map(async (raw, index) => {
    if (!raw || typeof raw !== "object") throw new Error("Instagram Contract 的 media 不正確。");
    const file = files[index];
    if (!INSTAGRAM_IMAGE_MIME_TYPES.includes(file.type as typeof INSTAGRAM_IMAGE_MIME_TYPES[number])) {
      throw new Error("Instagram 圖片只接受 JPEG 或 PNG。");
    }
    if (!file.size || file.size > INSTAGRAM_IMAGE_MAX_BYTES) {
      throw new Error("每張 Instagram 圖片必須小於 10 MB。");
    }
    return {
      ...(raw as Record<string, unknown>),
      mime: file.type,
      size_bytes: file.size,
      sha256: await sha256Hex(await file.arrayBuffer()),
    };
  }));
  return validateInstagramPostContract(draft);
}

export function instagramContractObjectPath(userId: string, assetId: string) {
  return `${userId}/${assetId}/original/instagram-contract.json`;
}

export function instagramOriginalMediaPath(userId: string, assetId: string, mediaId: string) {
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(mediaId)) throw new Error("Instagram media ID 不正確。");
  return `${userId}/${assetId}/original/images/${mediaId}`;
}

export function hasInstagramImageSignature(bytes: Uint8Array, mime: string) {
  if (mime === "image/jpeg") {
    return bytes.length >= 5
      && bytes[0] === 0xff && bytes[1] === 0xd8 && bytes[2] === 0xff
      && bytes.at(-2) === 0xff && bytes.at(-1) === 0xd9;
  }
  if (mime === "image/png") {
    const signature = [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a];
    return bytes.length >= signature.length
      && signature.every((byte, index) => bytes[index] === byte);
  }
  return false;
}

export function instagramCitation(locator: unknown) {
  if (!locator || typeof locator !== "object") return null;
  const value = locator as Record<string, unknown>;
  if (!["instagram_post_caption", "instagram_image"].includes(String(value.kind))) return null;
  if (typeof value.url !== "string" || typeof value.post_id !== "string") return null;
  const identity = normalizeInstagramPostUrl(value.url);
  if (!identity || identity.shortcode !== value.post_id) return null;
  if (value.kind === "instagram_image") {
    if (!Number.isInteger(value.image_index) || Number(value.image_index) < 1 || Number(value.image_index) > 20) return null;
    if (typeof value.media_id !== "string" || !/^[A-Za-z0-9_-]{1,128}$/.test(value.media_id)) return null;
  }
  return {
    sourceUrl: identity.canonicalUrl,
    imageIndex: value.kind === "instagram_image" ? Number(value.image_index) : null,
    mediaId: value.kind === "instagram_image" ? String(value.media_id) : null,
  };
}
