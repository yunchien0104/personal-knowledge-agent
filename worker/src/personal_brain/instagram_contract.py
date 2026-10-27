from __future__ import annotations

from datetime import datetime
from hashlib import sha256
import re
from typing import Any, Mapping


INSTAGRAM_POST_PROCESSOR_VERSION = "instagram-post-unified-v1"
MAX_INSTAGRAM_POST_IMAGES = 20
MAX_INSTAGRAM_IMAGE_BYTES = 10 * 1024 * 1024
MAX_INSTAGRAM_TOTAL_BYTES = 50 * 1024 * 1024
INSTAGRAM_IMAGE_MIME_TYPES = {"image/jpeg", "image/png"}

_HASH = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SHORTCODE = re.compile(r"^[A-Za-z0-9_-]{5,64}$")
_PROCESSOR_VERSION = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


class InstagramContractError(ValueError):
    """The uploaded integration artifact is unsafe or inconsistent."""


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise InstagramContractError(f"{name} must be an object")
    return value


def _exact(value: Mapping[str, Any], required: set[str], optional: set[str], name: str) -> None:
    if missing := required - set(value):
        raise InstagramContractError(f"{name} is missing {', '.join(sorted(missing))}")
    if extra := set(value) - required - optional:
        raise InstagramContractError(f"{name} has unexpected fields: {', '.join(sorted(extra))}")


def _nonempty(value: Any, name: str, *, maximum: int = 100_000) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise InstagramContractError(f"{name} is invalid")
    return value


def _hash(value: Any, name: str) -> str:
    text = _nonempty(value, name, maximum=64)
    if not _HASH.fullmatch(text):
        raise InstagramContractError(f"{name} must be lowercase SHA-256")
    return text


def _canonical_identity(source_url: Any, external_id: Any) -> tuple[str, str]:
    shortcode = _nonempty(external_id, "source_external_id", maximum=64)
    if not _SHORTCODE.fullmatch(shortcode):
        raise InstagramContractError("source_external_id is invalid")
    canonical = f"https://www.instagram.com/p/{shortcode}/"
    if source_url != canonical:
        raise InstagramContractError("source_url is not the canonical Instagram Post URL")
    return canonical, shortcode


def _author(value: Any) -> None:
    if value is None:
        return
    author = _mapping(value, "author")
    _exact(author, {"external_id", "handle", "display_name"}, set(), "author")
    for key in ("external_id", "handle", "display_name"):
        if author[key] is not None:
            _nonempty(author[key], f"author.{key}", maximum=512)


def _timestamp(value: Any) -> None:
    if value is None:
        return
    text = _nonempty(value, "published_at", maximum=64)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise InstagramContractError("published_at must be ISO-8601") from error
    if parsed.tzinfo is None:
        raise InstagramContractError("published_at must include a timezone")


def validate_instagram_post_contract(value: Any) -> dict[str, Any]:
    """Validate the merged Integration Contract at commit 15abe63.

    The Core upload bridge intentionally requires image hashes and sizes even
    though those fields are optional in the shared adapter contract. The Web
    client adds them from the user-selected files before upload.
    """

    result = dict(_mapping(value, "Instagram contract"))
    required = {
        "schema_version", "status", "source_type", "source_url",
        "source_external_id", "author", "published_at", "source_hash", "text",
        "media", "segments", "metadata", "provenance", "warnings",
    }
    _exact(result, required, set(), "Instagram contract")
    if result["schema_version"] != 1 or result["status"] != "ok":
        raise InstagramContractError("only successful Contract v1 artifacts are accepted")
    if result["source_type"] != "instagram_post":
        raise InstagramContractError("the Instagram Post bridge cannot accept another source type")
    canonical_url, shortcode = _canonical_identity(
        result["source_url"], result["source_external_id"]
    )
    _author(result["author"])
    _timestamp(result["published_at"])

    text = _nonempty(result["text"], "text", maximum=2_000_000)
    source_hash = _hash(result["source_hash"], "source_hash")
    if sha256(text.encode()).hexdigest() != source_hash:
        raise InstagramContractError("source_hash does not match text")

    raw_media = result["media"]
    if not isinstance(raw_media, list) or not 1 <= len(raw_media) <= MAX_INSTAGRAM_POST_IMAGES:
        raise InstagramContractError("Instagram Posts require 1 to 20 images")
    media: dict[str, dict[str, Any]] = {}
    total_size = 0
    for index, raw in enumerate(raw_media):
        item = dict(_mapping(raw, f"media[{index}]"))
        _exact(
            item,
            {"id", "kind", "mime", "sha256", "size_bytes"},
            {"source_url", "duration_seconds"},
            f"media[{index}]",
        )
        media_id = _nonempty(item["id"], f"media[{index}].id", maximum=128)
        if not _ID.fullmatch(media_id) or media_id in media or item["kind"] != "image":
            raise InstagramContractError("Instagram image media identity is invalid")
        if item["mime"] not in INSTAGRAM_IMAGE_MIME_TYPES:
            raise InstagramContractError("Instagram images must be JPEG or PNG")
        _hash(item["sha256"], f"media[{index}].sha256")
        size = item["size_bytes"]
        if type(size) is not int or not 1 <= size <= MAX_INSTAGRAM_IMAGE_BYTES:
            raise InstagramContractError("Instagram image size is invalid")
        if "source_url" in item and item["source_url"] is not None:
            _nonempty(item["source_url"], f"media[{index}].source_url", maximum=2048)
        if "duration_seconds" in item:
            raise InstagramContractError("Instagram Post images cannot have a duration")
        total_size += size
        media[media_id] = item
    if total_size > MAX_INSTAGRAM_TOTAL_BYTES:
        raise InstagramContractError("Instagram image upload exceeds 50 MB")

    raw_segments = result["segments"]
    if not isinstance(raw_segments, list) or not 1 <= len(raw_segments) <= 21:
        raise InstagramContractError("Instagram Post segments are invalid")
    pieces: list[str] = []
    seen_ids: set[str] = set()
    seen_images: set[str] = set()
    previous_end = 0
    previous_image_index = 0
    for ordinal, raw in enumerate(raw_segments):
        segment = dict(_mapping(raw, f"segments[{ordinal}]"))
        _exact(
            segment,
            {"id", "text", "content_hash", "evidence_locator"},
            {"artifact_refs"},
            f"segments[{ordinal}]",
        )
        segment_id = _nonempty(segment["id"], f"segments[{ordinal}].id", maximum=128)
        if segment_id in seen_ids:
            raise InstagramContractError("segment IDs must be unique")
        seen_ids.add(segment_id)
        segment_text = _nonempty(segment["text"], f"segments[{ordinal}].text")
        if sha256(segment_text.encode()).hexdigest() != _hash(
            segment["content_hash"], f"segments[{ordinal}].content_hash"
        ):
            raise InstagramContractError("segment content_hash does not match text")
        locator = dict(_mapping(segment["evidence_locator"], "evidence_locator"))
        kind = locator.get("kind")
        expected_start = 0 if ordinal == 0 else previous_end + 2
        start, end = locator.get("start_offset"), locator.get("end_offset")
        if type(start) is not int or type(end) is not int:
            raise InstagramContractError("Instagram locator offsets are invalid")
        if start != expected_start or end != start + len(segment_text):
            raise InstagramContractError("Instagram locator offsets are inconsistent")
        if text[start:end] != segment_text or locator.get("source_hash") != source_hash:
            raise InstagramContractError("Instagram locator does not select its segment text")
        if locator.get("url") != canonical_url or locator.get("post_id") != shortcode:
            raise InstagramContractError("Instagram locator identity does not match the source")

        if kind == "instagram_post_caption":
            _exact(
                locator,
                {"kind", "url", "post_id", "start_offset", "end_offset", "source_hash"},
                set(),
                "Instagram caption locator",
            )
            if ordinal != 0 or "artifact_refs" in segment:
                raise InstagramContractError("Instagram caption locator is invalid")
        elif kind == "instagram_image":
            _exact(
                locator,
                {
                    "kind", "url", "post_id", "image_index", "media_id",
                    "start_offset", "end_offset", "source_hash",
                },
                set(),
                "Instagram image locator",
            )
            image_index, media_id = locator["image_index"], locator["media_id"]
            ordered_ids = list(media)
            refs = segment.get("artifact_refs")
            if (
                type(image_index) is not int
                or not 1 <= image_index <= len(ordered_ids)
                or image_index <= previous_image_index
                or ordered_ids[image_index - 1] != media_id
                or media_id in seen_images
                or refs != [media_id]
            ):
                raise InstagramContractError("Instagram image locator is inconsistent")
            previous_image_index = image_index
            seen_images.add(media_id)
        else:
            raise InstagramContractError("Instagram Post locator kind is invalid")
        pieces.append(segment_text)
        previous_end = end
    if "\n\n".join(pieces) != text:
        raise InstagramContractError("segments do not reconstruct text")

    metadata = _mapping(result["metadata"], "metadata")
    _exact(metadata, {"post_shortcode", "image_count"}, {"ocr_languages"}, "metadata")
    if metadata["post_shortcode"] != shortcode or metadata["image_count"] != len(media):
        raise InstagramContractError("Instagram Post metadata is inconsistent")
    if "ocr_languages" in metadata and (
        not isinstance(metadata["ocr_languages"], list)
        or not all(isinstance(item, str) and item for item in metadata["ocr_languages"])
    ):
        raise InstagramContractError("metadata.ocr_languages is invalid")

    provenance = _mapping(result["provenance"], "provenance")
    _exact(
        provenance,
        {"retrieved_at", "processor_version", "retrieval_method"},
        set(),
        "provenance",
    )
    processor_version = _nonempty(
        provenance["processor_version"], "provenance.processor_version", maximum=64
    )
    if not _PROCESSOR_VERSION.fullmatch(processor_version):
        raise InstagramContractError("provenance.processor_version is invalid")
    _nonempty(provenance["retrieval_method"], "provenance.retrieval_method", maximum=128)
    _timestamp(provenance["retrieved_at"])
    if not isinstance(result["warnings"], list) or not all(
        isinstance(item, str) and item for item in result["warnings"]
    ):
        raise InstagramContractError("warnings must be strings")
    return result
