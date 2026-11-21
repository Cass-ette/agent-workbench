import importlib.util
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError


BACKEND_PATH = Path(__file__).resolve().parents[1] / "backend" / "claude-cchp-test.py"
SPEC = importlib.util.spec_from_file_location("claude_cchp_gateway_backend", BACKEND_PATH)
BACKEND = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BACKEND)


class GatewayProfileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        root = Path(self.temporary.name)
        BACKEND.TEST_ROOT = root / "test"
        BACKEND.CLIENT_ROOT = root
        BACKEND.CONFIG_DIR = BACKEND.TEST_ROOT / "config"
        BACKEND.WORKSPACE = BACKEND.TEST_ROOT / "workspace"
        BACKEND.KEY_FILE = BACKEND.TEST_ROOT / "free-key"
        BACKEND.KEY_DIR = BACKEND.TEST_ROOT / "keys"
        BACKEND.PROFILE_INDEX = BACKEND.TEST_ROOT / "key-profiles.json"
        BACKEND.WORKSPACE_INDEX = BACKEND.TEST_ROOT / "workspaces.json"
        BACKEND.LAST_LAUNCH = BACKEND.TEST_ROOT / "last-launch.json"
        BACKEND.MANAGER_LAST_LAUNCH = BACKEND.TEST_ROOT / "manager-last-launch.json"
        BACKEND.SETTINGS = BACKEND.CONFIG_DIR / "settings.json"
        BACKEND.EMPTY_MCP = BACKEND.TEST_ROOT / "empty-mcp.json"
        BACKEND.CODEX_HOME_ROOT = BACKEND.TEST_ROOT / "codex"
        BACKEND.CLAUDE_BRANCH_ROOT = BACKEND.TEST_ROOT / "claude-branches"
        BACKEND.GATEWAY_CONFIG = BACKEND.TEST_ROOT / "gateway.json"
        self.addCleanup(self.temporary.cleanup)

    def test_profiles_store_and_update_their_gateway_independently(self):
        first_gateway = "https://first.example.test"
        second_gateway = "https://second.example.test"
        first_key = "test-key-profile-0000000000000001"
        second_key = "test-key-profile-0000000000000002"
        with patch.object(BACKEND, "fetch_models", return_value=["model-a"]) as fetch:
            first = BACKEND.save_profile_key("First", first_key, first_gateway)
            second = BACKEND.save_profile_key("Second", second_key, second_gateway)
        self.assertEqual(fetch.call_args_list[0].args, (first_key, first_gateway))
        self.assertEqual(fetch.call_args_list[1].args, (second_key, second_gateway))

        profiles = {profile["id"]: profile for profile in BACKEND.load_profiles()}
        self.assertEqual(profiles[first["id"]]["gateway"], first_gateway)
        self.assertEqual(profiles[second["id"]]["gateway"], second_gateway)

        with patch.object(BACKEND, "fetch_models", return_value=["model-a"]), \
                patch("sys.stdin", io.StringIO(json.dumps({"url": second_gateway}))):
            BACKEND.set_profile_gateway_from_stdin(first["id"])
        updated = {profile["id"]: profile for profile in BACKEND.load_profiles()}
        self.assertEqual(updated[first["id"]]["gateway"], second_gateway)
        self.assertEqual(updated[second["id"]]["gateway"], second_gateway)

    def test_legacy_string_profiles_use_the_saved_default_gateway(self):
        BACKEND.KEY_DIR.mkdir(parents=True)
        key_file = BACKEND.KEY_DIR / "p-legacy.key"
        key_file.write_text("test-key-legacy-000000000000000001\n")
        key_file.chmod(0o600)
        BACKEND.PROFILE_INDEX.write_text(json.dumps({"p-legacy": "Legacy profile"}))
        BACKEND.save_gateway("https://default.example.test")

        profiles = BACKEND.load_profiles()
        self.assertEqual(profiles[0]["gateway"], "https://default.example.test")

    def test_remove_profile_deletes_key_and_billing_secrets_but_preserves_history(self):
        with patch.object(BACKEND, "fetch_models", return_value=["model-a"]):
            removed = BACKEND.save_profile_key("Remove me", "test-key-remove-000000000000000001")
            kept = BACKEND.save_profile_key("Keep me", "test-key-keep-0000000000000000001")

        billing_dir = BACKEND.TEST_ROOT / "billing"
        billing_dir.mkdir()
        billing = billing_dir / (removed["id"] + ".json")
        backup = billing_dir / (removed["id"] + ".json.bak")
        billing.write_text('{"token":"billing-secret"}')
        backup.write_text('{"token":"old-billing-secret"}')
        history = BACKEND.CONFIG_DIR / "projects/demo/session.jsonl"
        history.parent.mkdir(parents=True)
        history.write_text('{"title":"kept history"}\n')

        output = io.StringIO()
        with patch("sys.stdout", output):
            BACKEND.remove_profile_key(removed["id"])

        result = json.loads(output.getvalue())
        profiles = {item["id"]: item for item in BACKEND.load_profiles()}
        self.assertTrue(result["removed"])
        self.assertEqual(result["name"], "Remove me")
        self.assertNotIn(removed["id"], profiles)
        self.assertIn(kept["id"], profiles)
        self.assertFalse((BACKEND.KEY_DIR / (removed["id"] + ".key")).exists())
        self.assertFalse(billing.exists())
        self.assertFalse(backup.exists())
        self.assertTrue(history.is_file())

    def test_default_key_cannot_be_removed(self):
        BACKEND.KEY_FILE.parent.mkdir(parents=True)
        BACKEND.KEY_FILE.write_text("default-test-key-000000000000000001\n")

        with self.assertRaisesRegex(RuntimeError, "默认免费配置"):
            BACKEND.remove_profile_key("free")

        self.assertTrue(BACKEND.KEY_FILE.is_file())

    def test_refuses_to_follow_a_profile_key_symlink(self):
        with patch.object(BACKEND, "fetch_models", return_value=["model-a"]):
            profile = BACKEND.save_profile_key("Linked", "test-key-linked-000000000000000001")
        key_path = BACKEND.KEY_DIR / (profile["id"] + ".key")
        key_path.unlink()
        target = BACKEND.TEST_ROOT / "outside.key"
        target.write_text("must stay")
        key_path.symlink_to(target)

        with self.assertRaisesRegex(RuntimeError, "符号链接"):
            BACKEND.remove_profile_key(profile["id"])

        self.assertEqual(target.read_text(), "must stay")
        self.assertIn(profile["id"], {item["id"] for item in BACKEND.load_profiles()})

    def test_gateway_url_rejects_credentials_and_remote_http(self):
        for url in ("http://example.test", "https://user:secret@example.test"):
            with self.subTest(url=url), self.assertRaises(RuntimeError):
                BACKEND.normalize_gateway(url)
        self.assertEqual(BACKEND.normalize_gateway("http://127.0.0.1:8123/v1"), "http://127.0.0.1:8123")

    def test_model_list_supports_bearer_only_api(self):
        def respond(request, **kwargs):
            if request.get_header("X-api-key") == "test-key":
                raise HTTPError(request.full_url, 401, "Bearer required", {}, None)
            self.assertEqual(request.full_url, "https://api.example.test/v1/models")
            self.assertEqual(request.get_header("Authorization"), "Bearer test-key")
            return io.BytesIO(b'{"data":[{"id":"model-a"}]}')

        with patch.object(BACKEND, "build_opener") as build:
            opener = build.return_value
            opener.open.side_effect = respond
            self.assertEqual(BACKEND.fetch_models("test-key", "https://api.example.test"), ["model-a"])
            self.assertEqual(opener.open.call_count, 2)

    def test_usage_snapshot_aggregates_client_records_without_duplicate_assistant_rows(self):
        claude_file = BACKEND.CONFIG_DIR / "projects" / "demo" / "session.jsonl"
        claude_file.parent.mkdir(parents=True)
        usage = {
            "input_tokens": 100,
            "cache_creation_input_tokens": 20,
            "cache_read_input_tokens": 30,
            "output_tokens": 7,
        }
        assistant = {"type": "assistant", "sessionId": "s1",
                     "message": {"id": "m1", "usage": usage}}
        claude_file.write_text(json.dumps(assistant) + "\n" + json.dumps(assistant) + "\n")
        codex_file = BACKEND.CODEX_HOME_ROOT / "p1" / "sessions" / "rollout.jsonl"
        codex_file.parent.mkdir(parents=True)
        codex_file.write_text(json.dumps({
            "type": "token_usage_record",
            "payload": {"turn_id": "t1", "usage": {
                "input_tokens": 50, "cached_input_tokens": 40,
                "output_tokens": 8, "reasoning_output_tokens": 2,
            }},
        }) + "\n")

        output = io.StringIO()
        with patch("sys.stdout", output):
            BACKEND.usage_snapshot()
        result = json.loads(output.getvalue())
        # The summary normalizes Claude's input_tokens by adding its cache buckets,
        # matching Codex's already-inclusive input count.
        self.assertEqual(result["input_tokens"], 200)
        self.assertEqual(result["output_tokens"], 15)
        self.assertEqual(result["cached_input_tokens"], 70)
        self.assertEqual(result["cache_creation_input_tokens"], 20)
        self.assertEqual(result["reasoning_output_tokens"], 2)
        self.assertEqual(result["requests"], 2)
        self.assertEqual(result["by_source"], {"claude": 1, "codex": 1})
        self.assertIsNone(result["estimated_cost"])


if __name__ == "__main__":
    unittest.main()
