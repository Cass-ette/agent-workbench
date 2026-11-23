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
import subprocess
import sys
import tempfile
import termios
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid

MAX_OUTPUT = 1024 * 1024
MAX_REQUEST = 64 * 1024
NETWORK_ENV = ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy",
               "NO_PROXY", "no_proxy")
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
            parts = path.relative_to(root).parts
            inherited_through = ""
            if len(parts) >= 3 and parts[1].startswith("branch-"):
                from session_history import branch_usage_context
                imported, manifest = branch_usage_context(root / parts[0] / parts[1], path)
                if imported:
                    continue
                inherited_through = str(manifest.get("inherited_through") or "")
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
                    if inherited_through and str(row.get("timestamp") or "") <= inherited_through:
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


def history_context(home):
    import session_history
    root = home / ".local/share/agent-workbench"

    def homes(client):
        directory = root / client
        if not directory.is_dir() or directory.is_symlink():
            return []
        return [(path, "", "") for path in directory.iterdir()
                if path.is_dir() and not path.is_symlink()]

    return session_history, homes("codex"), homes("claude")


def paired_ssh_arguments(executable, home, env, target, host, user, port, remote_command=None):
    """Use an app-owned identity only for computers added via a connection code."""
    from contextlib import suppress
    target_id = str(uuid.UUID(str(target.get("id"))))
    if target.get("platform") not in ("macos", "windows") or not user:
        raise ValueError("配对信息无效")
    directory = home / ".local/share/agent-workbench/ssh" / target_id
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    known_hosts = directory / "known_hosts"
    host_key = target.get("host_key", "")
    if not re.fullmatch(r"ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI[A-Za-z0-9+/]{43}", host_key):
        raise ValueError("电脑主机密钥格式无效")
    address = host if port == 22 else f"[{host}]:{port}"
    private_write(known_hosts, address + " " + host_key + "\n")
    args = [executable, "-F", "/dev/null", "-tt", "-o", "HostKeyAlgorithms=ssh-ed25519",
            "-o", "StrictHostKeyChecking=yes", "-o", "GlobalKnownHostsFile=/dev/null",
            "-o", "UserKnownHostsFile=" + str(known_hosts), "-o", "UpdateHostKeys=no", "-o", "ServerAliveInterval=30",
            "-o", "ServerAliveCountMax=3", "-p", str(port), "-l", user]
    password_args = args + ["-o", "PubkeyAuthentication=no", host]
    def launch(arguments):
        if remote_command is None:
            return shlex.join(arguments)
        return shlex.join([arg for arg in arguments[:-1] if arg != "-tt"] + ["-T", arguments[-1], remote_command])
    if target.get("remember") is not True:
        return launch(password_args)
    identity = directory / "id_ed25519"
    if not identity.is_file():
        keygen = shutil.which("ssh-keygen", path=env["PATH"])
        if not keygen:
            raise ValueError("请先安装 Termux OpenSSH")
        try:
            subprocess.run([keygen, "-q", "-t", "ed25519", "-N", "", "-C", "AgentWorkbench-Android", "-f", str(identity)],
                           check=True, timeout=10, stdin=subprocess.DEVNULL, capture_output=True)
        except (subprocess.SubprocessError, OSError):
            with suppress(OSError):
                identity.unlink()
            raise HistoryUnavailable("无法生成手机的配对密钥，请检查 Termux OpenSSH") from None
    public_key = identity.with_suffix(".pub").read_text(encoding="ascii").split()
    if len(public_key) < 2 or not re.fullmatch(r"AAAAC3NzaC1lZDI1NTE5AAAAI[A-Za-z0-9+/]{43}", public_key[1]):
        raise ValueError("手机配对密钥格式无效")
    args += ["-i", str(identity), "-o", "IdentitiesOnly=yes", host]
    setup = directory / "paired"
    if setup.exists():
        return launch(args)
    key = "ssh-ed25519 " + public_key[1] + " AgentWorkbench-Android-" + target_id
    if target["platform"] == "macos":
        command = """umask 077
dir="$HOME/.ssh"
keys="$dir/authorized_keys"
mkdir -p "$dir" || exit 1
key='@KEY@'
if [ ! -f "$keys" ] || ! grep -Fqx -- "$key" "$keys"; then
    printf '\\n%s\\n' "$key" >> "$keys" || exit 1
fi
""".replace("@KEY@", key)
    else:
        powershell = """$ErrorActionPreference = 'Stop'
$key = '@KEY@'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
if ($identity.Groups.Value -contains 'S-1-5-32-544') {
    Write-Error 'Use a standard Windows user to remember this phone.'
    exit 1
}
$file = Join-Path $env:USERPROFILE '.ssh/authorized_keys'
[void][IO.Directory]::CreateDirectory([IO.Path]::GetDirectoryName($file))
if (!(Test-Path -LiteralPath $file)) { [void](New-Item -Path $file -ItemType File) }
if (!(Select-String -LiteralPath $file -SimpleMatch -Pattern $key -Quiet)) {
    [IO.File]::AppendAllText($file, [Environment]::NewLine + $key + [Environment]::NewLine, [Text.Encoding]::ASCII)
}
""".replace("@KEY@", key)
        command = "powershell.exe -NoProfile -EncodedCommand " + base64.b64encode(powershell.encode("utf-16le")).decode()
    # A local-only probe verifies the key works before the pairing is remembered.
    # Force a fresh auth exchange so a successful password login cannot mask a bad key.
    setup_command = shlex.join(args + [command])
    probe_args = [arg for arg in args[:-1] if arg != "-tt"] + ["-T", "-o", "BatchMode=yes", "-o", "PreferredAuthentications=publickey", args[-1], "exit 0"]
    return ("if " + setup_command + " && " + shlex.join(probe_args) + " && touch " + shlex.quote(str(setup))
            + "; then " + launch(args)
            + "; else printf '\\r\\n[未能记住此手机。按回车用密码连接，或按 Ctrl+C 取消。]\\r\\n'; "
            + "read -r reply && " + launch(password_args) + "; fi")


def agent_permission_args(client, mode):
    if mode not in {"restricted", "standard", "full"}:
        raise ValueError("权限模式无效，请重新设置")
    if client == "claude":
        return ["--permission-mode", "bypassPermissions" if mode == "full" else "default"]
    return ["--sandbox", "danger-full-access" if mode == "full" else "workspace-write",
            "--ask-for-approval", "on-request", "-c",
            "sandbox_workspace_write.network_access=" + ("false" if mode == "restricted" else "true")]


def local_network_bypass(environment):
    entries = ["127.0.0.1", "localhost", "::1"]
    for name in ("NO_PROXY", "no_proxy"):
        entries.extend(value.strip() for value in environment.get(name, "").split(",") if value.strip())
    return ",".join(dict.fromkeys(entries))


def launch_spec(payload, home=None, bundle=None):
    home = Path(home or Path.home())
    client = payload.get("client", "shell")
    if client not in ("shell", "debian-shell", "vm-shell", "claude", "codex", "ssh"):
        raise ValueError("不支持的客户端")
    mode = payload.get("mode", "new")
    if mode not in ("new", "continue", "resume"):
        raise ValueError("会话方式无效")
    source = None
    history_action = payload.get("history_action")
    if history_action is not None and (history_action not in ("original", "fork") or "history_ref" not in payload
            or payload.get("environment", "debian") != "debian"):
        raise ValueError("历史操作只适用于 Debian 中选定的历史会话")
    if "history_ref" in payload or "history_id" in payload:
        if mode != "resume" or client not in ("codex", "claude"):
            raise ValueError("历史会话标识无效")
        history, codex_homes, claude_homes = history_context(home)
        try:
            source = history.resolve_history(payload.get("history_ref"), payload.get("history_id"),
                                             client, codex_homes, claude_homes)
        except ValueError:
            raise HistoryUnavailable() from None
    workspace = str(home) if client in ("ssh", "vm-shell") else str(payload.get("workspace") or "~/projects")
    if "\0" in workspace or "\n" in workspace:
        raise ValueError("项目路径无效")
    if workspace == "~" or workspace.startswith("~/"):
        workspace = str(home) + workspace[1:]
    workspace = Path(workspace)
    if not workspace.is_absolute():
        workspace = home / workspace
    if source and (not source["workspace"]
            or workspace.resolve() != Path(source["workspace"]).resolve()):
        raise HistoryUnavailable("恢复必须使用这条会话所属的项目目录")
    if source and not workspace.is_dir():
        raise HistoryUnavailable("项目目录不存在，请重新选择该会话")
    workspace.mkdir(parents=True, exist_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in SENSITIVE_ENV}
    env["PATH"] = str(home / ".local/bin") + ":" + str(home / "bin") + ":" + env.get("PATH", "")
    env.update(TERM="xterm-256color", COLORTERM="truecolor")
    bash = shutil.which("bash", path=env["PATH"])
    if not bash:
        raise ValueError("未找到 bash，请先在 Termux 安装 bash")
    if client == "shell":
        return [bash, "-l"], env, str(workspace)
    if client == "vm-shell":
        from linux_vm import LinuxVM
        vm = LinuxVM(home)
        if not vm.status()["installed"]:
            raise ValueError("请先在运行环境页选装完整 Linux 虚拟机")
        script = shlex.join([sys.executable, str(Path(__file__).with_name("linux_vm.py")), "shell"])
        script += "; result=$?; printf '\\r\\n[Linux VM Shell 已退出：%s。已返回 Termux，可在运行环境页管理虚拟机。]\\r\\n' \"$result\"; exec " + shlex.quote(bash) + " -l"
        return [bash, "--noprofile", "--norc", "-c", script], env, str(workspace)
    if client == "debian-shell":
        proot = shutil.which("proot-distro", path=env["PATH"])
        if not proot:
            raise ValueError("未找到 proot-distro，请先在工作台的运行环境中安装 Debian")
        command = [proot, "login", "debian", "--user", "workbench",
                   "--bind", str(home) + ":" + str(home), "--work-dir", str(workspace),
                   "--", "/bin/bash", "-l"]
        script = shlex.join(command) + "; result=$?; printf '\\r\\n[Debian Shell 已退出：%s；返回 Termux Shell]\\r\\n' \"$result\"; exec " + shlex.quote(bash) + " -l"
        return [bash, "--noprofile", "--norc", "-c", script], env, str(workspace)
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
        ssh_command = paired_ssh_arguments(executable, home, env, target, host, user, port) if target.get("platform") else shlex.join(args)
        script = ssh_command + "; result=$?; printf '\\r\\n[SSH 已退出：%s；保留手机 Shell]\\r\\n' \"$result\"; exec " + shlex.quote(bash) + " -l"
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
    environment = payload.get("environment", "debian")
    if environment not in ("debian", "vm"):
        raise ValueError("请选择 Debian 轻量环境或完整 Linux 虚拟机")
    fingerprint = hashlib.sha256(key.encode()).hexdigest()
    scope = hashlib.sha256(json.dumps([payload.get("profile_id"), gateway, model, str(workspace), fingerprint], ensure_ascii=False).encode()).hexdigest()[:24]
    if environment == "vm":
        from linux_vm import LinuxVM
        vm = LinuxVM(home)
        if not vm.status()["installed"]:
            raise ValueError("请先在运行环境页安装完整 Linux 虚拟机")
        projects = (home / "projects").resolve()
        try:
            workspace.resolve().relative_to(projects)
        except ValueError:
            raise ValueError("Linux VM Agent 仅支持手机 ~/projects 下的项目目录") from None
        launch_file = home / ".local/share/agent-workbench" / ("vm-launch-" + uuid.uuid4().hex + ".json")
        private_write(launch_file, json.dumps({
            "client": client, "mode": mode, "profile_id": payload.get("profile_id"),
            "model": model, "gateway": gateway, "key": key,
            "workspace": str(workspace.resolve()), "codex_compatibility": payload.get("codex_compatibility") is True,
        }, ensure_ascii=False))
        vm_script = shlex.join([sys.executable, str(Path(__file__).with_name("linux_vm.py")), "agent", str(launch_file)])
        vm_script += "; result=$?; rm -f -- " + shlex.quote(str(launch_file))
        vm_script += "; printf '\\r\\n[VM Agent 已退出：%s；返回 Termux Shell]\\r\\n' \"$result\"; unset " + " ".join(SENSITIVE_ENV) + "; exec " + shlex.quote(bash) + " -l"
        return [bash, "--noprofile", "--norc", "-c", vm_script], env, str(workspace)
    proot = shutil.which("proot-distro", path=env["PATH"])
    runtime = Path(bundle) if bundle else home / ".local/share/agent-workbench/runtime"
    in_debian = bool(proot and (runtime / "debian_runner.py").is_file())
    executable = None if in_debian else shutil.which(client, path=env["PATH"])
    if not executable and not in_debian:
        raise ValueError("未找到 " + client + "；请在 Debian 或 Termux 完成安装")
    parent = home / ".local/share/agent-workbench" / client
    seed = None
    matches_original = bool(source and history.can_resume_in_place(
        source, str(payload.get("profile_id") or ""), model, gateway, fingerprint))
    if history_action == "original" and not matches_original:
        raise HistoryUnavailable("无法确认原 Key 或网关仍可用，可从当前项目的历史创建分支")
    same_config = matches_original and history_action != "fork"
    if source and not same_config:
        try:
            config, seed = history.stage_branch(source, parent, payload.get("profile_id"), "", model, gateway, fingerprint)
        except ValueError as exc:
            raise HistoryUnavailable(str(exc)) from None
        if client == "codex":
            seed = None  # Codex's native fork keeps references to these private input copies.
    elif source:
        config = Path(source["_home"])
    else:
        config = parent / scope
    config.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(config, 0o700)
    if not source:
        private_write(config / "workbench-profile.json", json.dumps({
            "profile_id": payload.get("profile_id"), "gateway": gateway, "model": model,
            "key_fingerprint": fingerprint,
        }))
    command = [executable] if executable else [client]
    bypass = local_network_bypass(env)
    env.update(NO_PROXY=bypass, no_proxy=bypass)
    if client == "codex":
        if not same_config or not (config / "config.toml").is_file():
            private_write(config / "config.toml", "model = " + json.dumps(model) + '\nmodel_provider = "workbench_gateway"\nsandbox_mode = "workspace-write"\n\n[model_providers.workbench_gateway]\nname = "Workbench gateway"\nwire_api = "responses"\nenv_key = "WORKBENCH_GATEWAY_KEY"\nbase_url = ' + json.dumps(gateway + "/v1") + "\n")
        env["WORKBENCH_GATEWAY_KEY"] = key
        env["CODEX_HOME"] = str(config)
        args = command
        permission_mode = payload.get("permission_mode", "restricted")
        if in_debian and payload.get("codex_compatibility") is True:
            # Explicit user choice in the app. PRoot cannot run Codex's Linux
            # sandbox on tested Android kernels. Keep command approvals active.
            permission_mode = "full"
        if source:
            args += ["resume" if same_config else "fork", source["id"]]
        elif mode != "new":
            args += ["resume"] + (["--last"] if mode == "continue" else [])
        args += ["--model", model, "--cd", str(workspace), "--no-alt-screen"]
        args += agent_permission_args("codex", permission_mode)
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
                          "--append-system-prompt", "全程使用简体中文。",
                          *agent_permission_args("claude", payload.get("permission_mode", "standard"))]
        if mode == "continue": args += ["--continue"]
        if mode == "resume":
            args += ["--resume"]
            if source:
                args += [source["id"]]
                if not same_config:
                    args += ["--fork-session"]
    import tool_bridge
    tool_root = home / ".local/share/agent-workbench/tools/debian" / client
    if not in_debian:
        if (tool_root / "tools.json").exists():
            args += tool_bridge.apply_tools(tool_root, config, client)
    if in_debian:
        # A unique one-use file survives proot's environment clearing. The guest
        # reads and removes it before launching, so no key enters argv/profile.d.
        launch_file = config / ("launch-" + uuid.uuid4().hex + ".json")
        private_write(launch_file, json.dumps({"args": args, "workspace": str(workspace),
            "history_seed": str(seed) if seed else None,
            "tools_root": str(tool_root), "client": client,
            "environment": {k: v for k, v in env.items() if k in SENSITIVE_ENV or k in NETWORK_ENV}}, ensure_ascii=False))
        env = {k: v for k, v in env.items() if k not in SENSITIVE_ENV}
        args = [proot, "login", "debian", "--user", "workbench", "--bind", str(home) + ":" + str(home),
                "--work-dir", str(workspace), "--", "/usr/bin/python3", str(runtime / "debian_runner.py"), str(launch_file)]
        # debian_runner retains a Debian shell; if guest bootstrap fails the
        # fallback Termux shell still works and removes the unconsumed key file.
        cleanup = "; rm -f -- " + shlex.quote(str(launch_file))
    else:
        cleanup = ""
    if seed:
        cleanup += "; rm -f -- " + shlex.quote(str(seed))
    # The command line and config files contain no credential. After the agent exits,
    # clear its environment and return to an ordinary shell in the same terminal.
    script = shlex.join(args) + "; result=$?" + cleanup + "; printf '\\r\\n[Agent 已退出：%s；保留终端]\\r\\n' \"$result\"; unset " + " ".join(SENSITIVE_ENV) + "; exec " + shlex.quote(bash) + " -l"
    return [bash, "--noprofile", "--norc", "-c", script], env, str(workspace)


class Session:
    def __init__(self, payload, bundle=None):
        args, env, workspace = launch_spec(payload, bundle=bundle)
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

    def write(self, data):
        if self.exit_code is not None:
            raise ValueError("终端已退出")
        view = memoryview(data)
        while view:
            view = view[os.write(self.fd, view):]


class HistoryUnavailable(ValueError):
    def __init__(self, message="找不到这条本地会话，请刷新对话历史"):
        super().__init__(message)


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True
    def __init__(self, address, token, version="", build=0):
        if address[0] != "127.0.0.1" or len(token) < 32:
            raise ValueError("桥接只允许本机回环与随机凭据")
        self.token, self.sessions, self.guard = token, {}, threading.Lock()
        self.remotes = {}
        self.version, self.build = version, build
        self.runtime = None
        self.bundle = Path(__file__).resolve().parent
        if str(self.bundle) not in sys.path:
            sys.path.insert(0, str(self.bundle))
        runtime_dir = Path.home() / ".local/share/agent-workbench/runtime"
        if (self.bundle / "workbench_runtime.py").is_file():
            import importlib.util
            spec = importlib.util.spec_from_file_location("workbench_runtime", self.bundle / "workbench_runtime.py")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.runtime = module.RuntimeInstaller(runtime_dir, bundle=self.bundle)
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
                return self.reply(200, {"service": "agent-workbench-termux", "version": self.server.version,
                                        "build": self.server.build, "capabilities": ["pty", "ssh", "ssh-pairing", "remote-workbench", "debian-runtime", "debian-shell", "linux-vm", "vm-agent", "versioned-runtime", "usage", "history", "history-actions", "tool-bridge"]})
            if self.command == "GET" and uri.path == "/usage":
                return self.reply(200, usage_snapshot())
            if self.command == "GET" and uri.path == "/tools":
                query = parse_qs(uri.query)
                client = query.get("client", [""])[0]
                environment = query.get("environment", ["debian"])[0]
                if client not in ("codex", "claude") or environment not in ("debian", "vm"):
                    raise ValueError("请选择本地运行环境与客户端")
                if environment == "vm":
                    from linux_vm import LinuxVM
                    return self.reply(200, LinuxVM().manage_tools("get", client, {}))
                import tool_bridge
                return self.reply(200, tool_bridge.public_settings(Path.home() / ".local/share/agent-workbench/tools/debian" / client))
            if self.command == "GET" and uri.path == "/history":
                history, codex_homes, claude_homes = history_context(Path.home())
                query = parse_qs(uri.query)
                selected_workspace = None if query.get("scope") == ["all"] else query.get("workspace", [""])[0]
                selected_client = query.get("client", [None])[0]
                rows = history.scan_history(codex_homes, claude_homes, limit=500,
                                            workspace=selected_workspace, client=selected_client)
                return self.reply(200, {"sessions": history.public_rows(
                    history.mark_scope(rows, "workbench"))})
            payload = {}
            if self.command == "POST":
                length = int(self.headers.get("Content-Length", 0))
                limit = 1024 * 1024 if uri.path.startswith("/remote/") and uri.path.endswith("/rpc") else MAX_REQUEST
                if not 0 <= length <= limit: return self.reply(413, {"error": "请求过大"})
                payload = json.loads(self.rfile.read(length) or b"{}")
                if not isinstance(payload, dict): raise ValueError("请求格式错误")
            if self.command == "POST" and uri.path == "/remote/connect":
                from remote_workbench import RemoteConnection, remote_command
                target = payload.get("ssh", {})
                target_id = str(uuid.UUID(str(target.get("id"))))
                # Apply the exact same host/user validation as the ordinary SSH launcher.
                launch_spec({"client": "ssh", "ssh": target}, bundle=self.server.bundle)
                with self.server.guard:
                    previous = self.server.remotes.get(target_id)
                    if previous and previous.exit_code is None:
                        previous.stop()
                    marker = "WB1:" + uuid.uuid4().hex + ":"
                    env = os.environ.copy()
                    env["PATH"] = str(Path.home().parent / "usr/bin") + ":" + env.get("PATH", "")
                    executable = shutil.which("ssh", path=env["PATH"])
                    if not executable: raise ValueError("请安装 Termux OpenSSH")
                    command = paired_ssh_arguments(executable, Path.home(), env, target, target["host"], target["user"], target.get("port", 22),
                                                   remote_command(target["platform"], marker))
                    connection = RemoteConnection.open(command, target_id, marker)
                    self.server.remotes[target_id] = connection
                    from remote_workbench import RemoteSession
                    for session in self.server.sessions.values():
                        if isinstance(session, RemoteSession) and session.connection.owner == target_id:
                            session.connection = connection
                    self.server.sessions[connection.id] = connection
                return self.reply(201, {"id": connection.id})
            remote_match = re.fullmatch(r"/remote/([0-9a-f-]{36})(/rpc|/attach)?", uri.path)
            if remote_match:
                from remote_workbench import RemoteSession
                target_id = str(uuid.UUID(remote_match.group(1)))
                connection = self.server.remotes.get(target_id)
                if self.command == "GET":
                    return self.reply(200, {"connected": bool(connection and connection.ready),
                                            "error": connection.error if connection else "", "schema": 1})
                if connection is None: raise HistoryUnavailable("请先连接电脑工作台")
                if self.command == "POST" and remote_match.group(2) == "/rpc":
                    return self.reply(200, connection.call(payload))
                if self.command == "POST" and remote_match.group(2) == "/attach":
                    value = connection.call({"op": "attach", "session_id": payload.get("session_id"), "take_control": True})
                    with self.server.guard:
                        existing = next((s for s in self.server.sessions.values() if isinstance(s, RemoteSession)
                                         and s.connection is connection and s.remote_id == value["id"]), None)
                        proxy = existing or RemoteSession(connection, value)
                        self.server.sessions[proxy.id] = proxy
                    return self.reply(200, {**value, "id": proxy.id, "remote_id": value["id"]})
            if self.command == "POST" and uri.path == "/tools":
                client, environment = payload.get("client"), payload.get("environment", "debian")
                action = payload.get("action")
                if client not in ("codex", "claude") or environment not in ("debian", "vm") or action not in ("save", "import"):
                    raise ValueError("工具操作参数无效")
                if action == "import" and payload.get("kind") == "plugin" and client != "claude":
                    raise ValueError("完整插件只支持 Claude，Codex 可接入 MCP 与 Skills")
                if environment == "vm":
                    from linux_vm import LinuxVM
                    return self.reply(200, LinuxVM().manage_tools(action, client, payload))
                import tool_bridge
                root = Path.home() / ".local/share/agent-workbench/tools/debian" / client
                value = tool_bridge.save_settings(root, payload, client) if action == "save" else tool_bridge.import_directory(root, payload.get("path", ""), payload.get("kind"))
                return self.reply(200, value)
            if uri.path == "/runtime" and self.server.runtime:
                if self.command == "GET": return self.reply(200, self.server.runtime.status())
                if self.command == "POST": return self.reply(202, self.server.runtime.start(payload.get("clients"), payload.get("environment", "debian")))
            if uri.path == "/vm" and self.command == "POST":
                from linux_vm import LinuxVM
                vm = LinuxVM()
                action = payload.get("action")
                if action == "start": result = vm.start()
                elif action == "stop": result = vm.stop()
                else: raise ValueError("虚拟机操作无效")
                return self.reply(200, result)
            if uri.path == "/sessions" and self.command == "POST":
                with self.server.guard:
                    if sum(s.exit_code is None for s in self.server.sessions.values()) >= 8:
                        return self.reply(409, {"error": "最多同时运行 8 个终端"})
                    session = Session(payload, bundle=self.server.bundle)
                    self.server.sessions[session.id] = session
                return self.reply(201, {"id": session.id})
            pairing = re.fullmatch(r"/ssh-pairing/([0-9a-f-]{36})", uri.path)
            if self.command == "DELETE" and pairing:
                pairing_id = str(uuid.UUID(pairing.group(1)))
                directory = Path.home() / ".local/share/agent-workbench/ssh" / pairing_id
                with self.server.guard:
                    for file in ("id_ed25519", "id_ed25519.pub", "known_hosts", "paired"):
                        (directory / file).unlink(missing_ok=True)
                    if directory.is_dir() and not any(directory.iterdir()):
                        directory.rmdir()
                return self.reply(200, {"ok": True})
            match = re.fullmatch(r"/sessions/([0-9a-f]{32})(/input|/resize|/terminate)?", uri.path)
            if not match: return self.reply(404, {"error": "接口不存在"})
            session = self.server.sessions.get(match.group(1))
            if session is None: return self.reply(404, {"error": "终端不存在，Termux 可能已重启"})
            if self.command == "GET" and not match.group(2):
                return self.reply(200, session.snapshot(int(parse_qs(uri.query).get("after", [0])[0])))
            if self.command == "DELETE" and not match.group(2):
                session.stop()
                if hasattr(session, "remote_id"):
                    self.server.sessions.pop(session.id, None)
                return self.reply(200, {"stopping": True})
            if self.command == "POST" and match.group(2) == "/input":
                data = base64.b64decode(payload.get("data", ""), validate=True)
                session.write(data)
                return self.reply(200, {"ok": True})
            if self.command == "POST" and match.group(2) == "/terminate":
                if hasattr(session, "terminate"): session.terminate()
                else: session.stop()
                return self.reply(200, {"stopping": True})
            if self.command == "POST" and match.group(2) == "/resize":
                session.resize(payload.get("cols", 80), payload.get("rows", 24))
                return self.reply(200, {"ok": True})
            return self.reply(405, {"error": "操作不支持"})
        except HistoryUnavailable as exc:
            return self.reply(409, {"error": str(exc)})
        except ValueError as exc:
            if uri.path.startswith("/remote/") or "/sessions/" in uri.path and hasattr(locals().get("session"), "remote_id"):
                return self.reply(409, {"error": str(exc)[:240]})
            return self.reply(400, {"error": "参数无效；请检查模型、目录及已安装的客户端"})
        except (TypeError, KeyError):
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
    parser.add_argument("--version", default="")
    parser.add_argument("--build", type=int, default=0)
    options = parser.parse_args()
    os.umask(0o077)
    BridgeServer(("127.0.0.1", options.port), options.token, options.version, options.build).serve_forever()
