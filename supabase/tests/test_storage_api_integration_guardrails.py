from __future__ import annotations

import ast
from contextlib import redirect_stderr
import importlib.util
from io import StringIO
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.request import Request


MODULE_PATH = Path(__file__).with_name("storage_api_integration.py")
SPEC = importlib.util.spec_from_file_location("storage_api_integration", MODULE_PATH)
assert SPEC and SPEC.loader
integration = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = integration
SPEC.loader.exec_module(integration)

STAGING_REF = "abcdefghijklmnopqrst"
PRODUCTION_REF = "zyxwvutsrqponmlkjihg"


def staging_environment(**overrides: str) -> dict[str, str]:
    environment = {
        "SUPABASE_STAGING_URL": f"https://{STAGING_REF}.supabase.co",
        "SUPABASE_STAGING_PUBLISHABLE_KEY": "sb_publishable_test-only",
        "SUPABASE_STAGING_SECRET_KEY": "sb_secret_test-only",
        "SUPABASE_STAGING_PROJECT_REF": STAGING_REF,
        "SUPABASE_STAGING_PROJECT_ALLOWLIST": STAGING_REF,
        "SUPABASE_PRODUCTION_PROJECT_REFS": PRODUCTION_REF,
    }
    environment.update(overrides)
    return environment


class CloudGuardrailTests(unittest.TestCase):
    def test_isolation_suite_still_contains_18_runtime_assertions(self) -> None:
        tree = ast.parse(MODULE_PATH.read_text(encoding="utf-8"))
        checks = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "check"
        ]
        self.assertEqual(len(checks), 18)

    def test_default_cli_mode_does_not_allow_staging(self) -> None:
        self.assertFalse(integration.build_parser().parse_args([]).allow_staging)

    def test_secret_cannot_be_supplied_on_command_line(self) -> None:
        with redirect_stderr(StringIO()), self.assertRaises(SystemExit):
            integration.build_parser().parse_args(["--secret-key", "do-not-accept"])

    def test_allowlisted_https_staging_configuration_is_accepted(self) -> None:
        config = integration.load_staging_config(staging_environment())
        self.assertEqual(config.mode, "staging")
        self.assertEqual(config.project_ref, STAGING_REF)

    def test_plain_http_staging_url_is_rejected(self) -> None:
        environment = staging_environment(
            SUPABASE_STAGING_URL=f"http://{STAGING_REF}.supabase.co",
        )
        with self.assertRaises(integration.IntegrationFailure):
            integration.load_staging_config(environment)

    def test_url_must_exactly_match_the_staging_ref(self) -> None:
        environment = staging_environment(
            SUPABASE_STAGING_URL=f"https://{PRODUCTION_REF}.supabase.co",
        )
        with self.assertRaises(integration.IntegrationFailure):
            integration.load_staging_config(environment)

    def test_production_ref_is_always_rejected(self) -> None:
        environment = staging_environment(
            SUPABASE_PRODUCTION_PROJECT_REFS=f"{PRODUCTION_REF},{STAGING_REF}",
        )
        with self.assertRaises(integration.IntegrationFailure):
            integration.load_staging_config(environment)

    def test_cross_host_redirect_is_rejected_before_a_request_is_rebuilt(self) -> None:
        api_url = f"https://{STAGING_REF}.supabase.co"
        handler = integration.SameOriginRedirectHandler(api_url)
        with self.assertRaises(integration.IntegrationFailure):
            handler.redirect_request(
                Request(f"{api_url}/auth/v1/token"),
                None,
                302,
                "Found",
                {},
                "https://example.com/capture",
            )

    def test_staging_user_id_is_recorded_before_login_failure(self) -> None:
        config = integration.load_staging_config(staging_environment())
        recorded: list[str] = []
        admin_payloads: list[dict[str, object]] = []

        def fake_service_request(*args: object, **kwargs: object) -> integration.Response:
            payload = kwargs["json_body"]
            assert isinstance(payload, dict)
            admin_payloads.append(payload)
            return integration.Response(200, integration.json.dumps({"id": payload["id"]}).encode())

        with (
            patch.object(integration, "service_request", side_effect=fake_service_request),
            patch.object(integration, "request", return_value=integration.Response(401, b"{}")),
            self.assertRaises(integration.IntegrationFailure),
        ):
            integration.create_session(
                config,
                "cloud-it@example.test",
                "not-logged",
                recorded.append,
            )

        self.assertEqual(len(recorded), 1)
        self.assertEqual(admin_payloads[0]["id"], recorded[0])
        self.assertIs(admin_payloads[0]["email_confirm"], True)

    def test_staging_secret_is_sent_only_as_an_api_key(self) -> None:
        config = integration.load_staging_config(staging_environment())
        with patch.object(
            integration,
            "request",
            return_value=integration.Response(200, b"{}"),
        ) as api_request:
            integration.service_request(config, "GET", "/auth/v1/admin/users/test")

        self.assertIsNone(api_request.call_args.kwargs["token"])
        self.assertEqual(
            api_request.call_args.kwargs["extra_headers"]["apikey"],
            config.secret_key,
        )


if __name__ == "__main__":
    unittest.main()
