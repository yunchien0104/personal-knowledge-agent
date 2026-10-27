from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
import json
import unittest

from personal_brain.instagram_contract import (
    InstagramContractError,
    validate_instagram_post_contract,
)
from personal_brain.instagram_ingestion import InstagramPostIngestionWorker
from personal_brain.models import JobPayloadV1
from personal_brain.supabase_gateway import ClaimedJob


USER_ID = "82000000-0000-4000-8000-000000000001"
ASSET_ID = "82000000-0000-4000-8000-000000000002"
JOB_ID = "82000000-0000-4000-8000-000000000003"
REVISION_ID = "82000000-0000-4000-8000-000000000004"
URL = "https://www.instagram.com/p/C0dExPost1/"
JPEG = b"\xff\xd8\xff\xe0synthetic-image\xff\xd9"


def contract() -> dict:
    caption = "Caption 😀"
    ocr = "圖片文字"
    text = f"{caption}\n\n{ocr}"
    source_hash = sha256(text.encode()).hexdigest()
    return {
        "schema_version": 1,
        "status": "ok",
        "source_type": "instagram_post",
        "source_url": URL,
        "source_external_id": "C0dExPost1",
        "author": None,
        "published_at": None,
        "source_hash": source_hash,
        "text": text,
        "media": [{
            "id": "image-001",
            "kind": "image",
            "mime": "image/jpeg",
            "sha256": sha256(JPEG).hexdigest(),
            "size_bytes": len(JPEG),
        }],
        "segments": [
            {
                "id": "caption",
                "text": caption,
                "content_hash": sha256(caption.encode()).hexdigest(),
                "evidence_locator": {
                    "kind": "instagram_post_caption",
                    "url": URL,
                    "post_id": "C0dExPost1",
                    "start_offset": 0,
                    "end_offset": len(caption),
                    "source_hash": source_hash,
                },
            },
            {
                "id": "image-001-ocr",
                "text": ocr,
                "content_hash": sha256(ocr.encode()).hexdigest(),
                "artifact_refs": ["image-001"],
                "evidence_locator": {
                    "kind": "instagram_image",
                    "url": URL,
                    "post_id": "C0dExPost1",
                    "image_index": 1,
                    "media_id": "image-001",
                    "start_offset": len(caption) + 2,
                    "end_offset": len(text),
                    "source_hash": source_hash,
                },
            },
        ],
        "metadata": {"post_shortcode": "C0dExPost1", "image_count": 1},
        "provenance": {
            "retrieved_at": "2026-10-27T00:00:00Z",
            "processor_version": "instagram-post-unified-v1",
            "retrieval_method": "ig_transcript_mac_app",
        },
        "warnings": [],
    }


def claimed_job() -> ClaimedJob:
    payload = JobPayloadV1.from_dict({
        "schemaVersion": 1,
        "jobId": JOB_ID,
        "userId": USER_ID,
        "jobType": "ingest_instagram_post",
        "idempotencyKey": f"ingest_instagram_post:{ASSET_ID}:contract-v1",
        "input": {
            "assetId": ASSET_ID,
            "sourceUrl": URL,
            "sourceExternalId": "C0dExPost1",
            "storageBucket": "asset-private",
            "contractObjectPath": f"{USER_ID}/{ASSET_ID}/original/instagram-contract.json",
            "mediaPrefix": f"{USER_ID}/{ASSET_ID}/original/images",
        },
    })
    return ClaimedJob(JOB_ID, USER_ID, ASSET_ID, 1, payload)


class Gateway:
    def __init__(self, value: dict | None = None, image: bytes = JPEG) -> None:
        self.job = claimed_job()
        self.value = value or contract()
        self.image = image
        self.claimed = False
        self.uploads: list[tuple[str, bytes, str, str]] = []
        self.publications: list[dict] = []
        self.failures: list[dict] = []

    def claim(self, *_args):
        if self.claimed:
            return None
        self.claimed = True
        return self.job

    def heartbeat(self, *_args):
        return True

    def download_instagram_contract(self, _job):
        return json.dumps(self.value, ensure_ascii=False).encode()

    def download_instagram_media(self, _job, _media_id):
        return self.image

    def upload_immutable(self, path, content, content_hash, content_type="application/json"):
        self.uploads.append((path, content, content_hash, content_type))

    def publish_instagram_post(self, _job, _worker_id, **result):
        self.publications.append(result)
        return {"revisionId": REVISION_ID, "status": "completed"}

    def report_failure(self, _job, _worker_id, **failure):
        self.failures.append(failure)
        return "retry_wait" if failure["retryable"] else "failed"


class InstagramContractTests(unittest.TestCase):
    def test_merged_contract_maps_unicode_offsets_and_private_media(self) -> None:
        value = contract()
        self.assertEqual(validate_instagram_post_contract(value), value)

    def test_contract_rejects_wrong_hash_locator_and_unsupported_media(self) -> None:
        for change in ("hash", "locator", "mime", "processor"):
            value = deepcopy(contract())
            if change == "hash":
                value["source_hash"] = "a" * 64
            elif change == "locator":
                value["segments"][1]["evidence_locator"]["media_id"] = "other"
            else:
                if change == "mime":
                    value["media"][0]["mime"] = "image/svg+xml"
                else:
                    value["provenance"]["processor_version"] = "../../escape"
            with self.subTest(change=change), self.assertRaises(InstagramContractError):
                validate_instagram_post_contract(value)

    def test_contract_accepts_caption_only_or_ocr_only_and_rejects_no_text(self) -> None:
        original = contract()
        caption_only = deepcopy(original)
        caption_only["text"] = caption_only["segments"][0]["text"]
        caption_only["source_hash"] = sha256(caption_only["text"].encode()).hexdigest()
        caption_only["segments"] = caption_only["segments"][:1]
        caption_only["segments"][0]["evidence_locator"]["source_hash"] = caption_only["source_hash"]

        ocr_only = deepcopy(original)
        ocr_only["text"] = ocr_only["segments"][1]["text"]
        ocr_only["source_hash"] = sha256(ocr_only["text"].encode()).hexdigest()
        ocr_only["segments"] = ocr_only["segments"][1:]
        ocr_locator = ocr_only["segments"][0]["evidence_locator"]
        ocr_locator["start_offset"] = 0
        ocr_locator["end_offset"] = len(ocr_only["text"])
        ocr_locator["source_hash"] = ocr_only["source_hash"]

        self.assertEqual(validate_instagram_post_contract(caption_only), caption_only)
        self.assertEqual(validate_instagram_post_contract(ocr_only), ocr_only)

        no_text = deepcopy(original)
        no_text["text"] = ""
        no_text["segments"] = []
        with self.assertRaises(InstagramContractError):
            validate_instagram_post_contract(no_text)


class InstagramPostWorkerTests(unittest.TestCase):
    def test_worker_validates_uploads_and_fenced_publication(self) -> None:
        gateway = Gateway()
        result = InstagramPostIngestionWorker(gateway, "ig-worker").process_one()

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.revision_id, REVISION_ID)
        self.assertEqual([upload[3] for upload in gateway.uploads], [
            "application/json", "image/jpeg",
        ])
        self.assertEqual(len(gateway.publications), 1)
        self.assertEqual(gateway.publications[0]["segments"], contract()["segments"])
        self.assertEqual(gateway.failures, [])

    def test_tampered_image_fails_closed_without_publication(self) -> None:
        gateway = Gateway(image=JPEG + b"tampered")
        result = InstagramPostIngestionWorker(gateway, "ig-worker").process_one()

        self.assertEqual(result.status, "failed")
        self.assertEqual(gateway.uploads, [])
        self.assertEqual(gateway.publications, [])
        self.assertEqual(gateway.failures[0]["error_code"], "invalid_instagram_contract")
        self.assertFalse(gateway.failures[0]["retryable"])


if __name__ == "__main__":
    unittest.main()
