"""Read-only CCHP account and billing APIs. No inference or balance-delta estimates."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


COST_UNITS_PER_USD = Decimal(10_000_000_000)


class BillingError(RuntimeError):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # API credentials must never follow a redirect, even to another port.
        return None


def get_json(base, path, token):
    request = Request(base + path, headers={
        "Authorization": "Bearer " + token,
        "Accept": "application/json",
        "User-Agent": "AgentWorkbench-Billing/1.0",
    })
    try:
        with build_opener(ProxyHandler({}), NoRedirect()).open(request, timeout=12) as response:
            raw = response.read(2_000_001)
        if len(raw) > 2_000_000:
            raise ValueError("oversized response")
        data = json.loads(raw, parse_float=Decimal)
        if not isinstance(data, dict):
            raise ValueError("expected object")
        return data
    except HTTPError as error:
        if error.code in (401, 403):
            raise BillingError("unauthorized", "凭据无效或缺少账单读取权限。") from None
        if error.code in (404, 405, 501):
            raise BillingError("unsupported", "此服务未提供兼容的 CCHP 查询接口。") from None
        if 300 <= error.code < 400:
            raise BillingError("redirect", "接口发生重定向，已停止发送凭据；请填写最终服务地址。") from None
        raise BillingError("error", "查询失败：HTTP " + str(error.code)) from None
    except (URLError, TimeoutError, OSError):
        raise BillingError("offline", "无法连接查询服务，请检查地址和网络。") from None
    except (ValueError, TypeError):
        raise BillingError("invalid", "服务返回了不兼容的数据，金额未知。") from None


def decimal_text(value, divisor=Decimal(1)):
    if value is None or isinstance(value, bool) or not isinstance(value, (str, int, float, Decimal)):
        return None
    try:
        amount = Decimal(str(value)) / divisor
        if not amount.is_finite():
            return None
        return format(amount, "f")
    except (InvalidOperation, ValueError):
        return None


def account_snapshot(gateway, key):
    try:
        data = get_json(gateway, "/v1/cchp/account", key)
        balance = data.get("balance")
        if not isinstance(balance, dict) or not isinstance(data.get("currency"), str):
            raise BillingError("invalid", "账户接口格式不兼容，余额未知。")
        ratio = data.get("ratio") or {}
        ratio = ratio if isinstance(ratio, dict) else {}
        limits = data.get("limits")
        has_limits = isinstance(limits, dict) and any(limits.get(k) for k in ("key", "user"))
        return {
            "status": "ok", "message": "", "currency": data["currency"],
            "available": decimal_text(balance.get("available")),
            "wallet": decimal_text(balance.get("wallet")),
            "packages": decimal_text(balance.get("packages")),
            "spendable": decimal_text(balance.get("spendable")),
            "unlimited": balance.get("unlimited") is True,
            "ratio": decimal_text(ratio.get("value")) if ratio.get("disclosed") is True else None,
            "group": ratio.get("groupName") if isinstance(ratio.get("groupName"), str) else None,
            "group_id": ratio.get("groupId") if isinstance(ratio.get("groupId"), str) else None,
            "limits": json.dumps(limits, ensure_ascii=False, indent=2, default=str) if has_limits else None,
            "observed_at": data.get("observedAt") if isinstance(data.get("observedAt"), str) else None,
        }
    except BillingError as error:
        return {"status": error.status, "message": str(error)}


def billing_identity(console, token):
    data = get_json(console, "/api/account/me", token)
    user = data.get("user") or {}
    if not isinstance(user, dict) or not isinstance(user.get("id"), str) or not user["id"]:
        raise BillingError("invalid", "管理接口未返回用户身份，不能确定账单范围。")
    label = user.get("displayName") or user.get("email") or user["id"]
    return {"id": user["id"], "label": str(label)}


def billing_record(row):
    pending = row.get("pendingPricing") not in (None, False) or row.get("inFlight") not in (None, False)
    units = row.get("adjustedCostUnits")
    if units is None:
        units = row.get("costUnits")
    amount = None if pending else decimal_text(units, COST_UNITS_PER_USD)
    return {
        "id": row["requestId"],
        "model": row.get("requestedModel") or row.get("model") or "模型未记录",
        "key_name": row.get("apiKeyName") or row.get("keyName"),
        "started_at": row.get("startedAt"),
        "cost": amount,
        "state": "待结算" if pending else ("金额未提供" if amount is None else "已记录"),
    }


def billing_snapshot(console, token):
    result = {"console": console, "records": [], "currency": "USD"}
    try:
        identity = billing_identity(console, token)
    except BillingError as error:
        return {**result, "status": error.status, "message": str(error)}
    scope = {"scopeType": "user", "scopeId": identity["id"]}
    result["scope_label"] = identity["label"] + " · 此用户的全部调用 Key"
    # Explicit UTC makes the 'today' boundary unambiguous across machines.
    result["timezone"] = "UTC"
    errors = []
    succeeded = 0
    try:
        summary = get_json(console, "/api/account/usage/summary?" + urlencode({**scope, "timezone": "UTC"}), token)
        today = summary.get("today")
        if not isinstance(today, dict):
            raise BillingError("invalid", "账单汇总格式不兼容。")
        result["today_cost"] = decimal_text(today.get("costUnits"), COST_UNITS_PER_USD)
        requests = today.get("requests")
        result["today_requests"] = requests if isinstance(requests, int) and not isinstance(requests, bool) else None
        succeeded += 1
    except BillingError as error:
        errors.append(str(error))
    now = datetime.now(timezone.utc)
    result["observed_at"] = now.isoformat()
    try:
        params = {**scope, "start": (now - timedelta(days=1)).isoformat(), "end": now.isoformat(), "limit": 20}
        logs = get_json(console, "/api/account/usage/logs?" + urlencode(params), token)
        page = logs.get("page")
        if not isinstance(page, dict) or not isinstance(page.get("items"), list):
            raise BillingError("invalid", "逐次账单格式不兼容。")
        rows = page["items"]
        if any(not isinstance(r, dict) or not isinstance(r.get("requestId"), str) for r in rows):
            raise BillingError("invalid", "逐次账单记录格式不兼容。")
        result["records"] = [billing_record(row) for row in rows[:20]]
        result["has_more"] = bool(page.get("nextCursor")) or len(rows) > 20
        succeeded += 1
    except BillingError as error:
        errors.append(str(error))
    result["status"] = "ok" if not errors else ("partial" if succeeded else "error")
    result["message"] = "；".join(errors)
    return result
