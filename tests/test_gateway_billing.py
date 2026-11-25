import importlib.util
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse


BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND_DIR))
import gateway_billing as BILLING

SPEC = importlib.util.spec_from_file_location("billing_backend", BACKEND_DIR / "workbench_backend.py")
BACKEND = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BACKEND)


class BillingTests(unittest.TestCase):
    def test_account_preserves_decimal_zero_and_does_not_invent_hidden_ratio(self):
        data = {"currency": "USD", "balance": {"wallet": "9997.665554384", "spendable": "0", "packages": None},
                "ratio": {"disclosed": False, "value": 0}, "limits": {"key": [], "user": []}}
        with patch.object(BILLING, "get_json", return_value=data):
            result = BILLING.account_snapshot("https://gateway.test", "test-token")
        self.assertEqual(result["wallet"], "9997.665554384")
        self.assertEqual(result["spendable"], "0")
        self.assertIsNone(result["packages"])
        self.assertIsNone(result["ratio"])
        data["ratio"]["disclosed"] = True
        data["balance"]["unlimited"] = True
        data["balance"]["spendable"] = None
        with patch.object(BILLING, "get_json", return_value=data):
            result = BILLING.account_snapshot("https://gateway.test", "test-token")
        self.assertEqual(result["ratio"], "0")
        self.assertTrue(result["unlimited"])
        self.assertIsNone(result["spendable"])

    def test_non_cchp_and_auth_failures_are_not_zero_balances(self):
        for status in ("unsupported", "unauthorized", "offline"):
            with self.subTest(status=status), patch.object(BILLING, "get_json", side_effect=BILLING.BillingError(status, "不可用")):
                result = BILLING.account_snapshot("https://gateway.test", "test-token")
                self.assertEqual(result["status"], status)
                self.assertNotIn("wallet", result)
        with patch.object(BILLING, "get_json", return_value={"data": []}):
            self.assertEqual(BILLING.account_snapshot("https://gateway.test", "test-token")["status"], "invalid")

    def test_bill_uses_adjusted_units_and_never_treats_pending_as_free(self):
        row = {"requestId": "r1", "costUnits": 65_312_000, "adjustedCostUnits": 0}
        self.assertEqual(BILLING.billing_record(row)["cost"], "0")
        row["adjustedCostUnits"] = None
        self.assertEqual(BILLING.billing_record(row)["cost"], "0.0065312")
        row["pendingPricing"] = {"reason": "waiting"}
        self.assertIsNone(BILLING.billing_record(row)["cost"])
        self.assertEqual(BILLING.billing_record(row)["state"], "待结算")
        self.assertIsNone(BILLING.billing_record({"requestId": "r2"})["cost"])
        self.assertIsNone(BILLING.decimal_text("NaN"))
        self.assertIsNone(BILLING.decimal_text(True))

    def test_management_reads_are_explicitly_scoped_to_the_token_user(self):
        def reply(console, path, token):
            self.assertEqual(token, "test-token")
            parsed = urlparse(path)
            if parsed.path.endswith("/me"):
                return {"user": {"id": "user-123", "displayName": "Test"}}
            query = parse_qs(parsed.query)
            self.assertEqual(query["scopeType"], ["user"])
            self.assertEqual(query["scopeId"], ["user-123"])
            if parsed.path.endswith("/summary"):
                self.assertEqual(query["timezone"], ["UTC"])
                return {"today": {"costUnits": 123456789, "requests": 2}}
            self.assertEqual(query["limit"], ["20"])
            self.assertIn("start", query)
            return {"page": {"items": [{"requestId": "r1", "costUnits": 0}], "nextCursor": "more"}}
        with patch.object(BILLING, "get_json", side_effect=reply):
            result = BILLING.billing_snapshot("https://console.test", "test-token")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["today_cost"], "0.0123456789")
        self.assertTrue(result["has_more"])
        self.assertEqual(result["records"][0]["cost"], "0")
        self.assertIn("全部调用 Key", result["scope_label"])

    def test_failed_summary_does_not_discard_valid_logs(self):
        responses = [{"user": {"id": "u1"}}, BILLING.BillingError("unsupported", "汇总不可用"),
                     {"page": {"items": [{"requestId": "r1", "costUnits": 1}]}}]
        with patch.object(BILLING, "get_json", side_effect=responses):
            result = BILLING.billing_snapshot("https://console.test", "test-token")
        self.assertEqual(result["status"], "partial")
        self.assertNotIn("today_cost", result)
        self.assertEqual(result["records"][0]["cost"], "0.0000000001")

    def test_missing_identity_never_queries_unscoped_platform_bills(self):
        with patch.object(BILLING, "get_json", return_value={}) as get:
            result = BILLING.billing_snapshot("https://console.test", "test-token")
        self.assertEqual(result["status"], "invalid")
        self.assertEqual(get.call_count, 1)

    def test_transport_only_uses_get_and_refuses_redirects_without_error_body_secrets(self):
        with patch.object(BILLING, "build_opener") as build:
            build.return_value.open.return_value.__enter__.return_value.read.return_value = b'{"value":0.0000000001}'
            result = BILLING.get_json("https://console.test", "/api/account/me", "private-test-token")
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.get_method(), "GET")
            self.assertEqual(request.get_header("Authorization"), "Bearer private-test-token")
            self.assertEqual(str(result["value"]), "1E-10")
            self.assertTrue(any(isinstance(h, BILLING.NoRedirect) for h in build.call_args.args))
            handler = BILLING.NoRedirect()
            self.assertIsNone(handler.redirect_request(request, None, 302, "Found", {}, "https://other.test"))
            for code, status in [(302, "redirect"), (401, "unauthorized"), (404, "unsupported")]:
                build.return_value.open.side_effect = HTTPError(request.full_url, code, "private-test-token", {}, None)
                with self.subTest(code=code), self.assertRaises(BILLING.BillingError) as caught:
                    BILLING.get_json("https://console.test", "/api/account/me", "private-test-token")
                self.assertEqual(caught.exception.status, status)
                self.assertNotIn("private-test-token", str(caught.exception))

    def test_saved_credentials_are_private_gateway_bound_and_not_returned(self):
        with tempfile.TemporaryDirectory() as folder, patch.object(BACKEND, "TEST_ROOT", Path(folder)), \
                patch.object(BACKEND, "billing_profile") as profile, patch.object(BACKEND, "billing_module", return_value=BILLING):
            profile.return_value = {"id": "p-test", "gateway": "https://gateway.test"}
            secret = "cch_auth_live_test00000000000000"
            payload = {"console": "https://console.test", "token": secret}
            snapshot = {"status": "ok", "message": "", "records": []}
            with patch("sys.stdin", io.StringIO(json.dumps(payload))), patch("sys.stdout", io.StringIO()) as out, \
                    patch.object(BILLING, "billing_snapshot", return_value=snapshot):
                BACKEND.save_billing_from_stdin("p-test")
            self.assertNotIn(secret, out.getvalue())
            path = Path(folder) / "billing/p-test.json"
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            changed = {"id": "p-test", "gateway": "https://other-gateway.test"}
            self.assertIsNone(BACKEND.billing_config(changed))
            with patch.object(BILLING, "billing_snapshot") as fetch, patch("sys.stdout", io.StringIO()) as out:
                profile.return_value = changed
                BACKEND.profile_billing("p-test")
            fetch.assert_not_called()
            self.assertEqual(json.loads(out.getvalue())["status"], "unconfigured")
            path.chmod(0o644)
            with self.assertRaises(RuntimeError):
                BACKEND.billing_config({"id": "p-test", "gateway": "https://gateway.test"})


if __name__ == "__main__":
    unittest.main()
