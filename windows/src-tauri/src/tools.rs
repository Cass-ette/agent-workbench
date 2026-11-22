use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};
use std::{
    collections::BTreeMap,
    fs,
    io::{self, BufRead, Write},
    path::{Path, PathBuf},
};
use tauri::Manager;

#[derive(Clone, Serialize, Deserialize)]
pub struct Item {
    id: String,
    name: String,
    kind: String,
    enabled: bool,
    #[serde(default)]
    slug: String,
    #[serde(default)]
    command: String,
}
#[derive(Default, Serialize, Deserialize)]
#[serde(default)]
pub struct Settings {
    #[serde(rename = "mcpServers")]
    servers: BTreeMap<String, Value>,
    screen: bool,
    items: Vec<Item>,
}

fn client_root(app: &tauri::AppHandle, client: &str) -> Result<PathBuf, String> {
    if !matches!(client, "codex" | "claude") {
        return Err("请选择客户端".into());
    }
    Ok(app
        .path()
        .app_config_dir()
        .map_err(|e| e.to_string())?
        .join("tools")
        .join(client))
}
fn load(root: &Path) -> Result<Settings, String> {
    let path = root.join("tools.json");
    if !path.exists() {
        return Ok(Settings::default());
    }
    serde_json::from_slice(&fs::read(path).map_err(|e| e.to_string())?)
        .map_err(|_| "工具配置损坏，未覆盖".into())
}
fn save(root: &Path, settings: &Settings) -> Result<(), String> {
    fs::create_dir_all(root).map_err(|e| e.to_string())?;
    fs::write(
        root.join("tools.json"),
        serde_json::to_vec_pretty(settings).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())
}
fn public(settings: Settings) -> Value {
    let config = serde_json::to_string_pretty(&json!({"mcpServers": settings.servers})).unwrap();
    json!({"config":config,"screen":settings.screen,"items":settings.items})
}

fn parse_servers(config: &str) -> Result<BTreeMap<String, Value>, String> {
    let value: Value = serde_json::from_str(config).map_err(|_| "MCP 配置须为 JSON".to_string())?;
    let servers = value
        .get("mcpServers")
        .or_else(|| value.get("mcp_servers"))
        .unwrap_or(&value)
        .as_object()
        .ok_or("MCP 配置必须为对象")?;
    if servers.len() > 64 {
        return Err("最多 64 个 MCP 服务".into());
    }
    for (name, server) in servers {
        if name.is_empty()
            || name.len() > 80
            || !name
                .bytes()
                .all(|c| c.is_ascii_alphanumeric() || c == b'_' || c == b'-')
        {
            return Err("服务名仅可包含字母、数字、下划线和连字符".into());
        }
        let fields = server.as_object().ok_or("MCP 服务须为对象")?;
        if fields.keys().any(|k| {
            ![
                "command",
                "args",
                "env",
                "url",
                "type",
                "headers",
                "http_headers",
                "enabled",
                "env_vars",
                "bearer_token_env_var",
                "env_http_headers",
                "cwd",
                "startup_timeout_sec",
                "tool_timeout_sec",
            ]
            .contains(&k.as_str())
        }) {
            return Err("MCP 含暂不支持的字段".into());
        }
        let command = server.get("command").and_then(Value::as_str);
        let url = server.get("url").and_then(Value::as_str);
        if command.is_some() == url.is_some() {
            return Err("MCP 须填写 command 或 url 其中一项".into());
        }
        for key in ["command", "url", "cwd", "bearer_token_env_var"] {
            if let Some(value) = server.get(key) {
                if value
                    .as_str()
                    .is_none_or(|s| s.is_empty() || s.contains('\0'))
                {
                    return Err("MCP 程序、路径或地址无效".into());
                }
            }
        }
        if let Some(url) = url {
            let url = reqwest::Url::parse(url).map_err(|_| "MCP 地址无效")?;
            if !matches!(url.scheme(), "http" | "https")
                || !url.username().is_empty()
                || url.password().is_some()
                || (url.scheme() == "http"
                    && !matches!(url.host_str(), Some("localhost" | "127.0.0.1" | "[::1]")))
            {
                return Err("远程 MCP 须用 HTTPS，地址不能带账号密码".into());
            }
        }
        if server.get("type").is_some_and(|v| {
            !matches!(
                v.as_str(),
                Some("stdio" | "http" | "streamable_http" | "sse")
            )
        }) {
            return Err("MCP 传输方式无效".into());
        }
        for key in ["args", "env_vars"] {
            if let Some(value) = server.get(key) {
                if value.as_array().is_none_or(|a| {
                    a.iter()
                        .any(|v| v.as_str().is_none_or(|s| s.contains('\0')))
                }) {
                    return Err("MCP 参数须为字符串数组".into());
                }
            }
        }
        for key in ["env", "headers", "http_headers", "env_http_headers"] {
            if let Some(value) = server.get(key) {
                if value.as_object().is_none_or(|a| {
                    a.values()
                        .any(|v| v.as_str().is_none_or(|s| s.contains('\0')))
                }) {
                    return Err("MCP 环境变量和请求头须为字符串映射".into());
                }
            }
        }
        if server.get("enabled").is_some_and(|v| !v.is_boolean()) {
            return Err("enabled 须为布尔值".into());
        }
    }
    Ok(servers
        .iter()
        .map(|(k, v)| (k.clone(), v.clone()))
        .collect())
}

#[tauri::command]
pub fn tools_get(app: tauri::AppHandle, client: String) -> Result<Value, String> {
    Ok(public(load(&client_root(&app, &client)?)?))
}
#[tauri::command]
pub fn tools_save(
    app: tauri::AppHandle,
    client: String,
    config: String,
    screen: bool,
    disabled: Vec<String>,
) -> Result<Value, String> {
    let root = client_root(&app, &client)?;
    let mut value = load(&root)?;
    value.servers = parse_servers(&config)?;
    // Validate per-client differences before persisting, without launching any service.
    for server in value.servers.values() {
        native_server(server, &client)?;
    }
    value.screen = screen;
    for item in &mut value.items {
        item.enabled = !disabled.contains(&item.id);
    }
    save(&root, &value)?;
    Ok(public(value))
}

fn collect_files(
    root: &Path,
    directory: &Path,
    files: &mut Vec<PathBuf>,
    size: &mut u64,
) -> Result<(), String> {
    for entry in fs::read_dir(directory).map_err(|e| e.to_string())? {
        let path = entry.map_err(|e| e.to_string())?.path();
        if matches!(
            path.file_name().and_then(|s| s.to_str()),
            Some(".git" | ".DS_Store")
        ) {
            continue;
        }
        let metadata = fs::symlink_metadata(&path).map_err(|e| e.to_string())?;
        if metadata.is_symlink() {
            return Err("组件含符号链接；请准备单独副本".into());
        }
        if metadata.is_dir() {
            collect_files(root, &path, files, size)?;
        } else if metadata.is_file() {
            *size += metadata.len();
            files.push(path.strip_prefix(root).unwrap().to_path_buf());
        }
        if files.len() > 10000 || *size > 100 * 1024 * 1024 {
            return Err("单个组件不得超过 100 MB 或一万文件".into());
        }
    }
    Ok(())
}
fn copy_component(source: &Path, destination: &Path) -> Result<(), String> {
    let mut files = vec![];
    collect_files(source, source, &mut files, &mut 0)?;
    fs::create_dir_all(destination).map_err(|e| e.to_string())?;
    for relative in files {
        let target = destination.join(&relative);
        fs::create_dir_all(target.parent().unwrap()).map_err(|e| e.to_string())?;
        fs::copy(source.join(relative), target).map_err(|e| e.to_string())?;
    }
    Ok(())
}
#[tauri::command]
pub fn tools_import(
    app: tauri::AppHandle,
    client: String,
    path: String,
    kind: String,
) -> Result<Value, String> {
    let root = client_root(&app, &client)?;
    let source = PathBuf::from(path)
        .canonicalize()
        .map_err(|_| "组件目录不存在")?;
    let marker = match kind.as_str() {
        "skill" => "SKILL.md",
        "plugin" if client == "claude" => ".claude-plugin/plugin.json",
        _ => return Err("Codex 暂支持 MCP 与 Skills，完整插件仅支持 Claude".into()),
    };
    if !source.join(marker).is_file() {
        return Err(format!("所选目录须包含 {marker}"));
    }
    let mut files = vec![];
    collect_files(&source, &source, &mut files, &mut 0)?;
    files.sort();
    let mut hash = Sha256::new();
    for file in files {
        hash.update(file.to_string_lossy().as_bytes());
        hash.update(fs::read(source.join(file)).map_err(|e| e.to_string())?);
    }
    let id = format!("{kind}-{}", &format!("{:x}", hash.finalize())[..16]);
    let destination = root.join("components").join(&id);
    if !destination.exists() {
        copy_component(&source, &destination)?;
    }
    let mut value = load(&root)?;
    if !value.items.iter().any(|item| item.id == id) {
        let name = source
            .file_name()
            .unwrap_or_default()
            .to_string_lossy()
            .to_string();
        let slug = name
            .to_ascii_lowercase()
            .chars()
            .map(|c| {
                if c.is_ascii_alphanumeric() || c == '-' {
                    c
                } else {
                    '-'
                }
            })
            .collect::<String>()
            .trim_matches('-')
            .chars()
            .take(50)
            .collect::<String>();
        let slug = if slug.is_empty() {
            "skill".to_string()
        } else {
            slug
        };
        let command = if kind == "skill" {
            format!("workbench-{}-{}:{}", slug, &id[id.len() - 6..], slug)
        } else {
            String::new()
        };
        value.items.push(Item {
            id,
            name,
            kind,
            enabled: false,
            slug,
            command,
        });
        save(&root, &value)?;
    }
    Ok(public(value))
}

fn native_server(server: &Value, client: &str) -> Result<Value, String> {
    let mut item = server.as_object().ok_or("MCP 配置无效")?.clone();
    let transport = item.remove("type");
    item.remove("enabled");
    if client == "codex" {
        if transport.as_ref().and_then(Value::as_str) == Some("sse") {
            return Err("Codex 不支持旧 SSE MCP；请使用 Streamable HTTP".into());
        }
        if let Some(headers) = item.remove("headers") {
            item.insert("http_headers".into(), headers);
        }
    } else {
        if item.contains_key("cwd") {
            return Err("Claude MCP 不支持服务级 cwd，请使用绝对程序路径".into());
        }
        if let Some(headers) = item.remove("http_headers") {
            item.insert("headers".into(), headers);
        }
        if let Some(vars) = item.remove("env_vars") {
            let env = item
                .entry("env")
                .or_insert(json!({}))
                .as_object_mut()
                .ok_or("env 格式错误")?;
            for key in vars.as_array().ok_or("env_vars 格式错误")? {
                let key = key.as_str().ok_or("变量名错误")?;
                env.insert(
                    key.into(),
                    json!(std::env::var(key).map_err(|_| format!("所需环境变量未设置：{key}"))?),
                );
            }
        }
        let mut headers = item.remove("headers").unwrap_or(json!({}));
        if let Some(variable) = item.remove("bearer_token_env_var") {
            let key = variable.as_str().ok_or("变量名错误")?;
            headers["Authorization"] = json!(format!(
                "Bearer {}",
                std::env::var(key).map_err(|_| format!("认证变量未设置：{key}"))?
            ));
        }
        if let Some(vars) = item.remove("env_http_headers") {
            for (header, variable) in vars.as_object().ok_or("请求头变量配置错误")? {
                let key = variable.as_str().ok_or("变量名错误")?;
                headers[header] =
                    json!(std::env::var(key).map_err(|_| format!("请求头变量未设置：{key}"))?);
            }
        }
        if item.contains_key("url") {
            item.insert(
                "type".into(),
                transport.filter(|v| v == "sse").unwrap_or(json!("http")),
            );
        }
        if headers.as_object().is_some_and(|v| !v.is_empty()) {
            item.insert("headers".into(), headers);
        }
        item.remove("startup_timeout_sec");
        item.remove("tool_timeout_sec");
    }
    Ok(Value::Object(item))
}
fn toml(value: &Value) -> String {
    match value {
        Value::Object(map) => format!(
            "{{{}}}",
            map.iter()
                .map(|(k, v)| format!("{} = {}", json!(k), toml(v)))
                .collect::<Vec<_>>()
                .join(", ")
        ),
        Value::Array(array) => format!(
            "[{}]",
            array.iter().map(toml).collect::<Vec<_>>().join(", ")
        ),
        _ => value.to_string(),
    }
}

fn plugin_mcp(component: &Path) -> Result<BTreeMap<String, Value>, String> {
    let manifest: Value = serde_json::from_slice(
        &fs::read(component.join(".claude-plugin/plugin.json")).map_err(|e| e.to_string())?,
    )
    .map_err(|_| "插件 manifest 格式错误")?;
    let mut declarations = vec![];
    if component.join(".mcp.json").is_file() {
        declarations.push(json!(".mcp.json"));
    }
    if let Some(value) = manifest.get("mcpServers") {
        if let Some(values) = value.as_array() {
            declarations.extend(values.clone());
        } else {
            declarations.push(value.clone());
        }
    }
    fn expand(value: &mut Value, component: &Path) {
        match value {
            Value::String(text) => {
                *text = text.replace("${CLAUDE_PLUGIN_ROOT}", &component.to_string_lossy())
            }
            Value::Array(values) => values.iter_mut().for_each(|v| expand(v, component)),
            Value::Object(values) => values.values_mut().for_each(|v| expand(v, component)),
            _ => {}
        }
    }
    let mut servers = BTreeMap::new();
    for mut value in declarations {
        if let Some(path) = value.as_str() {
            let path = component
                .join(path)
                .canonicalize()
                .map_err(|_| "插件 MCP 文件不存在")?;
            if !path.starts_with(component.canonicalize().map_err(|e| e.to_string())?) {
                return Err("插件 MCP 文件须在插件副本内部".into());
            }
            value = serde_json::from_slice(&fs::read(path).map_err(|e| e.to_string())?)
                .map_err(|_| "插件 MCP JSON 格式错误")?;
        }
        expand(&mut value, component);
        servers.extend(parse_servers(&value.to_string())?);
    }
    Ok(servers)
}

pub fn apply(root: &Path, home: &Path, client: &str) -> Result<Vec<String>, String> {
    if !root.join("tools.json").exists() {
        return Ok(vec![]);
    }
    let value = load(root)?;
    let mut servers = serde_json::Map::new();
    for (name, server) in value.servers {
        if server.get("enabled") == Some(&json!(false)) {
            continue;
        }
        servers.insert(format!("workbench_{name}"), native_server(&server, client)?);
    }
    if value.screen {
        if servers.contains_key("workbench_screen") {
            return Err("screen 名称已被自定义 MCP 使用；请重命名或停用内置屏幕读取".into());
        }
        servers.insert("workbench_screen".into(), json!({"command": std::env::current_exe().map_err(|e| e.to_string())?, "args":["--screen-mcp"]}));
    }
    let mut args = vec![];
    let mut selected = vec![];
    for item in value.items.iter().filter(|i| i.enabled) {
        let component = root.join("components").join(&item.id);
        if client == "codex" {
            if item.kind != "skill" {
                return Err("Codex 暂不支持完整 Claude 插件".into());
            }
            let name = format!("workbench-{}", item.id);
            let target = home.join("skills").join(&name);
            if !target.exists() {
                copy_component(&component, &target)?;
            }
            selected.push(name);
        } else {
            let plugin = if item.kind == "plugin" {
                for (name, server) in plugin_mcp(&component)? {
                    if server.get("enabled") == Some(&json!(false)) {
                        continue;
                    }
                    servers.insert(
                        format!("plugin_{}_{}", &item.id[item.id.len() - 8..], name),
                        native_server(&server, "claude")?,
                    );
                }
                component
            } else {
                let wrapper = root.join("skill-plugins").join(&item.id);
                if !wrapper.exists() {
                    fs::create_dir_all(wrapper.join(".claude-plugin"))
                        .map_err(|e| e.to_string())?;
                    fs::write(wrapper.join(".claude-plugin/plugin.json"),json!({"name":item.command.split(':').next().filter(|s| !s.is_empty()).map(String::from).unwrap_or(format!("workbench-{}",item.id)),"version":"1.0.0"}).to_string()).map_err(|e| e.to_string())?;
                    copy_component(
                        &component,
                        &wrapper.join("skills").join(if item.slug.is_empty() {
                            &item.id
                        } else {
                            &item.slug
                        }),
                    )?;
                }
                wrapper
            };
            args.extend(["--plugin-dir".into(), plugin.display().to_string()]);
        }
    }
    fs::create_dir_all(home).map_err(|e| e.to_string())?;
    if client == "codex" {
        const BEGIN: &str = "\n# BEGIN WORKBENCH MANAGED TOOLS\n";
        const END: &str = "# END WORKBENCH MANAGED TOOLS\n";
        let path = home.join("config.toml");
        let mut text = fs::read_to_string(&path).map_err(|e| e.to_string())?;
        if let Some(start) = text.find(BEGIN) {
            let end =
                text[start..].find(END).ok_or("工具配置标记损坏，未覆盖")? + start + END.len();
            text.replace_range(start..end, "");
        }
        text.push_str(BEGIN);
        for (name, server) in servers {
            text.push_str(&format!("[mcp_servers.{}]\n", json!(name)));
            for (key, val) in server.as_object().ok_or("MCP 配置错误")? {
                text.push_str(&format!("{} = {}\n", json!(key), toml(val)));
            }
        }
        text.push_str(END);
        fs::write(path, text).map_err(|e| e.to_string())?;
        let skills = home.join("skills");
        if skills.exists() {
            let entries = fs::read_dir(skills).map_err(|e| e.to_string())?.filter_map(Result::ok).filter(|e| e.file_name().to_string_lossy().starts_with("workbench-skill-")).map(|e| {
                json!({"path":e.path().join("SKILL.md"),"enabled":selected.contains(&e.file_name().to_string_lossy().to_string())})
            }).collect::<Vec<_>>();
            if !entries.is_empty() {
                args.extend([
                    "-c".into(),
                    format!("skills.config={}", toml(&json!(entries))),
                ]);
            }
        }
    } else {
        let content = json!({"mcpServers":servers}).to_string();
        let hash = format!("{:x}", Sha256::digest(content.as_bytes()));
        let path = home.join(format!("workbench-mcp-{}.json", &hash[..16]));
        fs::write(&path, content).map_err(|e| e.to_string())?;
        args.extend([
            "--strict-mcp-config".into(),
            "--mcp-config".into(),
            path.display().to_string(),
        ]);
    }
    Ok(args)
}

pub fn screen_mcp() {
    for line in io::stdin().lock().lines().map_while(Result::ok) {
        let Ok(request) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        let Some(id) = request.get("id") else {
            continue;
        };
        let result = match request["method"].as_str() {
            Some("initialize") => {
                json!({"protocolVersion":request["params"]["protocolVersion"].as_str().unwrap_or("2024-11-05"),"capabilities":{"tools":{}},"serverInfo":{"name":"workbench-screen","version":"1.0.0"}})
            }
            Some("ping") => json!({}),
            Some("tools/list") => {
                json!({"tools":[{"name":"capture_screen","description":"按请求读取当前主屏幕，返回图片；不点击或输入，需图片模型。","inputSchema":{"type":"object","properties":{},"additionalProperties":false},"annotations":{"readOnlyHint":true,"destructiveHint":false}}]})
            }
            Some("tools/call") if request["params"]["name"] == "capture_screen" => {
                match capture_screen() {
                    Ok(image) => {
                        json!({"content":[{"type":"image","mimeType":"image/png","data":image}]})
                    }
                    Err(error) => json!({"isError":true,"content":[{"type":"text","text":error}]}),
                }
            }
            _ => {
                println!(
                    "{}",
                    json!({"jsonrpc":"2.0","id":id,"error":{"code":-32601,"message":"Method not found"}})
                );
                continue;
            }
        };
        println!("{}", json!({"jsonrpc":"2.0","id":id,"result":result}));
        let _ = io::stdout().flush();
    }
}
fn capture_screen() -> Result<String, String> {
    #[cfg(not(windows))]
    {
        Err("此截图服务需要 Windows 交互桌面".into())
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        let script = r#"$ErrorActionPreference='Stop'; Add-Type -AssemblyName System.Windows.Forms; Add-Type -AssemblyName System.Drawing
$bounds=[Windows.Forms.Screen]::PrimaryScreen.Bounds
$bitmap=[Drawing.Bitmap]::new($bounds.Width,$bounds.Height)
$graphics=[Drawing.Graphics]::FromImage($bitmap)
try {
  $graphics.CopyFromScreen($bounds.Location,[Drawing.Point]::Empty,$bounds.Size)
  $width=[Math]::Min(1600,$bitmap.Width); $height=[int]($bitmap.Height*$width/$bitmap.Width)
  $scaled=[Drawing.Bitmap]::new($bitmap,$width,$height); $stream=[IO.MemoryStream]::new()
  try { $scaled.Save($stream,[Drawing.Imaging.ImageFormat]::Png); [Console]::Write([Convert]::ToBase64String($stream.ToArray())) }
  finally { $stream.Dispose(); $scaled.Dispose() }
} finally { $graphics.Dispose(); $bitmap.Dispose() }"#;
        let output = std::process::Command::new("powershell.exe")
            .args(["-NoProfile", "-NonInteractive", "-Command", script])
            .creation_flags(0x08000000)
            .output()
            .map_err(|_| "无法启动 Windows 截图进程")?;
        if !output.status.success()
            || output.stdout.is_empty()
            || output.stdout.len() > 16 * 1024 * 1024
        {
            return Err(
                "截图失败。需已解锁的交互桌面，不能读取 UAC 安全桌面；请检查系统策略。".into(),
            );
        }
        String::from_utf8(output.stdout).map_err(|_| "截图编码错误".into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn validates_and_translates_mcp_without_running_it() {
        let s = parse_servers(r#"{"mcpServers":{"test":{"command":"must-not-run","args":["--stdio"],"env":{"TOKEN":"local"}}}}"#).unwrap();
        assert_eq!(
            native_server(&s["test"], "claude").unwrap()["command"],
            "must-not-run"
        );
        assert!(parse_servers(r#"{"bad":{"url":"http://remote.test"}}"#).is_err());
        assert!(
            native_server(&json!({"url":"https://remote.test","type":"sse"}), "codex").is_err()
        );
    }
    #[test]
    fn tool_blocks_preserve_provider_and_do_not_duplicate() {
        let root = std::env::temp_dir().join(uuid::Uuid::new_v4().to_string());
        let home = root.join("home");
        fs::create_dir_all(&home).unwrap();
        fs::write(home.join("config.toml"), "model = \"kept\"\n").unwrap();
        save(
            &root,
            &Settings {
                servers: parse_servers(r#"{"test":{"command":"must-not-run"}}"#).unwrap(),
                ..Default::default()
            },
        )
        .unwrap();
        apply(&root, &home, "codex").unwrap();
        apply(&root, &home, "codex").unwrap();
        let text = fs::read_to_string(home.join("config.toml")).unwrap();
        assert!(text.starts_with("model = \"kept\"\n"));
        assert_eq!(text.matches("[mcp_servers.").count(), 1);
        fs::remove_dir_all(root).unwrap();
    }
}
