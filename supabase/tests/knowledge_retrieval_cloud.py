#!/usr/bin/env python3
"""Allowlisted Cloud Staging Auth/RLS integration test for knowledge retrieval."""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from storage_api_integration import (
    IntegrationFailure,
    create_session,
    load_staging_config,
    request,
    require_success,
    service_delete_asset,
    service_delete_user,
    service_request,
)


def service_insert(config: Any, table: str, payload: dict[str, Any]) -> None:
    response = service_request(
        config,
        "POST",
        f"/rest/v1/{table}",
        json_body=payload,
        extra_headers={"Prefer": "return=minimal"},
    )
    require_success(response, f"{table} fixture creation")


def seed_user(config: Any, user_id: str, term: str, page: int) -> dict[str, str]:
    ids = {name: str(uuid4()) for name in ("asset", "revision", "evidence", "note", "job", "claim")}
    service_insert(config, "assets", {
        "id": ids["asset"], "user_id": user_id, "source_type": "pdf", "status": "ready",
    })
    service_insert(config, "asset_revisions", {
        "id": ids["revision"], "user_id": user_id, "asset_id": ids["asset"],
        "revision_no": 1, "fingerprint": "a" * 64,
        "processor_version": "retrieval-cloud-test-v1", "status": "validated",
    })
    patch = service_request(
        config, "PATCH", f"/rest/v1/assets?id=eq.{quote(ids['asset'])}",
        json_body={"active_revision_id": ids["revision"]},
        extra_headers={"Prefer": "return=minimal"},
    )
    require_success(patch, "Asset fixture linking")
    locator = {"kind": "pdf_page", "page_number": page, "start_offset": 0, "end_offset": 40}
    service_insert(config, "asset_evidence", {
        "id": ids["evidence"], "user_id": user_id, "revision_id": ids["revision"],
        "locator_type": "pdf", "locator_json": locator,
        "excerpt": f"{term} original source", "source_hash": "b" * 64,
    })
    service_insert(config, "notes", {
        "id": ids["note"], "user_id": user_id, "note_type": "source_note",
        "title": f"{term} note", "body": f"{term} generated summary",
    })
    service_insert(config, "source_note_generations", {
        "user_id": user_id, "note_id": ids["note"], "asset_id": ids["asset"],
        "source_revision_id": ids["revision"], "asset_id_snapshot": ids["asset"],
        "source_revision_id_snapshot": ids["revision"], "source_fingerprint": "a" * 64,
        "job_id": ids["job"], "model_provider": "mock", "model_name": "mock",
        "prompt_version": "retrieval-test-v1", "output_hash": "c" * 64,
    })
    service_insert(config, "claims", {
        "id": ids["claim"], "user_id": user_id, "note_id": ids["note"],
        "text": f"{term} generated claim", "claim_type": "source_assertion",
        "verification_status": "unreviewed", "needs_review": True, "source_note_revision": 1,
    })
    service_insert(config, "claim_evidence", {
        "user_id": user_id, "claim_id": ids["claim"], "evidence_id": ids["evidence"],
        "evidence_id_snapshot": ids["evidence"], "source_revision_id": ids["revision"],
        "source_revision_id_snapshot": ids["revision"], "relation_type": "asserts",
        "locator_json": locator, "supporting_quote": f"{term} original source",
    })
    return ids


def search(config: Any, token: str | None, term: str):
    return request(
        config, "POST", "/rest/v1/rpc/search_knowledge", token=token,
        json_body={"p_query": term, "p_limit": 20, "p_offset": 0},
    )


def main() -> int:
    try:
        config = load_staging_config(os.environ)
    except IntegrationFailure as error:
        print(f"FAIL setup: {error}")
        return 1

    marker = uuid4().hex
    password = f"Pb-{uuid4().hex}-9!aA"
    user_ids: list[str] = []
    asset_ids: list[str] = []
    failures: list[str] = []
    assertions = 0

    def check(name: str, condition: bool) -> None:
        nonlocal assertions
        assertions += 1
        print(f"{'PASS' if condition else 'FAIL'} {assertions}: {name}")
        if not condition:
            failures.append(name)

    def record_user(user_id: str) -> None:
        user_ids.append(user_id)

    try:
        session_a = create_session(config, f"retrieval-{marker}-a@example.test", password, record_user)
        session_b = create_session(config, f"retrieval-{marker}-b@example.test", password, record_user)
        check("two real Auth sessions are distinct", session_a.user_id != session_b.user_id)
        fixtures_a = seed_user(config, session_a.user_id, marker, 7)
        asset_ids.append(fixtures_a["asset"])
        fixtures_b = seed_user(config, session_b.user_id, marker, 9)
        asset_ids.append(fixtures_b["asset"])

        result_a = search(config, session_a.access_token, marker)
        require_success(result_a, "User A retrieval")
        rows_a = result_a.json()
        check("User A receives Note, Claim, Evidence", {row["result_type"] for row in rows_a} == {"source_note", "claim", "evidence"})
        check("generated and original origins remain explicit", {row["content_origin"] for row in rows_a} == {"generated_note", "generated_claim", "original_source"})
        check("User A receives only owned Asset/Revision", all(row["asset_id"] == fixtures_a["asset"] and row["revision_id"] == fixtures_a["revision"] for row in rows_a))
        check("User A citations retain owned Evidence and PDF page", all(row["citations"] and row["citations"][0]["evidenceId"] == fixtures_a["evidence"] and row["citations"][0]["locator"]["page_number"] == 7 for row in rows_a))

        result_b = search(config, session_b.access_token, marker)
        require_success(result_b, "User B retrieval")
        rows_b = result_b.json()
        check("User B receives only its three isolated results", len(rows_b) == 3 and all(row["asset_id"] == fixtures_b["asset"] for row in rows_b))
        check("User B citations cannot expose User A Evidence", all(row["citations"][0]["evidenceId"] == fixtures_b["evidence"] for row in rows_b))
        check("anonymous retrieval is denied", search(config, None, marker).status in {401, 403, 404})
    except IntegrationFailure as error:
        failures.append(str(error))
        print(f"FAIL runtime: {error}")
    finally:
        cleanup_issues: list[str] = []
        for asset_id in reversed(asset_ids):
            try:
                service_delete_asset(config, asset_id)
            except IntegrationFailure:
                cleanup_issues.append(f"asset:{asset_id}")
        for user_id in reversed(user_ids):
            try:
                service_delete_user(config, user_id)
            except IntegrationFailure:
                cleanup_issues.append(f"user:{user_id}")
        for asset_id in asset_ids:
            verification = service_request(
                config, "GET", f"/rest/v1/assets?id=eq.{quote(asset_id)}&select=id",
            )
            if verification.status != 200 or verification.json():
                cleanup_issues.append(f"asset-still-present:{asset_id}")
        for user_id in user_ids:
            verification = service_request(
                config, "GET", f"/auth/v1/admin/users/{quote(user_id)}",
            )
            if verification.status != 404:
                cleanup_issues.append(f"user-still-present:{user_id}")
        check("all recorded Cloud fixtures were precisely cleaned", not cleanup_issues)
        if cleanup_issues:
            print("UNCLEANED " + ",".join(cleanup_issues))

    print(f"RESULT: {assertions - len(failures)}/{assertions} assertions passed")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
