import os
import unittest
from unittest.mock import patch

from personal_brain.models import JobPayloadV1
from personal_brain.supabase_gateway import ClaimedJob, GatewayError, SupabaseGateway


class FakeResponse:
    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self):
        return b"{}"


class RecordingOpener:
    def __init__(self):
        self.request = None

    def open(self, request, *, timeout):
        self.request = request
        self.timeout = timeout
        return FakeResponse()


class SupabaseGatewayAuthenticationTests(unittest.TestCase):
    def headers_for(self, service_key: str) -> dict[str, str]:
        gateway = SupabaseGateway("https://example.supabase.co", service_key)
        opener = RecordingOpener()
        gateway._opener = opener

        gateway._request("GET", "rest/v1/rpc/test")

        self.assertIsNotNone(opener.request)
        return {name.lower(): value for name, value in opener.request.header_items()}

    def test_secret_key_uses_apikey_without_bearer_authorization(self):
        service_key = "sb_secret_" + "a" * 32

        headers = self.headers_for(service_key)

        self.assertEqual(headers["apikey"], service_key)
        self.assertNotIn("authorization", headers)

    def test_legacy_service_role_jwt_remains_apikey_and_bearer(self):
        service_key = "eyJhbGciOiJIUzI1NiJ9.service-role.signature"

        headers = self.headers_for(service_key)

        self.assertEqual(headers["apikey"], service_key)
        self.assertEqual(headers["authorization"], f"Bearer {service_key}")

    def test_environment_variable_name_remains_service_role_key(self):
        service_key = "sb_secret_" + "b" * 32
        with patch.dict(
            os.environ,
            {
                "SUPABASE_SERVER_URL": "https://example.supabase.co",
                "SUPABASE_SERVICE_ROLE_KEY": service_key,
            },
            clear=True,
        ):
            gateway = SupabaseGateway.from_environment()

        self.assertEqual(gateway._service_key, service_key)

    def test_progress_uses_fenced_worker_rpc(self):
        gateway = SupabaseGateway(
            "https://example.supabase.co", "sb_secret_" + "a" * 32
        )
        calls = []
        gateway._rpc = lambda name, arguments: calls.append((name, arguments)) or True
        payload = JobPayloadV1.from_dict({
            "schemaVersion": 1,
            "jobId": "66000000-0000-4000-8000-000000000001",
            "userId": "66000000-0000-4000-8000-000000000002",
            "jobType": "ingest_instagram_reel",
            "idempotencyKey": "reel-progress",
            "input": {
                "assetId": "66000000-0000-4000-8000-000000000003",
                "sourceUrl": "https://www.instagram.com/reel/TestReel1/",
                "sourceExternalId": "TestReel1",
                "acquisitionMode": "cloud",
            },
        })
        job = ClaimedJob(
            payload.job_id, payload.user_id, payload.input["assetId"], 7, payload
        )

        self.assertTrue(gateway.progress(job, "reel-worker", 40, "transcribing_audio"))
        self.assertEqual(calls, [("report_job_progress", {
            "p_job_id": job.id,
            "p_worker_id": "reel-worker",
            "p_lease_generation": 7,
            "p_progress_percent": 40,
            "p_progress_stage": "transcribing_audio",
        })])

    def test_deletion_claim_requires_null_asset_and_valid_mode(self):
        row = {
            "id": "63000000-0000-4000-8000-000000000001",
            "user_id": "63000000-0000-4000-8000-000000000002",
            "asset_id": None,
            "lease_generation": 1,
            "payload_json": {
                "schemaVersion": 1,
                "jobId": "63000000-0000-4000-8000-000000000001",
                "userId": "63000000-0000-4000-8000-000000000002",
                "jobType": "delete_asset",
                "idempotencyKey": "delete-claim",
                "input": {
                    "assetId": "63000000-0000-4000-8000-000000000003",
                    "deletionId": "63000000-0000-4000-8000-000000000004",
                    "mode": "purge_all",
                },
            },
        }

        claim = ClaimedJob.from_row(row)
        self.assertEqual(claim.payload.job_type, "delete_asset")

        row["asset_id"] = "63000000-0000-4000-8000-000000000003"
        with self.assertRaises(GatewayError):
            ClaimedJob.from_row(row)

    def test_retrieval_index_claim_accepts_asset_and_revision(self):
        row = {
            "id": "64000000-0000-4000-8000-000000000001",
            "user_id": "64000000-0000-4000-8000-000000000002",
            "asset_id": "64000000-0000-4000-8000-000000000003",
            "lease_generation": 1,
            "payload_json": {
                "schemaVersion": 1,
                "jobId": "64000000-0000-4000-8000-000000000001",
                "userId": "64000000-0000-4000-8000-000000000002",
                "jobType": "index_retrieval",
                "idempotencyKey": "retrieval-index:test",
                "input": {
                    "assetId": "64000000-0000-4000-8000-000000000003",
                    "revisionId": "64000000-0000-4000-8000-000000000004",
                    "embeddingModel": "test-model",
                    "embeddingVersion": "test-v1",
                },
            },
        }

        claim = ClaimedJob.from_row(row)
        self.assertEqual(claim.payload.job_type, "index_retrieval")

        del row["payload_json"]["input"]["revisionId"]
        with self.assertRaises(GatewayError):
            ClaimedJob.from_row(row)

    def test_instagram_source_note_context_uses_namespaced_rpc(self):
        gateway = SupabaseGateway(
            "https://example.supabase.co", "sb_secret_" + "a" * 32
        )
        calls = []
        gateway._rpc = lambda name, arguments: calls.append((name, arguments)) or {
            "sourceType": "instagram_post"
        }
        payload = JobPayloadV1.from_dict({
            "schemaVersion": 1,
            "jobId": "65000000-0000-4000-8000-000000000001",
            "userId": "65000000-0000-4000-8000-000000000002",
            "jobType": "generate_source_note",
            "idempotencyKey": "instagram-source-note",
            "input": {
                "assetId": "65000000-0000-4000-8000-000000000003",
                "revisionId": "65000000-0000-4000-8000-000000000004",
                "sourceType": "instagram_post",
            },
        })
        job = ClaimedJob(
            payload.job_id, payload.user_id, payload.input["assetId"], 1, payload
        )

        result = gateway.source_note_context(job, "worker")

        self.assertEqual(result, {"sourceType": "instagram_post"})
        self.assertEqual(calls[0][0], "get_instagram_post_source_note_context")


if __name__ == "__main__":
    unittest.main()
