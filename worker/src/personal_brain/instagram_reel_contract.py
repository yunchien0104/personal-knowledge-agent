from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import math
import re
from typing import Any, Mapping

from personal_brain.instagram_contract import InstagramContractError

MAX_REEL_KEYFRAMES = 20
MAX_KEYFRAME_BYTES = 10 * 1024 * 1024
MAX_TOTAL_BYTES = 50 * 1024 * 1024
IMAGE_MIMES = {"image/jpeg", "image/png"}
_HASH = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SHORTCODE = re.compile(r"^[A-Za-z0-9_-]{5,64}$")
_PROCESSOR = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def _obj(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InstagramContractError(f"{name} must be an object")
    return value


def _exact(value: Mapping[str, Any], required: set[str], optional: set[str], name: str) -> None:
    if set(value) - required - optional or required - set(value):
        raise InstagramContractError(f"{name} fields are invalid")


def _timestamp(value: Any, name: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or len(value) > 64:
        raise InstagramContractError(f"{name} is invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise InstagramContractError(f"{name} must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise InstagramContractError(f"{name} must include a timezone")


def validate_instagram_reel_contract(value: Any) -> dict[str, Any]:
    result = dict(_obj(value, "Instagram Reel contract"))
    required = {"schema_version", "status", "source_type", "source_url", "source_external_id",
                "author", "published_at", "source_hash", "text", "media", "segments",
                "metadata", "provenance", "warnings"}
    _exact(result, required, set(), "Instagram Reel contract")
    if result["schema_version"] != 1 or result["status"] != "ok" or result["source_type"] != "instagram_reel":
        raise InstagramContractError("only successful Instagram Reel Contract v1 is accepted")
    shortcode = result["source_external_id"]
    canonical = f"https://www.instagram.com/reel/{shortcode}/"
    if not isinstance(shortcode, str) or not _SHORTCODE.fullmatch(shortcode) or result["source_url"] != canonical:
        raise InstagramContractError("Instagram Reel identity is invalid")
    author = result["author"]
    if author is not None:
        author = _obj(author, "author")
        _exact(author, {"external_id", "handle", "display_name"}, set(), "author")
        if any(item is not None and (not isinstance(item, str) or not item or len(item) > 512) for item in author.values()):
            raise InstagramContractError("author is invalid")
    _timestamp(result["published_at"], "published_at", nullable=True)
    text, source_hash = result["text"], result["source_hash"]
    if not isinstance(text, str) or not text or len(text) > 2_000_000 or not isinstance(source_hash, str) \
       or not _HASH.fullmatch(source_hash) or sha256(text.encode()).hexdigest() != source_hash:
        raise InstagramContractError("Instagram Reel text hash is invalid")

    raw_media = result["media"]
    if not isinstance(raw_media, list) or len(raw_media) > 21:
        raise InstagramContractError("Instagram Reel media is invalid")
    frame_ids: set[str] = set()
    media_ids: set[str] = set()
    video_count = total = 0
    for index, raw in enumerate(raw_media):
        item = _obj(raw, f"media[{index}]")
        media_id, kind = item.get("id"), item.get("kind")
        if not isinstance(media_id, str) or not _ID.fullmatch(media_id) or media_id in media_ids:
            raise InstagramContractError("Instagram Reel media identity is invalid")
        media_ids.add(media_id)
        if kind == "video":
            video_count += 1
            _exact(item, {"id", "kind", "duration_seconds"}, {"mime", "source_url", "sha256", "size_bytes"}, "video")
            duration = item["duration_seconds"]
            if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or not 0 < duration <= 21600:
                raise InstagramContractError("Instagram Reel duration is invalid")
            if item.get("mime") not in {None, "video/mp4"}:
                raise InstagramContractError("Instagram Reel video MIME is invalid")
            if item.get("sha256") is not None and (not isinstance(item["sha256"], str) or not _HASH.fullmatch(item["sha256"])):
                raise InstagramContractError("Instagram Reel video hash is invalid")
            if item.get("size_bytes") is not None and (type(item["size_bytes"]) is not int or item["size_bytes"] < 0):
                raise InstagramContractError("Instagram Reel video size is invalid")
        elif kind == "keyframe":
            _exact(item, {"id", "kind", "mime", "sha256", "size_bytes"}, {"source_url", "duration_seconds"}, "keyframe")
            size = item["size_bytes"]
            if item["mime"] not in IMAGE_MIMES or not isinstance(item["sha256"], str) or not _HASH.fullmatch(item["sha256"]) \
               or type(size) is not int or not 1 <= size <= MAX_KEYFRAME_BYTES:
                raise InstagramContractError("Instagram Reel keyframe is invalid")
            frame_ids.add(media_id)
            total += size
        else:
            raise InstagramContractError("Instagram Reel media kind is invalid")
    if video_count > 1 or len(frame_ids) > MAX_REEL_KEYFRAMES or total > MAX_TOTAL_BYTES:
        raise InstagramContractError("Instagram Reel media exceeds limits")

    segments = result["segments"]
    if not isinstance(segments, list) or not 1 <= len(segments) <= 1000:
        raise InstagramContractError("Instagram Reel segments are invalid")
    pieces: list[str] = []
    previous_end = 0
    previous_seconds = 0.0
    saw_caption = False
    seen: set[str] = set()
    for index, raw in enumerate(segments):
        segment = _obj(raw, f"segments[{index}]")
        _exact(segment, {"id", "text", "content_hash", "evidence_locator"}, set(), "segment")
        segment_id, segment_text = segment["id"], segment["text"]
        if not isinstance(segment_id, str) or not segment_id or len(segment_id) > 128 or segment_id in seen \
           or not isinstance(segment_text, str) or not segment_text or len(segment_text) > 100_000 \
           or sha256(segment_text.encode()).hexdigest() != segment["content_hash"]:
            raise InstagramContractError("Instagram Reel segment is invalid")
        seen.add(segment_id)
        locator = _obj(segment["evidence_locator"], "locator")
        separator = 0 if index == 0 else (2 if saw_caption and index == 1 else 1)
        start, end = locator.get("start_offset"), locator.get("end_offset")
        if type(start) is not int or type(end) is not int or start != previous_end + separator \
           or end != start + len(segment_text) or text[start:end] != segment_text \
           or locator.get("source_hash") != source_hash:
            raise InstagramContractError("Instagram Reel locator offsets are invalid")
        if locator.get("kind") == "instagram_caption":
            _exact(locator, {"kind", "url", "reel_id", "start_offset", "end_offset", "source_hash"}, set(), "caption locator")
            if index != 0 or locator["url"] != canonical or locator["reel_id"] != shortcode:
                raise InstagramContractError("Instagram Reel caption locator is invalid")
            saw_caption = True
        elif locator.get("kind") == "video_segment":
            _exact(locator, {"kind", "start_seconds", "end_seconds", "start_offset", "end_offset", "source_hash"}, {"frame_ids"}, "video locator")
            start_seconds, end_seconds = locator["start_seconds"], locator["end_seconds"]
            refs = locator.get("frame_ids", [])
            if any(isinstance(x, bool) or not isinstance(x, (int, float)) for x in (start_seconds, end_seconds)) \
               or start_seconds < previous_seconds or end_seconds <= start_seconds \
               or not isinstance(refs, list) or not set(refs).issubset(frame_ids):
                raise InstagramContractError("Instagram Reel video locator is invalid")
            previous_seconds = float(end_seconds)
        else:
            raise InstagramContractError("Instagram Reel locator kind is invalid")
        pieces.append(segment_text)
        previous_end = end
    rebuilt = (pieces[0] + ("\n\n" + "\n".join(pieces[1:]) if len(pieces) > 1 else "")) if saw_caption else "\n".join(pieces)
    if rebuilt != text:
        raise InstagramContractError("Instagram Reel segments do not reconstruct text")
    metadata = _obj(result["metadata"], "metadata")
    _exact(metadata, {"reel_shortcode"}, {"transcript_language"}, "metadata")
    if metadata["reel_shortcode"] != shortcode:
        raise InstagramContractError("Instagram Reel metadata is invalid")
    if metadata.get("transcript_language") is not None and (
        not isinstance(metadata["transcript_language"], str)
        or not metadata["transcript_language"]
        or len(metadata["transcript_language"]) > 64
    ):
        raise InstagramContractError("Instagram Reel transcript language is invalid")
    provenance = _obj(result["provenance"], "provenance")
    _exact(provenance, {"retrieved_at", "processor_version", "retrieval_method"}, set(), "provenance")
    processor = provenance["processor_version"]
    if not isinstance(processor, str) or not _PROCESSOR.fullmatch(processor):
        raise InstagramContractError("processor_version is invalid")
    _timestamp(provenance["retrieved_at"], "retrieved_at")
    if not isinstance(provenance["retrieval_method"], str) or not provenance["retrieval_method"]:
        raise InstagramContractError("retrieval_method is invalid")
    if not isinstance(result["warnings"], list) or not all(isinstance(item, str) and item for item in result["warnings"]):
        raise InstagramContractError("warnings are invalid")
    return result
