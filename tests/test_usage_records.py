import importlib.util
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location("usage_records", Path(__file__).resolve().parents[1] / "backend/usage_records.py")
USAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(USAGE)


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.claude, self.codex = self.root / "claude", self.root / "codex"

    def write(self, relative, *records):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(json.dumps(row) for row in records) + "\n")
        return path

    def scan(self):
        return USAGE.scan_usage(self.claude, self.codex)

    def test_route_context_preserves_custom_api_path_and_model_id(self):
        for suffix in ("/v1", "/proxy/v1", "/api/paas/v4"):
            path = self.write("codex/p/sessions/one.jsonl", {})
            (self.codex / "p/config.toml").write_text('base_url = "https://gateway.test' + suffix + '"\nmodel = "model/v1"\n')
            context = USAGE._context(path, self.codex, "codex")
            self.assertEqual(context["gateway"], "https://gateway.test" + ("" if suffix == "/v1" else suffix))
            self.assertEqual(context["model"], "model/v1")

    def test_claude_stream_chunks_and_copied_files_count_one_response_with_final_usage(self):
        first = {"type": "assistant", "sessionId": "session-a", "uuid": "first", "message": {
            "id": "response-a", "usage": {"input_tokens": 10, "output_tokens": 2,
                                          "cache_read_input_tokens": 5, "cache_creation_input_tokens": 3}}}
        last = json.loads(json.dumps(first))
        last["uuid"] = "second"
        last["message"]["usage"]["output_tokens"] = 8
        self.write("claude/projects/demo/a.jsonl", first, last)
        self.write("claude/projects/copy/a.jsonl", first, last)
        result = self.scan()
        self.assertEqual((result["input_tokens"], result["output_tokens"], result["requests"]), (18, 8, 1))
        self.assertEqual(len(result["sessions"]), 1)
        self.assertIsNone(result["reasoning_output_tokens"])

    def test_codex_same_turn_has_multiple_responses_and_cumulative_events_are_not_added_twice(self):
        meta = {"type": "session_meta", "payload": {"id": "codex-a", "cwd": "/test/project"}}
        responses = [{"type": "token_usage_record", "payload": {
            "response_id": f"r{i}", "turn_id": "same-turn", "usage": {
                "input_tokens": 100, "cached_input_tokens": 60, "output_tokens": 10, "reasoning_output_tokens": 3}}} for i in range(2)]
        cumulative = {"type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": {"input_tokens": 200, "cached_input_tokens": 120, "output_tokens": 20, "reasoning_output_tokens": 6}}}}
        self.write("codex/p/sessions/one.jsonl", meta, *responses, cumulative, cumulative)
        self.write("codex/copy/sessions/two.jsonl", meta, *responses)
        result = self.scan()
        self.assertEqual((result["input_tokens"], result["output_tokens"], result["requests"]), (200, 20, 2))
        self.assertEqual(result["cached_input_tokens"], 120)
        self.assertEqual(result["reasoning_output_tokens"], 6)

    def test_older_codex_cumulative_only_and_partial_writes(self):
        path = self.write("codex/p/sessions/old.jsonl", {"type": "session_meta", "payload": {"id": "older"}}, {
            "type": "event_msg", "payload": {"type": "token_count", "info": {
                "total_token_usage": {"input_tokens": 123, "output_tokens": 4}}}}, [], {"type": "event_msg", "payload": None})
        with path.open("a") as out:
            out.write('{"type":')
        self.write("codex/history.jsonl", {"type": "token_usage_record", "payload": {"usage": {"input_tokens": 999, "output_tokens": 999}}})
        result = self.scan()
        self.assertEqual(result["input_tokens"], 123)
        self.assertTrue(result["sessions"][0]["counts_from_cumulative"])
        self.assertIsNone(result["cached_input_tokens"])

    def test_unknown_price_and_missing_counters_stay_unknown_zero_estimate_is_explicit(self):
        self.write("claude/projects/a.jsonl", {"type": "assistant", "sessionId": "a", "message": {
            "id": "a", "usage": {"input_tokens": 1, "output_tokens": 2, "cost": 999}}})
        self.assertIsNone(self.scan()["estimated_cost"])
        self.write("claude/projects/b.jsonl", {"type": "assistant", "sessionId": "b", "message": {
            "id": "b", "usage": {"input_tokens": 0, "output_tokens": 0, "cache_read_input_tokens": 0}}},
            {"type": "result", "total_cost_usd": 0})
        result = self.scan()
        self.assertEqual(result["estimated_cost"], 0)
        self.assertEqual(result["pricing_status"], "partial_estimate")
        self.assertNotIn("message", json.dumps(result))

    def test_empty_logs_and_invalid_or_external_records(self):
        self.write("claude/projects/a.jsonl", {"type": "assistant", "message": {"usage": {"input_tokens": True, "output_tokens": -1}}},
                   {"type": "assistant", "message": {"usage": {}}}, "bad", None)
        target = self.write("outside/a.jsonl", {"type": "assistant", "message": {"usage": {"input_tokens": 999, "output_tokens": 2}}})
        (self.claude / "projects/link.jsonl").symlink_to(target)
        result = self.scan()
        self.assertEqual(result["sessions"], [])
        self.assertIsNone(result["input_tokens"])
        self.assertEqual(result["pricing_status"], "unavailable")

    def test_bundled_usage_scan_does_not_modify_signed_resources(self):
        backend = Path(__file__).resolve().parents[1] / "backend"
        for name in ("claude-cchp-test.py", "usage_records.py", "gateway_billing.py", "model_pricing.py", "pricing_service.py",
                     "pricing_reference.json", "deepseek_reference.json", "session_history.py"):
            shutil.copyfile(backend / name, self.root / name)
        # A normal interpreter, without -B, must leave the signed resource tree intact.
        result = subprocess.run([sys.executable, "-c", """
import runpy, sys
from pathlib import Path
sys.dont_write_bytecode = False
scan = runpy.run_path(sys.argv[1])["usage_snapshot"]
root = Path(sys.argv[1]).parent
scan.__globals__.update(CONFIG_DIR=root, CODEX_HOME_ROOT=root, CLAUDE_BRANCH_ROOT=root / "branches")
scan()
""", str(self.root / "claude-cchp-test.py")], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)["sessions"], [])
        self.assertFalse((self.root / "__pycache__").exists())


if __name__ == "__main__":
    unittest.main()
