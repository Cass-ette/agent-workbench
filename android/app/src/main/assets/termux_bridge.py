"""Loopback PTY host for the Android workbench. Python stdlib; no model calls here."""
import argparse
import base64
import fcntl
import hashlib
import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import pty
import re
import shlex
import shutil
import signal
import struct
import sys
import tempfile
import termios
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid

MAX_OUTPUT = 1024 * 1024
MAX_REQUEST = 64 * 1024
SENSITIVE_ENV = ("WORKBENCH_GATEWAY_KEY", "CCH_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
                 "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CODEX_HOME", "CLAUDE_CONFIG_DIR",
                 "ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
                 "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_SMALL_FAST_MODEL",
                 "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
                 "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
                 "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", "DISABLE_AUTOUPDATER", "CLAUDE_CODE_SIMPLE")
USAGE_COUNTERS = ("input_tokens", "output_tokens", "cached_input_tokens",
                  "cache_creation_input_tokens", "reasoning_output_tokens")


def _count(value):
    return value if type(value) is int and 0 <= value <= 2 ** 53 - 1 else None


def _usage(value, client):
    if not isinstance(value, dict):
        return None
    incoming, outgoing = _count(value.get("input_tokens")), _count(value.get("output_tokens"))
    if incoming is None or outgoing is None:
        return None
    cached = _count(value.get("cache_read_input_tokens" if client == "claude" else "cached_input_tokens"))
    created = _count(value.get("cache_creation_input_tokens" if client == "claude" else "cache_write_input_tokens"))
    if client == "claude":
        incoming += (cached or 0) + (created or 0)
    return dict(zip(USAGE_COUNTERS, (incoming, outgoing, cached, created,
                                     _count(value.get("reasoning_output_tokens")))))


def _merge_usage(previous, current):
    if previous is None:
        return current
    return {key: (max(a, b) if a is not None and b is not None else
                  a if b is None else b) for key in USAGE_COUNTERS
            for a, b in [(previous.get(key), current.get(key))]}


def _sum_usage(rows):
    rows = list(rows)
    return {key: (sum(values) if values else None) for key in USAGE_COUNTERS
            for values in [[row.get(key) for row in rows if row.get(key) is not None]]}


def usage_snapshot(home=None):
    """Read only usage fields from local CLI JSONL logs; never returns message text or keys."""
    root = Path(home or Path.home()) / ".local/share/agent-workbench"
    totals = {key: None for key in USAGE_COUNTERS}
    requests = 0
    estimated_cost = 0.0
    cost_records = 0
    files = 0
    if not root.is_dir():
        return {**totals, "requests": 0, "estimated_cost": None,
                "pricing_status": "unavailable", "note": "尚无本地用量记录"}
    for path in root.rglob("*.jsonl"):
        if files >= 200 or path.is_symlink():
            continue
        try:
            if path.stat().st_size > 16 * 1024 * 1024:
                continue
            files += 1
            client = "claude" if "claude" in path.parts else "codex"
            records, cumulative, reported = {}, None, None
            with path.open(encoding="utf-8", errors="replace") as stream:
                for ordinal, line in enumerate(stream):
                    try:
                        row = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(row, dict):
                        continue
                    kind = row.get("type")
                    payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                    message = row.get("message") if isinstance(row.get("message"), dict) else {}
                    value = None
                    identity = str(row.get("uuid") or ordinal)
                    if client == "claude" and kind == "assistant":
                        value = _usage(message.get("usage"), client)
                        identity = str(message.get("id") or identity)
                    elif client == "codex" and kind == "token_usage_record":
                        value = _usage(payload.get("usage"), client)
                        identity = str(payload.get("response_id") or identity)
                    elif client == "codex" and kind == "event_msg" and payload.get("type") == "token_count":
                        info = payload.get("info")
                        value = _usage(info.get("total_token_usage"), client) if isinstance(info, dict) else None
                        if value:
                            cumulative = _merge_usage(cumulative, value)
                    if value:
                        records[identity] = _merge_usage(records.get(identity), value)
                    cost = row.get("total_cost_usd")
                    if type(cost) in (int, float) and 0 <= cost < 1e9:
                        reported = max(reported or 0.0, float(cost))
            detail = _sum_usage(records.values())
            if cumulative:
                detail = _merge_usage(detail, cumulative)
            if any(value is not None for value in detail.values()):
                if records:
                    totals = {key: ((totals.get(key) or 0) + (detail.get(key) or 0)
                                    if detail.get(key) is not None else totals.get(key))
                              for key in USAGE_COUNTERS}
                else:
                    totals = _merge_usage(totals, detail)
                requests += len(records) or 1
            if reported is not None:
                estimated_cost += reported
                cost_records += 1
        except (OSError, ValueError):
            continue
    return {**totals, "requests": requests,
            "estimated_cost": estimated_cost if cost_records else None,
            "pricing_status": "client_estimate" if cost_records else "unavailable",
            "note": ("费用来自 CLI 记录的客户端估算，实际扣费以网关账单为准"
                     if cost_records else "网关/CLI 未返回费用；这里仅显示本地 Token 记录"),
            "scanned_files": files, "scanned_at": int(time.time())}


def private_write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def launch_spec(payload, home=None):
    home = Path(home or Path.home())
    client = payload.get("client", "shell")
    if client not in ("shell", "claude", "codex", "ssh"):
        raise ValueError("不支持的客户端")
    mode = payload.get("mode", "new")
    if mode not in ("new", "continue", "resume"):
        raise ValueError("会话方式无效")
    workspace = str(home) if client == "ssh" else str(payload.get("workspace") or "~/projects")
    if "\0" in workspace or "\n" in workspace:
        raise ValueError("项目路径无效")
    if workspace == "~" or workspace.startswith("~/"):
        workspace = str(home) + workspace[1:]
    workspace = Path(workspace)
    if not workspace.is_absolute():
        workspace = home / workspace
    workspace.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in SENSITIVE_ENV}
    env["PATH"] = str(home / ".local/bin") + ":" + str(home / "bin") + ":" + env.get("PATH", "")
    env.update(TERM="xterm-256color", COLORTERM="truecolor")
    bash = shutil.which("bash", path=env["PATH"])
    if not bash:
        raise ValueError("未找到 bash，请先在 Termux 安装 bash")
    if client == "shell":
        return [bash, "-l"], env, str(workspace)
    if client == "ssh":
        # Credentials, host aliases, ProxyJump and known_hosts stay with OpenSSH.
        # Never send the phone's model API key or an arbitrary remote command.
        target = payload.get("ssh")
        if not isinstance(target, dict):
            raise ValueError("请选择 SSH 电脑")
        host = str(target.get("host") or "").strip()
        user = str(target.get("user") or "").strip()
        port = int(target.get("port", 22))
        if not (1 <= len(host) <= 253 and (re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", host) or
                (":" in host and re.fullmatch(r"[0-9A-Fa-f:]+", host)))):
            raise ValueError("SSH 主机无效")
        if len(user) > 80 or (user and not re.fullmatch(r"[^\W\d][\w.@\\-]*|[0-9_][\w.@\\-]*", user)):
            raise ValueError("SSH 用户名无效")
        if not 1 <= port <= 65535:
            raise ValueError("SSH 端口无效")
        executable = shutil.which("ssh", path=env["PATH"])
        if not executable:
            raise ValueError("请先在 Termux 安装 openssh")
        args = [executable, "-tt", "-o", "StrictHostKeyChecking=ask", "-o", "ServerAliveInterval=30",
                "-o", "ServerAliveCountMax=3", "-p", str(port)]
        if user: args += ["-l", user]
        args += [host]
        script = shlex.join(args) + "; result=$?; printf '\\r\\n[SSH 已退出：%s；保留手机 Shell]\\r\\n' \"$result\"; exec " + shlex.quote(bash) + " -l"
        return [bash, "--noprofile", "--norc", "-c", script], env, str(workspace)
    model = str(payload.get("model") or "").strip()
    key = str(payload.get("key") or "")
    if not model or len(model) > 250 or any(c in model for c in "\0\r\n"):
        raise ValueError("请选择或填写模型 ID")
    if not key or any(c in key for c in "\0\r\n"):
        raise ValueError("调用 Key 无效")
    gateway = str(payload.get("gateway") or "").rstrip("/")
    uri = urlsplit(gateway)
    if uri.scheme not in ("http", "https") or not uri.hostname or "@" in uri.netloc or uri.query or uri.fragment or uri.path:
        raise ValueError("网关地址无效")
    proot = shutil.which("proot-distro", path=env["PATH"])
    runtime = home / ".local/share/agent-workbench/runtime"
    in_debian = bool(proot and (runtime / "debian_runner.py").is_file())
    executable = None if in_debian else shutil.which(client, path=env["PATH"])
    if not executable and not in_debian:
        raise ValueError("未找到 " + client + "；请在 Debian 或 Termux 完成安装")
    scope = hashlib.sha256(json.dumps([payload.get("profile_id"), gateway, str(workspace)], ensure_ascii=False).encode()).hexdigest()[:24]
    config = home / ".local/share/agent-workbench" / client / scope
    config.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(config, 0o700)
    command = [executable] if executable else [client]
    if client == "codex":
        private_write(config / "config.toml", "model = " + json.dumps(model) + '\nmodel_provider = "workbench_gateway"\nsandbox_mode = "workspace-write"\n\n[model_providers.workbench_gateway]\nname = "Workbench gateway"\nwire_api = "responses"\nenv_key = "WORKBENCH_GATEWAY_KEY"\nbase_url = ' + json.dumps(gateway + "/v1") + "\n")
        env["WORKBENCH_GATEWAY_KEY"] = key
        env["CODEX_HOME"] = str(config)
        args = command
        if in_debian and payload.get("codex_compatibility") is True:
            # Explicit user choice in the app. PRoot cannot run Codex's Linux
            # sandbox on tested Android kernels. Keep command approvals active.
            args += ["--sandbox", "danger-full-access", "--ask-for-approval", "on-request"]
        if mode != "new":
            args += ["resume"] + (["--last"] if mode == "continue" else [])
        args += ["--model", model, "--cd", str(workspace), "--no-alt-screen"]
    else:
        if not (config / "settings.json").exists():
            private_write(config / "settings.json", "{}\n")
        # Claude Code sends ANTHROPIC_API_KEY as x-api-key. This also works
        # with Anthropic Messages-compatible CCHP/AutoBits gateways.
        env.update(CLAUDE_CONFIG_DIR=str(config), ANTHROPIC_BASE_URL=gateway, ANTHROPIC_API_KEY=key,
                   ANTHROPIC_MODEL=model, ANTHROPIC_DEFAULT_OPUS_MODEL=model,
                   ANTHROPIC_DEFAULT_SONNET_MODEL=model, ANTHROPIC_DEFAULT_HAIKU_MODEL=model,
                   ANTHROPIC_SMALL_FAST_MODEL=model, CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
                   CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS="1", DISABLE_AUTOUPDATER="1")
        # The pinned CLI's ordinary first-run flow probes api.anthropic.com
        # even with BASE_URL set. Its documented --bare mode uses only API-key
        # authentication. Explicit --add-dir still supplies project CLAUDE.md.
        args = command + ["--bare", "--add-dir", str(workspace), "--model", model,
                          "--settings", str(config / "settings.json"),
                          "--append-system-prompt", "全程使用简体中文。"]
        if mode == "continue": args += ["--continue"]
        if mode == "resume": args += ["--resume"]
    if in_debian:
        # A unique one-use file survives proot's environment clearing. The guest
        # reads and removes it before launching, so no key enters argv/profile.d.
        launch_file = config / ("launch-" + uuid.uuid4().hex + ".json")
        private_write(launch_file, json.dumps({"args": args, "workspace": str(workspace),
            "environment": {k: v for k, v in env.items() if k in SENSITIVE_ENV}}, ensure_ascii=False))
        env = {k: v for k, v in env.items() if k not in SENSITIVE_ENV}
        args = [proot, "login", "debian", "--user", "workbench", "--bind", str(home) + ":" + str(home),
                "--work-dir", str(workspace), "--", "/usr/bin/python3", str(runtime / "debian_runner.py"), str(launch_file)]
        # debian_runner retains a Debian shell; if guest bootstrap fails the
        # fallback Termux shell still works and removes the unconsumed key file.
        cleanup = "; rm -f -- " + shlex.quote(str(launch_file))
    else:
        cleanup = ""
    # The command line and config files contain no credential. After the agent exits,
    # clear its environment and return to an ordinary shell in the same terminal.
    script = shlex.join(args) + "; result=$?" + cleanup + "; printf '\\r\\n[Agent 已退出：%s；保留终端]\\r\\n' \"$result\"; unset " + " ".join(SENSITIVE_ENV) + "; exec " + shlex.quote(bash) + " -l"
    return [bash, "--noprofile", "--norc", "-c", script], env, str(workspace)


class Session:
    def __init__(self, payload):
        args, env, workspace = launch_spec(payload)
        self.id = uuid.uuid4().hex
        self.lock = threading.Lock()
        self.output, self.base, self.exit_code = bytearray(), 0, None
        self.created = time.time()
        self.pid, self.fd = pty.fork()
        if self.pid == 0:
            try:
                os.chdir(workspace)
                os.execvpe(args[0], args, env)
            except BaseException:
                os.write(2, "无法启动终端进程\r\n".encode())
                os._exit(127)
        self.resize(payload.get("cols", 80), payload.get("rows", 24))
        threading.Thread(target=self.read, daemon=True).start()

    def read(self):
        try:
            while True:
                data = os.read(self.fd, 8192)
                if not data: break
                with self.lock:
                    self.output.extend(data)
                    if len(self.output) > MAX_OUTPUT:
                        removed = len(self.output) - MAX_OUTPUT
                        del self.output[:removed]
                        self.base += removed
        except OSError:
            return
        finally:
            _, status = os.waitpid(self.pid, 0)
            self.exit_code = os.waitstatus_to_exitcode(status)
            try: os.close(self.fd)
            except OSError: self.fd = -1

    def resize(self, cols, rows):
        cols, rows = max(10, min(int(cols), 400)), max(2, min(int(rows), 200))
        try: fcntl.ioctl(self.fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, cols, 0, 0))
        except OSError: return

    def snapshot(self, after):
        with self.lock:
            after = max(0, after)
            start = max(after, self.base)
            data = bytes(self.output[max(0, start - self.base):max(0, start - self.base) + 65536])
            return {"id": self.id, "data": base64.b64encode(data).decode(), "after": start + len(data),
                    "truncated": after < self.base, "exit_code": self.exit_code}

    def stop(self):
        if self.exit_code is not None: return
        try: os.killpg(self.pid, signal.SIGTERM)
        except ProcessLookupError: return
        def force():
            time.sleep(2)
            if self.exit_code is None:
                try: os.killpg(self.pid, signal.SIGKILL)
                except ProcessLookupError: return
        threading.Thread(target=force, daemon=True).start()


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, token):
        if address[0] != "127.0.0.1" or len(token) < 32:
            raise ValueError("桥接只允许本机回环与随机凭据")
        self.token, self.sessions, self.guard = token, {}, threading.Lock()
        self.runtime = None
        runtime_dir = Path.home() / ".local/share/agent-workbench/runtime"
        if (runtime_dir / "workbench_runtime.py").is_file():
            import importlib.util
            spec = importlib.util.spec_from_file_location("workbench_runtime", runtime_dir / "workbench_runtime.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.runtime = module.RuntimeInstaller(runtime_dir)
        super().__init__(address, Handler)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): return

    def reply(self, code, value):
        data = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try: self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError): return

    def handle_request(self):
        if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + self.server.token):
            return self.reply(401, {"error": "未授权的本机桥接请求"})
        uri = urlsplit(self.path)
        try:
            if self.command == "GET" and uri.path == "/health":
                return self.reply(200, {"service": "agent-workbench-termux", "version": "0.1.8", "build": 10, "capabilities": ["pty", "ssh", "debian-runtime", "usage"]})
            if self.command == "GET" and uri.path == "/usage":
                return self.reply(200, usage_snapshot())
            payload = {}
            if self.command == "POST":
                length = int(self.headers.get("Content-Length", 0))
                if not 0 <= length <= MAX_REQUEST: return self.reply(413, {"error": "请求过大"})
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict): raise ValueError("请求格式错误")
            if uri.path == "/runtime" and self.server.runtime:
                if self.command == "GET": return self.reply(200, self.server.runtime.status())
                if self.command == "POST": return self.reply(202, self.server.runtime.start(payload.get("clients")))
            if uri.path == "/sessions" and self.command == "POST":
                with self.server.guard:
                    if sum(s.exit_code is None for s in self.server.sessions.values()) >= 8:
                        return self.reply(409, {"error": "最多同时运行 8 个终端"})
                    session = Session(payload)
                    self.server.sessions[session.id] = session
                return self.reply(201, {"id": session.id})
            match = re.fullmatch(r"/sessions/([0-9a-f]{32})(/input|/resize)?", uri.path)
            if not match: return self.reply(404, {"error": "接口不存在"})
            session = self.server.sessions.get(match.group(1))
            if session is None: return self.reply(404, {"error": "终端不存在，Termux 可能已重启"})
            if self.command == "GET" and not match.group(2):
                return self.reply(200, session.snapshot(int(parse_qs(uri.query).get("after", [0])[0])))
            if self.command == "DELETE" and not match.group(2):
                session.stop()
                return self.reply(200, {"stopping": True})
            if self.command == "POST" and match.group(2) == "/input":
                data = base64.b64decode(payload.get("data", ""), validate=True)
                if session.exit_code is not None: raise ValueError("终端已退出")
                view = memoryview(data)
                while view: view = view[os.write(session.fd, view):]
                return self.reply(200, {"ok": True})
            if self.command == "POST" and match.group(2) == "/resize":
                session.resize(payload.get("cols", 80), payload.get("rows", 24))
                return self.reply(200, {"ok": True})
            return self.reply(405, {"error": "操作不支持"})
        except (ValueError, TypeError, KeyError):
            # Expected validation messages never contain the submitted key or body.
            return self.reply(400, {"error": "参数无效；请检查模型、目录及已安装的客户端"})
        except OSError:
            return self.reply(409, {"error": "进程无法启动或已经退出，请检查 Termux 环境"})

    do_GET = handle_request
    do_POST = handle_request
    do_DELETE = handle_request


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--token", required=True)
    options = parser.parse_args()
    os.umask(0o077)
    BridgeServer(("127.0.0.1", options.port), options.token).serve_forever()
