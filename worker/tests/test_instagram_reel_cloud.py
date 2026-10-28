from __future__ import annotations

from hashlib import sha256
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest

from imageio_ffmpeg import get_ffmpeg_exe

from personal_brain.instagram_reel_cloud import (
    AcquiredReel,
    Keyframe,
    ReelAcquisitionProvider,
    ReelMediaProcessor,
    TimedText,
    build_cloud_contract,
)
from personal_brain.instagram_reel_contract import validate_instagram_reel_contract
from personal_brain.instagram_reel_ingestion import InstagramReelIngestionWorker
from personal_brain.models import JobPayloadV1
from personal_brain.supabase_gateway import ClaimedJob


USER_ID = "84000000-0000-4000-8000-000000000001"
ASSET_ID = "84000000-0000-4000-8000-000000000002"
JOB_ID = "84000000-0000-4000-8000-000000000003"
REVISION_ID = "84000000-0000-4000-8000-000000000004"
SOURCE_URL = "https://www.instagram.com/reel/C10udReel/"
JPEG = b"\xff\xd8\xff\xe0cloud-frame\xff\xd9"


def cloud_contract():
    return build_cloud_contract(
        SOURCE_URL,
        "C10udReel",
        AcquiredReel(
            "https://media.example.test/reel.mp4",
            "公開說明文字",
            {"external_id": "author-1", "handle": "author", "display_name": "Author"},
            "2026-10-28T10:00:00Z",
        ),
        12.0,
        [TimedText("影片人聲逐字稿", 0.0, 12.0)],
        [Keyframe("frame-001", 0.0, JPEG, "白板上顯示三個步驟")],
        "gpt-4.1-mini",
    )


def cloud_job():
    payload = JobPayloadV1.from_dict({
        "schemaVersion": 1,
        "jobId": JOB_ID,
        "userId": USER_ID,
        "jobType": "ingest_instagram_reel",
        "idempotencyKey": f"ingest_instagram_reel:{ASSET_ID}:cloud-v2",
        "input": {
            "assetId": ASSET_ID,
            "sourceUrl": SOURCE_URL,
            "sourceExternalId": "C10udReel",
            "acquisitionMode": "cloud",
        },
    })
    return ClaimedJob(JOB_ID, USER_ID, ASSET_ID, 1, payload)


class FakePipeline:
    def process(self, source_url, external_id, pulse):
        assert source_url == SOURCE_URL
        assert external_id == "C10udReel"
        pulse()
        return cloud_contract(), {"frame-001": JPEG}


class Gateway:
    def __init__(self):
        self.job = cloud_job()
        self.claimed = False
        self.uploads = []
        self.publications = []
        self.failures = []

    def claim(self, *_args):
        if self.claimed:
            return None
        self.claimed = True
        return self.job

    def heartbeat(self, *_args): return True

    def upload_immutable(self, path, content, content_hash, content_type="application/json"):
        assert sha256(content).hexdigest() == content_hash
        self.uploads.append((path, content_type))

    def publish_instagram_reel(self, _job, _worker, **result):
        self.publications.append(result)
        return {"revisionId": REVISION_ID}

    def report_failure(self, _job, _worker, **failure):
        self.failures.append(failure)
        return "failed"


class InstagramReelCloudTests(unittest.TestCase):
    def test_contract_v2_combines_caption_transcript_and_labeled_visual_observation(self):
        contract = validate_instagram_reel_contract(cloud_contract())
        self.assertEqual(contract["schema_version"], 2)
        self.assertIn("影片人聲逐字稿", contract["text"])
        self.assertIn("AI 畫面觀察：白板上顯示三個步驟", contract["text"])
        visual = next(item for item in contract["segments"] if item["id"].startswith("visual-"))
        self.assertEqual(visual["evidence_locator"]["content_origin"], "ai_visual_observation")
        self.assertEqual(visual["evidence_locator"]["frame_ids"], ["frame-001"])

    def test_provider_requires_https_and_explicit_media_allowlist(self):
        with self.assertRaises(ValueError):
            ReelAcquisitionProvider("http://provider.test/reel", "secret", {"media.test"})
        with self.assertRaises(ValueError):
            ReelAcquisitionProvider("https://provider.test/reel", "secret", set())

    def test_bundled_ffmpeg_extracts_audio_and_keyframe(self):
        with TemporaryDirectory() as raw:
            directory = Path(raw)
            video = directory / "sample.mp4"
            subprocess.run([
                get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=blue:s=320x180:d=1",
                "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                "-shortest", "-c:v", "libx264", "-c:a", "aac", str(video),
            ], check=True)
            processor = ReelMediaProcessor()
            duration = processor.duration(video)
            audio = processor.audio_chunks(video, directory)
            frames = processor.keyframes(video, directory, duration)
            self.assertGreater(duration, 0)
            self.assertEqual(len(audio), 1)
            self.assertEqual(len(frames), 1)
            self.assertTrue(frames[0][2].startswith(b"\xff\xd8\xff"))

    def test_caption_only_video_without_audio_is_supported(self):
        with TemporaryDirectory() as raw:
            directory = Path(raw)
            video = directory / "silent.mp4"
            subprocess.run([
                get_ffmpeg_exe(), "-hide_banner", "-loglevel", "error",
                "-f", "lavfi", "-i", "color=c=black:s=320x180:d=1",
                "-c:v", "libx264", str(video),
            ], check=True)
            self.assertEqual(ReelMediaProcessor().audio_chunks(video, directory), [])

    def test_cloud_job_generates_and_publishes_without_uploaded_contract(self):
        gateway = Gateway()
        result = InstagramReelIngestionWorker(
            gateway, "reel-worker", cloud_pipeline=FakePipeline()
        ).process_one()
        self.assertEqual(result.status, "completed")
        self.assertEqual([content_type for _, content_type in gateway.uploads], ["application/json", "image/jpeg"])
        self.assertEqual(len(gateway.publications), 1)
        self.assertEqual(gateway.failures, [])

    def test_cloud_job_fails_closed_when_provider_is_not_configured(self):
        gateway = Gateway()
        result = InstagramReelIngestionWorker(gateway, "reel-worker").process_one()
        self.assertEqual(result.status, "retry_wait")
        self.assertEqual(gateway.uploads, [])
        self.assertEqual(gateway.publications, [])
        self.assertEqual(gateway.failures[0]["error_code"], "reel_cloud_not_configured")


if __name__ == "__main__":
    unittest.main()
