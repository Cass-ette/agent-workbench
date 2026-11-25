mod history;
mod remote;
mod discovery;
mod attachments;
mod pricing;
mod reasoning;
#[cfg(windows)]
mod terminal_job;
mod usage;
mod updates;
pub mod tools;
use keyring::Entry;
use portable_pty::{CommandBuilder, MasterPty, PtySize, native_pty_system};
use reqwest::{Client, Url};
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
const POWERSHELL_EXIT_CODE: &str =
    "if ($?) { 0 } elseif ($LASTEXITCODE) { $LASTEXITCODE } else { 1 }";

#[derive(Clone, Serialize, Deserialize, Default)]
#[serde(default)]
struct AppConfig {
    remote_enabled: bool,
    #[serde(default = "default_ssh_port")]
    remote_ssh_port: u16,
    remote_permissions: HashMap<String, String>,
    profiles: Vec<KeyProfile>,
    selected_profile_id: String,
    client: String,
    model: String,
    workspace: String,
    pricing_sources: HashMap<String, PricingSource>,
}

fn default_ssh_port() -> u16 { 22 }

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
    #[serde(default)]
    model_catalog_url: String,
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
    #[serde(default)]
    model_catalog_url: String,
    #[serde(default = "default_read_models")]
    read_models: bool,
}

#[derive(Deserialize)]
struct UpdateProfileConnectionRequest {
    profile_id: String,
    gateway: String,
    #[serde(default)]
    model_catalog_url: String,
    #[serde(default = "default_read_models")]
    read_models: bool,
}

fn default_read_models() -> bool { true }

#[derive(Serialize)]
struct AddProfileResponse {
    profile: KeyProfile,
    models: Vec<String>,
    catalog_notice: String,
}

#[derive(Serialize)]
struct ModelCatalog {
    models: Vec<String>,
    catalog_notice: String,
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
    history_ref: Option<String>,
    history_id: Option<String>,
    confirmed_gateway: Option<String>,
    history_action: Option<String>,
    #[serde(default)]
    permission_mode: AgentPermission,
    #[serde(default)]
    reasoning_effort: Option<String>,
}

#[derive(Clone, Copy, Default, Deserialize)]
#[serde(rename_all = "lowercase")]
enum AgentPermission {
    #[default]
    Restricted,
    Standard,
    Full,
    Dangerous,
}

impl AgentPermission {
    fn args(self, client: &str) -> Vec<String> {
        if client == "claude" {
            if matches!(self, Self::Dangerous) { return vec!["--dangerously-skip-permissions".into()]; }
            return vec!["--permission-mode".into(), if matches!(self, Self::Full) { "bypassPermissions" } else { "default" }.into()];
        }
        if matches!(self, Self::Dangerous) { return vec!["--dangerously-bypass-approvals-and-sandbox".into()]; }
        vec![
            "--sandbox".into(), if matches!(self, Self::Full) { "danger-full-access" } else { "workspace-write" }.into(),
            "--ask-for-approval".into(), "on-request".into(),
            "-c".into(), format!("sandbox_workspace_write.network_access={}", !matches!(self, Self::Restricted)),
        ]
    }
}

fn local_network_bypass(upper: Option<&str>, lower: Option<&str>) -> String {
    let mut entries = vec!["127.0.0.1", "localhost", "::1"];
    for value in upper.into_iter().chain(lower) {
        for entry in value.split(',').map(str::trim).filter(|entry| !entry.is_empty()) {
            if !entries.contains(&entry) {
                entries.push(entry);
            }
        }
    }
    entries.join(",")
}

#[derive(Clone, Serialize)]
struct TerminalInfo {
    id: String,
    title: String,
    workspace: String,
    client: String,
    model: String,
    scope: String,
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
    replay: Arc<Mutex<remote::Replay>>,
    writer: Mutex<Option<Box<dyn Write + Send>>>,
    master: Mutex<Option<Box<dyn MasterPty + Send>>>,
    #[cfg(windows)]
    job: Mutex<terminal_job::TerminalJob>,
    #[cfg(not(windows))]
    killer: Mutex<Box<dyn portable_pty::ChildKiller + Send + Sync>>,
}

impl TerminalHandle {
    fn stop(&self) -> Result<(), String> {
        #[cfg(windows)]
        let result = self.job.lock().map_err(|_| "终端进程锁异常")?.terminate_and_wait();
        #[cfg(not(windows))]
        let result = self.killer.lock().map_err(|_| "终端进程锁异常")?.kill();
        result.map_err(|error| format!("无法确认终端已停止：{error}"))
    }
}

struct AppState {
    remote: remote::Service,
    remote_sessions: Mutex<HashMap<String, Value>>,
    remote_output: Mutex<HashMap<String, Arc<Mutex<remote::Replay>>>>,
    remote_uploads: Mutex<remote::Uploads>,
    config_path: PathBuf,
    config: Mutex<AppConfig>,
    terminals: Mutex<HashMap<String, Arc<TerminalHandle>>>,
    closing_for_update: Mutex<bool>,
}

impl AppState {
    fn new(config_path: PathBuf) -> Self {
        let config = read_config(&config_path).unwrap_or_default();
        Self {
            remote: remote::Service::default(),
            remote_sessions: Mutex::new(HashMap::new()),
            remote_output: Mutex::new(HashMap::new()),
            remote_uploads: Mutex::new(remote::Uploads::default()),
            config_path,
            config: Mutex::new(config),
            terminals: Mutex::new(HashMap::new()),
            closing_for_update: Mutex::new(false),
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
    let config = state.config.lock().map_err(|_| "配置锁异常")?.clone();
    tauri::async_runtime::spawn_blocking(move || {
        let mut snapshot = usage::scan_cached(&root.join("claude"), &root.join("codex"), &root.join("usage"));
        for session in snapshot["sessions"].as_array_mut().into_iter().flatten() {
            if let Some(profile) = config.profiles.iter().find(|p| session["profile_id"] == p.id) {
                session["profile_name"] = serde_json::json!(profile.name);
            }
            for record in session["_pricing_records"].as_array_mut().into_iter().flatten() {
                if let Some(profile) = config.profiles.iter().find(|p| record["profile_id"] == p.id) { record["profile_name"] = serde_json::json!(profile.name); }
            }
        }
        pricing::apply_upstream_usage(snapshot)
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
    let catalog_url = config.profiles.iter().find(|p| p.id == profile_id).map(|p| p.model_catalog_url.as_str()).unwrap_or("");
    let models = if let Some(key) = selected_key {
        fetch_model_catalog_at(gateway, &key, catalog_url).await.ok().map(|c| c.models)
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
        return Err("网关必须是无凭据、无查询参数的 HTTP(S) API 基础地址".into());
    }
    if value.trim().chars().any(|c| c.is_whitespace() || c.is_control()) || value.contains('\\') || url.port() == Some(0) {
        return Err("网关地址格式无效".into());
    }
    let host = url.host_str().unwrap_or_default();
    let loopback = host.eq_ignore_ascii_case("localhost")
        || host
            .trim_matches(['[', ']'])
            .parse::<IpAddr>()
            .map(|address| address.is_loopback())
            .unwrap_or(false);
    if url.scheme() == "http" && !loopback {
        return Err("远程网关必须使用 HTTPS".into());
    }
    let path = url.path().trim_end_matches('/').to_string();
    if path == "/v1" { url.set_path(""); }
    Ok(url.as_str().trim_end_matches('/').to_string())
}

fn gateway_api_base(gateway: &str) -> String {
    let base = gateway.trim_end_matches('/');
    if Url::parse(base).map(|url| url.path().is_empty() || url.path() == "/").unwrap_or(false) {
        format!("{base}/v1")
    } else { base.to_string() }
}

fn gateway_claude_base(gateway: &str) -> &str {
    gateway.trim_end_matches('/').strip_suffix("/v1").unwrap_or(gateway.trim_end_matches('/'))
}

async fn fetch_model_catalog(gateway: &str, key: &str) -> Result<ModelCatalog, String> {
    fetch_model_catalog_at(gateway, key, "").await
}

fn normalize_model_catalog_url(value: &str) -> Result<String, String> {
    let value = value.trim().trim_end_matches('/');
    if value.is_empty() { return Ok(String::new()); }
    normalize_gateway(value)?;
    // Exact catalog endpoint; don't apply the legacy /v1 base normalization.
    Ok(value.to_string())
}

fn gateway_model_urls(gateway: &str, model_catalog_url: &str) -> Result<Vec<String>, String> {
    let explicit = normalize_model_catalog_url(model_catalog_url)?;
    if !explicit.is_empty() { return Ok(vec![explicit]); }
    let base = gateway.trim_end_matches('/');
    let mut urls = vec![format!("{}/models", gateway_api_base(base))];
    if Url::parse(base).map(|url| url.path() != "/" && !url.path().is_empty()).unwrap_or(false) && !base.ends_with("/v1") {
        urls.push(format!("{base}/v1/models"));
    }
    Ok(urls)
}

async fn fetch_model_catalog_at(gateway: &str, key: &str, model_catalog_url: &str) -> Result<ModelCatalog, String> {
    let urls = gateway_model_urls(gateway, model_catalog_url)?;
    let client = Client::builder()
        .redirect(reqwest::redirect::Policy::none())
        .user_agent("agent-workbench/0.1")
        .timeout(Duration::from_secs(15))
        .build().map_err(|_| "无法创建网关连接".to_string())?;
    for url in urls {
        let mut response = client.get(format!("{url}?limit=1000"))
            .header("x-api-key", key).header("anthropic-version", "2023-06-01")
            .send().await.map_err(|_| "连接网关失败，请检查地址、网络和服务状态".to_string())?;
        if matches!(response.status().as_u16(), 401 | 403) {
            response = client.get(&url).bearer_auth(key).send().await
                .map_err(|_| "连接网关失败，请检查地址、网络和服务状态".to_string())?;
        }
        let status = response.status();
        if matches!(status.as_u16(), 404 | 405 | 501) { continue; }
        if status.is_redirection() { return Err("模型列表接口要求重定向，请填写服务的最终地址。".into()); }
        if !status.is_success() { return Err(format!("网关模型列表读取失败：HTTP {}", status.as_u16())); }
        let body: Value = response.json().await.map_err(|_| "模型列表响应不是有效 JSON".to_string())?;
        let rows = body["data"].as_array().or_else(|| body["models"].as_array())
            .ok_or("网关没有返回有效模型列表；可取消读取列表后保存并手动填写模型 ID。")?;
        let mut models: Vec<String> = rows.iter()
            .filter_map(|row| row["id"].as_str().or_else(|| row["name"].as_str()))
            .filter(|id| !id.trim().is_empty()).map(str::to_string).collect();
        models.sort_unstable(); models.dedup();
        let catalog_notice = if models.is_empty() { "网关返回了空模型列表，请手动填写模型 ID；实际可用性由客户端请求确认。" } else { "" }.into();
        return Ok(ModelCatalog { models, catalog_notice });
    }
    Ok(ModelCatalog { models: vec![], catalog_notice: "网关未提供模型列表接口；Key 尚未验证，请手动填写模型 ID。".into() })
}

async fn fetch_models(gateway: &str, key: &str) -> Result<Vec<String>, String> {
    Ok(fetch_model_catalog(gateway, key).await?.models)
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
    if name.is_empty() || name.len() > 80 || request.key.trim().is_empty() || request.key.len() > 4096 || request.key.trim().chars().any(char::is_whitespace) {
        return Err("请填写名称和完整的下游调用 Key".into());
    }
    let gateway = normalize_gateway(&request.gateway)?;
    let model_catalog_url = normalize_model_catalog_url(&request.model_catalog_url)?;
    let catalog = if request.read_models { fetch_model_catalog_at(&gateway, request.key.trim(), &model_catalog_url).await? }
        else { ModelCatalog { models: vec![], catalog_notice: "已跳过模型列表读取；Key 和模型尚未验证，请手动填写模型 ID。".into() } };
    let profile = KeyProfile {
        id: Uuid::new_v4().to_string(),
        name: name.to_string(),
        gateway,
        model_catalog_url,
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
    Ok(AddProfileResponse { profile, models: catalog.models, catalog_notice: catalog.catalog_notice })
}

#[tauri::command]
async fn models_for_profile(
    profile_id: String,
    state: State<'_, AppState>,
) -> Result<ModelCatalog, String> {
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
    fetch_model_catalog_at(&profile.gateway, &key, &profile.model_catalog_url).await
}

#[tauri::command]
async fn update_profile_connection(request: UpdateProfileConnectionRequest, state: State<'_, AppState>) -> Result<AddProfileResponse, String> {
    let gateway = normalize_gateway(&request.gateway)?;
    let model_catalog_url = normalize_model_catalog_url(&request.model_catalog_url)?;
    let previous = state.config.lock().map_err(|_| "配置锁异常")?.profiles.iter()
        .find(|p| p.id == request.profile_id).cloned().ok_or("找不到所选 Key 配置")?;
    let catalog = if request.read_models {
        let key = key_entry(&previous.id)?.get_password().map_err(|_| "Windows 凭据管理器中没有这把 Key")?;
        fetch_model_catalog_at(&gateway, &key, &model_catalog_url).await?
    } else { ModelCatalog { models: vec![], catalog_notice: "已跳过模型列表读取；连接尚未验证。".into() } };
    let profile = KeyProfile { gateway, model_catalog_url, ..previous.clone() };
    let mut config = state.config.lock().map_err(|_| "配置锁异常")?;
    let index = config.profiles.iter().position(|p| p.id == previous.id).ok_or("调用配置已删除")?;
    if config.profiles[index].gateway != previous.gateway || config.profiles[index].model_catalog_url != previous.model_catalog_url {
        return Err("连接设置已更改，请重新打开后保存。".into());
    }
    let original = config.clone();
    config.profiles[index] = profile.clone();
    if let Err(error) = write_config(&state.config_path, &config) { *config = original; return Err(error); }
    Ok(AddProfileResponse { profile, models: catalog.models, catalog_notice: catalog.catalog_notice })
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
async fn ssh_connection_info() -> Result<Value, String> {
    tauri::async_runtime::spawn_blocking(|| {
        let program_data = std::env::var_os("ProgramData").ok_or("此功能需要 Windows OpenSSH 服务器。")?;
        let key = fs::read_to_string(PathBuf::from(program_data).join("ssh").join("ssh_host_ed25519_key.pub"))
            .map_err(|_| "读不到 SSH 主机密钥，请先安装并启动 OpenSSH 服务器。")?;
        let key = key.split_whitespace().take(2).collect::<Vec<_>>().join(" ");
        if !key.starts_with("ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAI") {
            return Err("电脑的 SSH 主机密钥无效。".into());
        }
        let script = r#"
[Console]::OutputEncoding = [Text.UTF8Encoding]::new()
$addresses = @(Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
    Sort-Object InterfaceIndex | Select-Object -ExpandProperty IPAddress -Unique)
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
[PSCustomObject]@{
    name = $env:COMPUTERNAME
    user = $identity.Name
    addresses = $addresses
    remember = !($identity.Groups.Value -contains 'S-1-5-32-544')
} | ConvertTo-Json -Compress
"#;
        let mut command = std::process::Command::new("powershell.exe");
        command.args(["-NoProfile", "-NonInteractive", "-Command", script]);
        #[cfg(target_os = "windows")]
        {
            use std::os::windows::process::CommandExt;
            command.creation_flags(0x08000000);
        }
        let output = command.output().map_err(|_| "无法读取这台电脑的 SSH 设置。")?;
        if !output.status.success() {
            return Err("无法读取这台电脑的 SSH 设置。".into());
        }
        let mut data: Value = serde_json::from_slice(&output.stdout).map_err(|_| "无法读取电脑地址。")?;
        data["host_key"] = Value::String(key);
        Ok(data)
    }).await.map_err(|_| "无法读取这台电脑的 SSH 设置。".to_string())?
}

#[tauri::command]
async fn list_history(app: tauri::AppHandle, workspace: String) -> Result<Vec<history::Conversation>, String> {
    let root = app.path().app_config_dir().map_err(|error| error.to_string())?;
    let workspace = PathBuf::from(workspace);
    tauri::async_runtime::spawn_blocking(move || history::scan_project(&root, &workspace))
        .await
        .map_err(|_| "读取本地对话历史失败".into())
}

#[tauri::command]
async fn list_all_history(app: tauri::AppHandle) -> Result<Vec<history::Conversation>, String> {
    let root = app.path().app_config_dir().map_err(|error| error.to_string())?;
    tauri::async_runtime::spawn_blocking(move || history::scan_all(&root))
        .await
        .map_err(|_| "读取全部本地对话历史失败".into())
}

#[tauri::command]
async fn set_history_archived(app: tauri::AppHandle, history_ref: String, history_id: String, client: String, archived: bool) -> Result<(), String> {
    let root = app.path().app_config_dir().map_err(|error| error.to_string())?;
    tauri::async_runtime::spawn_blocking(move || history::set_archived(&root, &history_ref, &history_id, &client, archived))
        .await.map_err(|_| "更新归档状态失败".to_string())?
}

#[tauri::command]
fn start_terminal(
    mut request: StartTerminalRequest,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<TerminalInfo, String> {
    let effort = reasoning::normalize(request.reasoning_effort.as_deref())?.map(str::to_owned);
    let closing = state.closing_for_update.lock().map_err(|_| "终端状态不可用。")?;
    if *closing { return Err("正在安装更新。".into()); }
    if !matches!(request.client.as_str(), "claude" | "codex") {
        return Err("暂不支持所选客户端".into());
    }
    if !matches!(request.session_mode.as_str(), "new" | "continue" | "resume") {
        return Err("会话模式无效".into());
    }
    let workspace = PathBuf::from(request.workspace.trim());
    if !workspace.is_absolute() || !workspace.is_dir() {
        return Err("请选择一个存在的项目目录".into());
    }
    let config_root = app.path().app_config_dir().map_err(|error| error.to_string())?;
    let original = request.history_action.as_deref() == Some("original");
    let force_fork = request.history_action.as_deref() == Some("fork");
    let source = match (&request.history_ref, &request.history_id) {
        (None, None) if !original && !force_fork => None,
        (Some(reference), Some(id)) if request.session_mode == "resume" => {
            Some(history::scan_project(&config_root, &workspace).into_iter().find(|entry| {
                entry.history_ref == *reference && entry.id == *id && entry.client == request.client
            }).ok_or("在原项目目录找不到这条会话，请刷新对话历史")?)
        }
        _ => return Err("历史会话标识无效".into()),
    };
    if original {
        let entry = source.as_ref().ok_or("缺少原对话")?;
        if entry.scope == "global" {
            // The system launcher acquires the update guard itself.
            drop(closing);
            return launch_system_terminal(request.client, request.workspace, Some(&entry.id), app, state);
        }
        request.profile_id.clone_from(&entry.profile_id);
        request.model.clone_from(&entry.configured_model);
    }
    if request.model.trim().is_empty() {
        return Err(if original { "无法识别原模型，可从当前项目的历史创建分支" } else { "请选择模型" }.into());
    }
    let profile = {
        let config = state.config.lock().map_err(|_| "配置锁异常")?;
        config.profiles.iter().find(|profile| profile.id == request.profile_id).cloned()
            .ok_or(if original { "原调用配置已移除或无法识别，可从当前项目的历史创建分支" } else { "找不到所选 Key 配置" })?
    };
    let key = key_entry(&profile.id)?.get_password()
        .map_err(|_| "Windows 凭据管理器中没有这把 Key".to_string())?;
    if source.is_some() && !original && request.confirmed_gateway.as_deref() != Some(profile.gateway.as_str()) {
        return Err("目标网关已变更，请刷新后重新确认".into());
    }
    if let Some(entry) = source.as_ref() {
        if entry.workspace.is_empty()
            || !history::same_workspace(&workspace, PathBuf::from(&entry.workspace).as_path())
        {
            return Err("恢复必须使用这条会话所属的项目目录".into());
        }
    }
    use sha2::{Digest, Sha256};
    let fingerprint = format!("{:x}", Sha256::digest(key.as_bytes()));
    let matches_original = source.as_ref().is_some_and(|entry| {
        history::can_resume_in_place(entry, &profile.id, &request.model, &profile.gateway, &fingerprint)
    });
    if original && !matches_original {
        return Err("无法确认原 Key 或网关仍可用，可从当前项目的历史创建分支。".into());
    }
    let needs_fork = source.is_some() && (force_fork || !matches_original);
    let branch = source.as_ref().filter(|_| needs_fork).map(|entry| {
        history::stage_branch(entry, &config_root, &profile.id, &profile.name, &request.model, &profile.gateway, &fingerprint)
    }).transpose()?;
    let selected_home = history::launch_home(source.as_ref(), branch.as_ref(),
        history::configuration_home(&config_root, &request.client, &profile.id, &request.model,
                                    &profile.gateway, &workspace, &fingerprint));
    if source.is_none() {
        history::record_profile(&selected_home, &profile.id, &profile.name, &request.model, &profile.gateway, &fingerprint)?;
    }
    let mut shell = CommandBuilder::new("powershell.exe");
    let command_text = if request.client == "codex" {
        let codex_home = selected_home.clone();
        fs::create_dir_all(&codex_home)
            .map_err(|error| format!("创建隔离 Codex 配置失败：{error}"))?;
        let config_text = format!(
            "model = {}\nmodel_provider = \"workbench_gateway\"\n{}sandbox_mode = \"workspace-write\"\ndisable_response_storage = true\n\n[model_providers.workbench_gateway]\nname = \"Workbench gateway\"\nbase_url = {}\nwire_api = \"responses\"\nenv_key = \"WORKBENCH_GATEWAY_KEY\"\n",
            serde_json::to_string(&request.model).map_err(|error| error.to_string())?,
            reasoning::config_line(effort.as_deref(), &fs::read_to_string(codex_home.join("config.toml")).unwrap_or_default())?,
            serde_json::to_string(&gateway_api_base(&profile.gateway))
                .map_err(|error| error.to_string())?,
        );
        if source.is_none() || branch.is_some() {
            fs::write(codex_home.join("config.toml"), config_text)
                .map_err(|error| format!("写入隔离 Codex 配置失败：{error}"))?;
        }
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
        if let Some(entry) = &source {
            let action = if branch.is_some() { "fork" } else { "resume" };
            args.splice(0..0, [action.into(), entry.id.clone()]);
        } else if request.session_mode == "resume" {
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
        args.extend(request.permission_mode.args("codex"));
        args.extend(reasoning::args("codex", effort.as_deref())?);
        args.extend(tools::apply(&config_root.join("tools/codex"), &selected_home, "codex")?);
        shell.env("CODEX_HOME", codex_home);
        shell.env("WORKBENCH_GATEWAY_KEY", &key);
        let command_args = args
            .iter()
            .map(|arg| quote_powershell(arg))
            .collect::<Vec<_>>()
            .join(" ");
        format!("& 'codex' {command_args}")
    } else if request.client == "claude" {
        let claude_home = selected_home.clone();
        fs::create_dir_all(&claude_home)
            .map_err(|error| format!("创建隔离 Claude 配置失败：{error}"))?;
        shell.env("CLAUDE_CONFIG_DIR", claude_home);
        shell.env("ANTHROPIC_BASE_URL", gateway_claude_base(&profile.gateway));
        shell.env("ANTHROPIC_API_KEY", &key);
        shell.env_remove("MAX_THINKING_TOKENS");
        shell.env_remove("CLAUDE_CODE_EFFORT_LEVEL");
        let mut args = vec![
            "--model".to_string(),
            request.model.clone(),
            "--add-dir".into(),
            workspace.display().to_string(),
        ];
        args.extend(request.permission_mode.args("claude"));
        args.extend(reasoning::args("claude", effort.as_deref())?);
        if source.is_some() {
            for name in ["ANTHROPIC_AUTH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK",
                "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY"] {
                shell.env_remove(name);
            }
            shell.env("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", "1");
            for name in ["ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL",
                "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_DEFAULT_HAIKU_MODEL",
                "ANTHROPIC_SMALL_FAST_MODEL"] {
                shell.env(name, &request.model);
            }
            args.push("--bare".into());
            if !config_root.join("tools/claude/tools.json").exists() {
                let empty_mcp = selected_home.join("empty-mcp.json");
                fs::write(&empty_mcp, r#"{"mcpServers":{}}"#)
                    .map_err(|error| format!("写入隔离 Claude MCP 设置失败：{error}"))?;
                args.extend(["--strict-mcp-config".into(), "--mcp-config".into(), empty_mcp.to_string_lossy().into_owned()]);
            }
        } else {
            shell.env("ANTHROPIC_AUTH_TOKEN", &key);
        }
        args.extend(tools::apply(&config_root.join("tools/claude"), &selected_home, "claude")?);
        match request.session_mode.as_str() {
            "new" => {}
            "continue" => args.push("--continue".into()),
            "resume" => {
                args.push("--resume".into());
                if let Some(entry) = &source {
                    args.push(entry.id.clone());
                    if branch.is_some() {
                        args.push("--fork-session".into());
                    }
                }
            }
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
    let command_text = if let Some((_, seed)) = &branch {
        let cleanup = if request.client == "claude" {
            format!("Remove-Item -LiteralPath {} -Force -ErrorAction SilentlyContinue; ",
                quote_powershell(&seed.to_string_lossy()))
        } else {
            String::new()
        };
        format!(
            "try {{ {command_text}; $workbenchExitCode = {POWERSHELL_EXIT_CODE} }} finally {{ {cleanup}Remove-Item Env:WORKBENCH_GATEWAY_KEY,Env:CCH_API_KEY,Env:ANTHROPIC_API_KEY,Env:ANTHROPIC_BASE_URL,Env:CODEX_HOME,Env:CLAUDE_CONFIG_DIR -ErrorAction SilentlyContinue }}; exit $workbenchExitCode",
        )
    } else {
        command_text
    };
    // Preserve inherited proxy settings, while the user's own bypass rules and
    // local gateway continue to work for both clients.
    let bypass = local_network_bypass(std::env::var("NO_PROXY").ok().as_deref(),
                                      std::env::var("no_proxy").ok().as_deref());
    shell.env("NO_PROXY", &bypass);
    shell.env("no_proxy", &bypass);
    shell.cwd(&workspace);
    if request.client == "codex" {
        shell.env_remove("OPENAI_API_KEY");
        shell.env_remove("OPENAI_BASE_URL");
        shell.env_remove("CODEX_SQLITE_HOME");
        shell.env_remove("ANTHROPIC_API_KEY");
        // Previous releases injected the key under this name; never leak a stale one.
        shell.env_remove("CCH_API_KEY");
    }
    let info = TerminalInfo {
        id: Uuid::new_v4().to_string(),
        title: format!("{} · {}", request.client, request.model),
        workspace: workspace.display().to_string(),
        client: request.client,
        model: request.model,
        scope: "workbench".into(),
    };
    let restoring = request.session_mode != "new";
    launch_terminal(shell, command_text, source.is_none(), !restoring, info, app, &state)
}

#[tauri::command]
fn start_global_terminal(
    client: String,
    workspace: String,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<TerminalInfo, String> {
    launch_system_terminal(client, workspace, None, app, state)
}

fn launch_system_terminal(
    client: String,
    workspace: String,
    history_id: Option<&str>,
    app: tauri::AppHandle,
    state: State<'_, AppState>,
) -> Result<TerminalInfo, String> {
    let closing = state.closing_for_update.lock().map_err(|_| "终端状态不可用。")?;
    if *closing { return Err("正在安装更新。".into()); }
    if !matches!(client.as_str(), "claude" | "codex") {
        return Err("暂不支持所选客户端".into());
    }
    let workspace = PathBuf::from(workspace.trim());
    if !workspace.is_absolute() || !workspace.is_dir() {
        return Err("请选择一个存在的项目目录".into());
    }
    let mut command = format!("& {}", quote_powershell(&client));
    if let Some(history_id) = history_id {
        Uuid::parse_str(history_id).map_err(|_| "历史会话标识无效")?;
        let flag = if client == "codex" { "resume" } else { "--resume" };
        command.push_str(&format!(" {flag} {}", quote_powershell(history_id)));
    }
    let mut shell = CommandBuilder::new("powershell.exe");
    let command = format!(
        "Set-Location -LiteralPath {} -ErrorAction Stop; {command}",
        quote_powershell(&workspace.to_string_lossy()),
    );
    shell.cwd(&workspace);
    let info = TerminalInfo {
        id: Uuid::new_v4().to_string(),
        title: format!("{} · 系统全局", client),
        workspace: workspace.display().to_string(),
        client,
        model: "系统配置".into(),
        scope: "system".into(),
    };
    launch_terminal(shell, command, true, history_id.is_none(), info, app, &state)
}

fn launch_terminal(
    shell: CommandBuilder,
    command: String,
    load_profiles: bool,
    keep_shell: bool,
    info: TerminalInfo,
    app: tauri::AppHandle,
    state: &AppState,
) -> Result<TerminalInfo, String> {
    let mut terminals = state.terminals.lock().map_err(|_| "终端会话锁异常")?;
    let (handle, reader, mut child) = spawn_terminal(shell, &command, load_profiles, keep_shell)?;
    let handle = Arc::new(handle);
    let id = info.id.clone();
    let mut metadata = serde_json::to_value(&info).map_err(|e| e.to_string())?;
    metadata["running"] = Value::Bool(true);
    state.remote_sessions.lock().map_err(|_| "会话登记失败")?.insert(id.clone(), metadata);
    state.remote_output.lock().map_err(|_| "会话登记失败")?.insert(id.clone(), handle.replay.clone());
    // Keep replay for recently ended tabs without retaining every past PTY.
    let expired = {
        let mut rows = state.remote_sessions.lock().map_err(|_| "会话登记失败")?;
        let excess = rows.len().saturating_sub(64);
        let expired: Vec<_> = rows.iter().filter(|(_, row)| row["running"] == false).take(excess).map(|(id, _)| id.clone()).collect();
        for id in &expired { rows.remove(id); }
        expired
    };
    if !expired.is_empty() {
        let mut output = state.remote_output.lock().map_err(|_| "会话登记失败")?;
        for id in expired { output.remove(&id); }
    }
    terminals.insert(id.clone(), Arc::clone(&handle));
    drop(terminals);

    let reader_id = id.clone();
    let reader_app = app.clone();
    let replay = handle.replay.clone();
    thread::spawn(move || stream_terminal(reader, reader_app, reader_id, replay));
    thread::spawn(move || {
        let exit_code = child.wait().ok().map(|status| status.exit_code());
        #[cfg(windows)]
        if let Err(error) = handle.stop() {
            // Keep the handle registered so Stop can retry if descendants are still alive.
            let _ = app.emit("terminal-stop-error", StopError { session_id: id, error });
            return;
        }
        if let Some(state) = app.try_state::<AppState>() {
            if let Ok(mut terminals) = state.terminals.lock() {
                terminals.remove(&id);
            }
            if let Ok(mut rows) = state.remote_sessions.lock() { if let Some(row) = rows.get_mut(&id) { row["running"] = Value::Bool(false); } }
        }
        if let Ok(mut replay) = handle.replay.lock() { replay.process_exited(exit_code.unwrap_or(1)); }
        let _ = app.emit("terminal-exit", ExitNotice { session_id: id, exit_code });
        handle.close_pty();
    });
    Ok(info)
}

#[cfg(windows)]
#[derive(Clone, Serialize)]
struct StopError {
    session_id: String,
    error: String,
}

impl TerminalHandle {
    fn close_pty(&self) {
        // Close input before closing ConPTY. Its output is drained on a separate thread.
        drop(self.writer.lock().ok().and_then(|mut writer| writer.take()));
        let master = self.master.lock().ok().and_then(|mut master| master.take());
        drop(master);
    }
}

fn terminal_script(mut script: String, command: &str, load_profiles: bool, keep_shell: bool) -> String {
    if load_profiles {
        // Apply profiles in PowerShell's normal order after the process is assigned to its job.
        script.push_str("foreach ($workbenchProfile in @($PROFILE.AllUsersAllHosts, $PROFILE.AllUsersCurrentHost, $PROFILE.CurrentUserAllHosts, $PROFILE.CurrentUserCurrentHost)) { if ([IO.File]::Exists($workbenchProfile)) { . $workbenchProfile } }; Remove-Variable workbenchProfile -ErrorAction SilentlyContinue; ");
    }
    script.push_str("chcp 65001 > $null; $OutputEncoding = [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new(); $env:TERM = 'xterm-256color'; $env:COLORTERM = 'truecolor'; ");
    script.push_str(command);
    if !keep_shell {
        script.push_str(&format!("; $workbenchExitCode = {POWERSHELL_EXIT_CODE}; exit $workbenchExitCode"));
    }
    script
}

type TerminalReader = Box<dyn Read + Send>;
type TerminalChild = Box<dyn portable_pty::Child + Send + Sync>;

fn spawn_terminal(
    mut shell: CommandBuilder,
    command: &str,
    load_profiles: bool,
    keep_shell: bool,
) -> Result<(TerminalHandle, TerminalReader, TerminalChild), String> {
    #[cfg(windows)]
    let job = terminal_job::TerminalJob::new().map_err(|error| format!("创建终端进程组失败：{error}"))?;
    #[cfg(windows)]
    let startup = job.startup_script();
    #[cfg(not(windows))]
    let startup = String::new();
    shell.args(["-NoLogo", "-NoProfile"]);
    if keep_shell {
        shell.arg("-NoExit");
    }
    let script = terminal_script(startup, command, load_profiles, keep_shell);
    shell.args(["-Command", &script]);
    let system = native_pty_system();
    let pair = system
        .openpty(PtySize { rows: 30, cols: 100, pixel_width: 0, pixel_height: 0 })
        .map_err(|error| format!("创建 ConPTY 失败：{error}"))?;
    let reader = pair.master.try_clone_reader()
        .map_err(|error| format!("读取终端失败：{error}"))?;
    let writer = pair.master.take_writer()
        .map_err(|error| format!("打开终端输入失败：{error}"))?;
    let child = pair.slave.spawn_command(shell)
        .map_err(|error| format!("启动 Agent 失败：{error}"))?;
    #[cfg(windows)]
    if let Err(error) = job.attach(&*child) {
        let _ = child.clone_killer().kill();
        return Err(format!("绑定终端进程组失败：{error}"));
    }
    drop(pair.slave);
    let handle = TerminalHandle {
        replay: Arc::new(Mutex::new(remote::Replay::default())),
        writer: Mutex::new(Some(writer)),
        master: Mutex::new(Some(pair.master)),
        #[cfg(windows)]
        job: Mutex::new(job),
        #[cfg(not(windows))]
        killer: Mutex::new(child.clone_killer()),
    };
    Ok((handle, reader, child))
}

fn stream_terminal(
    mut reader: Box<dyn Read + Send>,
    app: tauri::AppHandle,
    session_id: String,
    replay: Arc<Mutex<remote::Replay>>,
) {
    let mut buffer = [0u8; 8192];
    loop {
        match reader.read(&mut buffer) {
            Ok(0) | Err(_) => break,
            Ok(count) => {
                if let Ok(mut replay) = replay.lock() { replay.append(&buffer[..count]); }
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
    if let Ok(mut replay) = replay.lock() { replay.output_finished(); }
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
    handle.replay.lock().map_err(|_| "终端控制状态不可用")?.release();
    let mut writer = handle.writer.lock().map_err(|_| "终端输入锁异常")?;
    writer
        .as_mut()
        .ok_or("终端已退出")?
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
    if handle.replay.lock().map_err(|_| "终端控制状态不可用")?.has_remote_control() { return Ok(()); }
    handle
        .master
        .lock()
        .map_err(|_| "终端尺寸锁异常")?
        .as_ref()
        .ok_or("终端已退出")?
        .resize(PtySize {
            rows,
            cols,
            pixel_width: 0,
            pixel_height: 0,
        })
        .map_err(|error| format!("调整终端尺寸失败：{error}"))
}

#[tauri::command]
async fn stop_terminal(session_id: String, state: State<'_, AppState>) -> Result<(), String> {
    let handle = state
        .terminals
        .lock()
        .map_err(|_| "终端会话锁异常")?
        .get(&session_id)
        .cloned();
    let Some(handle) = handle else { return Ok(()); };
    let stop_handle = Arc::clone(&handle);
    tauri::async_runtime::spawn_blocking(move || stop_handle.stop())
        .await.map_err(|error| format!("停止终端失败：{error}"))??;
    state.terminals.lock().map_err(|_| "终端会话锁异常")?.remove(&session_id);
    // Release ConPTY independently of the process tree. Windows can block here while
    // flushing output; a restart of the same conversation only depends on the job.
    thread::spawn(move || handle.close_pty());
    Ok(())
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .plugin(tauri_plugin_dialog::init())
        .setup(|app| {
            let config_path = app.path().app_config_dir()?.join("settings.json");
            app.manage(AppState::new(config_path));
            app.manage(updates::UpdateState::default());
            if app.state::<AppState>().config.lock().map(|c| c.remote_enabled).unwrap_or(false) {
                let _ = app.state::<AppState>().remote.set(app.handle().clone(), true);
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            load_app_state,
            usage_snapshot,
            model_pricing,
            save_pricing_source,
            add_profile,
            update_profile_connection,
            models_for_profile,
            select_profile,
            save_preferences,
            remove_profile,
            start_terminal,
            start_global_terminal,
            ssh_connection_info,
            remote::set_remote_enabled,
            remote::set_remote_discovery_port,
            remote::sync_remote_permissions,
            list_history,
            list_all_history,
            set_history_archived,
            write_terminal,
            attachments::stage_clipboard_image,
            resize_terminal,
            stop_terminal,
            tools::tools_get,
            tools::tools_save,
            tools::tools_import,
            updates::check_workbench_update,
            updates::download_workbench_update,
            updates::cancel_workbench_update,
            updates::install_workbench_update
        ])
        .run(tauri::generate_context!())
        .expect("error while running tauri application");
}

#[cfg(test)]
mod tests {
    use super::normalize_gateway;

    #[test]
    fn proxy_bypass_keeps_user_rules_and_local_gateway() {
        assert_eq!(super::local_network_bypass(Some("build.test,localhost"), Some("build.test,.internal.test")),
                   "127.0.0.1,localhost,::1,build.test,.internal.test");
    }

    #[test]
    fn permission_modes_keep_codex_approvals_and_claude_requires_explicit_bypass() {
        use super::AgentPermission;
        for (mode, sandbox, network) in [
            (AgentPermission::Restricted, "workspace-write", "false"),
            (AgentPermission::Standard, "workspace-write", "true"),
            (AgentPermission::Full, "danger-full-access", "true"),
        ] {
            let args = mode.args("codex");
            assert_eq!(args[args.iter().position(|a| a == "--sandbox").unwrap() + 1], sandbox);
            assert_eq!(args[args.iter().position(|a| a == "--ask-for-approval").unwrap() + 1], "on-request");
            assert!(args.contains(&format!("sandbox_workspace_write.network_access={network}")));
            assert!(!args.iter().any(|a| a == "--dangerously-bypass-approvals-and-sandbox"));
        }
        assert_eq!(AgentPermission::Standard.args("claude"), ["--permission-mode", "default"]);
        assert_eq!(AgentPermission::Full.args("claude"), ["--permission-mode", "bypassPermissions"]);
        assert!(serde_json::from_str::<AgentPermission>(r#""unknown""#).is_err());
    }

    #[cfg(windows)]
    #[test]
    fn closing_terminal_releases_exclusive_file_held_by_child() {
        use super::terminal_job::TerminalJob;
        use std::{fs, process::Command, thread, time::{Duration, Instant}};

        let dir = std::env::temp_dir().join(format!("workbench-test-{}", uuid::Uuid::new_v4()));
        fs::create_dir(&dir).unwrap();
        let lock = dir.join("session.lock");
        let ready = dir.join("ready");
        fs::write(&lock, b"test").unwrap();
        let mut job = TerminalJob::new().unwrap();
        let script = job.startup_script() + r#"
            Start-Process -FilePath powershell.exe -NoNewWindow -ArgumentList '-NoProfile -Command "$f = [IO.File]::Open($env:WORKBENCH_TEST_LOCK, [IO.FileMode]::Open, [IO.FileAccess]::ReadWrite, [IO.FileShare]::None); [IO.File]::WriteAllText($env:WORKBENCH_TEST_READY, [string]$PID); Start-Sleep -Seconds 90"'
            Start-Sleep -Seconds 90
        "#;
        let mut parent = Command::new("powershell.exe")
            .env("WORKBENCH_TEST_LOCK", &lock)
            .env("WORKBENCH_TEST_READY", &ready)
            .args(["-NoProfile", "-NonInteractive", "-Command", &script])
            .spawn()
            .unwrap();
        job.attach(&parent).unwrap();

        let deadline = Instant::now() + Duration::from_secs(15);
        while !ready.exists() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(25));
        }
        let child_held_lock = ready.exists() && fs::read(&lock).is_err();
        job.terminate_and_wait().unwrap();
        let released = fs::read(&lock);
        parent.wait().unwrap();
        fs::remove_dir_all(dir).unwrap();

        assert!(child_held_lock, "child never acquired the exclusive file");
        assert_eq!(released.unwrap(), b"test");
    }

    #[cfg(windows)]
    #[test]
    fn failed_restore_exits_with_the_cli_status() {
        use super::{terminal_job::TerminalJob, terminal_script};
        use std::{process::Command, thread, time::{Duration, Instant}};

        let mut job = TerminalJob::new().unwrap();
        let script = terminal_script(job.startup_script(), "cmd.exe /d /c exit 37", false, false);
        let mut child = Command::new("powershell.exe")
            .args(["-NoProfile", "-NonInteractive", "-Command", &script])
            .spawn()
            .unwrap();
        job.attach(&child).unwrap();
        let deadline = Instant::now() + Duration::from_secs(15);
        let code = loop {
            if let Some(status) = child.try_wait().unwrap() {
                break status.code();
            }
            if Instant::now() >= deadline { break None; }
            thread::sleep(Duration::from_millis(25));
        };
        job.terminate_and_wait().unwrap();
        assert_eq!(code, Some(37));
    }

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
    fn gateway_rejects_credentials_and_keeps_api_prefixes() {
        assert!(normalize_gateway("https://user:example@gateway.example.com").is_err());
        assert_eq!(normalize_gateway("https://gateway.example.com/proxy/v1/").unwrap(), "https://gateway.example.com/proxy/v1");
        assert!(normalize_gateway("https://gateway.example.com/v1?key=value").is_err());
    }

    #[test]
    fn explicit_prefixes_build_correct_client_bases() {
        for prefix in ["/proxy/v1", "/api/paas/v4", "/tenant/anthropic", "/v2%2Ftenant/v1"] {
            let base = format!("https://gateway.test{prefix}");
            assert_eq!(super::gateway_api_base(&base), base);
            assert_eq!(super::gateway_claude_base(&base), base.strip_suffix("/v1").unwrap_or(&base));
        }
        assert_eq!(super::gateway_api_base("https://gateway.test"), "https://gateway.test/v1");
    }

    #[test]
    fn independent_catalog_preserves_call_route_and_legacy_profiles_default_to_shared() {
        let gateway = "https://gateway.test/api/v1";
        let endpoint = "https://gateway.test/api/paas/v4/models";
        let profile: super::KeyProfile = serde_json::from_str(r#"{"id":"p","name":"Legacy","gateway":"https://gateway.test/api/v1"}"#).unwrap();
        assert!(profile.model_catalog_url.is_empty());
        assert_eq!(super::gateway_model_urls(gateway, "").unwrap(), vec!["https://gateway.test/api/v1/models"]);
        assert_eq!(super::gateway_model_urls(gateway, endpoint).unwrap(), vec![endpoint]);
        assert_eq!(super::gateway_api_base(gateway), gateway);
        assert_eq!(super::normalize_model_catalog_url("https://gateway.test/v1").unwrap(), "https://gateway.test/v1");
        for url in ["http://gateway.test/models", "https://key@gateway.test/models", "https://gateway.test/models?token=x", "file:///tmp/models"] {
            assert!(super::gateway_model_urls(gateway, url).is_err());
        }
    }

    #[test]
    fn explicit_catalog_only_queries_the_selected_endpoint_and_does_not_fall_back() {
        let (url, server) = catalog_fixture(vec![("200 OK", r#"{"data":[{"id":"model-from-catalog"}]}"#)]);
        let endpoint = format!("{url}/directory");
        let catalog = tauri::async_runtime::block_on(super::fetch_model_catalog_at("https://call.test/api/v1", "dummy", &endpoint)).unwrap();
        assert_eq!(catalog.models, vec!["model-from-catalog"]);
        assert_eq!(server.join().unwrap(), vec!["GET /custom/directory?limit=1000 HTTP/1.1\r\n"]);
        let (url, server) = catalog_fixture(vec![("404 Not Found", "")]);
        let catalog = tauri::async_runtime::block_on(super::fetch_model_catalog_at("https://call.test/api/v1", "dummy", &url)).unwrap();
        assert!(catalog.models.is_empty());
        assert_eq!(server.join().unwrap().len(), 1);
    }

    fn catalog_fixture(statuses: Vec<(&'static str, &'static str)>) -> (String, std::thread::JoinHandle<Vec<String>>) {
        use std::io::{BufRead, BufReader, Write};
        let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}/custom", listener.local_addr().unwrap());
        let server = std::thread::spawn(move || {
            let mut paths = vec![];
            for (status, body) in statuses {
                let (mut stream, _) = listener.accept().unwrap();
                stream.set_read_timeout(Some(std::time::Duration::from_secs(5))).unwrap();
                let mut reader = BufReader::new(&stream);
                let mut first = String::new();
                reader.read_line(&mut first).unwrap();
                paths.push(first);
                loop {
                    let mut line = String::new();
                    if reader.read_line(&mut line).unwrap() == 0 || line == "\r\n" { break; }
                }
                write!(stream, "HTTP/1.1 {status}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}", body.len()).unwrap();
            }
            paths
        });
        (url, server)
    }

    #[test]
    fn missing_catalog_is_distinct_from_auth_redirect_and_empty_results() {
        let (url, server) = catalog_fixture(vec![("404 Not Found", ""), ("405 Method Not Allowed", "")]);
        let catalog = tauri::async_runtime::block_on(super::fetch_model_catalog(&url, "dummy")).unwrap();
        assert!(catalog.models.is_empty());
        assert!(catalog.catalog_notice.contains("尚未验证"));
        let paths = server.join().unwrap();
        assert!(paths[0].starts_with("GET /custom/models?limit=1000 "));
        assert!(paths[1].starts_with("GET /custom/v1/models?limit=1000 "));
        for statuses in [vec![("401 Unauthorized", ""), ("403 Forbidden", "")], vec![("302 Found", "")], vec![("500 Internal Server Error", "")]] {
            let (url, server) = catalog_fixture(statuses);
            let error = tauri::async_runtime::block_on(super::fetch_model_catalog(&url, "dummy")).err().unwrap();
            assert!(!error.contains("dummy"));
            server.join().unwrap();
        }
        let (url, server) = catalog_fixture(vec![("200 OK", r#"{"data":[]}"#)]);
        let catalog = tauri::async_runtime::block_on(super::fetch_model_catalog(&url, "dummy")).unwrap();
        assert!(catalog.catalog_notice.contains("空模型列表"));
        server.join().unwrap();
    }

    #[test]
    fn model_list_works_with_a_bearer_only_api() {
        use std::io::{BufRead, BufReader, Write};
        use std::net::TcpListener;

        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let url = format!("http://{}", listener.local_addr().unwrap());
        let server = std::thread::spawn(move || {
            for attempt in 0..2 {
                let (mut stream, _) = listener.accept().unwrap();
                stream
                    .set_read_timeout(Some(std::time::Duration::from_secs(5)))
                    .unwrap();
                let mut request = String::new();
                let mut reader = BufReader::new(&stream);
                loop {
                    let mut line = String::new();
                    if reader.read_line(&mut line).unwrap() == 0 || line == "\r\n" {
                        break;
                    }
                    request.push_str(&line.to_ascii_lowercase());
                }
                let (status, body) = if attempt == 0 {
                    assert!(request.starts_with("get /v1/models?limit=1000 "));
                    assert!(request.contains("\r\nx-api-key: test-key\r\n"));
                    ("401 Unauthorized", "Bearer required")
                } else {
                    assert!(request.starts_with("get /v1/models "));
                    assert!(request.contains("\r\nauthorization: bearer test-key\r\n"));
                    ("200 OK", r#"{"data":[{"id":"model-a"}]}"#)
                };
                write!(
                    stream,
                    "HTTP/1.1 {status}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                    body.len()
                )
                .unwrap();
            }
        });
        let models = tauri::async_runtime::block_on(super::fetch_models(&url, "test-key")).unwrap();
        assert_eq!(models, ["model-a"]);
        server.join().unwrap();
    }
}
