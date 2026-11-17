mod pricing;
mod usage;
use keyring::Entry;
use portable_pty::{Child, MasterPty, PtySize, native_pty_system};
use reqwest::{Client, Url, header};
use serde::{Deserialize, Serialize};
use serde_json::Value;
use std::{
    collections::HashMap,
    fs,
    io::{Read, Write},
    net::IpAddr,
    path::{Path, PathBuf},
    sync::{Arc, Mutex},
    thread,
    time::Duration,
};
use tauri::{Emitter, Manager, State};
use uuid::Uuid;

const KEYRING_SERVICE: &str = "com.cassette.agentworkbench";

#[derive(Clone, Serialize, Deserialize, Default)]
#[serde(default)]
struct AppConfig {
    profiles: Vec<KeyProfile>,
    selected_profile_id: String,
    client: String,
    model: String,
    workspace: String,
    pricing_sources: HashMap<String, PricingSource>,
}

#[derive(Clone, Serialize, Deserialize)]
struct PricingSource {
    gateway: String,
    url: String,
}

#[derive(Clone, Serialize, Deserialize)]
struct KeyProfile {
    id: String,
    name: String,
    gateway: String,
}

#[derive(Serialize)]
struct AppStateResponse {
    config: AppConfig,
    config_path: String,
}

#[derive(Deserialize)]
struct AddProfileRequest {
    name: String,
    gateway: String,
    key: String,
}

#[derive(Serialize)]
struct AddProfileResponse {
    profile: KeyProfile,
    models: Vec<String>,
}

#[derive(Deserialize)]
struct SavePreferencesRequest {
    client: String,
    model: String,
    workspace: String,
}

#[derive(Deserialize)]
struct StartTerminalRequest {
    profile_id: String,
    client: String,
    model: String,
    workspace: String,
    session_mode: String,
}

#[derive(Clone, Serialize)]
struct TerminalInfo {
    id: String,
    title: String,
    workspace: String,
}

#[derive(Clone, Serialize)]
struct OutputChunk {
    session_id: String,
    data: Vec<u8>,
}

#[derive(Clone, Serialize)]
struct ExitNotice {
    session_id: String,
    exit_code: Option<u32>,
}

struct TerminalHandle {
    writer: Mutex<Box<dyn Write + Send>>,
    master: Mutex<Box<dyn MasterPty + Send>>,
    child: Mutex<Box<dyn Child + Send>>,
}

struct AppState {
    config_path: PathBuf,
    config: Mutex<AppConfig>,
    terminals: Mutex<HashMap<String, Arc<TerminalHandle>>>,
}

impl AppState {
    fn new(config_path: PathBuf) -> Self {
        let config = read_config(&config_path).unwrap_or_default();
        Self {
            config_path,
            config: Mutex::new(config),
            terminals: Mutex::new(HashMap::new()),
        }
    }
}

fn read_config(path: &Path) -> Result<AppConfig, String> {
    if !path.exists() {
        return Ok(AppConfig::default());
    }
    let bytes = fs::read(path).map_err(|error| format!("读取配置失败：{error}"))?;
    serde_json::from_slice(&bytes).map_err(|error| format!("配置格式无效：{error}"))
}

#[tauri::command]
async fn usage_snapshot(
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<Value, String> {
    let root = app
        .path()
        .app_config_dir()
        .map_err(|error| error.to_string())?;
    let profiles = state
        .config
        .lock()
        .map_err(|_| "配置锁异常")?
        .profiles
        .clone();
    tauri::async_runtime::spawn_blocking(move || {
        let contexts: Vec<_> = profiles
            .iter()
            .map(|p| pricing::cached(&root.join("pricing"), &p.id, &p.gateway))
            .collect();
        pricing::apply_usage(
            usage::scan_for_pricing(&root.join("claude"), &root.join("codex")),
            &contexts,
        )
    })
    .await
    .map_err(|error| format!("读取本地用量失败：{error}"))
}

#[tauri::command]
async fn model_pricing(
    app: tauri::AppHandle,
    profile_id: String,
    force: Option<bool>,
    state: State<'_, AppState>,
) -> Result<Value, String> {
    let config = state.config.lock().map_err(|_| "配置锁异常")?.clone();
    let root = app
        .path()
        .app_config_dir()
        .map_err(|e| e.to_string())?
        .join("pricing");
    let mut selected = None;
    let mut selected_key = None;
    for profile in &config.profiles {
        let key = key_entry(&profile.id)
            .and_then(|e| e.get_password().map_err(|_| "没有保存调用 Key".into()))
            .ok();
        let source = config
            .pricing_sources
            .get(&profile.id)
            .filter(|s| s.gateway == profile.gateway)
            .map(|s| s.url.as_str())
            .unwrap_or("");
        let context = pricing::refresh(
            &root,
            &profile.id,
            &profile.gateway,
            key.as_deref(),
            source,
            force.unwrap_or(false),
        )
        .await;
        if profile.id == profile_id {
            selected = Some(context);
            selected_key = key;
        }
    }
    let context = selected.ok_or("找不到所选调用配置")?;
    let gateway = context["gateway"].as_str().ok_or("网关地址无效")?;
    let models = if let Some(key) = selected_key {
        fetch_models(gateway, &key).await.ok()
    } else {
        None
    }
    .unwrap_or_else(|| {
        context["catalog"]["models"]
            .as_object()
            .map(|m| m.keys().cloned().collect())
            .unwrap_or_default()
    });
    Ok(pricing::snapshot(&context, &models))
}

#[tauri::command]
async fn save_pricing_source(
    app: tauri::AppHandle,
    profile_id: String,
    url: String,
    state: State<'_, AppState>,
) -> Result<(), String> {
    let _ = app;
    let url = url.trim().to_string();
    if !url.is_empty() {
        pricing::verify_source(&url).await?;
    }
    let mut config = state.config.lock().map_err(|_| "配置锁异常")?;
    let gateway = config
        .profiles
        .iter()
        .find(|p| p.id == profile_id)
        .ok_or("找不到所选调用配置")?
        .gateway
        .clone();
    let original = config.clone();
    config
        .pricing_sources
        .insert(profile_id, PricingSource { gateway, url });
    if let Err(error) = write_config(&state.config_path, &config) {
        *config = original;
        return Err(error);
    }
    Ok(())
}

fn write_config(path: &Path, config: &AppConfig) -> Result<(), String> {
    let parent = path.parent().ok_or("配置路径无效")?;
    fs::create_dir_all(parent).map_err(|error| format!("创建配置目录失败：{error}"))?;
    let temporary = path.with_extension("json.tmp");
    let bytes = serde_json::to_vec_pretty(config).map_err(|error| error.to_string())?;
    fs::write(&temporary, bytes).map_err(|error| format!("写入配置失败：{error}"))?;
    fs::rename(temporary, path).map_err(|error| format!("保存配置失败：{error}"))
}

fn normalize_gateway(value: &str) -> Result<String, String> {
    let mut url = Url::parse(value.trim()).map_err(|_| "网关地址格式无效".to_string())?;
    if !matches!(url.scheme(), "http" | "https")
        || !url.username().is_empty()
        || url.as_str().contains('@')
        || url.query().is_some()
        || url.fragment().is_some()
    {
        return Err("网关必须是无凭据的 HTTP(S) 根地址".into());
    }
    if !matches!(url.path(), "" | "/" | "/v1") {
        return Err("网关地址只填写服务根地址".into());
    }
    let host = url.host_str().unwrap_or_default();
    let loopback = host.eq_ignore_ascii_case("localhost")
        || host
            .parse::<IpAddr>()
            .map(|address| address.is_loopback())
            .unwrap_or(false);
    if url.scheme() == "http" && !loopback {
        return Err("远程网关必须使用 HTTPS".into());
    }
    url.set_path("");
    Ok(url.as_str().trim_end_matches('/').to_string())
}

async fn fetch_models(gateway: &str, key: &str) -> Result<Vec<String>, String> {
    let url = format!("{}/v1/models?limit=1000", gateway.trim_end_matches('/'));
    let response = Client::new()
        .get(url)
        .header("x-api-key", key)
        .header("anthropic-version", "2023-06-01")
        .header(header::USER_AGENT, "agent-workbench/0.1")
        .timeout(Duration::from_secs(15))
        .send()
        .await
        .map_err(|error| format!("连接网关失败：{error}"))?;
    let status = response.status();
    let body: Value = response
        .json()
        .await
        .map_err(|error| format!("模型列表响应无效：{error}"))?;
    if !status.is_success() {
        let message = body["error"]["message"].as_str().unwrap_or("请求未通过");
        return Err(format!("网关返回 HTTP {}：{}", status.as_u16(), message));
    }
    let rows = body["data"]
        .as_array()
        .or_else(|| body["models"].as_array())
        .ok_or("网关没有返回模型列表")?;
    let mut models: Vec<String> = rows
        .iter()
        .filter_map(|row| row["id"].as_str().or_else(|| row["name"].as_str()))
        .map(str::to_string)
        .collect();
    models.sort_unstable();
    models.dedup();
    Ok(models)
}

fn key_entry(profile_id: &str) -> Result<Entry, String> {
    Entry::new(KEYRING_SERVICE, profile_id).map_err(|error| format!("系统凭据库不可用：{error}"))
}

#[tauri::command]
fn load_app_state(state: State<'_, AppState>) -> Result<AppStateResponse, String> {
    let config = state.config.lock().map_err(|_| "配置锁异常")?.clone();
    Ok(AppStateResponse {
        config,
        config_path: state.config_path.display().to_string(),
    })
}

#[tauri::command]
async fn add_profile(
    request: AddProfileRequest,
    state: State<'_, AppState>,
) -> Result<AddProfileResponse, String> {
    let name = request.name.trim();
    if name.is_empty() || name.len() > 80 || request.key.trim().len() < 20 {
        return Err("请填写名称和完整的下游调用 Key".into());
    }
    let gateway = normalize_gateway(&request.gateway)?;
    let models = fetch_models(&gateway, request.key.trim()).await?;
    let profile = KeyProfile {
        id: Uuid::new_v4().to_string(),
        name: name.to_string(),
        gateway,
    };
    key_entry(&profile.id)?
        .set_password(request.key.trim())
        .map_err(|error| format!("保存到 Windows 凭据管理器失败：{error}"))?;
    let mut config = state.config.lock().map_err(|_| "配置锁异常")?;
    let original = config.clone();
    config.profiles.push(profile.clone());
    config.selected_profile_id = profile.id.clone();
    if let Err(error) = write_config(&state.config_path, &config) {
        if let Ok(entry) = key_entry(&profile.id) {
            let _ = entry.delete_credential();
        }
        *config = original;
        return Err(error);
    }
    Ok(AddProfileResponse { profile, models })
}

#[tauri::command]
async fn models_for_profile(
    profile_id: String,
    state: State<'_, AppState>,
) -> Result<Vec<String>, String> {
    let profile = {
        let config = state.config.lock().map_err(|_| "配置锁异常")?;
        config
            .profiles
            .iter()
            .find(|item| item.id == profile_id)
            .cloned()
            .ok_or("找不到所选 Key 配置")?
    };
    let key = key_entry(&profile.id)?
        .get_password()
        .map_err(|_| "Windows 凭据管理器中没有这把 Key".to_string())?;
    fetch_models(&profile.gateway, &key).await
}

#[tauri::command]
fn select_profile(profile_id: String, state: State<'_, AppState>) -> Result<(), String> {
    let mut config = state.config.lock().map_err(|_| "配置锁异常")?;
    if !config
        .profiles
        .iter()
        .any(|profile| profile.id == profile_id)
    {
        return Err("找不到所选 Key 配置".into());
    }
    config.selected_profile_id = profile_id;
    write_config(&state.config_path, &config)
}

#[tauri::command]
fn save_preferences(
    request: SavePreferencesRequest,
    state: State<'_, AppState>,
) -> Result<(), String> {
    if !matches!(request.client.as_str(), "claude" | "codex") {
        return Err("暂不支持所选客户端".into());
    }
    let mut config = state.config.lock().map_err(|_| "配置锁异常")?;
    config.client = request.client;
    config.model = request.model.trim().to_string();
    config.workspace = request.workspace.trim().to_string();
    write_config(&state.config_path, &config)
}

#[tauri::command]
fn remove_profile(profile_id: String, state: State<'_, AppState>) -> Result<(), String> {
    let mut config = state.config.lock().map_err(|_| "配置锁异常")?;
    let original = config.clone();
    if !config
        .profiles
        .iter()
        .any(|profile| profile.id == profile_id)
    {
        return Err("找不到要删除的 Key 配置".into());
    }
    config.profiles.retain(|profile| profile.id != profile_id);
    if config.selected_profile_id == profile_id {
        config.selected_profile_id = config
            .profiles
            .first()
            .map(|profile| profile.id.clone())
            .unwrap_or_default();
        config.model.clear();
    }
    if let Err(error) = write_config(&state.config_path, &config) {
        *config = original;
        return Err(error);
    }
    if let Ok(entry) = key_entry(&profile_id) {
        let _ = entry.delete_credential();
    }
    Ok(())
}

fn quote_powershell(value: &str) -> String {
    format!("'{}'", value.replace('\'', "''"))
}

#[tauri::command]
fn start_terminal(
    request: StartTerminalRequest,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<TerminalInfo, String> {
    if !matches!(request.client.as_str(), "claude" | "codex") {
        return Err("暂不支持所选客户端".into());
    }
    if request.model.trim().is_empty() {
        return Err("请选择模型".into());
    }
    if !matches!(request.session_mode.as_str(), "new" | "continue" | "resume") {
        return Err("会话模式无效".into());
    }
    let profile = {
        let config = state.config.lock().map_err(|_| "配置锁异常")?;
        config
            .profiles
            .iter()
            .find(|profile| profile.id == request.profile_id)
            .cloned()
            .ok_or("找不到所选 Key 配置")?
    };
    let workspace = PathBuf::from(request.workspace.trim());
    if !workspace.is_dir() {
        return Err("请选择一个存在的项目目录".into());
    }
    let key = key_entry(&profile.id)?
        .get_password()
        .map_err(|_| "Windows 凭据管理器中没有这把 Key".to_string())?;
    let id = Uuid::new_v4().to_string();
    let config_root = app
        .path()
        .app_config_dir()
        .map_err(|error| error.to_string())?;
    let mut shell = portable_pty::CommandBuilder::new("powershell.exe");
    shell.args(["-NoLogo", "-NoExit", "-Command"]);
    let command_text = if request.client == "codex" {
        let codex_home = config_root.join("codex").join(&profile.id);
        fs::create_dir_all(&codex_home)
            .map_err(|error| format!("创建隔离 Codex 配置失败：{error}"))?;
        let config_text = format!(
            "model = {}\nmodel_provider = \"cchp_local\"\nmodel_reasoning_effort = \"medium\"\nsandbox_mode = \"workspace-write\"\ndisable_response_storage = true\n\n[model_providers.cchp_local]\nname = \"cchp_local\"\nbase_url = {}\nwire_api = \"responses\"\nenv_key = \"CCH_API_KEY\"\n",
            serde_json::to_string(&request.model).map_err(|error| error.to_string())?,
            serde_json::to_string(&format!("{}/v1", profile.gateway))
                .map_err(|error| error.to_string())?,
        );
        fs::write(codex_home.join("config.toml"), config_text)
            .map_err(|error| format!("写入隔离 Codex 配置失败：{error}"))?;
        let mut args = vec![
            "--model".to_string(),
            request.model.clone(),
            "--cd".into(),
            workspace.display().to_string(),
            "--no-alt-screen".into(),
            "--no-daemon".into(),
        ];
        if request.session_mode == "continue" {
            args = vec![
                "resume".into(),
                "--last".into(),
                "--model".into(),
                request.model.clone(),
                "--cd".into(),
                workspace.display().to_string(),
                "--no-alt-screen".into(),
                "--no-daemon".into(),
            ];
        }
        if request.session_mode == "resume" {
            args = vec![
                "resume".into(),
                "--model".into(),
                request.model.clone(),
                "--cd".into(),
                workspace.display().to_string(),
                "--no-alt-screen".into(),
                "--no-daemon".into(),
            ];
        }
        shell.env("CODEX_HOME", codex_home);
        shell.env("CCH_API_KEY", &key);
        let command_args = args
            .iter()
            .map(|arg| quote_powershell(arg))
            .collect::<Vec<_>>()
            .join(" ");
        format!("& 'codex' {command_args}")
    } else if request.client == "claude" {
        let claude_home = config_root.join("claude").join(&profile.id);
        fs::create_dir_all(&claude_home)
            .map_err(|error| format!("创建隔离 Claude 配置失败：{error}"))?;
        shell.env("CLAUDE_CONFIG_DIR", claude_home);
        shell.env("ANTHROPIC_BASE_URL", &profile.gateway);
        shell.env("ANTHROPIC_AUTH_TOKEN", &key);
        shell.env("ANTHROPIC_API_KEY", &key);
        let mut args = vec![
            "--model".to_string(),
            request.model.clone(),
            "--add-dir".into(),
            workspace.display().to_string(),
        ];
        match request.session_mode.as_str() {
            "new" => {}
            "continue" => args.push("--continue".into()),
            "resume" => args.push("--resume".into()),
            _ => return Err("会话模式无效".into()),
        }
        let command_args = args
            .iter()
            .map(|arg| quote_powershell(arg))
            .collect::<Vec<_>>()
            .join(" ");
        format!("& 'claude' {command_args}")
    } else {
        return Err("暂不支持所选客户端".into());
    };
    shell.arg(format!(
        "chcp 65001 > $null; $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); $env:TERM = 'xterm-256color'; $env:COLORTERM = 'truecolor'; {command_text}"
    ));
    shell.cwd(&workspace);
    if request.client == "codex" {
        shell.env_remove("OPENAI_API_KEY");
        shell.env_remove("ANTHROPIC_API_KEY");
    }
    let system = native_pty_system();
    let pair = system
        .openpty(PtySize {
            rows: 30,
            cols: 100,
            pixel_width: 0,
            pixel_height: 0,
        })
        .map_err(|error| format!("创建 ConPTY 失败：{error}"))?;
    let child = pair
        .slave
        .spawn_command(shell)
        .map_err(|error| format!("启动 Agent 失败：{error}"))?;
    drop(pair.slave);
    let reader = pair
        .master
        .try_clone_reader()
        .map_err(|error| format!("读取终端失败：{error}"))?;
    let writer = pair
        .master
        .take_writer()
        .map_err(|error| format!("打开终端输入失败：{error}"))?;
    let handle = Arc::new(TerminalHandle {
        writer: Mutex::new(writer),
        master: Mutex::new(pair.master),
        child: Mutex::new(child),
    });
    state
        .terminals
        .lock()
        .map_err(|_| "终端会话锁异常")?
        .insert(id.clone(), Arc::clone(&handle));
    let info = TerminalInfo {
        id: id.clone(),
        title: format!("{} · {}", request.client, request.model),
        workspace: workspace.display().to_string(),
    };
    let app_for_reader = app.clone();
    let session_id = id;
    thread::spawn(move || stream_terminal(reader, handle, app_for_reader, session_id));
    Ok(info)
}

fn stream_terminal(
    mut reader: Box<dyn Read + Send>,
    handle: Arc<TerminalHandle>,
    app: tauri::AppHandle,
    session_id: String,
) {
    let mut buffer = [0u8; 8192];
    loop {
        match reader.read(&mut buffer) {
            Ok(0) | Err(_) => break,
            Ok(count) => {
                let _ = app.emit(
                    "terminal-output",
                    OutputChunk {
                        session_id: session_id.clone(),
                        data: buffer[..count].to_vec(),
                    },
                );
            }
        }
    }
    let exit_code = handle
        .child
        .lock()
        .ok()
        .and_then(|mut child| child.wait().ok())
        .map(|status| status.exit_code());
    if let Some(state) = app.try_state::<AppState>() {
        if let Ok(mut terminals) = state.terminals.lock() {
            terminals.remove(&session_id);
        }
    }
    let _ = app.emit(
        "terminal-exit",
        ExitNotice {
            session_id,
            exit_code,
        },
    );
}

#[tauri::command]
fn write_terminal(
    session_id: String,
    data: Vec<u8>,
    state: State<'_, AppState>,
) -> Result<(), String> {
    let handle = state
        .terminals
        .lock()
        .map_err(|_| "终端会话锁异常")?
        .get(&session_id)
        .cloned()
        .ok_or("终端会话不存在")?;
    let mut writer = handle.writer.lock().map_err(|_| "终端输入锁异常")?;
    writer
        .write_all(&data)
        .map_err(|error| format!("写入终端失败：{error}"))
}

#[tauri::command]
fn resize_terminal(
    session_id: String,
    cols: u16,
    rows: u16,
    state: State<'_, AppState>,
) -> Result<(), String> {
    let handle = state
        .terminals
        .lock()
        .map_err(|_| "终端会话锁异常")?
        .get(&session_id)
        .cloned()
        .ok_or("终端会话不存在")?;
    handle
        .master
        .lock()
        .map_err(|_| "终端尺寸锁异常")?
        .resize(PtySize {
            rows,
            cols,
            pixel_width: 0,
            pixel_height: 0,
        })
        .map_err(|error| format!("调整终端尺寸失败：{error}"))
}

#[tauri::command]
fn stop_terminal(session_id: String, state: State<'_, AppState>) -> Result<(), String> {
    let handle = state
        .terminals
        .lock()
        .map_err(|_| "终端会话锁异常")?
        .get(&session_id)
        .cloned()
        .ok_or("终端会话不存在")?;
    handle
        .child
        .lock()
        .map_err(|_| "终端进程锁异常")?
        .kill()
        .map_err(|error| format!("停止 Agent 失败：{error}"))
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let config_path = app.path().app_config_dir()?.join("settings.json");
            app.manage(AppState::new(config_path));
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            load_app_state,
            usage_snapshot,
            model_pricing,
            save_pricing_source,
            add_profile,
            models_for_profile,
            select_profile,
            save_preferences,
            remove_profile,
            start_terminal,
            write_terminal,
            resize_terminal,
            stop_terminal
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::normalize_gateway;

    #[cfg(target_os = "windows")]
    #[test]
    fn windows_credential_survives_new_entry_handle() {
        let profile_id = format!("test-{}", uuid::Uuid::new_v4());
        let value = uuid::Uuid::new_v4().to_string();
        let entry = super::key_entry(&profile_id).unwrap();
        assert!(
            entry
                .get_credential()
                .downcast_ref::<keyring::windows::WinCredential>()
                .is_some(),
            "Windows builds must use the native credential store"
        );
        entry.set_password(&value).unwrap();
        drop(entry);
        let reread = super::key_entry(&profile_id).unwrap();
        let result = reread.get_password();
        let cleanup = reread.delete_credential();
        assert!(cleanup.is_ok(), "Remove the temporary test credential");
        assert_eq!(result.unwrap(), value);
    }

    #[test]
    fn local_gateway_is_normalized_to_service_root() {
        assert_eq!(
            normalize_gateway("http://127.0.0.1:8081/v1").unwrap(),
            "http://127.0.0.1:8081"
        );
    }

    #[test]
    fn remote_gateway_requires_https() {
        assert!(normalize_gateway("http://gateway.example.com").is_err());
        assert_eq!(
            normalize_gateway("https://gateway.example.com/v1").unwrap(),
            "https://gateway.example.com"
        );
    }

    #[test]
    fn gateway_rejects_credentials_and_extra_paths() {
        assert!(normalize_gateway("https://user:example@gateway.example.com").is_err());
        assert!(normalize_gateway("https://gateway.example.com/proxy/v1").is_err());
        assert!(normalize_gateway("https://gateway.example.com/v1?key=value").is_err());
    }
}
