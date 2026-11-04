from __future__ import annotations

from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from imageio_ffmpeg import get_ffmpeg_exe

from personal_brain.instagram_reel_cloud import (
    AcquiredReel,
    ApifyReelAcquisitionProvider,
    InstagramReelCloudPipeline,
    Keyframe,
    ReelAcquisitionProvider,
    ReelMediaProcessor,
    TimedText,
    build_cloud_contract,
)
from personal_brain.instagram_reel_contract import validate_instagram_reel_contract
from personal_brain.instagram_reel_ingestion import InstagramReelIngestionWorker
from personal_brain.models import JobPayloadV1
from personal_brain.supabase_gateway import ClaimedJob, GatewayError


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
    def process(self, source_url, external_id, pulse, progress):
        assert source_url == SOURCE_URL
        assert external_id == "C10udReel"
        progress(40, "transcribing_audio")
        progress(92, "assembling_contract")
        pulse()
        return cloud_contract(), {"frame-001": JPEG}


class Gateway:
    def __init__(self):
        self.job = cloud_job()
        self.claimed = False
        self.uploads = []
        self.publications = []
        self.failures = []
        self.progress_events = []

    def claim(self, *_args):
        if self.claimed:
            return None
        self.claimed = True
        return self.job

    def heartbeat(self, *_args): return True

    def progress(self, _job, _worker, percent, stage):
        self.progress_events.append((percent, stage))
        return True

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

    def test_apify_all_fields_output_maps_to_contract_boundary(self):
        acquired = ApifyReelAcquisitionProvider._map_output(SOURCE_URL, [{
            "inputUrl": SOURCE_URL,
            "id": "4000000000000000000",
            "type": "Video",
            "shortCode": "C10udReel",
            "caption": "公開說明文字",
            "videoUrl": "https://instagram.fmad26-1.fna.fbcdn.net/short-lived.mp4",
            "audioUrl": "https://instagram.cdn.example/short-lived-audio.mp4",
            "downloadedVideo": "https://api.apify.com/v2/key-value-stores/store/records/reel.mp4",
            "timestamp": "2026-10-11T13:53:50.000Z",
            "ownerId": "123456789",
            "ownerUsername": "example_author",
            "ownerFullName": "Example Author",
            "productType": "clips",
            "videoDuration": 106.166664,
        }])
        self.assertEqual(
            acquired.video_url,
            "https://instagram.fmad26-1.fna.fbcdn.net/short-lived.mp4",
        )
        self.assertEqual(acquired.caption, "公開說明文字")
        self.assertEqual(acquired.author, {
            "external_id": "123456789",
            "handle": "example_author",
            "display_name": "Example Author",
        })
        self.assertEqual(acquired.published_at, "2026-10-11T13:53:50.000Z")

    def test_apify_fetch_uses_direct_video_and_keeps_stored_fallback(self):
        class Response:
            def __enter__(self): return self
            def __exit__(self, *_args): return None
            def read(self):
                return json.dumps([{
                    "type": "Video",
                    "shortCode": "C10udReel",
                    "productType": "clips",
                    "videoUrl": "https://scontent.cdninstagram.com/reel.mp4",
                    "downloadedVideo": "https://api.apify.com/v2/key-value-stores/store/records/reel.mp4",
                }]).encode()

        class Opener:
            request = None
            timeout = None

            def open(self, request, timeout):
                self.request = request
                self.timeout = timeout
                return Response()

        provider = ApifyReelAcquisitionProvider("server-secret")
        opener = Opener()
        provider._opener = opener
        acquired = provider.fetch(SOURCE_URL)
        request_payload = json.loads(opener.request.data)
        self.assertEqual(acquired.video_url, "https://scontent.cdninstagram.com/reel.mp4")
        self.assertEqual(request_payload, {
            "username": [SOURCE_URL],
            "resultsLimit": 1,
            "includeDownloadedVideo": False,
            "includeTranscript": False,
            "includeSharesCount": False,
        })
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer server-secret")
        self.assertEqual(
            opener.request.full_url,
            "https://api.apify.com/v2/acts/apify~instagram-reel-scraper/run-sync-get-dataset-items",
        )
        self.assertEqual(opener.timeout, 300)

        fallback = provider.fetch_fallback(SOURCE_URL)
        self.assertEqual(
            fallback.video_url,
            "https://api.apify.com/v2/key-value-stores/store/records/reel.mp4",
        )
        self.assertTrue(json.loads(opener.request.data)["includeDownloadedVideo"])

    def test_apify_output_must_match_shortcode_and_include_trusted_direct_video(self):
        item = {
            "type": "Video",
            "shortCode": "DifferentReel",
            "productType": "clips",
            "videoUrl": "https://instagram.fmad26-1.fna.fbcdn.net/reel.mp4",
        }
        with self.assertRaisesRegex(Exception, "uniquely match"):
            ApifyReelAcquisitionProvider._map_output(SOURCE_URL, [item])
        item["shortCode"] = "C10udReel"
        item.pop("videoUrl")
        with self.assertRaisesRegex(Exception, "direct Reel video"):
            ApifyReelAcquisitionProvider._map_output(SOURCE_URL, [item])

    def test_apify_direct_and_stored_video_urls_fail_closed(self):
        item = {
            "type": "Video",
            "shortCode": "C10udReel",
            "productType": "clips",
            "videoUrl": "https://instagram.fmad26-1.fna.fbcdn.net.attacker.test/reel.mp4",
            "downloadedVideo": "https://attacker@api.apify.com/v2/key-value-stores/store/records/reel.mp4",
        }
        with self.assertRaisesRegex(Exception, "untrusted direct Reel video"):
            ApifyReelAcquisitionProvider._map_output(SOURCE_URL, [item])
        with self.assertRaisesRegex(Exception, "stored Reel video"):
            ApifyReelAcquisitionProvider._map_output(
                SOURCE_URL, [item], use_stored_video=True
            )
        item["videoUrl"] = "https://instagram.fmad26-1.fna.fbcdn.net/reel.mp4"
        item["downloadedVideo"] = "https://api.apify.com/v2/key-value-stores/store/records/reel.mp4"
        item["timestamp"] = "2026-10-11"
        with self.assertRaisesRegex(Exception, "timestamp is invalid"):
            ApifyReelAcquisitionProvider._map_output(SOURCE_URL, [item])

    def test_pipeline_falls_back_only_after_direct_media_download_failure(self):
        direct = AcquiredReel(
            "https://instagram.fmad26-1.fna.fbcdn.net/reel.mp4",
            "caption",
            None,
            None,
        )
        stored = AcquiredReel(
            "https://api.apify.com/v2/key-value-stores/store/records/reel.mp4",
            "caption",
            None,
            None,
        )

        class Provider:
            downloads = []
            fallback_calls = 0
            def fetch(self, _url): return direct
            def fetch_fallback(self, _url):
                self.fallback_calls += 1
                return stored
            def download_video(self, url, target):
                self.downloads.append(url)
                if url == direct.video_url:
                    raise GatewayError("reel_media_http_error", "expired", retryable=False)
                target.write_bytes(b"video")

        class Media:
            def duration(self, _video): return 1.0
            def audio_chunks(self, _video, _directory): return []
            def keyframes(self, _video, _directory, _duration): return []

        class Understanding:
            vision_model = "test-vision"
            def transcribe(self, _chunks): return []

        provider = Provider()
        progress = []
        pipeline = InstagramReelCloudPipeline(provider, Understanding(), Media())
        contract, frames = pipeline.process(
            SOURCE_URL,
            "C10udReel",
            lambda: None,
            lambda percent, stage: progress.append((percent, stage)),
        )
        self.assertEqual(provider.downloads, [direct.video_url, stored.video_url])
        self.assertEqual(provider.fallback_calls, 1)
        self.assertIn((16, "acquiring_fallback"), progress)
        self.assertEqual(contract["text"], "caption")
        self.assertEqual(frames, {})

    def test_apify_provider_rejects_actor_id_injection(self):
        with self.assertRaises(ValueError):
            ApifyReelAcquisitionProvider("secret", actor_id="actor?token=leak")

    def test_direct_cdn_download_never_receives_apify_credentials(self):
        class Headers:
            def get_content_type(self): return "video/mp4"

        class Response:
            headers = Headers()
            def __enter__(self): return self
            def __exit__(self, *_args): return None
            def read(self, _size): return b""

        class Opener:
            request = None
            def open(self, request, timeout):
                self.request = request
                return Response()

        provider = ApifyReelAcquisitionProvider("server-secret")
        opener = Opener()
        provider._opener = opener
        with TemporaryDirectory() as raw:
            provider.download_video(
                "https://instagram.fper9-1.fna.fbcdn.net/reel.mp4",
                Path(raw) / "reel.mp4",
            )
        self.assertIsNone(opener.request.get_header("Authorization"))

    def test_cloud_pipeline_selects_apify_from_server_environment(self):
        with patch.dict(os.environ, {
            "REEL_ACQUISITION_PROVIDER": "apify",
            "APIFY_API_TOKEN": "server-secret",
            "OPENAI_API_KEY": "openai-secret",
        }, clear=True):
            pipeline = InstagramReelCloudPipeline.from_environment()
        self.assertIsInstance(pipeline.provider, ApifyReelAcquisitionProvider)

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
        self.assertEqual(
            gateway.progress_events,
            [
                (2, "starting"),
                (40, "transcribing_audio"),
                (92, "assembling_contract"),
                (94, "validating_contract"),
                (96, "uploading_artifacts"),
                (99, "publishing_source"),
            ],
        )

    def test_cloud_job_fails_closed_when_provider_is_not_configured(self):
        gateway = Gateway()
        result = InstagramReelIngestionWorker(gateway, "reel-worker").process_one()
        self.assertEqual(result.status, "retry_wait")
        self.assertEqual(gateway.uploads, [])
        self.assertEqual(gateway.publications, [])
        self.assertEqual(gateway.failures[0]["error_code"], "reel_cloud_not_configured")


if __name__ == "__main__":
    unittest.main()
