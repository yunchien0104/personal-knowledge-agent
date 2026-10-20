#!/usr/bin/env python3
"""Local or explicitly allowed Staging isolation test for asset-private."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any, Callable, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4


BUCKET = "asset-private"
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
PROJECT_REF_PATTERN = re.compile(r"^[a-z0-9]{20}$")
STAGING_ENV_NAMES = (
    "SUPABASE_STAGING_URL",
    "SUPABASE_STAGING_PUBLISHABLE_KEY",
    "SUPABASE_STAGING_SECRET_KEY",
    "SUPABASE_STAGING_PROJECT_REF",
    "SUPABASE_STAGING_PROJECT_ALLOWLIST",
    "SUPABASE_PRODUCTION_PROJECT_REFS",
)


@dataclass(frozen=True)
class IntegrationConfig:
    api_url: str
    publishable_key: str
    secret_key: str
    mode: str
    project_ref: str | None = None


@dataclass(frozen=True)
class Response:
    status: int
    body: bytes

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8")) if self.body else None


@dataclass(frozen=True)
class Session:
    user_id: str
    access_token: str


class IntegrationFailure(RuntimeError):
    pass


class SameOriginRedirectHandler(HTTPRedirectHandler):
    def __init__(self, api_url: str) -> None:
        self.expected_origin = origin(api_url)

    def redirect_request(
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        if origin(newurl) != self.expected_origin:
            raise IntegrationFailure("cross-origin API redirect refused")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def origin(url: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(url)
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.port


def load_local_config() -> IntegrationConfig:
    result = subprocess.run(
        ["supabase", "status", "-o", "json"],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )
    if result.returncode != 0:
        raise IntegrationFailure("local Supabase status is unavailable")
    values = json.loads(result.stdout)
    return IntegrationConfig(
        api_url=values["API_URL"].rstrip("/"),
        publishable_key=values["ANON_KEY"],
        secret_key=values["SERVICE_ROLE_KEY"],
        mode="local",
    )


def require_environment(environment: Mapping[str, str], name: str) -> str:
    value = environment.get(name, "").strip()
    if not value:
        raise IntegrationFailure(f"missing required staging environment variable: {name}")
    return value


def parse_project_refs(value: str, name: str) -> set[str]:
    refs = {item.strip() for item in value.split(",") if item.strip()}
    if not refs or any(not PROJECT_REF_PATTERN.fullmatch(ref) for ref in refs):
        raise IntegrationFailure(f"{name} must contain valid project refs")
    return refs


def load_staging_config(environment: Mapping[str, str]) -> IntegrationConfig:
    values = {name: require_environment(environment, name) for name in STAGING_ENV_NAMES}
    project_ref = values["SUPABASE_STAGING_PROJECT_REF"]
    if not PROJECT_REF_PATTERN.fullmatch(project_ref):
        raise IntegrationFailure("invalid staging project ref")

    allowlist = parse_project_refs(
        values["SUPABASE_STAGING_PROJECT_ALLOWLIST"],
        "SUPABASE_STAGING_PROJECT_ALLOWLIST",
    )
    production_refs = parse_project_refs(
        values["SUPABASE_PRODUCTION_PROJECT_REFS"],
        "SUPABASE_PRODUCTION_PROJECT_REFS",
    )
    if project_ref not in allowlist:
        raise IntegrationFailure("staging project ref is not allowlisted")
    if project_ref in production_refs:
        raise IntegrationFailure("production project ref is forbidden")

    api_url = values["SUPABASE_STAGING_URL"].rstrip("/")
    expected_url = f"https://{project_ref}.supabase.co"
    if api_url != expected_url or origin(api_url) != ("https", f"{project_ref}.supabase.co", None):
        raise IntegrationFailure("staging URL does not exactly match the allowlisted project ref")

    publishable_key = values["SUPABASE_STAGING_PUBLISHABLE_KEY"]
    secret_key = values["SUPABASE_STAGING_SECRET_KEY"]
    if not publishable_key.startswith("sb_publishable_"):
        raise IntegrationFailure("invalid staging publishable key format")
    if not secret_key.startswith("sb_secret_"):
        raise IntegrationFailure("invalid staging secret key format")
    if publishable_key == secret_key:
        raise IntegrationFailure("staging public and secret keys must differ")

    return IntegrationConfig(
        api_url=api_url,
        publishable_key=publishable_key,
        secret_key=secret_key,
        mode="staging",
        project_ref=project_ref,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--allow-staging",
        action="store_true",
        help="explicitly enable the allowlisted Cloud Staging target",
    )
    return parser


def load_config(allow_staging: bool, environment: Mapping[str, str]) -> IntegrationConfig:
    return load_staging_config(environment) if allow_staging else load_local_config()


def request(
    config: IntegrationConfig,
    method: str,
    path: str,
    *,
    token: str | None = None,
    json_body: Any | None = None,
    body: bytes | None = None,
    content_type: str | None = None,
    extra_headers: dict[str, str] | None = None,
) -> Response:
    if not path.startswith("/") or path.startswith("//"):
        raise IntegrationFailure("API path must be relative to the validated origin")

    headers = {"apikey": config.publishable_key}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if json_body is not None:
        body = json.dumps(json_body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif content_type is not None:
        headers["Content-Type"] = content_type
    if extra_headers:
        headers.update(extra_headers)

    req = Request(config.api_url + path, data=body, headers=headers, method=method)
    opener = build_opener(SameOriginRedirectHandler(config.api_url))
    try:
        with opener.open(req, timeout=15) as response:
            return Response(response.status, response.read())
    except HTTPError as error:
        return Response(error.code, error.read())
    except URLError as error:
        raise IntegrationFailure("API request failed before receiving a response") from error


def service_request(
    config: IntegrationConfig,
    method: str,
    path: str,
    **kwargs: Any,
) -> Response:
    extra_headers = dict(kwargs.pop("extra_headers", {}) or {})
    if config.mode == "staging":
        expected_url = f"https://{config.project_ref}.supabase.co"
        if config.api_url != expected_url:
            raise IntegrationFailure("refusing to send staging secret to an unverified origin")
    extra_headers["apikey"] = config.secret_key
    return request(
        config,
        method,
        path,
        token=config.secret_key if config.mode == "local" else None,
        extra_headers=extra_headers,
        **kwargs,
    )


def require_success(response: Response, operation: str) -> None:
    if not 200 <= response.status < 300:
        raise IntegrationFailure(f"{operation} failed with HTTP {response.status}")


def create_session(
    config: IntegrationConfig,
    email: str,
    password: str,
    record_user: Callable[[str], None],
) -> Session:
    if config.mode == "staging":
        user_id = str(uuid4())
        record_user(user_id)
        created = service_request(
            config,
            "POST",
            "/auth/v1/admin/users",
            json_body={
                "id": user_id,
                "email": email,
                "password": password,
                "email_confirm": True,
            },
        )
        require_success(created, "Auth admin user creation")
        if created.json().get("id") != user_id:
            raise IntegrationFailure("Auth admin returned an unexpected user id")
        payload: dict[str, Any] = {}
    else:
        signup = request(
            config,
            "POST",
            "/auth/v1/signup",
            json_body={"email": email, "password": password},
        )
        require_success(signup, "Auth signup")
        payload = signup.json()
        record_user(payload["user"]["id"])

    if not payload.get("access_token"):
        login = request(
            config,
            "POST",
            "/auth/v1/token?grant_type=password",
            json_body={"email": email, "password": password},
        )
        require_success(login, "Auth password session")
        payload = login.json()
    return Session(
        user_id=payload["user"]["id"],
        access_token=payload["access_token"],
    )


def create_asset(config: IntegrationConfig, session: Session, asset_id: str, marker: str) -> None:
    response = request(
        config,
        "POST",
        "/rest/v1/assets",
        token=session.access_token,
        json_body={
            "id": asset_id,
            "user_id": session.user_id,
            "source_type": "pdf",
            "source_external_id": marker,
        },
        extra_headers={"Prefer": "return=minimal"},
    )
    require_success(response, "Asset creation")


def object_path(session: Session, asset_id: str, marker: str) -> str:
    return f"{session.user_id}/{asset_id}/original/{marker}.txt"


def upload(
    config: IntegrationConfig,
    token: str,
    path: str,
    content: bytes,
    *,
    method: str = "POST",
) -> Response:
    return request(
        config,
        method,
        f"/storage/v1/object/{BUCKET}/{quote(path, safe='/')}",
        token=token,
        body=content,
        content_type="text/plain",
        extra_headers={"x-upsert": "false"},
    )


def download(config: IntegrationConfig, token: str | None, path: str) -> Response:
    return request(
        config,
        "GET",
        f"/storage/v1/object/authenticated/{BUCKET}/{quote(path, safe='/')}",
        token=token,
    )


def public_download(config: IntegrationConfig, path: str) -> Response:
    return request(
        config,
        "GET",
        f"/storage/v1/object/public/{BUCKET}/{quote(path, safe='/')}",
    )


def list_objects(config: IntegrationConfig, token: str, prefix: str) -> Response:
    return request(
        config,
        "POST",
        f"/storage/v1/object/list/{BUCKET}",
        token=token,
        json_body={
            "prefix": prefix,
            "limit": 100,
            "offset": 0,
            "sortBy": {"column": "name", "order": "asc"},
        },
    )


def remove_objects(config: IntegrationConfig, token: str, paths: list[str]) -> Response:
    return request(
        config,
        "DELETE",
        f"/storage/v1/object/{BUCKET}",
        token=token,
        json_body={"prefixes": paths},
    )


def service_remove(config: IntegrationConfig, paths: list[str]) -> None:
    if not paths:
        return
    response = service_request(
        config,
        "DELETE",
        f"/storage/v1/object/{BUCKET}",
        json_body={"prefixes": paths},
    )
    require_success(response, "Storage cleanup")


def service_delete_asset(config: IntegrationConfig, asset_id: str) -> None:
    response = service_request(
        config,
        "DELETE",
        f"/rest/v1/assets?id=eq.{quote(asset_id)}",
        extra_headers={"Prefer": "return=minimal"},
    )
    require_success(response, "Asset cleanup")


def service_delete_user(config: IntegrationConfig, user_id: str) -> None:
    response = service_request(
        config,
        "DELETE",
        f"/auth/v1/admin/users/{quote(user_id)}",
    )
    if response.status != 404:
        require_success(response, "Auth user cleanup")


def cleanup_resources(
    config: IntegrationConfig,
    paths: list[str],
    asset_ids: list[str],
    user_ids: list[str],
) -> list[str]:
    issues: list[str] = []
    for path in tuple(paths):
        try:
            service_remove(config, [path])
            paths.remove(path)
        except IntegrationFailure:
            issues.append(f"storage:{path}")
    for asset_id in tuple(asset_ids):
        try:
            service_delete_asset(config, asset_id)
            asset_ids.remove(asset_id)
        except IntegrationFailure:
            issues.append(f"asset:{asset_id}")
    for user_id in tuple(user_ids):
        try:
            service_delete_user(config, user_id)
            user_ids.remove(user_id)
        except IntegrationFailure:
            issues.append(f"user:{user_id}")
    return issues


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = load_config(args.allow_staging, os.environ)
    except IntegrationFailure as error:
        print(f"FAIL setup: {error}")
        return 1

    marker_prefix = "cloud-it" if config.mode == "staging" else "it"
    marker = f"{marker_prefix}-{uuid4().hex}"
    password = f"Pb-{uuid4().hex}-9!aA"
    sessions: list[Session] = []
    user_ids: list[str] = []
    asset_ids: list[str] = []
    paths: list[str] = []
    failures: list[str] = []
    assertions = 0
    failed_assertions = 0

    def check(name: str, condition: bool) -> None:
        nonlocal assertions, failed_assertions
        assertions += 1
        if condition:
            print(f"PASS {assertions}: {name}")
        else:
            failed_assertions += 1
            failures.append(name)
            print(f"FAIL {assertions}: {name}")

    def record_user(user_id: str) -> None:
        if user_id not in user_ids:
            user_ids.append(user_id)

    try:
        bucket_is_private = False
        if config.mode == "local":
            bucket = service_request(config, "GET", f"/storage/v1/bucket/{BUCKET}")
            bucket_is_private = bucket.status == 200 and bucket.json().get("public") is False

        session_a = create_session(
            config,
            f"{marker}-a@example.test",
            password,
            record_user,
        )
        sessions.append(session_a)
        session_b = create_session(
            config,
            f"{marker}-b@example.test",
            password,
            record_user,
        )
        sessions.append(session_b)
        check(
            "two distinct Auth sessions were created",
            session_a.user_id != session_b.user_id
            and session_a.access_token != session_b.access_token,
        )

        asset_a = str(uuid4())
        asset_b = str(uuid4())
        asset_ids.append(asset_a)
        create_asset(config, session_a, asset_a, f"{marker}-a")
        asset_ids.append(asset_b)
        create_asset(config, session_b, asset_b, f"{marker}-b")
        check("each Auth user created an owned Asset", True)

        path_a = object_path(session_a, asset_a, f"{marker}-a")
        path_b = object_path(session_b, asset_b, f"{marker}-b")
        paths.extend([path_a, path_b])
        content_a = f"{marker}:user-a".encode()
        content_b = f"{marker}:user-b".encode()

        uploaded_a = upload(config, session_a.access_token, path_a, content_a)
        uploaded_b = upload(config, session_b.access_token, path_b, content_b)
        if config.mode == "staging":
            public_file = public_download(config, path_a)
            bucket_is_private = (
                uploaded_a.status == 200 and public_file.status in {400, 401, 403, 404}
            )
        check("asset-private bucket is private", bucket_is_private)
        check("user A can upload within the owned original path", uploaded_a.status == 200)
        check("user B can upload within the owned original path", uploaded_b.status == 200)

        own_download = download(config, session_a.access_token, path_a)
        check(
            "user A can download the owned file",
            own_download.status == 200 and own_download.body == content_a,
        )

        own_prefix = f"{session_a.user_id}/{asset_a}/original"
        own_list = list_objects(config, session_a.access_token, own_prefix)
        own_names = [item.get("name") for item in own_list.json()] if own_list.status == 200 else []
        check(
            "user A can list the owned file",
            own_list.status == 200 and f"{marker}-a.txt" in own_names,
        )

        cross_download = download(config, session_a.access_token, path_b)
        check("user A cannot download user B file", cross_download.status in {400, 401, 403, 404})

        cross_prefix = f"{session_b.user_id}/{asset_b}/original"
        cross_list = list_objects(config, session_a.access_token, cross_prefix)
        check(
            "user A cannot list user B file",
            cross_list.status in {401, 403} or (cross_list.status == 200 and cross_list.json() == []),
        )

        cross_overwrite = upload(
            config,
            session_a.access_token,
            path_b,
            b"tampered",
            method="PUT",
        )
        preserved_b = download(config, session_b.access_token, path_b)
        check(
            "user A cannot overwrite user B file",
            cross_overwrite.status in {400, 401, 403, 404}
            and preserved_b.status == 200
            and preserved_b.body == content_b,
        )

        remove_objects(config, session_a.access_token, [path_b])
        preserved_after_delete = download(config, session_b.access_token, path_b)
        check(
            "user A cannot delete user B file",
            preserved_after_delete.status == 200 and preserved_after_delete.body == content_b,
        )

        anon_download = download(config, None, path_a)
        check("unauthenticated download is denied", anon_download.status in {400, 401, 403, 404})

        anon_list = request(
            config,
            "POST",
            f"/storage/v1/object/list/{BUCKET}",
            json_body={"prefix": own_prefix, "limit": 100, "offset": 0},
        )
        check(
            "unauthenticated listing is denied",
            anon_list.status in {401, 403} or (anon_list.status == 200 and anon_list.json() == []),
        )

        deleted_a = remove_objects(config, session_a.access_token, [path_a])
        check("user A can delete the owned file", deleted_a.status == 200)
        check(
            "deleted user A file is no longer downloadable",
            download(config, session_a.access_token, path_a).status in {400, 404},
        )

        deleted_b = remove_objects(config, session_b.access_token, [path_b])
        check("user B can delete the owned file during cleanup", deleted_b.status == 200)

        remaining_a = list_objects(config, session_a.access_token, own_prefix)
        remaining_b = list_objects(config, session_b.access_token, cross_prefix)
        check(
            "Storage API cleanup removed both test objects",
            remaining_a.status == 200
            and remaining_a.json() == []
            and remaining_b.status == 200
            and remaining_b.json() == [],
        )

        expected_user_ids = tuple(user_ids)
        cleanup_issues = cleanup_resources(config, paths, asset_ids, user_ids)
        assets_after_cleanup = service_request(
            config,
            "GET",
            "/rest/v1/assets?select=id&source_external_id=like."
            + quote(f"{marker}*"),
        )
        users_after_cleanup = [
            service_request(
                config,
                "GET",
                f"/auth/v1/admin/users/{quote(user_id)}",
            )
            for user_id in expected_user_ids
        ]
        check(
            "temporary Assets and Auth users were removed",
            not cleanup_issues
            and not paths
            and not asset_ids
            and not user_ids
            and assets_after_cleanup.status == 200
            and assets_after_cleanup.json() == []
            and all(response.status == 404 for response in users_after_cleanup),
        )
    except IntegrationFailure as error:
        failures.append(str(error))
        print(f"FAIL setup: {error}")
    finally:
        cleanup_issues = cleanup_resources(config, paths, asset_ids, user_ids)
        if cleanup_issues or paths or asset_ids or user_ids:
            unresolved = cleanup_issues + [
                *(f"storage:{path}" for path in paths),
                *(f"asset:{asset_id}" for asset_id in asset_ids),
                *(f"user:{user_id}" for user_id in user_ids),
            ]
            message = "unresolved test resources: " + ", ".join(dict.fromkeys(unresolved))
            failures.append(message)
            print(f"FAIL cleanup: {message}")

    print(f"RESULT: {assertions - failed_assertions}/{assertions} assertions passed")
    if failures:
        print("FAILED: " + "; ".join(failures))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
