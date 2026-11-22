"""Private MCP/skill/plugin registration. Importing never starts a service."""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
from urllib.parse import urlsplit

BEGIN = "\n# BEGIN WORKBENCH MANAGED TOOLS\n"
END = "# END WORKBENCH MANAGED TOOLS\n"


def private_write(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(text)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def toml(value):
    if isinstance(value, dict):
        return "{" + ", ".join(json.dumps(k) + " = " + toml(v) for k, v in value.items()) + "}"
    if isinstance(value, list):
        return "[" + ", ".join(map(toml, value)) + "]"
    return json.dumps(value, ensure_ascii=False)


def normalize_servers(value):
    if not isinstance(value, dict):
        raise ValueError("MCP 配置须为 JSON 对象")
    servers = value.get("mcpServers", value.get("mcp_servers", value))
    if not isinstance(servers, dict) or len(servers) > 64:
        raise ValueError("MCP 配置必须为对象，最多 64 个服务")
    result = {}
    allowed = {"command", "args", "env", "url", "type", "headers", "http_headers", "enabled",
               "env_vars", "bearer_token_env_var", "env_http_headers", "cwd", "startup_timeout_sec", "tool_timeout_sec"}
    for name, server in servers.items():
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name) or not isinstance(server, dict):
            raise ValueError("服务名仅可包含字母、数字、下划线和连字符")
        if set(server) - allowed:
            raise ValueError("配置含暂不支持的 MCP 字段；请使用 command/args/env 或 HTTP url/headers")
        item = dict(server)
        if bool(item.get("command")) == bool(item.get("url")):
            raise ValueError("每个 MCP 服务须填写 command 或 url 其中一项")
        for key in ("command", "url", "cwd", "bearer_token_env_var"):
            if key in item and (not isinstance(item[key], str) or not item[key] or "\0" in item[key]):
                raise ValueError("MCP 程序、路径或地址格式错误")
        if item.get("type", "stdio" if item.get("command") else "http") not in ("stdio", "http", "streamable_http", "sse"):
            raise ValueError("不支持此 MCP 传输方式")
        if item.get("url"):
            address = urlsplit(item["url"])
            if address.scheme not in ("http", "https") or not address.hostname or address.username or address.password:
                raise ValueError("MCP 地址须为不含账号密码的 HTTP(S) 地址")
            if address.scheme == "http" and address.hostname not in ("localhost", "127.0.0.1", "::1"):
                raise ValueError("远程 MCP 服务须使用 HTTPS")
        for key in ("args", "env_vars"):
            if key in item and (not isinstance(item[key], list) or any(not isinstance(v, str) or "\0" in v for v in item[key])):
                raise ValueError("MCP 参数须为字符串数组")
        for key in ("env", "headers", "http_headers", "env_http_headers"):
            if key in item and (not isinstance(item[key], dict) or any(not isinstance(v, str) or "\0" in v for v in item[key].values())):
                raise ValueError("MCP 环境变量与请求头须为字符串映射")
        if "enabled" in item and not isinstance(item["enabled"], bool):
            raise ValueError("enabled 须为布尔值")
        for key in ("startup_timeout_sec", "tool_timeout_sec"):
            if key in item and (type(item[key]) not in (int, float) or not 0 < item[key] <= 600):
                raise ValueError("MCP 超时须为 0 至 600 秒")
        result[name] = item
    return result


def native_servers(servers, client):
    result = {}
    for name, original in normalize_servers(servers).items():
        if original.get("enabled", True) is False:
            continue
        item = dict(original)
        item.pop("enabled", None)
        transport = item.pop("type", "stdio" if item.get("command") else "http")
        if client == "codex":
            if transport == "sse":
                raise ValueError("Codex 不支持旧 SSE MCP；请改用 Streamable HTTP")
            if "headers" in item:
                item["http_headers"] = item.pop("headers")
        else:
            if "http_headers" in item:
                item["headers"] = item.pop("http_headers")
            if "env_vars" in item:
                env = item.setdefault("env", {})
                for key in item.pop("env_vars"):
                    env[key] = environment_reference(key)
            headers = item.setdefault("headers", {}) if item.get("url") else {}
            if item.get("bearer_token_env_var"):
                variable = item.pop("bearer_token_env_var")
                headers["Authorization"] = "Bearer " + environment_reference(variable)
            for key, variable in item.pop("env_http_headers", {}).items():
                headers[key] = environment_reference(variable)
            if item.pop("cwd", None):
                raise ValueError("Claude MCP 不支持服务级 cwd；请改用绝对程序路径")
            item.pop("startup_timeout_sec", None)
            item.pop("tool_timeout_sec", None)
            if item.get("url"):
                item["type"] = "sse" if transport == "sse" else "http"
        result["workbench_" + name] = item
    return result


def environment_reference(name):
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
        raise ValueError("MCP 环境变量名格式错误")
    # Claude expands these in its actual launch environment. Never snapshot tokens.
    return "${" + name + "}"


def publish_directory(destination, build):
    destination = Path(destination)
    if destination.exists():
        return
    destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix=".workbench-stage-", dir=destination.parent) as stage:
        content = Path(stage) / "content"
        build(content)
        content.rename(destination)


def copy_component(source, destination):
    publish_directory(destination, lambda stage: shutil.copytree(
        source, stage, ignore=shutil.ignore_patterns(".git", ".DS_Store")))


def settings(root):
    path = Path(root) / "tools.json"
    if not path.exists():
        return {"mcpServers": {}, "screen": False, "items": []}
    return json.loads(path.read_text(encoding="utf-8"))


def save_settings(root, payload, client=None):
    value = settings(root)
    value["mcpServers"] = normalize_servers(json.loads(payload.get("config", "{}")))
    value["screen"] = payload.get("screen") is True
    disabled = payload.get("disabled", [])
    for item in value["items"]:
        item["enabled"] = item["id"] not in disabled
    if value["screen"] and value["mcpServers"].get("screen", {}).get("enabled", True) and "screen" in value["mcpServers"]:
        raise ValueError("screen 名称已被自定义 MCP 使用；请重命名或停用内置屏幕读取")
    if client:
        native_servers(value["mcpServers"], client)
        for item in value["items"]:
            if item["enabled"] and item["kind"] == "plugin":
                if client != "claude":
                    raise ValueError("完整 Claude 插件仅能用于 Claude")
                native_servers(plugin_mcp(Path(root) / "components" / item["id"]), client)
    private_write(Path(root) / "tools.json", json.dumps(value, ensure_ascii=False, indent=2))
    return public_settings(root)


def public_settings(root):
    value = settings(root)
    # The configuration editor is local and intentionally reveals only settings
    # entered/imported here. Never include it in logs, diagnostics or releases.
    return {**value, "config": json.dumps({"mcpServers": value["mcpServers"]}, ensure_ascii=False, indent=2)}


def import_directory(root, source, kind):
    source = Path(source).expanduser().resolve()
    marker = source / ("SKILL.md" if kind == "skill" else ".claude-plugin/plugin.json")
    if kind not in ("skill", "plugin") or not marker.is_file():
        raise ValueError("请选择包含 SKILL.md 的技能目录或包含 .claude-plugin/plugin.json 的 Claude 插件目录")
    # Copy a dedicated component, never an entire external client home. No hooks
    # or services run during import. Imported plugin execution requires Save.
    files = []
    size = 0
    for path in source.rglob("*"):
        if ".git" in path.relative_to(source).parts:
            continue
        if path.is_symlink():
            raise ValueError("组件包含符号链接；请先准备独立副本，避免带入外部目录")
        if path.is_file():
            size += path.stat().st_size
            files.append(path)
        if len(files) > 10000 or size > 100 * 1024 * 1024:
            raise ValueError("组件超过 100 MB 或一万文件；请选择单个技能/插件")
    digest = hashlib.sha256()
    for path in sorted(files):
        for field in (str(path.relative_to(source)).encode(), path.read_bytes()):
            digest.update(len(field).to_bytes(8, "big"))
            digest.update(field)
    identity = kind + "-" + digest.hexdigest()[:16]
    destination = Path(root) / "components" / identity
    copy_component(source, destination)
    value = settings(root)
    if not any(item["id"] == identity for item in value["items"]):
        slug = re.sub(r"[^a-z0-9-]+", "-", source.name.lower()).strip("-")[:50] or "skill"
        name = skill_name(source / "SKILL.md", slug) if kind == "skill" else ""
        command = "workbench-" + slug + "-" + identity[-6:] + ":" + name if kind == "skill" else ""
        value["items"].append({"id": identity, "name": source.name, "kind": kind, "enabled": False,
                               "slug": slug, "command": command})
        private_write(Path(root) / "tools.json", json.dumps(value, ensure_ascii=False, indent=2))
    return public_settings(root)


def skill_name(path, fallback):
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        frontmatter = text.split("---", 2)
        if len(frontmatter) == 3:
            match = re.search(r"(?m)^name:\s*['\"]?([a-z0-9-]+)['\"]?\s*$", frontmatter[1])
            if match:
                return match[1]
    return fallback


def plugin_mcp(component):
    component = Path(component)
    manifest = json.loads((component / ".claude-plugin/plugin.json").read_text())
    declarations = []
    if (component / ".mcp.json").is_file():
        declarations.append(".mcp.json")
    declared = manifest.get("mcpServers", [])
    declarations.extend(declared if isinstance(declared, list) else [declared])
    result = {}
    for entry in declarations:
        if isinstance(entry, str):
            path = (component / entry).resolve()
            if not path.is_relative_to(component.resolve()):
                raise ValueError("插件 MCP 配置文件须位于插件副本内部")
            entry = json.loads(path.read_text())
        def expand(value):
            if isinstance(value, str): return value.replace("${CLAUDE_PLUGIN_ROOT}", str(component))
            if isinstance(value, list): return [expand(v) for v in value]
            if isinstance(value, dict): return {k: expand(v) for k, v in value.items()}
            return value
        result.update(normalize_servers(expand(entry)))
    return result


def apply_tools(root, home, client, screen_command=None):
    root, home = Path(root), Path(home)
    value = settings(root)
    servers = native_servers(value["mcpServers"], client)
    if value["screen"]:
        if "workbench_screen" in servers:
            raise ValueError("screen 名称已被自定义 MCP 使用；请重命名或停用内置屏幕读取")
        if not screen_command:
            raise ValueError("当前运行环境不提供系统屏幕读取，请停用此选项或连接远端截图 MCP")
        servers["workbench_screen"] = {"command": screen_command[0], "args": screen_command[1:]}
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    enabled = [item for item in value["items"] if item["enabled"]]
    plugins = []
    skill_config = []
    if client == "codex":
        # Keep old copies for already-running sessions; disable them in the new
        # invocation instead of deleting files that an old session may read.
        skill_root = home / "skills"
        skill_root.mkdir(exist_ok=True, mode=0o700)
        for item in enabled:
            if item["kind"] != "skill":
                raise ValueError("Codex 暂只接入 MCP 和 Skills；完整 Claude 插件请切换客户端")
            target = skill_root / ("workbench-" + item["id"])
            copy_component(root / "components" / item["id"], target)
        selected = {"workbench-" + item["id"] for item in enabled}
        skill_config = [{"path": str(path / "SKILL.md"), "enabled": path.name in selected}
                        for path in sorted(skill_root.glob("workbench-skill-*"))]
        path = home / "config.toml"
        text = path.read_text() if path.exists() else ""
        text = re.sub(re.escape(BEGIN) + ".*?" + re.escape(END), "", text, flags=re.S)
        block = "".join("[mcp_servers." + json.dumps(name) + "]\n" +
                        "".join(json.dumps(k) + " = " + toml(v) + "\n" for k, v in server.items())
                        for name, server in servers.items())
        private_write(path, text + BEGIN + block + END)
        return ["-c", "skills.config=" + toml(skill_config)] if skill_config else []
    for item in enabled:
        component = root / "components" / item["id"]
        if item["kind"] == "plugin":
            plugins += ["--plugin-dir", str(component)]
            prefix = "plugin_" + item["id"][-8:] + "_"
            for name, server in native_servers(plugin_mcp(component), "claude").items():
                servers[prefix + name] = server
        else:
            # A synthetic, immutable Claude plugin registers only this skill.
            wrapper = root / "skill-plugins" / item["id"]
            def build_wrapper(stage):
                namespace = item.get("command", "workbench-" + item["id"]).split(":")[0]
                private_write(stage / ".claude-plugin/plugin.json", json.dumps({"name": namespace, "version": "1.0.0"}))
                shutil.copytree(component, stage / "skills" / item.get("slug", item["id"]))
            publish_directory(wrapper, build_wrapper)
            plugins += ["--plugin-dir", str(wrapper)]
    content = json.dumps({"mcpServers": servers}, ensure_ascii=False)
    mcp = home / ("workbench-mcp-" + hashlib.sha256(content.encode()).hexdigest()[:16] + ".json")
    private_write(mcp, content)
    return ["--strict-mcp-config", "--mcp-config", str(mcp), *plugins]
