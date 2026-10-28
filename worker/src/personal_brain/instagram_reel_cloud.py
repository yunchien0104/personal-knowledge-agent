from __future__ import annotations

from base64 import b64encode
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import subprocess
from tempfile import TemporaryDirectory
from typing import Any, Callable, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from personal_brain.instagram_contract import InstagramContractError
from personal_brain.supabase_gateway import GatewayError


MAX_VIDEO_BYTES = 150 * 1024 * 1024
MAX_DURATION_SECONDS = 15 * 60
MAX_KEYFRAMES = 8
PROCESSOR_VERSION = "instagram-reel-cloud-v2"


class _RejectRedirects(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> None:
        return None


@dataclass(frozen=True, slots=True)
class AcquiredReel:
    video_url: str
    caption: str | None
    author: Mapping[str, str | None] | None
    published_at: str | None


@dataclass(frozen=True, slots=True)
class TimedText:
    text: str
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True, slots=True)
class Keyframe:
    id: str
    timestamp_seconds: float
    content: bytes
    observation: str


class ReelAcquisitionProvider:
    """Strict boundary for a licensed cloud provider that resolves public Reels."""

    def __init__(self, endpoint: str, api_key: str, allowed_media_hosts: set[str], *, timeout_seconds: int = 45) -> None:
        parsed = urlparse(endpoint)
        if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("REEL_ACQUISITION_API_URL must be a trusted HTTPS URL")
        if not api_key:
            raise ValueError("REEL_ACQUISITION_API_KEY is required")
        if not allowed_media_hosts:
            raise ValueError("REEL_MEDIA_ALLOWED_HOSTS is required")
        self.endpoint = endpoint
        self.api_key = api_key
        self.allowed_media_hosts = {host.lower() for host in allowed_media_hosts}
        self.timeout_seconds = timeout_seconds
        self._opener = build_opener(_RejectRedirects())

    @classmethod
    def from_environment(cls) -> ReelAcquisitionProvider:
        hosts = {item.strip() for item in os.environ.get("REEL_MEDIA_ALLOWED_HOSTS", "").split(",") if item.strip()}
        return cls(
            os.environ.get("REEL_ACQUISITION_API_URL", ""),
            os.environ.get("REEL_ACQUISITION_API_KEY", ""),
            hosts,
        )

    def fetch(self, canonical_url: str) -> AcquiredReel:
        request = Request(
            self.endpoint,
            data=json.dumps({"url": canonical_url}, separators=(",", ":")).encode(),
            headers={"authorization": f"Bearer {self.api_key}", "content-type": "application/json"},
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                payload = json.loads(response.read())
        except HTTPError as error:
            raise GatewayError("reel_provider_http_error", f"Reel provider failed with status {error.code}", retryable=error.code == 429 or error.code >= 500) from error
        except (TimeoutError, URLError, json.JSONDecodeError) as error:
            raise GatewayError("reel_provider_unavailable", "Reel provider is temporarily unavailable", retryable=True) from error
        if not isinstance(payload, Mapping):
            raise GatewayError("invalid_reel_provider_response", "Reel provider returned invalid data", retryable=False)
        video_url = payload.get("video_url")
        parsed_video = urlparse(video_url if isinstance(video_url, str) else "")
        if parsed_video.scheme != "https" or parsed_video.hostname is None or parsed_video.hostname.lower() not in self.allowed_media_hosts:
            raise GatewayError("untrusted_reel_media_url", "Reel provider returned an untrusted media URL", retryable=False)
        caption = payload.get("caption")
        if caption is not None and (not isinstance(caption, str) or len(caption) > 200_000):
            raise GatewayError("invalid_reel_provider_response", "Reel provider caption is invalid", retryable=False)
        author = payload.get("author")
        if author is not None and not isinstance(author, Mapping):
            raise GatewayError("invalid_reel_provider_response", "Reel provider author is invalid", retryable=False)
        return AcquiredReel(video_url, caption.strip() if isinstance(caption, str) and caption.strip() else None, author, payload.get("published_at") if isinstance(payload.get("published_at"), str) else None)

    def download_video(self, url: str, target: Path) -> None:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname is None or parsed.hostname.lower() not in self.allowed_media_hosts:
            raise GatewayError("untrusted_reel_media_url", "Reel media URL is not trusted", retryable=False)
        request = Request(url, headers={"accept": "video/mp4,video/webm"}, method="GET")
        try:
            with self._opener.open(request, timeout=90) as response, target.open("wb") as output:
                content_type = response.headers.get_content_type()
                if content_type not in {"video/mp4", "video/webm", "application/octet-stream"}:
                    raise GatewayError("invalid_reel_media", "Reel provider returned a non-video response", retryable=False)
                total = 0
                while chunk := response.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_VIDEO_BYTES:
                        raise GatewayError("reel_media_too_large", "Reel video exceeds the processing limit", retryable=False)
                    output.write(chunk)
        except HTTPError as error:
            raise GatewayError("reel_media_http_error", f"Reel media download failed with status {error.code}", retryable=error.code == 429 or error.code >= 500) from error
        except (TimeoutError, URLError) as error:
            raise GatewayError("reel_media_unavailable", "Reel media is temporarily unavailable", retryable=True) from error


class ReelMediaProcessor:
    def __init__(self, executable: str | None = None) -> None:
        if executable:
            self.executable = executable
        else:
            try:
                from imageio_ffmpeg import get_ffmpeg_exe
                self.executable = get_ffmpeg_exe()
            except (ImportError, RuntimeError) as error:
                raise GatewayError("ffmpeg_unavailable", "FFmpeg runtime is not installed", retryable=False) from error

    def _run(self, arguments: list[str], *, timeout: int = 180, check: bool = True) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run([self.executable, *arguments], check=check, capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError as error:
            raise GatewayError("ffmpeg_unavailable", "FFmpeg is not installed in the Worker image", retryable=False) from error
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as error:
            raise GatewayError("reel_media_processing_failed", "Reel media processing failed", retryable=True) from error

    def duration(self, video: Path) -> float:
        result = self._run(["-hide_banner", "-i", str(video)], check=False)
        matched = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", result.stderr)
        try:
            duration = int(matched.group(1)) * 3600 + int(matched.group(2)) * 60 + float(matched.group(3)) if matched else 0.0
        except (ValueError, IndexError) as error:
            raise GatewayError("invalid_reel_media", "Reel duration is invalid", retryable=False) from error
        if not 0 < duration <= MAX_DURATION_SECONDS:
            raise GatewayError("reel_duration_unsupported", "Reel duration exceeds the processing limit", retryable=False)
        return duration

    def audio_chunks(self, video: Path, directory: Path) -> list[tuple[Path, float, float]]:
        pattern = directory / "audio-%03d.mp3"
        result = self._run(["-hide_banner", "-loglevel", "error", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-b:a", "48k", "-f", "segment", "-segment_time", "30", "-reset_timestamps", "1", str(pattern)], check=False)
        if result.returncode != 0:
            if "does not contain any stream" in result.stderr or "matches no streams" in result.stderr:
                return []
            raise GatewayError("reel_media_processing_failed", "Reel audio extraction failed", retryable=True)
        files = sorted(directory.glob("audio-*.mp3"))
        return [(path, index * 30.0, (index + 1) * 30.0) for index, path in enumerate(files)]

    def keyframes(self, video: Path, directory: Path, duration: float) -> list[tuple[str, float, bytes]]:
        count = min(MAX_KEYFRAMES, max(1, int(duration // 30) + 1))
        interval = max(duration / count, 1.0)
        pattern = directory / "frame-%03d.jpg"
        self._run(["-hide_banner", "-loglevel", "error", "-i", str(video), "-vf", f"fps=1/{interval},scale='min(960,iw)':-2", "-frames:v", str(count), "-q:v", "3", str(pattern)])
        return [(f"frame-{index:03d}", min((index - 1) * interval, duration), path.read_bytes()) for index, path in enumerate(sorted(directory.glob("frame-*.jpg")), start=1)]


class OpenAIReelUnderstanding:
    def __init__(self, api_key: str, *, transcription_model: str = "gpt-transcribe", vision_model: str = "gpt-4.1-mini", timeout_seconds: int = 90) -> None:
        if not api_key:
            raise ValueError("OPENAI_API_KEY is required for cloud Reel processing")
        self.api_key = api_key
        self.transcription_model = transcription_model
        self.vision_model = vision_model
        self.timeout_seconds = timeout_seconds
        self._opener = build_opener(_RejectRedirects())

    @classmethod
    def from_environment(cls) -> OpenAIReelUnderstanding:
        return cls(
            os.environ.get("OPENAI_API_KEY", ""),
            transcription_model=os.environ.get("REEL_TRANSCRIPTION_MODEL", "gpt-transcribe"),
            vision_model=os.environ.get("REEL_VISION_MODEL", "gpt-4.1-mini"),
        )

    def _json_request(self, path: str, payload: Mapping[str, Any]) -> Mapping[str, Any]:
        request = Request(f"https://api.openai.com/v1/{path}", data=json.dumps(payload).encode(), headers={"authorization": f"Bearer {self.api_key}", "content-type": "application/json"}, method="POST")
        try:
            with self._opener.open(request, timeout=self.timeout_seconds) as response:
                value = json.loads(response.read())
        except HTTPError as error:
            raise GatewayError("openai_http_error", f"OpenAI request failed with status {error.code}", retryable=error.code == 429 or error.code >= 500) from error
        except (TimeoutError, URLError, json.JSONDecodeError) as error:
            raise GatewayError("openai_unavailable", "OpenAI is temporarily unavailable", retryable=True) from error
        if not isinstance(value, Mapping):
            raise GatewayError("invalid_openai_response", "OpenAI returned invalid data", retryable=True)
        return value

    def transcribe(self, chunks: list[tuple[Path, float, float]]) -> list[TimedText]:
        result: list[TimedText] = []
        for path, start, end in chunks:
            boundary = f"----personal-brain-{uuid4().hex}"
            content = path.read_bytes()
            parts = [
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"model\"\r\n\r\n{self.transcription_model}\r\n".encode(),
                f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"audio.mp3\"\r\nContent-Type: audio/mpeg\r\n\r\n".encode() + content + b"\r\n",
                f"--{boundary}--\r\n".encode(),
            ]
            request = Request("https://api.openai.com/v1/audio/transcriptions", data=b"".join(parts), headers={"authorization": f"Bearer {self.api_key}", "content-type": f"multipart/form-data; boundary={boundary}"}, method="POST")
            try:
                with self._opener.open(request, timeout=self.timeout_seconds) as response:
                    payload = json.loads(response.read())
            except HTTPError as error:
                raise GatewayError("openai_http_error", f"OpenAI transcription failed with status {error.code}", retryable=error.code == 429 or error.code >= 500) from error
            except (TimeoutError, URLError, json.JSONDecodeError) as error:
                raise GatewayError("openai_unavailable", "OpenAI transcription is temporarily unavailable", retryable=True) from error
            text = payload.get("text") if isinstance(payload, Mapping) else None
            if isinstance(text, str) and text.strip():
                result.append(TimedText(text.strip(), start, end))
        return result

    def describe_frame(self, content: bytes, timestamp: float) -> str:
        payload = self._json_request("responses", {
            "model": self.vision_model,
            "input": [{"role": "user", "content": [
                {"type": "input_text", "text": f"Describe only visible, knowledge-relevant facts in this Reel frame at {timestamp:.1f}s. Be concise. Do not infer hidden facts."},
                {"type": "input_image", "image_url": "data:image/jpeg;base64," + b64encode(content).decode(), "detail": "low"},
            ]}],
            "max_output_tokens": 160,
        })
        for item in payload.get("output", []):
            if isinstance(item, Mapping):
                for part in item.get("content", []):
                    if isinstance(part, Mapping) and part.get("type") == "output_text" and isinstance(part.get("text"), str):
                        return part["text"].strip()
        raise GatewayError("invalid_openai_response", "OpenAI vision response contained no text", retryable=True)


class InstagramReelCloudPipeline:
    def __init__(self, provider: ReelAcquisitionProvider, understanding: OpenAIReelUnderstanding, media: ReelMediaProcessor | None = None) -> None:
        self.provider = provider
        self.understanding = understanding
        self.media = media or ReelMediaProcessor()

    @classmethod
    def from_environment(cls) -> InstagramReelCloudPipeline:
        return cls(ReelAcquisitionProvider.from_environment(), OpenAIReelUnderstanding.from_environment())

    def process(self, source_url: str, external_id: str, pulse: Callable[[], None]) -> tuple[dict[str, Any], dict[str, bytes]]:
        acquired = self.provider.fetch(source_url)
        with TemporaryDirectory(prefix="personal-brain-reel-") as temporary:
            directory = Path(temporary)
            video = directory / "source-video"
            self.provider.download_video(acquired.video_url, video)
            pulse()
            duration = self.media.duration(video)
            transcript = self.understanding.transcribe(self.media.audio_chunks(video, directory))
            pulse()
            raw_frames = self.media.keyframes(video, directory, duration)
            frames = [Keyframe(frame_id, timestamp, content, self.understanding.describe_frame(content, timestamp)) for frame_id, timestamp, content in raw_frames]
            pulse()
        if not acquired.caption and not transcript:
            raise InstagramContractError("Reel has neither a caption nor a usable transcript")
        return build_cloud_contract(source_url, external_id, acquired, duration, transcript, frames, self.understanding.vision_model), {frame.id: frame.content for frame in frames}


def build_cloud_contract(source_url: str, external_id: str, acquired: AcquiredReel, duration: float, transcript: list[TimedText], frames: list[Keyframe], vision_model: str) -> dict[str, Any]:
    entries: list[dict[str, Any]] = []
    if acquired.caption:
        entries.append({"id": "caption", "text": acquired.caption, "kind": "instagram_caption"})
    timed = [{"id": f"transcript-{index:03d}", "text": item.text, "kind": "video_segment", "start": item.start_seconds, "end": item.end_seconds, "origin": "source_transcript", "frames": []} for index, item in enumerate(transcript, 1)]
    timed += [{"id": f"visual-{index:03d}", "text": f"AI 畫面觀察：{frame.observation}", "kind": "video_segment", "start": frame.timestamp_seconds, "end": min(frame.timestamp_seconds + 1.0, duration), "origin": "ai_visual_observation", "frames": [frame.id]} for index, frame in enumerate(frames, 1) if frame.observation]
    entries += sorted(timed, key=lambda item: (item["start"], 0 if item["origin"] == "source_transcript" else 1))
    text = ""
    for index, entry in enumerate(entries):
        separator = "" if index == 0 else ("\n\n" if index == 1 and entries[0]["kind"] == "instagram_caption" else "\n")
        entry["start_offset"] = len(text) + len(separator)
        text += separator + entry["text"]
        entry["end_offset"] = len(text)
    source_hash = sha256(text.encode()).hexdigest()
    segments: list[dict[str, Any]] = []
    for entry in entries:
        if entry["kind"] == "instagram_caption":
            locator = {"kind": "instagram_caption", "url": source_url, "reel_id": external_id, "start_offset": entry["start_offset"], "end_offset": entry["end_offset"], "source_hash": source_hash}
        else:
            locator = {"kind": "video_segment", "start_seconds": entry["start"], "end_seconds": max(entry["end"], entry["start"] + 0.001), "start_offset": entry["start_offset"], "end_offset": entry["end_offset"], "source_hash": source_hash, "frame_ids": entry["frames"], "content_origin": entry["origin"]}
            if entry["origin"] == "ai_visual_observation":
                locator["model_name"] = vision_model
        segments.append({"id": entry["id"], "text": entry["text"], "content_hash": sha256(entry["text"].encode()).hexdigest(), "evidence_locator": locator})
    media = [{"id": frame.id, "kind": "keyframe", "mime": "image/jpeg", "sha256": sha256(frame.content).hexdigest(), "size_bytes": len(frame.content), "duration_seconds": frame.timestamp_seconds} for frame in frames]
    author = None
    if acquired.author is not None:
        author = {name: acquired.author.get(name) if isinstance(acquired.author.get(name), str) else None for name in ("external_id", "handle", "display_name")}
    return {
        "schema_version": 2, "status": "ok", "source_type": "instagram_reel",
        "source_url": source_url, "source_external_id": external_id,
        "author": author, "published_at": acquired.published_at,
        "source_hash": source_hash, "text": text, "media": media, "segments": segments,
        "metadata": {"reel_shortcode": external_id, "duration_seconds": duration},
        "provenance": {"retrieved_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"), "processor_version": PROCESSOR_VERSION, "retrieval_method": "licensed_cloud_provider"},
        "warnings": ["AI visual observations are labeled in locator.content_origin and are not verbatim source text."],
    }
