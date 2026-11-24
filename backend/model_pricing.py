"""Public model prices and explicitly labelled token-cost estimates; no admin credential."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlparse, urlunparse
from urllib.request import ProxyHandler, Request, build_opener

from gateway_billing import NoRedirect

PRICE_KEYS = ("prompt", "completion", "cache_read", "cache_write")
OFFICIAL_DS = "https://api-docs.deepseek.com/quick_start/pricing"
CATALOG_TTL = 6 * 3600
RATIO_TTL = 60


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        value = Decimal(str(value))
        return value if value.is_finite() and value >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def amount(value):
    return format(value, "f") if value is not None else None


def model_key(name):
    return re.sub(r"[._]", "-", name.lower().rsplit("/", 1)[-1])


def catalog_models(data):
    """CCHP public control-plane catalog: prices are base prices, not group-adjusted."""
    rows = data.get("models") if isinstance(data, dict) else None
    if not isinstance(rows, list):
        raise ValueError("未返回模型价格目录")
    models = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get("modelName"), str):
            continue
        charges = row.get("charges")
        if not isinstance(charges, dict) or row.get("hasPricing") is False:
            continue
        normalized = {}
        for key, charge in charges.items():
            if isinstance(charge, dict) and charge.get("unit") == "per_M_tokens":
                value = number(charge.get("price"))
                if value is not None:
                    normalized[key] = amount(value)
        if "prompt" not in normalized or "completion" not in normalized:
            continue
        tracks = []
        for track in row.get("tracks") or []:
            if not isinstance(track, dict):
                continue
            factors = {k: amount(n) for k, v in (track.get("chargeFactors") or {}).items()
                       if (n := number(v)) is not None}
            tracks.append({"label": str(track.get("label") or ""), "factors": factors,
                           "triggers": track.get("triggers") or []})
        models[row["modelName"]] = {"model": row["modelName"], "charges": normalized,
                                     "tracks": tracks, "source": str(row.get("source") or "gateway"),
                                     "group_ids": row.get("availableGroupIds") or []}
    if not models:
        raise ValueError("目录未提供可计算的文本模型单价")
    return models


class PriceTables(HTMLParser):
    def __init__(self):
        super().__init__()
        self.depth, self.cell, self.row, self.rows = 0, None, [], []

    def handle_starttag(self, tag, attrs):
        if tag == "table": self.depth += 1
        if self.depth and tag == "tr": self.row = []
        if self.depth and tag in ("th", "td"): self.cell = []

    def handle_endtag(self, tag):
        if self.depth and tag in ("th", "td") and self.cell is not None:
            self.row.append(" ".join(self.cell)); self.cell = None
        if self.depth and tag == "tr": self.rows.append(self.row)
        if tag == "table": self.depth -= 1

    def handle_data(self, value):
        if self.cell is not None: self.cell.append(value.strip())


def deepseek_prices(html):
    """Read model IDs and price ranges from the official table, never a fuzzy alias."""
    parser = PriceTables(); parser.feed(html)
    names, result, current = [], {}, None
    for row in parser.rows:
        if row and row[0].strip().upper() == "MODEL":
            names = [re.sub(r"\s*\(\d+\).*", "", cell).strip() for cell in row[1:]]
            if any(not re.fullmatch(r"deepseek-[a-z0-9.-]+", name) for name in names):
                raise ValueError("DeepSeek 官方模型表头已改变")
            result = {name: {"model": name, "charges": {}, "upper_charges": {},
                             "tracks": [], "source": "DeepSeek 官方", "group_ids": []} for name in names}
        title = " ".join(" ".join(row).upper().split())
        if "1M INPUT TOKENS (CACHE HIT)" in title: current = "cache_read"
        elif "1M INPUT TOKENS (CACHE MISS)" in title: current = "prompt"
        elif "1M OUTPUT TOKENS" in title: current = "completion"
        prices = re.findall(r"\$\s*([0-9]+(?:\.[0-9]+)?)", " ".join(row))
        if current and names and len(prices) == len(names):
            for name, price in zip(names, prices):
                value = number(price)
                previous = number(result[name]["charges"].get(current))
                high = number(result[name]["upper_charges"].get(current))
                result[name]["charges"][current] = amount(min(previous, value) if previous is not None else value)
                result[name]["upper_charges"][current] = amount(max(high, value) if high is not None else value)
        if current and row and any(term in row[0].upper() for term in ("CONCURRENCY", "ITEM", "API")):
            current = None
    result = {name: row for name, row in result.items() if all(k in row["charges"] for k in ("prompt", "completion", "cache_read"))}
    if not result: raise ValueError("DeepSeek 官方单价表格式已改变")
    return result


def read_json(path):
    try:
        if path.is_symlink(): return None
        value = json.loads(path.read_text())
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def public_get(url):
    parsed = urlparse(url)
    local = parsed.hostname in ("localhost", "127.0.0.1", "::1")
    if "@" in parsed.netloc or parsed.query or parsed.fragment or (parsed.scheme != "https" and not (local and parsed.scheme == "http")):
        raise ValueError("价格来源需要 HTTPS，或本机 HTTP；地址不能带凭据")
    req = Request(url, headers={"Accept": "application/json,text/html", "User-Agent": "AgentWorkbench-PublicPricing/1.0"})
    # Public requests never carry a model key, management token, or session content.
    with build_opener(ProxyHandler({}), NoRedirect()).open(req, timeout=15) as response:
        value = response.read(8_000_001)
    if len(value) > 8_000_000: raise ValueError("公开价格表超过大小限制")
    return value


def source_candidates(gateway, override=""):
    if override: return [override]
    parsed = urlparse(gateway)
    candidates = [gateway.rstrip("/").removesuffix("/v1") + "/api/public/catalog/models"]
    # The normal local CCHP deployment publishes control and gateway on adjacent ports.
    # It is only a discovery candidate; any saved custom URL takes precedence.
    if parsed.hostname in ("localhost", "127.0.0.1", "::1") and parsed.port and parsed.port > 1:
        host = "[::1]" if parsed.hostname == "::1" else parsed.hostname
        candidates.append(urlunparse((parsed.scheme, host + ":" + str(parsed.port - 1), "/api/public/catalog/models", "", "", "")))
    return candidates


def load_catalog(gateway, cache_dir, write, force=False, override="", online=True):
    cache_key = hashlib.sha256((gateway + "|" + override).encode()).hexdigest()[:20]
    path = Path(cache_dir) / ("catalog-" + cache_key + ".json")
    cached = read_json(path)
    if cached and (not online or not force and time.time() - cached.get("fetched_at", 0) < CATALOG_TTL):
        return {**cached, "stale": time.time() - cached.get("fetched_at", 0) >= CATALOG_TTL}
    if online:
        for url in source_candidates(gateway, override):
            try:
                models = catalog_models(json.loads(public_get(url), parse_float=Decimal))
                result = {"models": models, "source_url": url, "source_kind": "gateway",
                          "fetched_at": int(time.time()), "stale": False}
                write(path, json.dumps(result, ensure_ascii=False))
                return result
            except (OSError, ValueError, TypeError):
                continue
    if cached:
        return {**cached, "stale": True, "notice": "价格源暂不可达，使用上次缓存"}
    seed = read_json(Path(__file__).with_name("pricing_reference.json")) or {"models": {}}
    return {**seed, "stale": True, "source_kind": "reference",
            "notice": "未连接网关公开价目表，使用随应用保存的参考价；可设置价格来源"}


def official_catalog(cache_dir, write, force=False, online=True):
    path = Path(cache_dir) / "deepseek-official.json"
    cached = read_json(path)
    if cached and (not online or not force and time.time() - cached.get("fetched_at", 0) < 86400):
        return {**cached, "stale": time.time() - cached.get("fetched_at", 0) >= 86400}
    if online:
        try:
            models = deepseek_prices(public_get(OFFICIAL_DS).decode("utf-8"))
            result = {"models": models, "source_url": OFFICIAL_DS, "source_kind": "official",
                      "fetched_at": int(time.time()), "stale": False}
            write(path, json.dumps(result, ensure_ascii=False)); return result
        except (OSError, ValueError, TypeError):
            cached = cached or {}
    seed = cached or read_json(Path(__file__).with_name("deepseek_reference.json")) or {"models": {}}
    return {**seed, "stale": True}


def find_price(models, model):
    if model in models: return models[model]
    matches = [value for key, value in models.items() if model_key(key) == model_key(model)]
    return matches[0] if len(matches) == 1 else None


def quote_for(catalog, model, ratio=None):
    row = find_price(catalog.get("models", {}), model)
    if not row: return None
    factor = number(ratio)
    rates = row["charges"]
    result = {**row, "model": model, "source_url": catalog.get("source_url"),
              "source_kind": catalog.get("source_kind", "reference"),
              "fetched_at": catalog.get("fetched_at"), "stale": catalog.get("stale", False),
              "ratio": amount(factor), "base_charges": rates}
    result["is_gateway_price"] = result["source_kind"] == "gateway" and factor is not None
    result["charges"] = {key: amount(number(value) * factor) for key, value in rates.items()} if factor is not None else rates
    if row.get("upper_charges") and factor is not None:
        result["upper_charges"] = {key: amount(number(value) * factor) for key, value in row["upper_charges"].items()}
    result["basis"] = ("网关目录 × 分组倍率" if factor is not None and result["source_kind"] == "gateway"
                       else "DeepSeek 官方参考价" + (" × 分组倍率" if factor not in (None, Decimal(1)) else "") if result["source_kind"] == "official"
                       else "参考单价" + (" × 分组倍率" if factor is not None else "（倍率未知）"))
    return result


def estimate_tokens(counts, quote, context_known=False, upper=False):
    rates = quote.get("upper_charges") if upper and quote.get("upper_charges") else quote["charges"]
    incoming, outgoing = counts.get("input_tokens"), counts.get("output_tokens")
    if not isinstance(incoming, int) or not isinstance(outgoing, int): return None
    cached = counts.get("cached_input_tokens") or 0
    created = counts.get("cache_creation_input_tokens") or 0
    if cached + created > incoming: return None
    factors = {}
    # Only token-context tracks can be inferred from usage. Batch/priority/tool fees
    # stay outside this estimate and are disclosed in the UI, never guessed.
    for track in sorted(quote.get("tracks") or [], key=lambda t: len(t.get("triggers") or []), reverse=True):
        triggers = track.get("triggers") or []
        if any(not isinstance(t, dict) or t.get("kind") != "input_tokens_above" for t in triggers): continue
        if triggers and not context_known: continue
        if all(incoming >= t.get("threshold", 0) if t.get("inclusive") else incoming > t.get("threshold", 0) for t in triggers):
            factors = track.get("factors") or {}; break
    def rate(key, fallback):
        value = number(rates.get(key))
        if value is None: value = number(rates.get(fallback))
        factor = number(factors.get(key))
        if upper and not context_known:
            possible = [number(t.get("factors", {}).get(key)) for t in quote.get("tracks") or []
                        if all(isinstance(c, dict) and c.get("kind") == "input_tokens_above" for c in t.get("triggers") or [])]
            factor = max([v for v in possible if v is not None] + [factor or Decimal(1)])
        return value * (factor if factor is not None else Decimal(1)) if value is not None else None
    quantities = (("prompt", incoming-cached-created, "prompt"), ("cache_read", cached, "prompt"),
                  ("cache_write", created, "prompt"), ("completion", outgoing, "completion"))
    total = Decimal(0)
    for key, quantity, fallback in quantities:
        if not quantity: continue
        price = rate(key, fallback)
        if price is None: return None
        total += Decimal(quantity) * price / Decimal(1_000_000)
    return total


def apply_usage_pricing(snapshot, resolve):
    costs, uppers, unpriced = [], [], 0
    for session in snapshot["sessions"]:
        records = session.pop("_pricing_records", [])
        remainder = session.pop("_pricing_remainder", {})
        session["cost_source"] = "client" if session.get("estimated_cost") is not None else "pending"
        reported = session.get("estimated_cost")
        if reported is not None:
            session["client_estimated_cost"] = reported
        quote = resolve(session, session.get("model", ""))
        if quote is None:
            session.pop("_pricing_contexts", None)
            if reported is not None:
                costs.append(Decimal(str(reported))); uppers.append(costs[-1]); continue
            session["pricing_note"] = "未匹配模型单价，可设置价格来源"; unpriced += 1; continue
        total, high = Decimal(0), Decimal(0)
        if not records or session.get("counts_from_cumulative"):
            records = [{**session, "context_known": False}]
        elif any(remainder.get(key, 0) for key in ("input_tokens", "output_tokens")):
            records.append({**remainder, "model": session["model"], "context_known": False})
        quotes = []
        valid = True
        for record in records:
            record_quote = resolve(session, record.get("model") or session["model"])
            if not record_quote: valid = False; break
            low_cost = estimate_tokens(record, record_quote, record.get("context_known", False))
            high_cost = estimate_tokens(record, record_quote, record.get("context_known", False), upper=True)
            if low_cost is None or high_cost is None: valid = False; break
            total += low_cost; high += high_cost
            quotes.append(record_quote)
        session.pop("_pricing_contexts", None)
        if not valid:
            if reported is not None:
                costs.append(Decimal(str(reported))); uppers.append(costs[-1]); continue
            session["pricing_note"] = "用量或模型价格不完整"; unpriced += 1; continue
        session.update(estimated_cost=float(total), estimated_cost_upper=float(high),
                       cost_source="catalog" if all(q.get("is_gateway_price") for q in quotes) else "reference",
                       pricing_note="；".join(sorted({q["basis"] for q in quotes})), price_source_url=quote.get("source_url"),
                       price_fetched_at=quote.get("fetched_at"), price_ratio=quote.get("ratio"),
                       price_stale=quote.get("stale", False))
        costs.append(total); uppers.append(high)
    snapshot["estimated_cost"] = float(sum(costs)) if costs else None
    snapshot["estimated_cost_upper"] = float(sum(uppers)) if uppers else None
    snapshot["unpriced_sessions"] = unpriced
    snapshot["priced_sessions"] = len(costs)
    snapshot["pricing_notice"] = "按当前单价重估历史 Token，并非历史实扣；缓存按日志拆分，缺缓存明细时按普通输入计价。累计差额按末次模型估算，未含工具费及无法识别的服务档位。"
    sources = {s.get("cost_source") for s in snapshot["sessions"] if s.get("estimated_cost") is not None}
    status = ("reference_estimate" if "reference" in sources else "mixed_estimate" if len(sources) > 1
              else "catalog_estimate" if "catalog" in sources else "client_estimate")
    snapshot["pricing_status"] = (("partial_" if unpriced else "") + status) if costs else "pending_price"
    return snapshot


def apply_upstream_usage(snapshot):
    """Only explicit upstream USD cost/rates; do not substitute external prices."""
    from usage_records import timeline
    for session in snapshot["sessions"]:
        records = session.pop("_pricing_records", [])
        session.pop("_pricing_contexts", None)
        session.pop("_pricing_remainder", None)
        if not records:
            records = [{**session, "date": "", "context_known": False}]
        for row in records:
            reported = number(row.get("upstream_cost_usd"))
            row["cost_source"] = "upstream" if reported is not None else "unavailable"
            cost = reported
            if cost is None and row.get("unit_prices"):
                quote = row["unit_prices"][0]
                rates = quote.get("charges") or {}
                # Require each used category's explicit rate, including cache.
                categories = (("prompt", (row.get("input_tokens") or 0) - (row.get("cached_input_tokens") or 0) - (row.get("cache_creation_input_tokens") or 0)),
                              ("completion", row.get("output_tokens") or 0),
                              ("cache_read", row.get("cached_input_tokens") or 0),
                              ("cache_write", row.get("cache_creation_input_tokens") or 0))
                if row.get("input_tokens") is not None and row.get("output_tokens") is not None and all(n >= 0 and (not n or number(rates.get(key)) is not None) for key, n in categories):
                    cost = sum((Decimal(n) * number(rates[key]) / 1_000_000 for key, n in categories if n), Decimal(0))
                    row["cost_source"] = "upstream_rates"
            row["estimated_cost"] = float(cost) if cost is not None else None
            row["unpriced_records"] = int(cost is None)
        session["timeline"] = timeline(records, by_model=True)
        session["usage_records"] = records
        costs = [r["estimated_cost"] for r in records if r["estimated_cost"] is not None]
        sources = {r["cost_source"] for r in records if r["estimated_cost"] is not None}
        source = "upstream_rates" if "upstream_rates" in sources else "upstream"
        missing = sum(r["unpriced_records"] for r in records)
        session.update(estimated_cost=sum(costs) if costs else None, estimated_cost_upper=None,
                       cost_source=("partial_" if missing else "") + source if costs else "unavailable",
                       unpriced_records=missing,
                       pricing_note="只显示上游明确提供的费用或单价；没有则显示未提供")
    snapshot["timeline"] = timeline(r for s in snapshot["sessions"] for r in s["timeline"])
    snapshot["usage_records"] = [r for s in snapshot["sessions"] for r in s["usage_records"]]
    snapshot["schema_version"] = 2
    costs = [s["estimated_cost"] for s in snapshot["sessions"] if s["estimated_cost"] is not None]
    missing = sum(s["unpriced_records"] for s in snapshot["sessions"])
    source = "upstream_rates" if any("rates" in s["cost_source"] for s in snapshot["sessions"]) else "upstream"
    snapshot.update(estimated_cost=sum(costs) if costs else None, estimated_cost_upper=None,
                    pricing_status=("partial_" if missing else "") + source if costs else "unavailable",
                    priced_sessions=sum(s["unpriced_records"] == 0 for s in snapshot["sessions"]),
                    unpriced_sessions=sum(s["unpriced_records"] > 0 for s in snapshot["sessions"]),
                    pricing_notice="费用只显示日志明确保留的上游 USD 金额，或按上游明确单价计算；未提供不套参考价。CLI 未保留的信息无法追溯。日期按 UTC，日期未知仅纳入全部。缓存／推理已含在输入／输出中。")
    return snapshot
