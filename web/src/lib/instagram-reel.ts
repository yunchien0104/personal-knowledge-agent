import { z } from "zod";

import {
  INSTAGRAM_CONTRACT_MAX_BYTES,
  INSTAGRAM_IMAGE_MAX_BYTES,
  INSTAGRAM_IMAGE_MIME_TYPES,
  INSTAGRAM_TOTAL_MAX_BYTES,
  hasInstagramImageSignature,
} from "./instagram.ts";

export { INSTAGRAM_CONTRACT_MAX_BYTES };
export { hasInstagramImageSignature } from "./instagram.ts";
export const INSTAGRAM_REEL_MAX_KEYFRAMES = 20;

const hashSchema = z.string().regex(/^[0-9a-f]{64}$/);
const mediaIdSchema = z.string().regex(/^[A-Za-z0-9_-]{1,128}$/);
const shortcodeSchema = z.string().regex(/^[A-Za-z0-9_-]{5,64}$/);
const authorSchema = z.object({
  external_id: z.string().min(1).max(512).nullable(),
  handle: z.string().min(1).max(512).nullable(),
  display_name: z.string().min(1).max(512).nullable(),
}).strict().nullable();

const videoSchema = z.object({
  id: mediaIdSchema,
  kind: z.literal("video"),
  mime: z.literal("video/mp4").nullable().optional(),
  source_url: z.url().max(2048).nullable().optional(),
  sha256: hashSchema.nullable().optional(),
  size_bytes: z.number().int().min(0).nullable().optional(),
  duration_seconds: z.number().positive().max(21_600),
}).strict();

const keyframeSchema = z.object({
  id: mediaIdSchema,
  kind: z.literal("keyframe"),
  mime: z.enum(INSTAGRAM_IMAGE_MIME_TYPES),
  source_url: z.url().max(2048).nullable().optional(),
  sha256: hashSchema,
  size_bytes: z.number().int().min(1).max(INSTAGRAM_IMAGE_MAX_BYTES),
  duration_seconds: z.number().min(0).nullable().optional(),
}).strict();

const captionLocatorSchema = z.object({
  kind: z.literal("instagram_caption"),
  url: z.url().max(2048),
  reel_id: shortcodeSchema,
  start_offset: z.number().int().min(0),
  end_offset: z.number().int().positive(),
  source_hash: hashSchema,
}).strict();

const videoLocatorSchema = z.object({
  kind: z.literal("video_segment"),
  start_seconds: z.number().min(0),
  end_seconds: z.number().positive(),
  start_offset: z.number().int().min(0),
  end_offset: z.number().int().positive(),
  source_hash: hashSchema,
  frame_ids: z.array(mediaIdSchema).max(INSTAGRAM_REEL_MAX_KEYFRAMES).optional(),
  content_origin: z.enum(["source_transcript", "ai_visual_observation"]).optional(),
  model_name: z.string().min(1).max(128).optional(),
}).strict();

const segmentSchema = z.object({
  id: z.string().min(1).max(128),
  text: z.string().min(1).max(100_000),
  content_hash: hashSchema,
  evidence_locator: z.discriminatedUnion("kind", [captionLocatorSchema, videoLocatorSchema]),
}).strict();

const reelContractSchema = z.object({
  schema_version: z.union([z.literal(1), z.literal(2)]),
  status: z.literal("ok"),
  source_type: z.literal("instagram_reel"),
  source_url: z.url().max(2048),
  source_external_id: shortcodeSchema,
  author: authorSchema,
  published_at: z.iso.datetime({ offset: true }).nullable(),
  source_hash: hashSchema,
  text: z.string().min(1).max(2_000_000),
  media: z.array(z.discriminatedUnion("kind", [videoSchema, keyframeSchema])).max(21),
  segments: z.array(segmentSchema).min(1).max(1000),
  metadata: z.object({
    reel_shortcode: shortcodeSchema,
    transcript_language: z.string().min(1).max(64).nullable().optional(),
    duration_seconds: z.number().positive().max(21_600).optional(),
  }).strict(),
  provenance: z.object({
    retrieved_at: z.iso.datetime({ offset: true }),
    processor_version: z.string().regex(/^[a-z0-9][a-z0-9._-]{0,63}$/),
    retrieval_method: z.string().min(1).max(128),
  }).strict(),
  warnings: z.array(z.string().min(1)).max(100),
}).strict();

export type InstagramReelContract = z.infer<typeof reelContractSchema>;

function codePoints(value: string) {
  return Array.from(value);
}

async function sha256Hex(value: string | ArrayBuffer) {
  const bytes = typeof value === "string" ? new TextEncoder().encode(value) : value;
  const digest = await globalThis.crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
}

export function normalizeInstagramReelUrl(value: string) {
  try {
    const url = new URL(value.trim());
    const match = url.pathname.match(/^\/reel\/([A-Za-z0-9_-]{5,64})\/?$/);
    if (
      url.protocol !== "https:"
      || !["instagram.com", "www.instagram.com"].includes(url.hostname.toLowerCase())
      || url.username || url.password || url.port || !match
    ) return null;
    return { canonicalUrl: `https://www.instagram.com/reel/${match[1]}/`, shortcode: match[1] };
  } catch {
    return null;
  }
}

export async function validateInstagramReelContract(input: unknown) {
  const parsed = reelContractSchema.safeParse(input);
  if (!parsed.success) throw new Error("Instagram Reel Contract 格式不正確。");
  const contract = parsed.data;
  const identity = normalizeInstagramReelUrl(contract.source_url);
  if (!identity || identity.shortcode !== contract.source_external_id) {
    throw new Error("Instagram Reel Contract 的來源網址不一致。");
  }
  if (await sha256Hex(contract.text) !== contract.source_hash) {
    throw new Error("Instagram Reel Contract 的全文 hash 不一致。");
  }
  const ids = contract.media.map((item) => item.id);
  if (new Set(ids).size !== ids.length) throw new Error("Instagram Reel media ID 重複。");
  const videos = contract.media.filter((item) => item.kind === "video");
  const frames = contract.media.filter((item) => item.kind === "keyframe");
  if (videos.length > 1 || frames.length > INSTAGRAM_REEL_MAX_KEYFRAMES) {
    throw new Error("Instagram Reel media 數量超出限制。");
  }
  if (frames.reduce((total, item) => total + item.size_bytes, 0) > INSTAGRAM_TOTAL_MAX_BYTES) {
    throw new Error("Instagram Reel keyframes 總大小不得超過 50 MB。");
  }
  const frameIds = new Set(frames.map((item) => item.id));
  const text = codePoints(contract.text);
  const seenSegments = new Set<string>();
  let previousEnd = 0;
  let previousSeconds = 0;
  let sawCaption = false;
  const pieces: string[] = [];
  for (const [index, segment] of contract.segments.entries()) {
    if (seenSegments.has(segment.id)) throw new Error("Instagram Reel segment ID 重複。");
    seenSegments.add(segment.id);
    if (await sha256Hex(segment.text) !== segment.content_hash) {
      throw new Error("Instagram Reel segment hash 不一致。");
    }
    const locator = segment.evidence_locator;
    const separator = index === 0 ? 0 : sawCaption && index === 1 ? 2 : 1;
    const expectedStart = previousEnd + separator;
    const expectedEnd = expectedStart + codePoints(segment.text).length;
    if (
      locator.start_offset !== expectedStart || locator.end_offset !== expectedEnd
      || locator.source_hash !== contract.source_hash
      || text.slice(expectedStart, expectedEnd).join("") !== segment.text
    ) throw new Error("Instagram Reel locator 與全文不一致。");
    if (locator.kind === "instagram_caption") {
      if (index !== 0 || locator.url !== identity.canonicalUrl || locator.reel_id !== identity.shortcode) {
        throw new Error("Instagram Reel caption locator 不正確。");
      }
      sawCaption = true;
    } else {
      if (locator.end_seconds <= locator.start_seconds
        || (contract.schema_version === 1 && locator.start_seconds < previousSeconds)) {
        throw new Error("Instagram Reel 時間定位不正確。");
      }
      if (contract.schema_version === 2) {
        const visual = locator.content_origin === "ai_visual_observation";
        if (!locator.content_origin || visual !== Boolean(locator.model_name)) {
          throw new Error("Instagram Reel 內容來源標記不正確。");
        }
      }
      if ((locator.frame_ids ?? []).some((frameId) => !frameIds.has(frameId))) {
        throw new Error("Instagram Reel frame locator 不正確。");
      }
      previousSeconds = locator.end_seconds;
    }
    previousEnd = expectedEnd;
    pieces.push(segment.text);
  }
  const reconstructed = sawCaption
    ? `${pieces[0]}${pieces.length > 1 ? `\n\n${pieces.slice(1).join("\n")}` : ""}`
    : pieces.join("\n");
  if (reconstructed !== contract.text || contract.metadata.reel_shortcode !== identity.shortcode) {
    throw new Error("Instagram Reel Contract 無法重建全文。");
  }
  return contract;
}

export async function attachInstagramReelKeyframes(raw: unknown, files: File[]) {
  if (!raw || typeof raw !== "object") throw new Error("Instagram Reel Contract 格式不正確。");
  const draft = structuredClone(raw) as Record<string, unknown>;
  if (!Array.isArray(draft.media)) throw new Error("Instagram Reel media 格式不正確。");
  const keyframes = draft.media.filter((item) =>
    item && typeof item === "object" && (item as Record<string, unknown>).kind === "keyframe"
  );
  if (keyframes.length !== files.length) throw new Error("Keyframe 數量必須和 Contract 完全相同。");
  let cursor = 0;
  draft.media = await Promise.all(draft.media.map(async (rawMedia) => {
    if (!rawMedia || typeof rawMedia !== "object") throw new Error("Instagram Reel media 不正確。");
    const media = rawMedia as Record<string, unknown>;
    if (media.kind !== "keyframe") return media;
    const file = files[cursor++];
    if (!INSTAGRAM_IMAGE_MIME_TYPES.includes(file.type as typeof INSTAGRAM_IMAGE_MIME_TYPES[number])) {
      throw new Error("Reel keyframe 只接受 JPEG 或 PNG。");
    }
    if (!file.size || file.size > INSTAGRAM_IMAGE_MAX_BYTES) throw new Error("每張 keyframe 必須小於 10 MB。");
    const buffer = await file.arrayBuffer();
    const bytes = new Uint8Array(buffer);
    if (!hasInstagramImageSignature(bytes, file.type)) throw new Error("Reel keyframe 檔案格式不正確。");
    return { ...media, mime: file.type, size_bytes: file.size, sha256: await sha256Hex(buffer) };
  }));
  return validateInstagramReelContract(draft);
}

export function instagramReelContractObjectPath(userId: string, assetId: string) {
  return `${userId}/${assetId}/original/instagram-reel-contract.json`;
}

export function instagramReelKeyframePath(userId: string, assetId: string, mediaId: string) {
  if (!/^[A-Za-z0-9_-]{1,128}$/.test(mediaId)) throw new Error("Reel keyframe ID 不正確。");
  return `${userId}/${assetId}/original/keyframes/${mediaId}`;
}

export function instagramReelCitation(locator: unknown, sourceUrl?: string | null) {
  if (!locator || typeof locator !== "object") return null;
  const value = locator as Record<string, unknown>;
  if (value.kind === "instagram_caption") {
    if (typeof value.url !== "string" || typeof value.reel_id !== "string") return null;
    const identity = normalizeInstagramReelUrl(value.url);
    if (!identity || identity.shortcode !== value.reel_id) return null;
    return { sourceUrl: identity.canonicalUrl, startSeconds: null, endSeconds: null, mediaId: null };
  }
  if (value.kind !== "video_segment" || typeof sourceUrl !== "string") return null;
  const identity = normalizeInstagramReelUrl(sourceUrl);
  if (!identity || typeof value.start_seconds !== "number" || typeof value.end_seconds !== "number"
    || value.start_seconds < 0 || value.end_seconds <= value.start_seconds) return null;
  const frames = Array.isArray(value.frame_ids) ? value.frame_ids : [];
  if (!frames.every((item) => typeof item === "string" && /^[A-Za-z0-9_-]{1,128}$/.test(item))) return null;
  return {
    sourceUrl: identity.canonicalUrl,
    startSeconds: value.start_seconds,
    endSeconds: value.end_seconds,
    mediaId: frames[0] ? String(frames[0]) : null,
  };
}
