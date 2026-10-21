import unittest

from personal_brain.deletion import AssetDeletionWorker
from personal_brain.models import JobPayloadV1
from personal_brain.supabase_gateway import ClaimedJob, GatewayError


USER_ID = "62000000-0000-4000-8000-000000000001"
ASSET_ID = "62000000-0000-4000-8000-000000000002"
DELETION_ID = "62000000-0000-4000-8000-000000000003"


def deletion_job(mode: str = "purge_all", generation: int = 1) -> ClaimedJob:
    job_id = "62000000-0000-4000-8000-000000000004"
    return ClaimedJob(
        job_id,
        USER_ID,
        None,
        generation,
        JobPayloadV1.from_dict({
            "schemaVersion": 1,
            "jobId": job_id,
            "userId": USER_ID,
            "jobType": "delete_asset",
            "idempotencyKey": "delete-test",
            "input": {
                "assetId": ASSET_ID,
                "deletionId": DELETION_ID,
                "mode": mode,
                "storageBucket": "asset-private",
            },
        }),
    )


class FakeDeletionGateway:
    def __init__(self, mode: str = "purge_all") -> None:
        self.job = deletion_job(mode)
        self.claimed = False
        self.listed_prefixes: list[str] = []
        self.removed: list[list[str]] = []
        self.failures: list[dict[str, object]] = []
        self.complete_error: GatewayError | None = None

    def claim(self, _worker_id: str, _lease_seconds: int, resource_class: str):
        self.assert_resource_class = resource_class
        if self.claimed:
            return None
        self.claimed = True
        return self.job

    def heartbeat(self, _job: ClaimedJob, _worker_id: str, _lease_seconds: int):
        return True

    def asset_deletion_context(self, job: ClaimedJob, _worker_id: str):
        mode = job.payload.input["mode"]
        suffix = "/original" if mode == "remove_original" else ""
        return {
            "deletionId": DELETION_ID,
            "assetId": ASSET_ID,
            "mode": mode,
            "storageBucket": "asset-private",
            "storagePrefix": f"{USER_ID}/{ASSET_ID}{suffix}",
        }

    def list_storage_objects(self, prefix: str):
        self.listed_prefixes.append(prefix)
        return [f"{prefix}/source.pdf"]

    def remove_storage_objects(self, paths: list[str]):
        self.removed.append(paths)

    def complete_asset_deletion(self, _job: ClaimedJob, _worker_id: str):
        if self.complete_error:
            raise self.complete_error
        return {"deletionId": DELETION_ID, "status": "completed"}

    def report_failure(self, _job: ClaimedJob, _worker_id: str, **failure: object):
        self.failures.append(failure)
        return "retry_wait" if failure["retryable"] else "failed"


class AssetDeletionWorkerTests(unittest.TestCase):
    def test_remove_original_deletes_only_original_prefix(self) -> None:
        gateway = FakeDeletionGateway("remove_original")

        result = AssetDeletionWorker(gateway, "delete-worker").process_one()

        self.assertEqual(result.status, "completed")
        self.assertEqual(gateway.assert_resource_class, "deletion")
        self.assertEqual(
            gateway.listed_prefixes,
            [f"{USER_ID}/{ASSET_ID}/original"],
        )
        self.assertEqual(
            gateway.removed,
            [[f"{USER_ID}/{ASSET_ID}/original/source.pdf"]],
        )

    def test_purge_deletes_entire_asset_prefix(self) -> None:
        gateway = FakeDeletionGateway("purge_all")

        result = AssetDeletionWorker(gateway, "delete-worker").process_one()

        self.assertEqual(result.status, "completed")
        self.assertEqual(gateway.listed_prefixes, [f"{USER_ID}/{ASSET_ID}"])

    def test_database_failure_after_storage_delete_is_retryable(self) -> None:
        first = FakeDeletionGateway("purge_all")
        first.complete_error = GatewayError(
            "supabase_unavailable", "Database unavailable", retryable=True
        )

        failed = AssetDeletionWorker(first, "delete-worker").process_one()

        self.assertEqual(failed.status, "retry_wait")
        self.assertEqual(len(first.removed), 1)
        self.assertTrue(first.failures[0]["retryable"])

        retry = FakeDeletionGateway("purge_all")
        retry.list_storage_objects = lambda prefix: []
        completed = AssetDeletionWorker(retry, "delete-worker").process_one()
        self.assertEqual(completed.status, "completed")
        self.assertEqual(retry.removed, [[]])


if __name__ == "__main__":
    unittest.main()
