#!/usr/bin/env python3
"""Isolated Claude Code entry for the local CCHP free key. Stdlib only."""

import argparse
import getpass
import hashlib
import ipaddress
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


USER_ROOT = Path.home()
CLIENT_ROOT = USER_ROOT / ".config/cchp-client"
TEST_ROOT = CLIENT_ROOT / "claude-test"
CONFIG_DIR = TEST_ROOT / "config"
WORKSPACE = TEST_ROOT / "workspace"
KEY_FILE = TEST_ROOT / "free-api-key"
KEY_DIR = TEST_ROOT / "keys"
PROFILE_INDEX = TEST_ROOT / "key-profiles.json"
WORKSPACE_INDEX = TEST_ROOT / "workspaces.json"
LAST_LAUNCH = TEST_ROOT / "last-launch.json"
MANAGER_LAST_LAUNCH = TEST_ROOT / "manager-last-launch.json"
SETTINGS = CONFIG_DIR / "settings.json"
EMPTY_MCP = TEST_ROOT / "empty-mcp.json"
CODEX_HOME_ROOT = TEST_ROOT / "codex"
CLAUDE_BRANCH_ROOT = TEST_ROOT / "claude-branches"
CLAUDE = USER_ROOT / ".local/bin/claude"
CODEX = USER_ROOT / ".local/bin/codex"
GATEWAY = "http://127.0.0.1:8081"
DEFAULT_MODEL = "kimi-for-coding-highspeed"
GATEWAY_CONFIG = TEST_ROOT / "gateway.json"


def normalize_gateway(value):
    value = value.strip().rstrip("/")
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise RuntimeError("网关地址必须是完整的 http:// 或 https:// 地址。")
    if parsed.query or parsed.fragment or "@" in parsed.netloc:
        raise RuntimeError("网关地址不能包含查询参数、片段或账号密码。")
    if parsed.path not in {"", "/", "/v1"}:
        raise RuntimeError("网关地址填写服务根地址即可，例如 https://gateway.example.com。")
    host = (parsed.hostname or "").lower()
    loopback = host == "localhost"
    try:
        loopback = loopback or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if parsed.scheme == "http" and not loopback:
        raise RuntimeError("远程网关必须使用 HTTPS，避免下游调用 Key 明文传输。")
    value = value[:-3] if value.endswith("/v1") else value
    return value


def gateway_url():
    if GATEWAY_CONFIG.is_file():
        try:
            data = json.loads(GATEWAY_CONFIG.read_text())
            if isinstance(data, dict) and isinstance(data.get("url"), str):
                return normalize_gateway(data["url"])
        except (OSError, ValueError, RuntimeError):
            data = {}
    configured = os.environ.get("CCHP_GATEWAY_URL", "").strip()
    if configured:
        return normalize_gateway(configured)
    return GATEWAY


def save_gateway(value):
    value = normalize_gateway(value)
    prepare_isolated_config()
    private_write(GATEWAY_CONFIG, json.dumps({"url": value}, ensure_ascii=False, indent=2) + "\n")
    return value


def private_write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def prepare_isolated_config():
    for directory in (TEST_ROOT, CONFIG_DIR):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(directory, 0o700)
    WORKSPACE.mkdir(parents=True, exist_ok=True, mode=0o700)

    if not SETTINGS.exists():
        private_write(SETTINGS, "{}\n")
    else:
        settings = json.loads(SETTINGS.read_text())
        if not isinstance(settings, dict):
            raise RuntimeError("隔离 Claude 设置文件不是 JSON 对象，已停止且未覆盖。")

    if not EMPTY_MCP.exists():
        private_write(EMPTY_MCP, '{"mcpServers":{}}\n')
    else:
        mcp = json.loads(EMPTY_MCP.read_text())
        if not isinstance(mcp, dict) or not isinstance(mcp.get("mcpServers", {}), dict):
            raise RuntimeError("隔离 MCP 配置不正确，已停止且未覆盖。")


def read_key():
    if not KEY_FILE.is_file():
        raise RuntimeError("请先运行 claude-cchp-test setup，填入本机免费调用 Key。")
    if KEY_FILE.is_symlink() or KEY_FILE.stat().st_mode & 0o077:
        raise RuntimeError("密钥文件权限不符合要求；请重新运行 setup。")
    value = KEY_FILE.read_text().strip()
    if len(value) < 20 or any(c.isspace() for c in value):
        raise RuntimeError("本机密钥文件格式不正确，请重新运行 setup。")
    return value


def load_profiles():
    profiles = []
    if KEY_FILE.is_file():
        profiles.append({"id": "free", "name": "AutoBits 免费（默认）", "path": KEY_FILE,
                         "gateway": gateway_url()})
    if PROFILE_INDEX.is_file():
        data = json.loads(PROFILE_INDEX.read_text())
        if not isinstance(data, dict):
            raise RuntimeError("本机 Key 配置索引损坏，已停止且未修改。")
        valid_profiles = []
        for profile_id, value in data.items():
            if not isinstance(profile_id, str) or not profile_id.startswith("p-"):
                continue
            if isinstance(value, str):
                label, profile_gateway = value, gateway_url()
            elif isinstance(value, dict) and isinstance(value.get("name"), str):
                label = value["name"]
                profile_gateway = value.get("gateway", gateway_url())
                if not isinstance(profile_gateway, str):
                    profile_gateway = gateway_url()
            else:
                continue
            valid_profiles.append((profile_id, label, normalize_gateway(profile_gateway)))
        for profile_id, label, profile_gateway in sorted(valid_profiles, key=lambda item: item[1]):
            path = KEY_DIR / (profile_id + ".key")
            if path.is_file():
                profiles.append({"id": profile_id, "name": label, "path": path, "gateway": profile_gateway})
    return profiles


def read_profile_key(profile):
    path = profile["path"]
    if not path.is_file():
        raise RuntimeError("所选本机调用 Key 不存在，请先添加。")
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise RuntimeError("密钥文件权限不符合要求，请重新保存该 Key。")
    value = path.read_text().strip()
    if len(value) < 20 or any(c.isspace() for c in value):
        raise RuntimeError("本机密钥文件格式不正确，请重新保存该 Key。")
    return value


def save_profile_key(label, key, gateway=None):
    label = label.strip()
    if not label or len(label) > 80:
        raise RuntimeError("名称不能为空，且最多 80 个字符。")
    profiles = load_profiles()
    if any(profile["name"] == label for profile in profiles):
        raise RuntimeError("已有同名 Key 配置；请换一个名称，避免意外覆盖。")
    key = key.strip()
    if len(key) < 20 or any(c.isspace() for c in key) or key[:1] in {"'", '"'}:
        raise RuntimeError("格式不正确；请粘贴完整 Key，不带引号、空格或换行。")
    gateway = normalize_gateway(gateway) if gateway else gateway_url()
    models = fetch_models(key, gateway)
    prepare_isolated_config()
    KEY_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(KEY_DIR, 0o700)
    profile_id = "p-" + hashlib.sha256(label.encode("utf-8")).hexdigest()[:12]
    index = {}
    if PROFILE_INDEX.is_file():
        loaded = json.loads(PROFILE_INDEX.read_text())
        if not isinstance(loaded, dict):
            raise RuntimeError("本机 Key 配置索引损坏，已停止且未覆盖。")
        index = loaded
    index[profile_id] = {"name": label, "gateway": gateway}
    private_write(KEY_DIR / (profile_id + ".key"), key + "\n")
    private_write(PROFILE_INDEX, json.dumps(index, ensure_ascii=False, indent=2) + "\n")
    return {"id": profile_id, "name": label, "gateway": gateway, "model_count": len(models)}


def add_profile_key():
    if not sys.stdin.isatty():
        raise RuntimeError("请在你自己的终端或桌面入口运行此操作；密钥输入会隐藏。")
    label = input("给这把本机调用 Key 起个名字（例如 AutoBits 付费）：")
    key = getpass.getpass("本机 CCHP 下游调用 Key（输入隐藏）：")
    profile = save_profile_key(label, key)
    print("已安全保存；只校验模型列表，没有发起推理。")
    print("名称：" + profile["name"] + "；此 Key 可用模型：" + str(profile["model_count"]))


def add_profile_from_stdin():
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict) or not isinstance(payload.get("name"), str) or not isinstance(payload.get("key"), str):
        raise RuntimeError("新增 Key 请求格式不正确。")
    profile = save_profile_key(payload["name"], payload["key"], payload.get("gateway"))
    print(json.dumps({"id": profile["id"], "name": profile["name"], "gateway": profile["gateway"],
                      "model_count": profile["model_count"]}, ensure_ascii=False))


def remove_profile_key(profile_id):
    if profile_id == "free" or not isinstance(profile_id, str) or not profile_id.startswith("p-"):
        raise RuntimeError("默认免费配置不能删除。")
    if Path(profile_id).name != profile_id or ".." in profile_id:
        raise RuntimeError("调用配置标识无效。")
    if PROFILE_INDEX.is_symlink() or not PROFILE_INDEX.is_file():
        raise RuntimeError("找不到本机调用配置索引，未删除任何文件。")
    index = json.loads(PROFILE_INDEX.read_text(encoding="utf-8"))
    if not isinstance(index, dict) or profile_id not in index:
        raise RuntimeError("找不到这把调用 Key；请刷新列表。")
    if KEY_DIR.is_symlink():
        raise RuntimeError("Key 目录是符号链接，已停止删除。")

    key_path = KEY_DIR / (profile_id + ".key")
    if key_path.is_symlink():
        raise RuntimeError("本机 Key 文件是符号链接，已停止删除。")
    if not key_path.is_file():
        raise RuntimeError("本机 Key 文件不存在或不是普通文件，已停止删除。")
    billing_dir = TEST_ROOT / "billing"
    if billing_dir.is_symlink():
        raise RuntimeError("账单目录是符号链接，已停止删除。")
    billing_paths = [billing_dir / (profile_id + ".json"),
                     billing_dir / (profile_id + ".json.bak")]
    for path in billing_paths:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise RuntimeError("关联的账单凭据不是普通文件，已停止删除。")

    name = index.pop(profile_id)
    if isinstance(name, dict):
        name = name.get("name", "未知配置")
    if not isinstance(name, str):
        name = "未知配置"
    for path in billing_paths:
        path.unlink(missing_ok=True)
    key_path.unlink()
    private_write(PROFILE_INDEX, json.dumps(index, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"id": profile_id, "name": name, "removed": True}, ensure_ascii=False))


def gui_state():
    prepare_isolated_config()
    print(json.dumps({
        "gateway": gateway_url(),
        "profiles": [{"id": item["id"], "name": item["name"], "gateway": item["gateway"]}
                     for item in load_profiles()],
        "last_launch": load_last_launch(MANAGER_LAST_LAUNCH),
        "workspaces": load_workspaces(),
    }, ensure_ascii=False))


def profile_models(profile_id, gateway_value=None):
    profile = next((item for item in load_profiles() if item["id"] == profile_id), None)
    if profile is None:
        raise RuntimeError("找不到所选调用 Key；请刷新列表。")
    gateway = normalize_gateway(gateway_value) if gateway_value else profile["gateway"]
    print(json.dumps({"profile_id": profile_id, "gateway": gateway,
                      "models": fetch_models(read_profile_key(profile), gateway)}, ensure_ascii=False))


def usage_snapshot():
    """Read isolated records; estimate from cached public prices, without network."""
    # Importing a bundled helper must not add files to the signed application.
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from usage_records import scan_usage
    from model_pricing import apply_usage_pricing
    service = pricing_service()
    branches = (home for home in CLAUDE_BRANCH_ROOT.glob("*")
                if home.name.startswith(("branch-", "scope-")) and not home.is_symlink())
    snapshot = scan_usage(CONFIG_DIR / "projects", CODEX_HOME_ROOT, include_pricing=True, claude_branches=branches)
    print(json.dumps(apply_usage_pricing(snapshot, service.resolve), ensure_ascii=False))


def pricing_service(online=False, force=False):
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from pricing_service import PricingService
    return PricingService(load_profiles(), TEST_ROOT / "pricing", read_profile_key, private_write, online, force)


def profile_pricing(profile_id, force=False):
    profile = billing_profile(profile_id)
    service = pricing_service(online=True, force=force)
    # Prime routing for all historic profiles, so the selected Key cannot change
    # how unrelated sessions are priced. Calls only public/account metadata APIs.
    for identity in service.profiles:
        service.context(identity)
    models_path = TEST_ROOT / "pricing" / (profile_id + "-models.json")
    from model_pricing import read_json
    cached = read_json(models_path) or {}
    models = cached.get("models", []) if cached.get("gateway") == profile["gateway"] else []
    if force or not models or time.time() - cached.get("fetched_at", 0) >= 21600:
        try:
            models = fetch_models(read_profile_key(profile), profile["gateway"])
            private_write(models_path, json.dumps({"gateway": profile["gateway"], "models": models, "fetched_at": int(time.time())}))
        except (OSError, RuntimeError, ValueError):
            models = models or list(service.context(profile_id)["catalog"].get("models", {}))
    print(json.dumps(service.snapshot(profile_id, models), ensure_ascii=False))


def save_pricing_source(profile_id):
    profile = billing_profile(profile_id)
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict) or not isinstance(payload.get("url"), str):
        raise RuntimeError("价格来源格式不正确。")
    url = payload["url"].strip()
    if url:
        from model_pricing import public_get, catalog_models
        catalog_models(json.loads(public_get(url)))  # public URL only; no credentials
    private_write(TEST_ROOT / "pricing" / (profile_id + "-source.json"),
                  json.dumps({"gateway": profile["gateway"], "url": url}))
    profile_pricing(profile_id, force=True)


def billing_module():
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import gateway_billing
    return gateway_billing


def billing_profile(profile_id):
    profile = next((p for p in load_profiles() if p["id"] == profile_id), None)
    if profile is None:
        raise RuntimeError("找不到所选调用配置。")
    return profile


def billing_config(profile):
    path = TEST_ROOT / "billing" / (profile["id"] + ".json")
    if not path.exists():
        return None
    if path.is_symlink() or path.stat().st_mode & 0o077:
        raise RuntimeError("账单凭据文件权限不符合要求，请重新连接。")
    data = json.loads(path.read_text())
    if not isinstance(data, dict):
        raise RuntimeError("账单连接配置损坏，请重新连接。")
    if data.get("gateway") != profile["gateway"]:
        return None  # Changing the gateway never forwards a saved management token.
    data["console"] = normalize_gateway(data["console"])
    return data


def profile_account(profile_id):
    profile = billing_profile(profile_id)
    data = billing_module().account_snapshot(profile["gateway"], read_profile_key(profile))
    print(json.dumps({**data, "profile_id": profile_id, "gateway": profile["gateway"]}, ensure_ascii=False))


def profile_billing(profile_id):
    profile = billing_profile(profile_id)
    config = billing_config(profile)
    data = billing_module().billing_snapshot(config["console"], config["token"]) if config else {
        "status": "unconfigured", "message": "逐次账单需要单独连接控制台的只读管理 Token。", "records": []}
    print(json.dumps({**data, "profile_id": profile_id, "gateway": profile["gateway"]}, ensure_ascii=False))


def billing_settings(profile_id):
    config = billing_config(billing_profile(profile_id))
    print(json.dumps({"console": config["console"] if config else "", "configured": config is not None}))


def save_billing_from_stdin(profile_id):
    profile = billing_profile(profile_id)
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict) or not isinstance(payload.get("console"), str) or not isinstance(payload.get("token"), str):
        raise RuntimeError("账单连接请求格式不正确。")
    console = normalize_gateway(payload["console"])
    token = payload["token"].strip()
    existing = billing_config(profile)
    if not token and existing and existing["console"] == console:
        token = existing["token"]
    if not token.startswith("cch_auth_live_") or len(token) < 20 or any(c.isspace() for c in token):
        raise RuntimeError("请填写 CCHP 只读管理 Token（cch_auth_live_ 开头），不是模型调用 Key。")
    snapshot = billing_module().billing_snapshot(console, token)
    if snapshot["status"] not in {"ok", "partial"}:
        raise RuntimeError(snapshot["message"] or "无法读取账单，未保存凭据。")
    path = TEST_ROOT / "billing" / (profile_id + ".json")
    if path.exists():
        private_write(path.with_suffix(".json.bak"), path.read_text())
    private_write(path, json.dumps({"gateway": profile["gateway"], "console": console, "token": token}) + "\n")
    print(json.dumps({**snapshot, "profile_id": profile_id, "gateway": profile["gateway"]}, ensure_ascii=False))


def remove_billing(profile_id):
    profile = billing_profile(profile_id)
    path = TEST_ROOT / "billing" / (profile["id"] + ".json")
    for candidate in (path, path.with_suffix(".json.bak")):
        candidate.unlink(missing_ok=True)
    print(json.dumps({"removed": True}))


def set_gateway_from_stdin():
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict) or not isinstance(payload.get("url"), str):
        raise RuntimeError("网关配置请求格式不正确。")
    print(json.dumps({"gateway": save_gateway(payload["url"])}, ensure_ascii=False))


def set_profile_gateway_from_stdin(profile_id):
    profile = next((item for item in load_profiles() if item["id"] == profile_id), None)
    if profile is None or profile_id == "free":
        raise RuntimeError("找不到可编辑的 Key 配置。")
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict) or not isinstance(payload.get("url"), str):
        raise RuntimeError("网关配置请求格式不正确。")
    gateway = normalize_gateway(payload["url"])
    models = fetch_models(read_profile_key(profile), gateway)
    data = json.loads(PROFILE_INDEX.read_text())
    data[profile_id] = {"name": profile["name"], "gateway": gateway}
    private_write(PROFILE_INDEX, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps({"profile_id": profile_id, "gateway": gateway, "model_count": len(models)}, ensure_ascii=False))


def launch_profile(profile_id, model, workspace_value, session_mode, gateway_value=None, permission_mode="restricted"):
    profile = next((item for item in load_profiles() if item["id"] == profile_id), None)
    if profile is None:
        raise RuntimeError("找不到所选调用 Key；请刷新列表。")
    workspace = Path(workspace_value).expanduser()
    try:
        workspace = workspace.resolve(strict=True)
    except OSError:
        raise RuntimeError("工作目录不存在或无法访问。") from None
    if not workspace.is_dir():
        raise RuntimeError("工作目录必须是文件夹。")
    if session_mode not in {"new", "continue", "resume"}:
        raise RuntimeError("会话方式无效。")
    key = read_profile_key(profile)
    gateway = normalize_gateway(gateway_value) if gateway_value else profile["gateway"]
    if model not in fetch_models(key, gateway):
        raise RuntimeError("此 Key 不允许所选模型；没有启动 Claude。")
    remember_workspace(workspace)
    save_last_launch(profile, model, workspace, session_mode, "claude", MANAGER_LAST_LAUNCH)
    prepare_isolated_config()
    fingerprint = history_module().key_fingerprint(key)
    scope = json.dumps([profile_id, model, gateway, str(workspace), fingerprint], ensure_ascii=False)
    home = CLAUDE_BRANCH_ROOT / ("scope-" + hashlib.sha256(scope.encode()).hexdigest()[:24])
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not (home / "settings.json").exists():
        private_write(home / "settings.json", SETTINGS.read_text())
    private_write(home / "workbench-profile.json", json.dumps({
        "profile_id": profile_id, "profile_name": profile["name"], "model": model,
        "gateway": gateway, "key_fingerprint": fingerprint,
    }, ensure_ascii=False))
    return launch(model, selected_key=key, selected_name=profile["name"],
                  workspace=workspace, session_mode=session_mode, gateway=gateway, config_dir=home,
                  permission_mode=permission_mode)


def codex_config(profile_id, model, gateway=None, workspace=None, branch_home=None, fingerprint=""):
    gateway = gateway or gateway_url()
    identity = [profile_id, model, gateway, str(workspace or "")]
    if fingerprint:
        identity.append(fingerprint)
    scope = json.dumps(identity, ensure_ascii=False, separators=(",", ":"))
    scope_id = hashlib.sha256(scope.encode("utf-8")).hexdigest()[:16]
    home = branch_home or CODEX_HOME_ROOT / profile_id / scope_id
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(home, 0o700)
    config = (
        'model = ' + json.dumps(model, ensure_ascii=False) + '\n'
        'model_provider = "cchp_local"\n'
        'model_reasoning_effort = "medium"\n'
        'sandbox_mode = "workspace-write"\n'
        'disable_response_storage = true\n\n'
        '[model_providers.cchp_local]\n'
        'name = "cchp_local"\n'
        'base_url = ' + json.dumps((gateway or gateway_url()) + "/v1", ensure_ascii=False) + '\n'
        'wire_api = "responses"\n'
        'env_key = "CCH_API_KEY"\n'
    )
    private_write(home / "config.toml", config)
    if branch_home is None:
        private_write(home / "workbench-profile.json", json.dumps({
            "profile_id": profile_id, "model": model, "gateway": gateway, "key_fingerprint": fingerprint,
        }, ensure_ascii=False))
    return home


def agent_permission_args(client, mode="restricted"):
    if mode not in {"restricted", "standard", "full"}:
        raise RuntimeError("权限模式无效；请重新设置。")
    if client == "claude":
        return ["--permission-mode", "bypassPermissions" if mode == "full" else "default"]
    if client != "codex":
        raise RuntimeError("未知客户端权限。")
    return ["--sandbox", "danger-full-access" if mode == "full" else "workspace-write",
            "--ask-for-approval", "on-request",
            "-c", "sandbox_workspace_write.network_access=" + ("false" if mode == "restricted" else "true")]

def local_network_bypass(environment):
    entries = ["127.0.0.1", "localhost", "::1"]
    for name in ("NO_PROXY", "no_proxy"):
        entries.extend(value.strip() for value in environment.get(name, "").split(",") if value.strip())
    return ",".join(dict.fromkeys(entries))


def build_codex_command(model, workspace, session_mode, history_id=None, fork_history=True, permission_mode="restricted"):
    options = ["--model", model, "--cd", str(workspace), "--no-alt-screen", "--no-daemon",
               *agent_permission_args("codex", permission_mode)]
    command = [str(CODEX), *options]
    if history_id:
        action = "fork" if fork_history else "resume"
        return [str(CODEX), action, history_id, *command[1:]]
    if session_mode == "continue":
        return [str(CODEX), "resume", "--last", *options]
    if session_mode == "resume":
        return [str(CODEX), "resume", *options]
    if session_mode != "new":
        raise RuntimeError("会话方式无效。")
    return command


def history_module():
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import session_history
    return session_history


def history_roots():
    profiles = {item["id"]: item["name"] for item in load_profiles()}
    codex_roots = []
    try:
        parents = [] if CODEX_HOME_ROOT.is_symlink() else list(CODEX_HOME_ROOT.iterdir())
    except OSError:
        parents = []
    for profile_dir in parents:
        if profile_dir.is_symlink() or not profile_dir.is_dir():
            continue
        label = profiles.get(profile_dir.name, "已移除的调用配置")
        codex_roots.append((profile_dir, profile_dir.name, label))
        try:
            homes = [p for p in profile_dir.iterdir()
                     if p.name.startswith("branch-") or (len(p.name) == 16
                     and all(c in "0123456789abcdef" for c in p.name))]
        except OSError:
            continue
        for home in homes:
            if home.is_dir() and not home.is_symlink():
                codex_roots.append((home, profile_dir.name, label))
    claude_roots = [(CONFIG_DIR, "", "")]
    if CLAUDE_BRANCH_ROOT.is_dir() and not CLAUDE_BRANCH_ROOT.is_symlink():
        claude_roots.extend((p, "", "") for p in CLAUDE_BRANCH_ROOT.iterdir()
                            if p.name.startswith(("branch-", "scope-")))
    return codex_roots, claude_roots


def global_history_roots():
    """Return user-level client homes only when the user explicitly imports them."""
    codex = [(USER_ROOT / ".codex", "global", "系统全局")]
    claude = [(USER_ROOT / ".claude", "global", "系统全局")]
    return codex, claude


def history_list(workspace=None, client=None):
    session_history = history_module()
    codex_roots, claude_roots = history_roots()
    rows = session_history.mark_scope(
        session_history.scan_history(codex_roots, claude_roots, workspace=workspace, client=client), "workbench")
    print(json.dumps({"sessions": session_history.public_rows(rows)}, ensure_ascii=False))


def global_history_list(workspace=None, client=None):
    session_history = history_module()
    codex_roots, claude_roots = global_history_roots()
    workbench_codex, workbench_claude = history_roots()
    rows = session_history.scan_all_history(
        workbench_codex, workbench_claude, codex_roots, claude_roots, workspace=workspace, client=client)
    print(json.dumps({"sessions": session_history.public_rows(rows)}, ensure_ascii=False))


def launch_history(history_ref, session_id, client, profile_id, model, workspace_value,
                   gateway_value=None, include_global=False, *, original=False, force_fork=False, permission_mode="restricted"):
    session_history = history_module()
    codex_roots, claude_roots = history_roots()
    if include_global:
        global_codex, global_claude = global_history_roots()
        codex_roots += global_codex
        claude_roots += global_claude
    row = session_history.resolve_history(
        history_ref, session_id, client, codex_roots, claude_roots, workspace=workspace_value)
    workspace = Path(workspace_value or row["workspace"]).expanduser()
    try:
        workspace = workspace.resolve(strict=True)
    except OSError:
        raise RuntimeError("原项目目录不存在或无法访问；请在历史记录中重新选择目录。") from None
    if not workspace.is_dir():
        raise RuntimeError("工作目录必须是文件夹。")
    if not row["workspace"] or workspace != Path(row["workspace"]).resolve():
        raise RuntimeError("恢复必须使用这条会话所属的项目目录；请从该目录的历史列表重新选择。")
    if original:
        profile_id = row["profile_id"]
        model = row["_configured_model"]
    profile = next((item for item in load_profiles() if item["id"] == profile_id), None)
    if profile is None:
        raise RuntimeError("原调用配置已移除或无法识别；可从当前项目的历史创建分支。" if original
                           else "找不到目标调用配置；请刷新配置列表。")
    if not isinstance(model, str) or not model.strip() or len(model) > 250 or any(c in model for c in "\0\r\n"):
        raise RuntimeError("无法识别原模型；可从当前项目的历史创建分支。" if original else "目标模型无效。")
    gateway = profile["gateway"]
    if gateway_value and normalize_gateway(gateway_value) != gateway:
        raise RuntimeError("目标网关已变更；请刷新配置后重新确认。")
    key = read_profile_key(profile)
    if not original and model not in fetch_models(key, gateway):
        raise RuntimeError("目标调用配置不允许此模型；没有启动恢复会话。")
    executable = CODEX if client == "codex" else CLAUDE
    if not executable.is_file():
        raise RuntimeError("未找到对应客户端；没有恢复会话。")
    fingerprint = session_history.key_fingerprint(key)
    matches = session_history.can_resume_in_place(row, profile_id, model, gateway, fingerprint)
    if original and not matches:
        raise RuntimeError("无法确认原 Key 或网关仍可用；可从当前项目的历史创建分支。")
    same_config = matches and not force_fork
    if same_config:
        home, seed = Path(row["_home"]), Path(row["_path"])
    else:
        parent = CODEX_HOME_ROOT / profile_id if client == "codex" else CLAUDE_BRANCH_ROOT
        home, seed = session_history.stage_branch(row, parent, profile_id, profile["name"], model, gateway, fingerprint)
    print("恢复方式：" + ("原地续接（保留原会话 ID）" if same_config else "独立分支（保留来源会话）"), flush=True)
    try:
        if client == "codex":
            if not same_config:
                codex_config(profile_id, model, gateway, workspace, branch_home=home)
            environment = dict(os.environ)
            for name in ("OPENAI_API_KEY", "CODEX_SQLITE_HOME", "OPENAI_BASE_URL"):
                environment.pop(name, None)
            bypass = local_network_bypass(environment)
            environment.update(CODEX_HOME=str(home), CCH_API_KEY=key,
                               NO_PROXY=bypass, no_proxy=bypass)
            command = build_codex_command(model, workspace, "resume", session_id, fork_history=not same_config,
                                          permission_mode=permission_mode)
        else:
            if not (home / "settings.json").exists():
                private_write(home / "settings.json", "{}\n")
            command, environment = build_launch(key, model, workspace=workspace, gateway=gateway,
                                               config_dir=home, isolated_branch=True, permission_mode=permission_mode)
            command += ["--resume", session_id]
            if not same_config:
                command += ["--fork-session"]
        with subprocess.Popen(command, cwd=workspace, env=environment) as child:
            # The CLI owns terminal interrupts; keep the wrapper alive to remove its input copy.
            previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
            try:
                return child.wait()
            finally:
                signal.signal(signal.SIGINT, previous)
    finally:
        # Codex paginated history references its input rollouts from the new session.
        if client == "claude" and not same_config:
            seed.unlink(missing_ok=True)


def launch_codex_profile(profile_id, model, workspace_value, session_mode, gateway_value=None, permission_mode="restricted"):
    profile = next((item for item in load_profiles() if item["id"] == profile_id), None)
    if profile is None:
        raise RuntimeError("找不到所选调用 Key；请刷新列表。")
    workspace = Path(workspace_value).expanduser()
    try:
        workspace = workspace.resolve(strict=True)
    except OSError:
        raise RuntimeError("工作目录不存在或无法访问。") from None
    if not workspace.is_dir():
        raise RuntimeError("工作目录必须是文件夹。")
    if session_mode not in {"new", "continue", "resume"}:
        raise RuntimeError("会话方式无效。")
    key = read_profile_key(profile)
    gateway = normalize_gateway(gateway_value) if gateway_value else profile["gateway"]
    if model not in fetch_models(key, gateway):
        raise RuntimeError("此 Key 不允许所选模型；没有启动 Codex。")
    if not CODEX.is_file():
        raise RuntimeError("未找到原有 Codex 可执行文件；没有修改原入口。")
    codex_home = codex_config(profile_id, model, gateway, workspace,
                              fingerprint=history_module().key_fingerprint(key))
    remember_workspace(workspace)
    save_last_launch(profile, model, workspace, session_mode, "codex", MANAGER_LAST_LAUNCH)
    environment = dict(os.environ)
    for name in ("OPENAI_API_KEY", "CODEX_HOME", "CCH_API_KEY"):
        environment.pop(name, None)
    bypass = local_network_bypass(environment)
    environment.update({
        "CODEX_HOME": str(codex_home),
        "CCH_API_KEY": key,
        "NO_PROXY": bypass,
        "no_proxy": bypass,
    })
    command = build_codex_command(model, workspace, session_mode, permission_mode=permission_mode)
    os.chdir(workspace)
    os.execve(CODEX, command, environment)


def choose_profile():
    profiles = load_profiles()
    if not profiles:
        raise RuntimeError("还没有保存本机调用 Key；请先选菜单 1 或 6。")
    print("\n选择本机调用 Key：")
    for number, profile in enumerate(profiles, 1):
        print(str(number) + ". " + profile["name"])
    choice = input("编号：").strip()
    if not choice.isdigit() or not 1 <= int(choice) <= len(profiles):
        raise RuntimeError("Key 编号无效。")
    return profiles[int(choice) - 1]


def choose_model(profile):
    key = read_profile_key(profile)
    models = fetch_models(key)
    print("\n" + profile["name"] + " 可用模型（只显示此 Key 的白名单）：")
    for number, model in enumerate(models, 1):
        print(str(number) + ". " + model)
    choice = input("输入模型编号或完整模型名：").strip()
    if choice.isdigit() and 1 <= int(choice) <= len(models):
        return key, models[int(choice) - 1]
    if choice in models:
        return key, choice
    raise RuntimeError("该模型不在所选 Key 的白名单中；没有启动 Claude。")


def load_workspaces():
    paths = [str(WORKSPACE.resolve())]
    if WORKSPACE_INDEX.is_file():
        data = json.loads(WORKSPACE_INDEX.read_text())
        if not isinstance(data, list) or any(not isinstance(item, str) for item in data):
            raise RuntimeError("最近使用的目录索引损坏，已停止且未修改。")
        for item in data:
            path = Path(item).expanduser()
            if path.is_dir():
                resolved = str(path.resolve())
                if resolved not in paths:
                    paths.append(resolved)
    return paths


def remember_workspace(path):
    paths = [str(path)]
    if WORKSPACE_INDEX.is_file():
        data = json.loads(WORKSPACE_INDEX.read_text())
        if not isinstance(data, list) or any(not isinstance(item, str) for item in data):
            raise RuntimeError("最近使用的目录索引损坏，已停止且未修改。")
        paths.extend(item for item in data if item != str(path))
    private_write(WORKSPACE_INDEX, json.dumps(paths[:12], ensure_ascii=False, indent=2) + "\n")


def choose_workspace():
    paths = load_workspaces()
    print("\n选择 Claude 工作目录（会话按目录区分）：")
    for number, path in enumerate(paths, 1):
        print(str(number) + ". " + path)
    print("0. 输入其他目录路径")
    choice = input("编号：").strip()
    if choice == "0":
        raw_path = input("粘贴目录完整路径：").strip().strip("\"").strip("'")
        path = Path(raw_path).expanduser()
        try:
            path = path.resolve(strict=True)
        except OSError:
            raise RuntimeError("目录不存在或无法访问；没有启动 Claude。") from None
        if not path.is_dir():
            raise RuntimeError("所选路径不是文件夹；没有启动 Claude。")
    elif choice.isdigit() and 1 <= int(choice) <= len(paths):
        path = Path(paths[int(choice) - 1])
    else:
        raise RuntimeError("目录编号无效；没有启动 Claude。")
    remember_workspace(path)
    print("Claude 工作目录：" + str(path))
    return path


def choose_session_mode():
    print("\n会话：")
    print("1. 新建会话")
    print("2. 继续此目录最近的会话")
    print("3. 从此目录的会话列表中恢复")
    choice = input("编号：").strip()
    if choice == "1":
        return "new"
    if choice not in {"2", "3"}:
        raise RuntimeError("会话选项无效；没有启动 Claude。")
    print("恢复会话会把该会话的历史上下文发送给刚才选择的 CCHP Key 和上游模型。")
    if input("确认恢复？输入 Y 继续：").strip().lower() != "y":
        raise RuntimeError("已取消恢复；原会话未修改。")
    return "continue" if choice == "2" else "resume"


def load_last_launch(path=LAST_LAUNCH):
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text())
    except (ValueError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    if not all(isinstance(data.get(name), str) for name in ("profile_id", "model", "workspace", "session_mode")):
        return None
    if data["session_mode"] not in {"new", "continue", "resume"}:
        return None
    return data


def save_last_launch(profile, model, workspace, session_mode, client="claude", path=LAST_LAUNCH):
    private_write(path, json.dumps({
        "client": client,
        "profile_id": profile["id"],
        "model": model,
        "workspace": str(workspace),
        "session_mode": session_mode,
    }, ensure_ascii=False, indent=2) + "\n")


def quick_launch():
    previous = load_last_launch()
    if previous:
        profiles = {profile["id"]: profile for profile in load_profiles()}
        profile = profiles.get(previous["profile_id"])
        workspace = Path(previous["workspace"])
        if profile and workspace.is_dir():
            key = read_profile_key(profile)
            models = fetch_models(key)
            if previous["model"] in models:
                mode_label = {"new": "新会话", "continue": "继续最近会话", "resume": "打开会话选择器"}[previous["session_mode"]]
                print("\n上次配置：" + profile["name"] + " / " + previous["model"])
                print("工作目录：" + str(workspace))
                print("会话方式：" + mode_label)
                print("回车快速启动；输入 c 更改；输入 q 取消。")
                choice = input("请选择：").strip().lower()
                if choice == "q":
                    return 0
                if choice == "":
                    if previous["session_mode"] in {"continue", "resume"}:
                        print("这会把所选会话历史提供给当前 Key/上游模型。")
                        if input("确认继续？输入 Y：").strip().lower() != "y":
                            print("已取消；没有启动 Claude。")
                            return 0
                    return launch(previous["model"], selected_key=key, selected_name=profile["name"],
                                  workspace=workspace, session_mode=previous["session_mode"])
                if choice != "c":
                    print("请输入回车、c 或 q。")
                    return 0
            else:
                print("上次模型已不在此 Key 的白名单中，请重新选择。")
        else:
            print("上次 Key 或工作目录不可用，请重新选择。")
    profile = choose_profile()
    key, model = choose_model(profile)
    workspace = choose_workspace()
    session_mode = choose_session_mode()
    save_last_launch(profile, model, workspace, session_mode)
    return launch(model, selected_key=key, selected_name=profile["name"],
                  workspace=workspace, session_mode=session_mode)


class NoModelListRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def close_http_error(exc):
    # Test doubles and some urllib handlers do not provide a closable response body.
    if getattr(exc, "fp", None) is not None:
        exc.close()


def fetch_models(key, gateway=None):
    gateway = gateway or gateway_url()
    url = gateway + "/v1/models"
    headers = {"User-Agent": "claude-cchp-test-metadata/1.0"}
    request = Request(
        url + "?limit=1000",
        headers={
            **headers,
            "x-api-key": key,
            "anthropic-version": "2023-06-01",
        },
    )
    # Keep credentials on the configured host and preserve existing x-api-key routes.
    opener = build_opener(ProxyHandler({}), NoModelListRedirect())
    try:
        try:
            response = opener.open(request, timeout=12)
        except HTTPError as exc:
            if exc.code not in (401, 403):
                raise
            close_http_error(exc)
            response = opener.open(Request(url, headers={**headers, "Authorization": "Bearer " + key}), timeout=12)
        with response:
            payload = json.load(response)
    except HTTPError as exc:
        close_http_error(exc)
        if 300 <= exc.code < 400:
            raise RuntimeError("模型列表接口要求重定向，请填写服务的最终地址。") from None
        raise RuntimeError("网关模型列表校验失败：HTTP " + str(exc.code)) from None
    except (URLError, TimeoutError, OSError):
        raise RuntimeError("无法连接网关 " + gateway + "，请先确认网关服务已启动。") from None
    except (ValueError, TypeError):
        raise RuntimeError("模型列表不是预期的 JSON，请检查网关地址。") from None
    rows = payload.get("data", []) if isinstance(payload, dict) else []
    models = sorted({row["id"] for row in rows if isinstance(row, dict) and isinstance(row.get("id"), str)})
    if not models:
        raise RuntimeError("此 Key 未返回可用模型；未启动客户端，也不会回退其他来源。")
    return models


def setup_key():
    if not sys.stdin.isatty():
        raise RuntimeError("请在你自己的终端或桌面测试入口中运行 setup；密钥输入会隐藏。")
    print("只填本机 CCHP 创建的「本机自用 · AutoBits 免费」完整调用 Key。")
    print("输入不显示，不要填上游 Key；本步骤仅查询模型列表，不调用模型。")
    key = getpass.getpass("本机免费调用 Key：").strip()
    if len(key) < 20 or any(c.isspace() for c in key) or key[:1] in {"'", '"'}:
        raise RuntimeError("格式不正确；请粘贴完整 Key，不带引号、空格或换行。")
    models = fetch_models(key)
    if DEFAULT_MODEL not in models:
        raise RuntimeError("此 Key 的模型列表没有 " + DEFAULT_MODEL + "，因此没有保存。请核对是否选了免费调用 Key。")
    prepare_isolated_config()
    private_write(KEY_FILE, key + "\n")
    if KEY_FILE.stat().st_mode & 0o077:
        raise RuntimeError("密钥文件权限校验失败；请删除该文件后重试。")
    private_write(TEST_ROOT / "model-list-check.json", json.dumps({"checked_at": time.strftime("%Y-%m-%d %H:%M:%S%z"), "gateway": gateway_url(), "model_count": len(models), "models": models, "inference_sent": False}, ensure_ascii=False, indent=2) + "\n")
    print("已保存到本机私有文件（权限 600）。模型列表校验通过，共 " + str(len(models)) + " 个。")
    print("尚未发起推理。可以回聊天告诉我“密钥已填”，或选择菜单中的连通性测试。")


def build_launch(key, model, smoke=False, workspace=None, session_mode="new", gateway=None, config_dir=None,
                 isolated_branch=False, permission_mode="restricted"):
    gateway = normalize_gateway(gateway) if gateway else gateway_url()
    environment = dict(os.environ)
    for name in list(environment):
        if name.startswith("ANTHROPIC_") or name in {
            "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK",
            "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY", "DEBUG",
        }:
            environment.pop(name, None)
    bypass = local_network_bypass(environment)
    environment.update({
        "CLAUDE_CONFIG_DIR": str(config_dir or CONFIG_DIR),
        "ANTHROPIC_BASE_URL": gateway,
        # Installed Claude's --bare mode reads API_KEY, not AUTH_TOKEN/OAuth/keychain.
        "ANTHROPIC_API_KEY": key,
        "ANTHROPIC_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
        "ANTHROPIC_SMALL_FAST_MODEL": model,
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
        "DISABLE_AUTOUPDATER": "1",
        "NO_PROXY": bypass,
        "no_proxy": bypass,
        "MAX_THINKING_TOKENS": "0",
    })
    settings = config_dir / "settings.json" if isolated_branch else SETTINGS
    mcp = '{"mcpServers":{}}' if isolated_branch else str(EMPTY_MCP)
    command = [str(CLAUDE), "--bare", "--setting-sources", "user", "--settings", str(settings),
               "--strict-mcp-config", "--mcp-config", mcp, "--model", model,
               "--append-system-prompt", "全程使用简体中文。"]
    if smoke:
        environment["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = "256"
        command += ["--print", "--no-session-persistence", "--tools", "", "--",
                    "只回复：本机网关已连通。不要调用工具，不要读取文件，不要执行命令。"]
    else:
        command += ["--add-dir", str(workspace or WORKSPACE), *agent_permission_args("claude", permission_mode)]
        if session_mode == "continue":
            command.append("--continue")
        elif session_mode == "resume":
            command.append("--resume")
        elif session_mode != "new":
            raise RuntimeError("会话选项无效；没有启动 Claude。")
    return command, environment


def launch(model, smoke=False, selected_key=None, selected_name=None, workspace=None, session_mode="new", gateway=None,
           config_dir=None, permission_mode="restricted"):
    if not CLAUDE.is_file():
        raise RuntimeError("未找到原有 Claude 可执行文件；没有修改原入口。")
    key = selected_key if selected_key is not None else read_key()
    gateway = normalize_gateway(gateway) if gateway else gateway_url()
    if model not in fetch_models(key, gateway):
        raise RuntimeError("此调用 Key 不允许模型 " + model + "；已停止，不会回退。")
    prepare_isolated_config()
    cwd = workspace or WORKSPACE
    command, environment = build_launch(key, model, smoke, cwd, session_mode, gateway,
                                        config_dir=config_dir, isolated_branch=config_dir is not None,
                                        permission_mode=permission_mode)
    if selected_name:
        print("本机调用 Key：" + selected_name, flush=True)
    print("网关：" + gateway + "；模型：" + model, flush=True)
    print("Claude 工作目录：" + str(cwd), flush=True)
    if smoke:
        print("现在将发起单轮模型请求，禁用工具，不加载原有项目、插件、MCP 或钩子。", flush=True)
        started = time.monotonic()
        try:
            result = subprocess.run(command, env=environment, cwd=cwd, capture_output=True, text=True, timeout=120)
        except subprocess.TimeoutExpired:
            raise RuntimeError("请求超过 120 秒，已停止本次客户端测试；请查看 CCHP 使用记录。") from None
        stdout = result.stdout.replace(key, "[密钥已隐藏]")
        stderr = result.stderr.replace(key, "[密钥已隐藏]")
        reservation_rejected = "billing/reserve: insufficient balance" in stdout or "billing/reserve: insufficient balance" in stderr
        private_write(TEST_ROOT / "latest-smoke.json", json.dumps({"model": model, "gateway": gateway_url(), "exit_code": result.returncode, "duration_seconds": round(time.monotonic()-started, 2), "stdout": stdout, "stderr": stderr}, ensure_ascii=False, indent=2) + "\n")
        if stdout.strip(): print(stdout.strip())
        if stderr.strip(): print(stderr.strip(), file=sys.stderr)
        if reservation_rejected:
            print("CCHP 本地预扣余额不足，请求在转发到上游前已被拒绝；没有调用 AutoBits。请勿重复重试。")
        return result.returncode
    os.chdir(cwd)
    os.execve(CLAUDE, command, environment)


def status():
    prepare_isolated_config()
    latest = CLIENT_ROOT / "latest-backup.txt"
    backup = Path(latest.read_text().strip()) if latest.is_file() else None
    checks = {}
    if backup and (backup / "manifest.json").is_file():
        manifest = json.loads((backup / "manifest.json").read_text())
        for row in manifest.get("files", []):
            source = Path(row["source"])
            checks[str(source)] = source.is_file() and hashlib.sha256(source.read_bytes()).hexdigest() == row["sha256"]
        for row in manifest.get("entrypoints", []):
            source = Path(row["path"])
            checks[str(source)] = source.is_symlink() and os.readlink(source) == row["target"] if row["symlink"] else source.is_file()
    print(json.dumps({"gateway": gateway_url(), "default_model": DEFAULT_MODEL, "key_saved": KEY_FILE.is_file(),
                      "additional_key_profiles": [profile["name"] for profile in load_profiles() if profile["id"] != "free"],
                      "last_launch": load_last_launch(),
                      "isolated_settings_ready": SETTINGS.is_file() and EMPTY_MCP.is_file(),
                      "config_directory": str(CONFIG_DIR), "workspaces": load_workspaces(), "backup": str(backup) if backup else None,
                      "original_file_checks": checks, "all_originals_unchanged": all(checks.values()) if checks else None}, ensure_ascii=False, indent=2))


def plan(model):
    command, environment = build_launch("<not-a-real-key>", model, workspace=WORKSPACE)
    print(json.dumps({"command": command, "base_url": environment["ANTHROPIC_BASE_URL"],
                      "config_directory": environment["CLAUDE_CONFIG_DIR"], "auth_variable": "ANTHROPIC_API_KEY",
                      "legacy_auth_token_removed": "ANTHROPIC_AUTH_TOKEN" not in environment,
                      "model": model, "workspace": str(WORKSPACE), "session_mode": "new",
                      "all_tier_models_pinned": all(environment[name] == model for name in ("ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL")),
                      "inference_sent": False}, ensure_ascii=False, indent=2))


def menu():
    while True:
        print("\nClaude 本机网关（独立入口；原来的 claude、Codex 不变）")
        print("1  填入/更新本机免费调用 Key（隐藏输入，只查询模型列表）")
        print("2  查看配置、备份和原文件校验（不调用模型）")
        print("3  快速启动 Claude（回车沿用上次配置；c 切换）")
        print("4  单轮连通性测试（选择 Key 和模型；会调用模型，禁用工具）")
        print("5  查看某把 Key 的可用模型（不调用模型）")
        print("6  添加其他本机调用 Key（只查询白名单，不调用模型）")
        print("0  退出")
        choice = input("请选择：").strip()
        try:
            if choice == "0": return 0
            if choice == "1": setup_key()
            elif choice == "2": status()
            elif choice == "3":
                return quick_launch()
            elif choice == "4":
                profile = choose_profile()
                key, model = choose_model(profile)
                launch(model, True, selected_key=key, selected_name=profile["name"])
            elif choice == "5":
                profile = choose_profile()
                print("\n".join(fetch_models(read_profile_key(profile))))
            elif choice == "6": add_profile_key()
            else: print("请输入 0 至 6。")
        except (RuntimeError, ValueError, OSError) as exc:
            print("未完成：" + str(exc), file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="独立 Claude/CCHP 测试，不修改原客户端配置")
    parser.add_argument("action", nargs="?", default="menu", choices=[
        "menu", "setup", "status", "models", "plan", "chat", "smoke", "usage",
        "profile-account", "profile-billing", "billing-settings", "save-billing-stdin", "remove-billing",
        "profile-pricing", "save-pricing-source-stdin",
        "gui-state", "history-list", "history-list-global", "history-launch", "history-import-launch", "history-resume-original",
        "profile-models", "add-profile-stdin", "remove-profile", "set-gateway-stdin",
        "set-profile-gateway-stdin", "launch", "codex-launch",
    ])
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--profile-id")
    parser.add_argument("--workspace")
    parser.add_argument("--session-mode", choices=["new", "continue", "resume"], default="new")
    parser.add_argument("--gateway-url")
    parser.add_argument("--history-ref")
    parser.add_argument("--session-id")
    parser.add_argument("--client", choices=["codex", "claude"])
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--fork-history", action="store_true")
    parser.add_argument("--permission-mode", choices=["restricted", "standard", "full"], default="restricted")
    arguments = parser.parse_args()
    if arguments.action == "menu": return menu()
    if arguments.action == "setup": setup_key(); return 0
    if arguments.action == "status": status(); return 0
    if arguments.action == "usage": usage_snapshot(); return 0
    if arguments.action in {"profile-pricing", "save-pricing-source-stdin"}:
        if not arguments.profile_id: raise RuntimeError("缺少 profile-id。")
        pricing_service()  # initialise bundled imports without writing bytecode
        if arguments.action == "profile-pricing": profile_pricing(arguments.profile_id, arguments.force)
        else: save_pricing_source(arguments.profile_id)
        return 0
    billing_actions = {"profile-account": profile_account, "profile-billing": profile_billing,
                       "billing-settings": billing_settings, "save-billing-stdin": save_billing_from_stdin,
                       "remove-billing": remove_billing}
    if arguments.action in billing_actions:
        if not arguments.profile_id: raise RuntimeError("缺少 profile-id。")
        billing_actions[arguments.action](arguments.profile_id); return 0
    if arguments.action == "plan": plan(arguments.model); return 0
    if arguments.action == "gui-state": gui_state(); return 0
    if arguments.action == "history-list": history_list(arguments.workspace, arguments.client); return 0
    if arguments.action == "history-list-global": global_history_list(arguments.workspace, arguments.client); return 0
    if arguments.action == "history-resume-original":
        if not all((arguments.history_ref, arguments.session_id, arguments.client, arguments.workspace)):
            raise RuntimeError("恢复会话缺少历史来源或工作目录。")
        return launch_history(arguments.history_ref, arguments.session_id, arguments.client,
                              None, None, arguments.workspace, original=True, permission_mode=arguments.permission_mode)
    if arguments.action in {"history-launch", "history-import-launch"}:
        if not all((arguments.history_ref, arguments.session_id, arguments.client,
                    arguments.profile_id, arguments.workspace)):
            raise RuntimeError("恢复会话缺少历史来源、目标配置或工作目录。")
        return launch_history(arguments.history_ref, arguments.session_id, arguments.client,
                              arguments.profile_id, arguments.model, arguments.workspace,
                              arguments.gateway_url,
                              include_global=arguments.action == "history-import-launch",
                              force_fork=arguments.fork_history, permission_mode=arguments.permission_mode)
    if arguments.action == "profile-models":
        if not arguments.profile_id: raise RuntimeError("缺少 profile-id。")
        profile_models(arguments.profile_id, arguments.gateway_url); return 0
    if arguments.action == "add-profile-stdin": add_profile_from_stdin(); return 0
    if arguments.action == "remove-profile":
        if not arguments.profile_id: raise RuntimeError("缺少 profile-id。")
        remove_profile_key(arguments.profile_id); return 0
    if arguments.action == "set-gateway-stdin": set_gateway_from_stdin(); return 0
    if arguments.action == "set-profile-gateway-stdin":
        if not arguments.profile_id: raise RuntimeError("缺少 profile-id。")
        set_profile_gateway_from_stdin(arguments.profile_id); return 0
    if arguments.action == "launch":
        if not arguments.profile_id or not arguments.workspace:
            raise RuntimeError("启动 Claude 时必须指定 profile-id 和工作目录。")
        return launch_profile(arguments.profile_id, arguments.model, arguments.workspace, arguments.session_mode, arguments.gateway_url,
                              permission_mode=arguments.permission_mode)
    if arguments.action == "codex-launch":
        if not arguments.profile_id or not arguments.workspace:
            raise RuntimeError("启动 Codex 时必须指定 profile-id 和工作目录。")
        return launch_codex_profile(arguments.profile_id, arguments.model, arguments.workspace, arguments.session_mode, arguments.gateway_url,
                                    permission_mode=arguments.permission_mode)
    if arguments.action == "models": print("\n".join(fetch_models(read_key()))); return 0
    return launch(arguments.model, arguments.action == "smoke")


if __name__ == "__main__":
    try:
        sys.exit(main() or 0)
    except (RuntimeError, ValueError, OSError) as error:
        print("未完成：" + str(error), file=sys.stderr)
        sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print("\n已退出，原客户端配置未修改。")
        sys.exit(130)
