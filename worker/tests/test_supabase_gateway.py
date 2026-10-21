import os
import unittest
from unittest.mock import patch

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


if __name__ == "__main__":
    unittest.main()
