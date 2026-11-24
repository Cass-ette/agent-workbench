"""Explicit effort overrides; changing effort never changes a model or session identity."""
import json
import re

LEVELS = ("low", "medium", "high", "xhigh", "max")


def normalize(value):
    if value in (None, "", "default"):
        return ""
    if not isinstance(value, str) or value not in LEVELS:
        raise ValueError("思考强度无效；请选择客户端默认或明确的档位。")
    return value


def arguments(client, value):
    effort = normalize(value)
    if client not in ("codex", "claude"):
        raise ValueError("此终端不是 Codex 或 Claude。")
    if not effort:
        return []
    return ["-c", "model_reasoning_effort=" + json.dumps(effort)] if client == "codex" else ["--effort", effort]


def config_line(value, existing=""):
    effort = normalize(value)
    if not effort:
        match = re.search(r'(?m)^model_reasoning_effort\s*=\s*("[^"\n]*")\s*$', existing)
        if match:
            try:
                saved = json.loads(match[1])
                effort = saved if saved and len(saved) <= 80 and not any(ord(char) < 32 for char in saved) else ""
            except (ValueError, TypeError):
                effort = ""
    return "model_reasoning_effort = " + json.dumps(effort) + "\n" if effort else ""
