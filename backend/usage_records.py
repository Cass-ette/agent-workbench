"""Read-only usage summaries. No keys, conversation text, or pricing guesses leave this module."""

import json
import hashlib
import copy
import math
import os
import re
import time
import tempfile
from datetime import datetime, timezone
from pathlib import Path

COUNTERS = ("input_tokens", "output_tokens", "cached_input_tokens",
            "cache_creation_input_tokens", "reasoning_output_tokens")


def _integer(value):
    return value if type(value) is int and 0 <= value <= 2**53 - 1 else None


def _usage(value, client):
    if not isinstance(value, dict):
        return None
    incoming = _integer(value.get("input_tokens", value.get("prompt_tokens")))
    outgoing = _integer(value.get("output_tokens", value.get("completion_tokens")))
    if incoming is None or outgoing is None:
        return None
    cached = _integer(value.get("cache_read_input_tokens" if client == "claude" else "cached_input_tokens"))
    created = _integer(value.get("cache_creation_input_tokens" if client == "claude" else "cache_write_input_tokens"))
    details = value.get("input_tokens_details", value.get("prompt_tokens_details")) or {}
    output_details = value.get("output_tokens_details", value.get("completion_tokens_details")) or {}
    if client != "claude" and isinstance(details, dict):
        cached = cached if cached is not None else _integer(details.get("cached_tokens"))
        created = created if created is not None else _integer(details.get("cache_write_tokens"))
    if client == "claude":
        # Anthropic's input_tokens excludes the two cache categories; Codex's includes them.
        incoming += (cached or 0) + (created or 0)
    return dict(zip(COUNTERS, (incoming, outgoing, cached, created,
                              _integer(value.get("reasoning_output_tokens", output_details.get("reasoning_tokens") if isinstance(output_details, dict) else None)))))


def _merge_max(previous, current):
    if previous is None:
        return current
    return {key: max(a, b) if a is not None and b is not None else a if b is None else b
            for key in COUNTERS for a, b in [(previous.get(key), current.get(key))]}


def _sum(rows):
    rows = list(rows)
    return {key: sum(known) if known else None for key in COUNTERS
            for known in [[row[key] for row in rows if row.get(key) is not None]]}


def _day(timestamp):
    """Use UTC dates; missing/invalid timestamps remain explicitly undated."""
    try:
        value = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        if value.tzinfo is None:
            return ""
        return value.astimezone(timezone.utc).date().isoformat()
    except (ValueError, TypeError):
        return ""


def _stamp(timestamp):
    try:
        value = datetime.fromisoformat(str(timestamp).replace("Z", "+00:00"))
        return value.astimezone(timezone.utc).isoformat() if value.tzinfo else ""
    except (ValueError, TypeError):
        return ""


def timeline(rows, by_model=False):
    groups = {}
    for row in rows:
        groups.setdefault((row.get("date", ""), row.get("model", "") if by_model else ""), []).append(row)
    result = []
    for (day, model), points in sorted(groups.items()):
        costs = [p["estimated_cost"] for p in points if p.get("estimated_cost") is not None]
        prices = []
        for point in points:
            for price in point.get("unit_prices", []):
                if price not in prices:
                    prices.append(price)
        result.append({"date": day, **({"model": model} if by_model else {}), **_sum(points), "unit_prices": prices,
                       "requests": sum(p.get("requests", 0) for p in points),
                       "estimated_cost": sum(costs) if costs else None,
                       "unpriced_records": sum(p.get("unpriced_records", 0) for p in points)})
    return result


def _paths(root):
    if not root.is_dir() or root.is_symlink():
        return
    for directory, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(name for name in dirs if not (Path(directory) / name).is_symlink())
        for name in sorted(files):
            path = Path(directory) / name
            if path.suffix == ".jsonl" and not path.is_symlink():
                yield path


def _context(path, root, client):
    """Only return routing metadata, never config text or credentials."""
    from session_history import gateway_from_api_base
    parts = path.relative_to(root).parts
    profile = parts[0] if parts and client == "codex" else ""
    if profile.startswith(("scope-", "branch-")) or re.fullmatch(r"[0-9a-f]{16,64}", profile):
        profile = ""
    result = {"profile_id": profile} if profile else {}
    for parent in path.parents:
        if parent == Path(root).parent.parent:
            break
        manifest = parent / "workbench-profile.json"
        if manifest.is_file() and not manifest.is_symlink():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    result.update({k: data[k] for k in ("profile_id", "profile_name", "gateway", "model") if isinstance(data.get(k), str)})
            except (OSError, ValueError, TypeError):
                result.update({})
            break
    if client != "codex":
        return result
    for parent in path.parents:
        if parent == Path(root).parent:
            break
        config = parent / "config.toml"
        if config.is_file() and not config.is_symlink():
            try:
                text = config.read_text()
                for key, target in (("base_url", "gateway"), ("model", "model")):
                    match = re.search(r'^' + key + r'\s*=\s*("[^"\n]*")', text, re.M)
                    if match:
                        value = json.loads(match[1])
                        result[target] = gateway_from_api_base(value) if target == "gateway" else value
            except (OSError, ValueError):
                result.update({})
            break
    return result


def scan_usage(claude_root, codex_root, include_pricing=False, claude_branches=()):
    from session_history import branch_usage_context

    sessions = {}
    unreadable = 0
    roots = [("claude", claude_root), ("codex", codex_root)]
    roots.extend(("claude", branch / "projects") for branch in claude_branches)
    for client, root in roots:
        root = Path(root)
        for path in _paths(root):
            if client == "codex" and "sessions" not in path.relative_to(root).parts:
                continue
            homes = (parent for parent in path.parents if parent.name.startswith("branch-") and parent.is_relative_to(root.parent))
            branch = next(homes, None)
            scope_home = next((parent for parent in path.parents if parent.name.startswith("scope-") and parent.is_relative_to(root.parent)), None)
            imported, manifest = branch_usage_context(branch, path) if branch else (False, {})
            if not manifest and scope_home:
                try:
                    manifest_path = scope_home / "workbench-profile.json"
                    if manifest_path.is_file() and not manifest_path.is_symlink():
                        data = json.loads(manifest_path.read_text(encoding="utf-8"))
                        manifest = data if isinstance(data, dict) else {}
                except (OSError, ValueError, TypeError):
                    manifest = {}
            if imported:
                continue
            context = _context(path, Path(root), client) if include_pricing else {}
            if include_pricing and manifest:
                context.update({key: manifest[key] for key in ("profile_id", "profile_name", "gateway") if isinstance(manifest.get(key), str)})
            inherited_through = str(manifest.get("inherited_through") or "")
            session_id, model, workspace = path.stem, "", ""
            records, cumulative, reported_cost = {}, None, None
            checkpoints, baseline = {}, None
            record_models, record_times, record_costs, record_prices = {}, {}, {}, {}
            record_profiles, record_workspaces = {}, {}
            updated = ""
            try:
                with path.open(encoding="utf-8", errors="replace") as stream:
                    for ordinal, line in enumerate(stream):
                        try:
                            row = json.loads(line)
                        except (ValueError, TypeError):
                            continue  # Includes an unfinished last line while the CLI is writing.
                        if not isinstance(row, dict):
                            continue
                        kind = row.get("type")
                        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                        if kind == "session_meta":
                            session_id = str(payload.get("id") or payload.get("session_id") or session_id)
                            workspace = str(payload.get("cwd") or workspace)
                        if client == "claude":
                            session_id = str(row.get("sessionId") or session_id)
                            workspace = str(row.get("cwd") or workspace)
                        if kind == "turn_context":
                            workspace = str(payload.get("cwd") or workspace)
                            if isinstance(payload.get("model"), str):
                                model = payload["model"]
                        if inherited_through and str(row.get("timestamp") or "") <= inherited_through:
                            payload = row.get("payload") or {}
                            if row.get("type") == "event_msg" and isinstance(payload, dict) and payload.get("type") == "token_count":
                                info = payload.get("info") or {}
                                candidate = _usage(info.get("total_token_usage"), client) if isinstance(info, dict) else None
                                if candidate:
                                    baseline = _merge_max(baseline, candidate)
                            continue
                        kind = row.get("type")
                        payload = row.get("payload")
                        payload = payload if isinstance(payload, dict) else {}
                        if kind == "session_meta":
                            session_id = str(payload.get("id") or payload.get("session_id") or session_id)
                            workspace = str(payload.get("cwd") or workspace)
                        if client == "claude":
                            session_id = str(row.get("sessionId") or session_id)
                            workspace = str(row.get("cwd") or workspace)
                        if kind == "turn_context" and isinstance(payload.get("model"), str):
                            model = payload["model"]
                        message = row.get("message")
                        message = message if isinstance(message, dict) else {}
                        usage = None
                        if client == "claude" and kind == "assistant":
                            usage = _usage(message.get("usage"), client)
                            model = str(message.get("model") or model)
                            identity = str(message.get("id") or row.get("uuid") or ordinal)
                        elif client == "codex" and kind == "token_usage_record":
                            if isinstance(payload.get("model"), str):
                                model = payload["model"]
                            usage = _usage(payload.get("usage"), client)
                            identity = str(payload.get("response_id") or (str(payload.get("turn_id")) + ":" + str(row.get("ordinal", ordinal))))
                        elif client == "codex" and kind == "event_msg" and payload.get("type") == "token_count":
                            info = payload.get("info")
                            candidate = _usage(info.get("total_token_usage"), client) if isinstance(info, dict) else None
                            if candidate:
                                if baseline:
                                    candidate = {k: max(0, v - (baseline.get(k) or 0)) if v is not None else None for k, v in candidate.items()}
                                cumulative = _merge_max(cumulative, candidate)
                                stamp = _stamp(row.get("timestamp"))
                                checkpoints[stamp] = {**_merge_max(checkpoints.get(stamp), candidate), "model": model, "project_path": workspace}
                                updated = max(updated, str(row.get("timestamp") or ""))
                        if usage:
                            records[identity] = _merge_max(records.get(identity), usage)
                            record_models[identity] = model
                            record_times[identity] = str(row.get("timestamp") or "")
                            record_workspaces[identity] = workspace
                            record_profiles.setdefault(identity, set()).add((context.get("profile_id", ""), context.get("profile_name", "")))
                            # Only a field explicitly identified as upstream USD is a receipt.
                            # CLI total_cost_usd is a separate client estimate.
                            source = payload if client == "codex" else message
                            billing = source.get("billing") if isinstance(source.get("billing"), dict) else {}
                            cost = next((source.get(name) for name in ("upstream_cost_usd", "cost_usd") if source.get(name) is not None), billing.get("cost_usd"))
                            if type(cost) in (int, float) and math.isfinite(cost) and cost >= 0:
                                record_costs[identity] = float(cost)
                            raw_prices = source.get("upstream_prices") or source.get("upstream_prices_usd_per_million") or billing.get("unit_prices")
                            currency = source.get("currency") or billing.get("currency")
                            unit = source.get("unit") or billing.get("unit")
                            if isinstance(raw_prices, dict) and currency == "USD" and unit in ("per_M_tokens", "USD_per_1M_tokens"):
                                names = {"prompt": "prompt", "input": "prompt", "input_tokens": "prompt",
                                         "completion": "completion", "output": "completion", "output_tokens": "completion",
                                         "cache_read": "cache_read", "cache_read_input_tokens": "cache_read",
                                         "cache_write": "cache_write", "cache_creation_input_tokens": "cache_write"}
                                rates = {names[k]: v for k, v in raw_prices.items() if k in names
                                         and type(v) in (int, float) and math.isfinite(v) and v >= 0}
                                if rates:
                                    record_prices[identity] = {"model": model, "currency": currency, "unit": "per_M_tokens", "charges": rates, "source": "upstream_log"}
                            updated = max(updated, str(row.get("timestamp") or ""))
                        # Claude's result is a client-side USD estimate, never a gateway billing receipt.
                        if kind == "result":
                            cost = row.get("total_cost_usd")
                            if type(cost) in (int, float) and math.isfinite(cost) and cost >= 0:
                                reported_cost = max(reported_cost or 0.0, cost)
            except OSError:
                unreadable += 1
                continue
            if not records and cumulative is None:
                continue
            key = client + ":" + session_id
            item = sessions.setdefault(key, {"id": key, "client": client, "model": model,
                                            "workspace": workspace, "profile_id": context.get("profile_id", ""),
                                            "profile_name": context.get("profile_name", ""), "updated_at": updated,
                                            "records": {}, "cumulative": None,
                                            "record_models": {}, "record_times": {}, "record_costs": {}, "record_prices": {}, "record_profiles": {}, "record_workspaces": {},
                                            "profiles": set(), "contexts": [], "checkpoints": {},
                                            "estimated_cost": None})
            for identity, usage in records.items():
                item["records"][identity] = _merge_max(item["records"].get(identity), usage)
                item["record_models"][identity] = record_models.get(identity) or item["record_models"].get(identity, "")
                item["record_times"][identity] = max(record_times.get(identity, ""), item["record_times"].get(identity, ""))
                item["record_workspaces"][identity] = record_workspaces.get(identity) or item["record_workspaces"].get(identity, "")
                if identity in record_costs:
                    item["record_costs"][identity] = record_costs[identity]
                price = record_prices.get(identity)
                if price:
                    item["record_prices"][identity] = price
                item["record_profiles"].setdefault(identity, set()).update(record_profiles.get(identity, set()))
            if context.get("profile_id"):
                item["profiles"].add((context.get("profile_id", ""), context.get("profile_name", "")))
            if context and context not in item["contexts"]:
                item["contexts"].append(context)
            if cumulative:
                item["cumulative"] = _merge_max(item["cumulative"], cumulative)
            for stamp, value in checkpoints.items():
                item["checkpoints"][stamp] = {**_merge_max(item["checkpoints"].get(stamp), value), "model": value["model"], "project_path": value["project_path"]}
            if reported_cost is not None:
                item["estimated_cost"] = max(item["estimated_cost"] or 0.0, reported_cost)
            item.update(model=model or item["model"], workspace=workspace or item["workspace"],
                        profile_id=context.get("profile_id") or item["profile_id"],
                        profile_name=context.get("profile_name") or item["profile_name"],
                        updated_at=max(item["updated_at"], updated))
    result = []
    for item in sessions.values():
        records, cumulative = item.pop("records"), item.pop("cumulative")
        models, record_times, contexts = item.pop("record_models"), item.pop("record_times"), item.pop("contexts")
        checkpoints = item.pop("checkpoints")
        record_costs = item.pop("record_costs")
        record_prices = item.pop("record_prices")
        record_profiles = item.pop("record_profiles")
        record_workspaces = item.pop("record_workspaces")
        profiles = item.pop("profiles")
        item["profile_id"], item["profile_name"] = next(iter(profiles)) if len(profiles) == 1 else ("", "")
        counts = _sum(records.values())
        detailed_counts = counts.copy()
        if cumulative:
            counts = _merge_max(counts, cumulative)
        item.update(counts)
        item["requests"] = len(records)
        item["counts_from_cumulative"] = cumulative is not None and not records
        points = [{**value, "record_id": item["id"] + ":" + identity, "session_id": item["id"], "client": item["client"],
                   "project_path": record_workspaces.get(identity, ""),
                   "profile_id": next(iter(record_profiles.get(identity, set())))[0] if len(record_profiles.get(identity, set())) == 1 else "",
                   "profile_name": next(iter(record_profiles.get(identity, set())))[1] if len(record_profiles.get(identity, set())) == 1 else "",
                   "model": models.get(identity, ""),
                   "timestamp": _stamp(record_times.get(identity)),
                   "date": _day(record_times.get(identity)), "requests": 1, "context_known": True,
                   "upstream_cost_usd": record_costs.get(identity),
                   "unit_prices": [record_prices[identity]] if identity in record_prices else []}
                  for identity, value in records.items()]
        if not records and checkpoints:
            previous = {k: 0 for k in COUNTERS}
            for stamp, value in sorted(checkpoints.items()):
                delta = {k: max(0, v - (previous.get(k) or 0)) if v is not None else None
                         for k, v in value.items() if k in COUNTERS}
                previous = _merge_max(previous, value)
                points.append({**delta, "record_id": item["id"] + ":cumulative:" + stamp, "session_id": item["id"], "client": item["client"],
                               "project_path": value["project_path"], "profile_id": item["profile_id"], "profile_name": item["profile_name"],
                               "model": value["model"], "timestamp": stamp, "date": _day(stamp), "context_known": False})
        elif cumulative:
            remainder = {k: max(0, (counts.get(k) or 0) - (detailed_counts.get(k) or 0)) for k in COUNTERS}
            if any(remainder.values()):
                points.append({**remainder, "record_id": item["id"] + ":cumulative-remainder", "session_id": item["id"], "client": item["client"],
                               "project_path": "", "profile_id": item["profile_id"], "profile_name": item["profile_name"],
                               "model": "", "date": "", "context_known": False})
        item["timeline"] = timeline(points, by_model=True)
        if include_pricing:
            item["_pricing_records"] = points
            item["_pricing_contexts"] = contexts
            item["_pricing_remainder"] = {}
        result.append(item)
    result.sort(key=lambda item: (item["updated_at"], item["id"]), reverse=True)
    total = _sum(result)
    costs = [item["estimated_cost"] for item in result if item["estimated_cost"] is not None]
    total.update(requests=sum(item["requests"] for item in result),
                 by_source={client: sum(item["requests"] for item in result if item["client"] == client)
                            for client in ("claude", "codex")},
                 estimated_cost=sum(costs) if costs else None,
                 pricing_status="client_estimate" if costs and len(costs) == len(result) else "partial_estimate" if costs else "unavailable",
                 sessions=result,
                 timeline=timeline(row for item in result for row in item["timeline"]),
                 unreadable_files=unreadable, scanned_at=int(time.time()))
    return total


def scan_cached(claude_root, codex_root, cache_dir, claude_branches=()):
    """Cache sanitized usage only. Logs, configs and credentials stay untouched."""
    branches = list(claude_branches)
    roots = [Path(claude_root), Path(codex_root), *map(Path, branches)]

    def fingerprint():
        digest = hashlib.sha256(b"workbench-usage-v4")
        for root in roots:
            digest.update(str(root).encode())
            if not root.is_dir() or root.is_symlink():
                continue
            for directory, dirs, files in os.walk(root, followlinks=False):
                dirs[:] = sorted(d for d in dirs if not (Path(directory) / d).is_symlink())
                for name in sorted(files):
                    if not (name.endswith(".jsonl") or name in ("config.toml", "workbench-profile.json")):
                        continue
                    path = Path(directory) / name
                    if path.is_symlink():
                        continue
                    try:
                        stat = path.stat()
                        digest.update(str((path, stat.st_size, stat.st_mtime_ns)).encode())
                    except OSError:
                        digest.update(str(path).encode())
        return digest.hexdigest()

    before = fingerprint()
    requested_folder = Path(cache_dir)
    try:
        folder = requested_folder.resolve()
    except OSError:
        folder = requested_folder
    path = folder / "snapshot.json"
    safe = not requested_folder.is_symlink() and not path.is_symlink()
    if safe:
        try:
            cached = json.loads(path.read_text(encoding="utf-8"))
            if cached.get("signature") == before and isinstance(cached.get("snapshot"), dict):
                return {**cached["snapshot"], "scanned_at": int(time.time())}
        except (OSError, ValueError, TypeError, AttributeError):
            cached = None
    snapshot = scan_usage(claude_root, codex_root, include_pricing=True, claude_branches=branches)
    if safe and before == fingerprint():
        temporary = None
        try:
            folder.mkdir(parents=True, exist_ok=True, mode=0o700)
            os.chmod(folder, 0o700)
            fd, temporary = tempfile.mkstemp(prefix=".usage-", dir=folder)
            os.fchmod(fd, 0o600)
            clean_snapshot = copy.deepcopy(snapshot)
            for session in clean_snapshot.get("sessions", []):
                session.pop("_pricing_contexts", None)
                session.pop("_pricing_remainder", None)
                session.pop("estimated_cost", None)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump({"signature": before, "snapshot": clean_snapshot}, stream, ensure_ascii=False)
            os.replace(temporary, path)
        except OSError:
            # A read-only cache must never prevent usage from being displayed.
            temporary = temporary or None
        finally:
            if temporary:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    temporary = None
    return snapshot
