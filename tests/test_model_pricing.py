import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from decimal import Decimal

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
import model_pricing as P
from pricing_service import PricingService
from usage_records import scan_usage


def catalog(model="test", prompt="2", output="10"):
    return {"source_kind": "gateway", "models": {model: {"charges": {"prompt": prompt, "completion": output,
            "cache_read": "0.2", "cache_write": "2.5"}, "tracks": [], "group_ids": []}}}


class PricingTests(unittest.TestCase):
    def test_shared_mac_windows_cost_contract(self):
        cases = json.loads((Path(__file__).parent / "fixtures/pricing-contracts.json").read_text())
        for case in cases:
            with self.subTest(name=case["name"]):
                counts, quote = case["counts"], case["quote"]
                self.assertAlmostEqual(float(P.estimate_tokens(counts, quote, counts.get("context_known", False))), case["low"], places=12)
                self.assertAlmostEqual(float(P.estimate_tokens(counts, quote, counts.get("context_known", False), True)), case["high"], places=12)

    def test_zero_and_hidden_ratio_are_distinct_and_factor_is_applied_once(self):
        q = P.quote_for(catalog(), "test", "0.5")
        self.assertEqual(q["charges"]["prompt"], "1.0")
        self.assertEqual(q["base_charges"]["prompt"], "2")
        self.assertEqual(P.quote_for(catalog(), "test", 0)["charges"]["completion"], "0")
        hidden = P.quote_for(catalog(), "test")
        self.assertIsNone(hidden["ratio"])
        self.assertIn("未知", hidden["basis"])

    def test_only_gateway_catalog_with_disclosed_ratio_is_confirmed_price(self):
        self.assertTrue(P.quote_for(catalog(), "test", "0")["is_gateway_price"])
        self.assertFalse(P.quote_for(catalog(), "test")["is_gateway_price"])
        for kind in ("official", "reference"):
            self.assertFalse(P.quote_for({**catalog(), "source_kind": kind}, "test", "1")["is_gateway_price"])

    def test_usage_distinguishes_gateway_reference_client_mixed_and_pending(self):
        def session(model, reported=None):
            return dict(model=model, input_tokens=1000000, output_tokens=0, estimated_cost=reported)
        for source, expected in (("gateway", "catalog_estimate"), ("official", "reference_estimate"), ("reference", "reference_estimate")):
            result = P.apply_usage_pricing({"sessions": [session("test")]},
                lambda s, m: P.quote_for({**catalog(), "source_kind": source}, m, 1))
            self.assertEqual(result["pricing_status"], expected)
        def resolve(s, model):
            return P.quote_for(catalog(), model, 1)
        mixed = P.apply_usage_pricing({"sessions": [session("test"), session("client-only", 0)]}, resolve)
        self.assertEqual(mixed["pricing_status"], "mixed_estimate")
        client = P.apply_usage_pricing({"sessions": [session("client-only", 0)]}, resolve)
        self.assertEqual(client["pricing_status"], "client_estimate")
        pending = P.apply_usage_pricing({"sessions": [session("unknown")]}, resolve)
        self.assertEqual(pending["pricing_status"], "pending_price")
        self.assertIsNone(pending["estimated_cost"])

    def test_cache_is_subtracted_from_input_and_reasoning_not_added_to_output(self):
        counts = dict(input_tokens=1000, output_tokens=100, cached_input_tokens=600,
                      cache_creation_input_tokens=100, reasoning_output_tokens=90)
        self.assertEqual(P.estimate_tokens(counts, P.quote_for(catalog(), "test", 1)), Decimal("0.00197"))
        counts["cached_input_tokens"] = 1001
        self.assertIsNone(P.estimate_tokens(counts, P.quote_for(catalog(), "test", 1)))

    def test_context_threshold_does_not_apply_batch_or_priority_tracks(self):
        c = catalog()
        c["models"]["test"]["tracks"] = [
            {"triggers": [], "factors": {"prompt": "1"}},
            {"triggers": [{"kind": "input_tokens_above", "threshold": 100, "inclusive": False}], "factors": {"prompt": "2"}},
            {"triggers": [{"kind": "endpoint_matches", "pattern": "batch"}], "factors": {"prompt": "0.1"}}]
        q = P.quote_for(c, "test", 1)
        counts = {"input_tokens": 200, "output_tokens": 0}
        self.assertEqual(P.estimate_tokens(counts, q, True), Decimal("0.0008"))
        self.assertEqual(P.estimate_tokens(counts, q, False), Decimal("0.0004"))
        self.assertEqual(P.estimate_tokens(counts, q, False, True), Decimal("0.0008"))

    def test_deepseek_official_table_parses_peak_range_and_no_model_alias_guess(self):
        html = '''<table><tr><td>MODEL</td><td>deepseek-flash<sup>(1)</sup></td></tr>
        <tr><td>1M INPUT TOKENS<br>(CACHE HIT)</td><td>OFF-PEAK</td><td>$0.003</td></tr>
        <tr><td>PEAK</td><td>$0.006</td></tr>
        <tr><td>1M INPUT TOKENS<br>(CACHE MISS)</td><td>OFF-PEAK</td><td>$0.15</td></tr>
        <tr><td>PEAK</td><td>$0.30</td></tr>
        <tr><td>1M OUTPUT TOKENS</td><td>OFF-PEAK</td><td>$0.60</td></tr>
        <tr><td>PEAK</td><td>$1.20</td></tr></table>'''
        models = P.deepseek_prices(html)
        self.assertEqual(models["deepseek-flash"]["upper_charges"]["prompt"], "0.30")
        self.assertIsNone(P.find_price(models, "deepseek-chat"))
        q = P.quote_for({"models": models, "source_kind": "official"}, "deepseek-flash", 1)
        self.assertEqual(P.estimate_tokens({"input_tokens": 1000000, "output_tokens": 0}, q, upper=True), Decimal("0.30"))

    def test_cached_catalog_survives_network_failure_and_invalid_fresh_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            row = {"modelName": "test", "charges": {k: {"unit": "per_M_tokens", "price": v}
                   for k, v in (("prompt", 2), ("completion", 10))}}
            write = lambda p, t: (p.parent.mkdir(parents=True, exist_ok=True), p.write_text(t))
            with patch.object(P, "public_get", return_value=json.dumps({"models": [row]}).encode()):
                first = P.load_catalog("http://localhost:9234", tmp, write)
            with patch.object(P, "public_get", side_effect=ValueError("invalid")):
                old = P.load_catalog("http://localhost:9234", tmp, write, force=True)
            self.assertEqual(old["models"], first["models"])
            self.assertTrue(old["stale"])
            self.assertIn("9233", P.source_candidates("http://localhost:9234")[1])
            self.assertEqual(P.source_candidates("https://a.test", "https://b.test/prices"), ["https://b.test/prices"])

    def test_public_source_rejects_credentials_remote_http_and_oversize(self):
        for url in ("http://example.com/prices", "https://user:secret@example.com/prices", "https://a.test/?key=abc"):
            with self.subTest(url=url), self.assertRaises(ValueError):
                P.public_get(url)

    def test_usage_prices_mixed_models_and_cumulative_remainder_without_leaking_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); path = root / "codex/p/sessions/a.jsonl"; path.parent.mkdir(parents=True)
            (path.parent.parent / "config.toml").write_text('model = "a"\nbase_url = "http://localhost:9999/v1"\n')
            rows = [{"type": "session_meta", "payload": {"id": "thread"}}]
            for model in ("a", "b"):
                rows += [{"type": "turn_context", "payload": {"model": model}},
                         {"type": "token_usage_record", "payload": {"response_id": model, "usage": {"input_tokens": 1000000, "output_tokens": 0}}}]
            rows += [{"type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": {"input_tokens": 3000000, "output_tokens": 0}}}}]
            path.write_text("\n".join(json.dumps(r) for r in rows))
            s = scan_usage(root / "claude", root / "codex", include_pricing=True)
            c = catalog("a"); c["models"].update(catalog("b", "4")["models"])
            result = P.apply_usage_pricing(s, lambda session, model: P.quote_for(c, model, 1))
            self.assertEqual(result["estimated_cost"], 10)
            self.assertNotIn("_pricing", json.dumps(result))

    def test_profile_isolation_and_conflicting_historic_keys_show_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            profiles = [{"id": "one", "gateway": "https://a.test"}, {"id": "two", "gateway": "https://b.test"}]
            service = PricingService(profiles, tmp, lambda p: "test", lambda p, t: None)
            for profile, ratio in zip(profiles, ("1", "0")):
                service.contexts[profile["id"]] = {"catalog": catalog(), "ratio": ratio, "account": {}, "direct": False}
            exact = service.resolve({"_pricing_contexts": [{"profile_id": "two", "gateway": "https://b.test"}]}, "test")
            self.assertEqual(exact["charges"]["prompt"], "0")
            unknown = service.resolve({}, "test")
            self.assertIsNone(unknown["ratio"])
            self.assertIn("无法归属", unknown["basis"])
            self.assertFalse(unknown["is_gateway_price"])
            partial = service.resolve({"_pricing_contexts": [
                {"profile_id": "two", "gateway": "https://b.test"},
                {"profile_id": "deleted", "gateway": "https://old.test"}]}, "test")
            self.assertFalse(partial["is_gateway_price"])
            changed = service.resolve({"_pricing_contexts": [
                {"profile_id": "two", "gateway": "https://old.test"}]}, "test")
            self.assertFalse(changed["is_gateway_price"])

    def test_direct_deepseek_does_not_require_cchp_account_or_admin_token(self):
        with tempfile.TemporaryDirectory() as tmp:
            service = PricingService([{"id": "ds", "gateway": "https://api.deepseek.com"}], tmp,
                                     lambda p: self.fail("direct pricing must not read credentials"), lambda p, t: None)
            self.assertEqual(service.quote("ds", "deepseek-flash")["ratio"], "1")


if __name__ == "__main__":
    unittest.main()
