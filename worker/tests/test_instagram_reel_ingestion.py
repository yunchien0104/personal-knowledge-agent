from __future__ import annotations

from hashlib import sha256
import json
import unittest

from personal_brain.instagram_contract import InstagramContractError
from personal_brain.instagram_reel_contract import validate_instagram_reel_contract
from personal_brain.instagram_reel_ingestion import InstagramReelIngestionWorker
from personal_brain.models import JobPayloadV1
from personal_brain.supabase_gateway import ClaimedJob

USER_ID = "83000000-0000-4000-8000-000000000001"
ASSET_ID = "83000000-0000-4000-8000-000000000002"
JOB_ID = "83000000-0000-4000-8000-000000000003"
REVISION_ID = "83000000-0000-4000-8000-000000000004"
JPEG = b"\xff\xd8\xff\xe0synthetic-frame\xff\xd9"


def digest(value: str) -> str:
    return sha256(value.encode()).hexdigest()


def reel_contract() -> dict:
    caption, transcript = "Reel caption", "逐字稿"
    text = f"{caption}\n\n{transcript}"
    source_hash = digest(text)
    return {
        "schema_version": 1, "status": "ok", "source_type": "instagram_reel",
        "source_url": "https://www.instagram.com/reel/C0dExReel1/",
        "source_external_id": "C0dExReel1", "author": None, "published_at": None,
        "source_hash": source_hash, "text": text,
        "media": [{"id": "frame-001", "kind": "keyframe", "mime": "image/jpeg",
                   "sha256": sha256(JPEG).hexdigest(), "size_bytes": len(JPEG)}],
        "segments": [
            {"id": "caption", "text": caption, "content_hash": digest(caption),
             "evidence_locator": {"kind": "instagram_caption",
                "url": "https://www.instagram.com/reel/C0dExReel1/", "reel_id": "C0dExReel1",
                "start_offset": 0, "end_offset": len(caption), "source_hash": source_hash}},
            {"id": "transcript-1", "text": transcript, "content_hash": digest(transcript),
             "evidence_locator": {"kind": "video_segment", "start_seconds": 0,
                "end_seconds": 4.2, "start_offset": len(caption) + 2,
                "end_offset": len(text), "source_hash": source_hash, "frame_ids": ["frame-001"]}},
        ],
        "metadata": {"reel_shortcode": "C0dExReel1", "transcript_language": "zh"},
        "provenance": {"retrieved_at": "2026-10-27T00:00:00Z",
                       "processor_version": "instagram-reel-unified-v1",
                       "retrieval_method": "ig_transcript_mac_app"},
        "warnings": [],
    }


def claimed_job() -> ClaimedJob:
    payload = JobPayloadV1.from_dict({
        "schemaVersion": 1, "jobId": JOB_ID, "userId": USER_ID,
        "jobType": "ingest_instagram_reel",
        "idempotencyKey": f"ingest_instagram_reel:{ASSET_ID}:contract-v1",
        "input": {"assetId": ASSET_ID, "sourceUrl": "https://www.instagram.com/reel/C0dExReel1/",
                  "sourceExternalId": "C0dExReel1", "storageBucket": "asset-private",
                  "contractObjectPath": f"{USER_ID}/{ASSET_ID}/original/instagram-reel-contract.json",
                  "mediaPrefix": f"{USER_ID}/{ASSET_ID}/original/keyframes"},
    })
    return ClaimedJob(JOB_ID, USER_ID, ASSET_ID, 1, payload)


class Gateway:
    def __init__(self, frame: bytes = JPEG) -> None:
        self.job, self.frame, self.claimed = claimed_job(), frame, False
        self.uploads, self.publications, self.failures, self.progress_events = [], [], [], []
    def claim(self, *_args):
        if self.claimed: return None
        self.claimed = True
        return self.job
    def heartbeat(self, *_args): return True
    def progress(self, _job, _worker, percent, stage):
        self.progress_events.append((percent, stage))
        return True
    def download_instagram_reel_contract(self, _job): return json.dumps(reel_contract(), ensure_ascii=False).encode()
    def download_instagram_reel_keyframe(self, _job, _media_id): return self.frame
    def upload_immutable(self, path, content, content_hash, content_type="application/json"):
        self.uploads.append((path, content, content_hash, content_type))
    def publish_instagram_reel(self, _job, _worker, **result):
        self.publications.append(result)
        return {"revisionId": REVISION_ID}
    def report_failure(self, _job, _worker, **failure):
        self.failures.append(failure)
        return "failed"


class ReelContractTests(unittest.TestCase):
    def test_caption_and_transcript_contract(self) -> None:
        value = reel_contract()
        self.assertEqual(validate_instagram_reel_contract(value), value)

    def test_unknown_keyframe_reference_fails_closed(self) -> None:
        value = reel_contract()
        value["segments"][1]["evidence_locator"]["frame_ids"] = ["missing"]
        with self.assertRaises(InstagramContractError):
            validate_instagram_reel_contract(value)

    def test_caption_only_without_keyframes(self) -> None:
        value = reel_contract()
        value["text"] = value["segments"][0]["text"]
        value["source_hash"] = digest(value["text"])
        value["segments"] = [value["segments"][0]]
        value["segments"][0]["evidence_locator"]["source_hash"] = value["source_hash"]
        value["media"] = []
        self.assertEqual(len(validate_instagram_reel_contract(value)["segments"]), 1)

    def test_worker_uploads_only_normalized_contract_and_keyframes(self) -> None:
        gateway = Gateway()
        result = InstagramReelIngestionWorker(gateway, "reel-worker").process_one()
        self.assertEqual(result.status, "completed")
        self.assertEqual([upload[3] for upload in gateway.uploads], ["application/json", "image/jpeg"])
        self.assertEqual(len(gateway.publications), 1)
        self.assertEqual(gateway.failures, [])
        self.assertEqual(
            gateway.progress_events,
            [
                (2, "starting"),
                (35, "loading_contract"),
                (94, "validating_contract"),
                (96, "uploading_artifacts"),
                (99, "publishing_source"),
            ],
        )

    def test_worker_rejects_tampered_keyframe(self) -> None:
        gateway = Gateway(JPEG + b"tampered")
        result = InstagramReelIngestionWorker(gateway, "reel-worker").process_one()
        self.assertEqual(result.status, "failed")
        self.assertEqual(gateway.uploads, [])
        self.assertEqual(gateway.publications, [])


if __name__ == "__main__":
    unittest.main()
