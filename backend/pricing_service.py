"""Join public prices and disclosed Key ratios. Reads metadata, never calls inference."""
import hashlib
import json
from pathlib import Path
import time
from urllib.parse import urlparse

from gateway_billing import account_snapshot
from model_pricing import (load_catalog, official_catalog, quote_for, read_json,
                           number, public_get, CATALOG_TTL)


class PricingService:
    def __init__(self, profiles, root, read_key, write, online=False, force=False):
        self.profiles = {p["id"]: p for p in profiles}
        self.root, self.read_key, self.write = Path(root), read_key, write
        self.online, self.force, self.contexts, self.catalogs = online, force, {}, {}
        self.official = official_catalog(self.root, write, online=False)

    def settings(self, profile):
        data = read_json(self.root / (profile["id"] + "-source.json")) or {}
        return data.get("url", "") if data.get("gateway") == profile["gateway"] else ""

    def context(self, profile_id):
        if profile_id in self.contexts:
            return self.contexts[profile_id]
        profile = self.profiles[profile_id]
        gateway, override = profile["gateway"], self.settings(profile)
        direct = urlparse(gateway).hostname == "api.deepseek.com"
        cache_id = (gateway, override)
        if direct:
            catalog = official_catalog(self.root, self.write, self.force, self.online)
            ratio, account = "1", {"status": "official", "ratio": "1"}
        else:
            if cache_id not in self.catalogs:
                self.catalogs[cache_id] = load_catalog(gateway, self.root, self.write, self.force, override, self.online)
            catalog = self.catalogs[cache_id]
            key = self.read_key(profile)
            fingerprint = hashlib.sha256((gateway + "|" + key).encode()).hexdigest()[:20]
            path = self.root / ("ratio-" + fingerprint + ".json")
            account = read_json(path) or {}
            if self.online and (self.force or time.time() - account.get("fetched_at", 0) >= 60):
                fresh = account_snapshot(gateway, key)
                if fresh.get("status") == "ok":
                    account = {**fresh, "fetched_at": int(time.time())}
                else:
                    # Do not silently reuse a previously visible ratio after it is hidden/revoked.
                    account = {**fresh, "fetched_at": int(time.time())}
                self.write(path, json.dumps(account, ensure_ascii=False))
            ratio = account.get("ratio")
        context = {"catalog": catalog, "ratio": ratio, "account": account,
                   "gateway": gateway, "profile_id": profile_id, "direct": direct}
        self.contexts[profile_id] = context
        return context

    def quote(self, profile_id, model):
        context = self.context(profile_id)
        quote = quote_for(context["catalog"], model, context["ratio"])
        if quote is None:
            quote = quote_for(self.official, model, context["ratio"])
        if quote:
            age = time.time() - context["account"].get("fetched_at", 0)
            if not context["direct"] and age > 120:
                quote["basis"] += "（倍率为缓存值）" if context["ratio"] is not None else "（倍率未公开）"
            quote["profile_id"] = profile_id
        return quote

    def resolve(self, session, model):
        routing = session.get("_pricing_contexts") or []
        if routing:
            quotes = []
            for route in routing:
                profile = self.profiles.get(route.get("profile_id"))
                if not profile or route.get("gateway", profile["gateway"]) != profile["gateway"]:
                    continue
                quote = self.quote(profile["id"], model)
                if quote:
                    quotes.append(quote)
            if quotes and len({json.dumps({"charges": q["charges"], "upper": q.get("upper_charges")}, sort_keys=True) for q in quotes}) == 1:
                confirmed = len(quotes) == len(routing) and all(q.get("is_gateway_price") for q in quotes)
                return {**quotes[0], "is_gateway_price": confirmed,
                        "basis": quotes[0]["basis"] + ("" if confirmed else "（历史售价未完全确认，仅供参考）")}
            # A saved route that no longer matches must not be reassigned to an
            # unrelated Key just because the user currently selected it.
            for profile in self.profiles.values():
                reference = quote_for(self.context(profile["id"])["catalog"], model)
                if reference:
                    return {**reference, "is_gateway_price": False, "basis": "基础价参考估算（历史网关或 Key 配置已改变）"}
            return None
        # Old Claude records do not retain a Key identity. Only use a shared rate if
        # all matching profiles publish the same charge; never apply the selected Key.
        quotes = []
        for profile_id in self.profiles:
            context = self.context(profile_id)
            quote = self.quote(profile_id, model)
            if not quote:
                continue
            group = context["account"].get("group_id")
            if group and quote.get("group_ids") and group not in quote["group_ids"]:
                continue
            quotes.append(quote)
        signatures = {json.dumps({"charges": q["charges"], "upper": q.get("upper_charges"), "ratio": q["ratio"]}, sort_keys=True)
                      for q in quotes}
        if quotes and len(signatures) == 1:
            confirmed = all(q.get("is_gateway_price") for q in quotes)
            return {**quotes[0], "is_gateway_price": confirmed,
                    "basis": quotes[0]["basis"] + ("（历史 Key 未记录，匹配配置同价）" if confirmed else "（含未确认售价，仅供参考）")}
        # Conflicting or absent routes: show a clearly labelled base reference.
        reference = quote_for(next(iter(self.contexts.values()))["catalog"], model) if self.contexts else None
        if reference:
            reference["basis"] = "基础价参考估算（历史 Key／倍率无法归属）"
            reference["is_gateway_price"] = False
        return reference

    def snapshot(self, profile_id, models):
        context = self.context(profile_id)
        catalog = context["catalog"]
        quotes = [q for name in sorted(set(models)) if (q := self.quote(profile_id, name))]
        return {"profile_id": profile_id, "gateway": context["gateway"],
                "source_url": catalog.get("source_url", ""), "source_override": self.settings(self.profiles[profile_id]),
                "source_kind": catalog.get("source_kind", "reference"), "fetched_at": catalog.get("fetched_at"),
                "stale": catalog.get("stale", False), "ratio": context["ratio"],
                "notice": catalog.get("notice", ""), "quotes": quotes,
                "missing_models": len(set(models)) - len(quotes),
                "disclaimer": "单位 USD／百万 Token。当前网关目录基础价 × 此 Key 公开倍率，只乘一次。按该售价估算 Token 费用，最终实扣以 CCHP 账单为准；参考值不代表网关承诺售价。"}
