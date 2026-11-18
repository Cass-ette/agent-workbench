"""Read-only usage summaries. No keys, conversation text, or pricing guesses leave this module."""

import json
import math
import os
import re
import time
from pathlib import Path

COUNTERS = ("input_tokens", "output_tokens", "cached_input_tokens",
            "cache_creation_input_tokens", "reasoning_output_tokens")


def _integer(value):
    return value if type(value) is int and 0 <= value <= 2**53 - 1 else None


def _usage(value, client):
    if not isinstance(value, dict):
        return None
    incoming, outgoing = _integer(value.get("input_tokens")), _integer(value.get("output_tokens"))
    if incoming is None or outgoing is None:
        return None
    cached = _integer(value.get("cache_read_input_tokens" if client == "claude" else "cached_input_tokens"))
    created = _integer(value.get("cache_creation_input_tokens" if client == "claude" else "cache_write_input_tokens"))
    if client == "claude":
        # Anthropic's input_tokens excludes the two cache categories; Codex's includes them.
        incoming += (cached or 0) + (created or 0)
    return dict(zip(COUNTERS, (incoming, outgoing, cached, created,
                              _integer(value.get("reasoning_output_tokens")))))


def _merge_max(previous, current):
    if previous is None:
        return current
    return {key: max(a, b) if a is not None and b is not None else a if b is None else b
            for key in COUNTERS for a, b in [(previous.get(key), current.get(key))]}


def _sum(rows):
    rows = list(rows)
    return {key: sum(known) if known else None for key in COUNTERS
            for known in [[row[key] for row in rows if row.get(key) is not None]]}


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
    if client != "codex":
        return {}
    parts = path.relative_to(root).parts
    result = {"profile_id": parts[0]} if parts else {}
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
                        result[target] = json.loads(match[1]).removesuffix("/v1").rstrip("/")
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
            imported, manifest = branch_usage_context(branch, path) if branch else (False, {})
            if imported:
                continue
            context = _context(path, Path(root), client) if include_pricing else {}
            if include_pricing and manifest:
                context.update({key: manifest[key] for key in ("profile_id", "gateway") if isinstance(manifest.get(key), str)})
            inherited_through = str(manifest.get("inherited_through") or "")
            session_id, model, workspace = path.stem, context.get("model", ""), ""
            records, cumulative, reported_cost = {}, None, None
            record_models = {}
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
                        if inherited_through and str(row.get("timestamp") or "") <= inherited_through:
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
                            usage = _usage(payload.get("usage"), client)
                            identity = str(payload.get("response_id") or (str(payload.get("turn_id")) + ":" + str(row.get("ordinal", ordinal))))
                        elif client == "codex" and kind == "event_msg" and payload.get("type") == "token_count":
                            info = payload.get("info")
                            candidate = _usage(info.get("total_token_usage"), client) if isinstance(info, dict) else None
                            if candidate:
                                cumulative = _merge_max(cumulative, candidate)
                                updated = max(updated, str(row.get("timestamp") or ""))
                        if usage:
                            records[identity] = _merge_max(records.get(identity), usage)
                            record_models[identity] = model
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
                                            "workspace": workspace, "updated_at": updated,
                                            "records": {}, "cumulative": None,
                                            "record_models": {}, "contexts": [],
                                            "estimated_cost": None})
            for identity, usage in records.items():
                item["records"][identity] = _merge_max(item["records"].get(identity), usage)
                item["record_models"][identity] = record_models.get(identity) or item["record_models"].get(identity, "")
            if context and context not in item["contexts"]:
                item["contexts"].append(context)
            if cumulative:
                item["cumulative"] = _merge_max(item["cumulative"], cumulative)
            if reported_cost is not None:
                item["estimated_cost"] = max(item["estimated_cost"] or 0.0, reported_cost)
            item.update(model=model or item["model"], workspace=workspace or item["workspace"],
                        updated_at=max(item["updated_at"], updated))
    result = []
    for item in sessions.values():
        records, cumulative = item.pop("records"), item.pop("cumulative")
        models, contexts = item.pop("record_models"), item.pop("contexts")
        counts = _sum(records.values())
        detailed_counts = counts.copy()
        if cumulative:
            counts = _merge_max(counts, cumulative)
        item.update(counts)
        item["requests"] = len(records)
        item["counts_from_cumulative"] = cumulative is not None and not records
        if include_pricing:
            item["_pricing_records"] = [{**value, "model": models.get(identity) or item["model"], "context_known": True}
                                        for identity, value in records.items()]
            item["_pricing_contexts"] = contexts
            item["_pricing_remainder"] = {key: max(0, (counts.get(key) or 0) - (detailed_counts.get(key) or 0))
                                          for key in COUNTERS}
        result.append(item)
    result.sort(key=lambda item: (item["updated_at"], item["id"]), reverse=True)
    total = _sum(result)
    costs = [item["estimated_cost"] for item in result if item["estimated_cost"] is not None]
    total.update(requests=sum(item["requests"] for item in result),
                 by_source={client: sum(item["requests"] for item in result if item["client"] == client)
                            for client in ("claude", "codex")},
                 estimated_cost=sum(costs) if costs else None,
                 pricing_status="client_estimate" if costs and len(costs) == len(result) else "partial_estimate" if costs else "unavailable",
                 sessions=result, unreadable_files=unreadable, scanned_at=int(time.time()))
    return total
