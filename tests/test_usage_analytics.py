import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from usage_records import scan_usage, scan_cached, _usage, _day
from model_pricing import apply_upstream_usage


class UsageAnalyticsTests(unittest.TestCase):
    def test_local_cache_reuses_scan_and_invalidates_for_logs_and_profile_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); log = root/"claude/scope-one/projects/p/s.jsonl"
            log.parent.mkdir(parents=True)
            manifest = root/"claude/scope-one/workbench-profile.json"
            manifest.write_text(json.dumps({"profile_id":"p","profile_name":"名称A","key_fingerprint":"private-fingerprint"}))
            row = {"type":"assistant","sessionId":"s","timestamp":"2026-11-22T00:00:00Z","cwd":"/project",
                   "message":{"id":"r1","model":"one","usage":{"input_tokens":10,"output_tokens":1},"content":"private-conversation-text"}}
            log.write_text(json.dumps(row)+"\n")
            first = scan_cached(root/"claude", root/"codex", root/"usage")
            with patch("usage_records.scan_usage", side_effect=AssertionError("unchanged logs must not be parsed")):
                cached = scan_cached(root/"claude", root/"codex", root/"usage")
            self.assertEqual(first["input_tokens"],cached["input_tokens"])
            self.assertEqual(cached["sessions"][0]["profile_name"],"名称A")
            manifest.write_text(json.dumps({"profile_id":"p","profile_name":"名称B"}))
            changed = scan_cached(root/"claude", root/"codex", root/"usage")
            self.assertEqual(changed["sessions"][0]["profile_name"],"名称B")
            row["message"]["id"]="r2"
            with log.open("a") as out: out.write(json.dumps(row)+"\n")
            self.assertEqual(scan_cached(root/"claude",root/"codex",root/"usage")["input_tokens"],20)
            disk = (root/"usage/snapshot.json").read_text()
            for secret in ("private-fingerprint","private-conversation-text","device_id"):
                self.assertNotIn(secret,disk)
            log.unlink()
            self.assertEqual(scan_cached(root/"claude",root/"codex",root/"usage")["sessions"],[])

    def test_records_keep_each_calls_project_model_and_explicit_price(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); log=root/"codex/p/sessions/s.jsonl"; log.parent.mkdir(parents=True)
            rows=[{"type":"session_meta","payload":{"id":"s","cwd":"/project-a"}},
                  {"type":"turn_context","payload":{"model":"first","cwd":"/project-a"}},
                  {"type":"token_usage_record","timestamp":"2026-11-21T23:30:00Z","payload":{"response_id":"r1","usage":{"input_tokens":1000000,"output_tokens":0},"billing":{"currency":"USD","unit":"per_M_tokens","unit_prices":{"input":2}}}},
                  {"type":"turn_context","payload":{"model":"last","cwd":"/project-b"}},
                  {"type":"token_usage_record","timestamp":"2026-11-22T01:00:00+08:00","payload":{"response_id":"r2","usage":{"input_tokens":100,"output_tokens":1},"upstream_cost_usd":0}}]
            log.write_text("\n".join(json.dumps(r) for r in rows))
            result=apply_upstream_usage(scan_usage(root/"claude",root/"codex",True))
            records=result["usage_records"]
            self.assertEqual([r["project_path"] for r in records],["/project-a","/project-b"])
            self.assertEqual([r["model"] for r in records],["first","last"])
            self.assertEqual([r["estimated_cost"] for r in records],[2,0])
            self.assertEqual(len({r["record_id"] for r in records}),2)
            self.assertEqual(result["schema_version"],2)
            self.assertNotIn("device_id",json.dumps(result))

    def test_prices_without_currency_and_unit_do_not_become_cost(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); log=root/"claude/projects/s.jsonl"; log.parent.mkdir(parents=True)
            row={"type":"assistant","sessionId":"s","message":{"id":"r","model":"x","usage":{"input_tokens":100,"output_tokens":2},"upstream_prices":{"input":2,"output":5}}}
            log.write_text(json.dumps(row))
            result=apply_upstream_usage(scan_usage(root/"claude",root/"codex",True))
            self.assertIsNone(result["estimated_cost"])
            self.assertEqual(result["usage_records"][0]["unit_prices"],[])
    def test_standard_compatibility_usage_has_counts_not_implicit_prices(self):
        for value in ({"prompt_tokens": 100, "completion_tokens": 9, "prompt_tokens_details": {"cached_tokens": 40}},
                      {"input_tokens": 100, "output_tokens": 9, "input_tokens_details": {"cached_tokens": 40}}):
            self.assertEqual(_usage(value, "codex")["cached_input_tokens"], 40)
            self.assertEqual(_usage(value, "codex")["input_tokens"], 100)
        self.assertEqual(_usage({"input_tokens": 60, "output_tokens": 9, "cache_read_input_tokens": 40}, "claude")["input_tokens"], 100)

    def test_cross_day_models_copies_unknown_dates_and_partial_prices(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rows = [
                {"type": "assistant", "sessionId": "s", "timestamp": "2026-11-22T01:00:00+08:00", "message": {"id": "one", "model": "a", "usage": {"input_tokens": 1000000, "output_tokens": 0}}},
                {"type": "assistant", "sessionId": "s", "timestamp": "2026-11-22T13:00:00Z", "message": {"id": "two", "model": "b", "usage": {"input_tokens": 1000000, "output_tokens": 0}}},
                {"type": "assistant", "sessionId": "s", "message": {"id": "three", "model": "unknown", "usage": {"input_tokens": 10, "output_tokens": 0}}},
            ]
            for name in ("a", "copy"):
                path = root / "claude/projects" / (name + ".jsonl"); path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("\n".join(json.dumps(r) for r in rows))
            result = apply_upstream_usage(scan_usage(root/"claude", root/"codex", True))
            self.assertEqual(result["input_tokens"], 2000010)
            self.assertIsNone(result["estimated_cost"])
            self.assertEqual(result["pricing_status"], "unavailable")
            self.assertEqual([p["date"] for p in result["timeline"]], ["", "2026-11-21", "2026-11-22"])
            self.assertIsNone(result["timeline"][0]["estimated_cost"])
            self.assertEqual(sum(p["input_tokens"] for p in result["timeline"]), result["input_tokens"])
            self.assertNotIn("_pricing", json.dumps(result))

    def test_cumulative_checkpoints_are_differences_and_duplicates_do_not_add(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path = root/"codex/p/sessions/one.jsonl"; path.parent.mkdir(parents=True)
            rows = [{"type": "event_msg", "timestamp": stamp, "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": n, "output_tokens": 0}}}}
                    for stamp, n in (("2026-11-21T01:00:00Z",100),("2026-11-22T01:00:00Z",250),("2026-11-22T01:00:00Z",250))]
            path.write_text("\n".join(json.dumps(r) for r in rows))
            result = scan_usage(root/"claude",root/"codex",True)
            self.assertEqual([p["input_tokens"] for p in result["timeline"]], [100,150])
            self.assertEqual(result["input_tokens"],250)

    def test_cumulative_history_keeps_each_projects_context_and_does_not_guess_missing_models(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path = root/"codex/p/sessions/one.jsonl"; path.parent.mkdir(parents=True)
            (path.parent.parent/"config.toml").write_text('model = "current-config-model"\n')
            rows = [{"type":"session_meta","payload":{"id":"s","cwd":"/first"}},
                    {"type":"event_msg","timestamp":"2026-11-21T01:00:00Z","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":100,"output_tokens":0}}}},
                    {"type":"turn_context","payload":{"model":"later-model","cwd":"/second"}},
                    {"type":"event_msg","timestamp":"2026-11-22T01:00:00Z","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":250,"output_tokens":0}}}}]
            path.write_text("\n".join(json.dumps(r) for r in rows))
            records = apply_upstream_usage(scan_usage(root/"claude",root/"codex",True))["usage_records"]
            self.assertEqual([(r["project_path"],r["model"],r["input_tokens"]) for r in records],
                             [("/first","",100),("/second","later-model",150)])

    def test_reported_upstream_zero_works_without_price_and_client_estimate_is_not_receipt(self):
        record = {"input_tokens": 100, "output_tokens": 5, "model": "unknown", "upstream_cost_usd": 0, "date": "2026-11-21"}
        result = apply_upstream_usage({"sessions": [{"model": "unknown", "estimated_cost": 99, "_pricing_records": [record]}]})
        self.assertEqual(result["estimated_cost"], 0)
        self.assertEqual(result["sessions"][0]["cost_source"], "upstream")
        result = apply_upstream_usage({"sessions": [{"model": "unknown", "input_tokens": 100, "output_tokens": 5, "estimated_cost": 99}]})
        self.assertIsNone(result["estimated_cost"])

    def test_dates_are_valid_utc_and_missing_is_not_assumed_today(self):
        self.assertEqual(_day("2026-11-22T01:00:00+08:00"), "2026-11-21")
        for invalid in ("2026-99-99", "2026-11-21", None, ""):
            self.assertEqual(_day(invalid), "")

    def test_only_explicit_upstream_unit_prices_are_used(self):
        record = {"input_tokens": 1000000, "output_tokens": 0, "model": "x", "date": "2026-11-22",
                  "unit_prices": [{"model":"x", "currency":"USD", "unit":"per_M_tokens", "charges":{"prompt":2}, "source":"upstream_log"}]}
        result = apply_upstream_usage({"sessions":[{"model":"x","_pricing_records":[record]}]})
        self.assertEqual(result["estimated_cost"], 2)
        self.assertEqual(result["sessions"][0]["cost_source"], "upstream_rates")
        unknown = apply_upstream_usage({"sessions":[{"model":"x","_pricing_records":[{"input_tokens":100,"output_tokens":2,"model":"x"}]}]})
        self.assertIsNone(unknown["estimated_cost"])


if __name__ == "__main__":
    unittest.main()
