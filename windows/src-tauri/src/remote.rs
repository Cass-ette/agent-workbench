use crate::{AgentPermission, AppState, StartTerminalRequest};
use base64::{Engine, engine::general_purpose::STANDARD};
use serde_json::{Value, json};
use std::{
    collections::VecDeque,
    fs,
    io::{Read, Write},
    net::{TcpListener, TcpStream},
    path::PathBuf,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    thread,
    time::{Duration, Instant},
};
use tauri::{Emitter, Manager};

struct Upload {
    owner: String,
    session: String,
    path: PathBuf,
    total: u64,
    received: u64,
}
#[derive(Default)]
pub struct Uploads {
    files: std::collections::HashMap<String, Upload>,
}
impl Uploads {
    fn receive(
        &mut self,
        root: PathBuf,
        request: &Value,
        session: &str,
        owner: &str,
    ) -> Result<Value, String> {
        let id = request["upload_id"].as_str().ok_or("附件标识无效")?;
        uuid::Uuid::parse_str(id).map_err(|_| "附件标识无效")?;
        let total = request["size"]
            .as_u64()
            .filter(|n| *n > 0 && *n <= 20 * 1024 * 1024)
            .ok_or("附件超过 20 MB")?;
        let offset = request["offset"].as_u64().ok_or("附件偏移无效")?;
        let data = STANDARD
            .decode(request["data"].as_str().ok_or("附件数据无效")?)
            .map_err(|_| "附件数据无效")?;
        if data.is_empty() || data.len() > 131_072 {
            return Err("附件分段过大".into());
        }
        if !self.files.contains_key(id) {
            if offset != 0 || self.files.values().filter(|f| f.received < f.total).count() >= 4 {
                return Err("请重新选择附件或等待当前传输完成".into());
            }
            fs::create_dir_all(&root).map_err(|e| e.to_string())?;
            let name: String = request["name"]
                .as_str()
                .unwrap_or("attachment")
                .chars()
                .map(|c| {
                    if c.is_alphanumeric() || ".-_".contains(c) {
                        c
                    } else {
                        '_'
                    }
                })
                .take(80)
                .collect();
            let path = root.join(format!("{id}-{name}"));
            fs::OpenOptions::new()
                .write(true)
                .create_new(true)
                .open(&path)
                .map_err(|_| "电脑无法保存附件")?;
            self.files.insert(
                id.into(),
                Upload {
                    owner: owner.into(),
                    session: session.into(),
                    path,
                    total,
                    received: 0,
                },
            );
        }
        let upload = self.files.get_mut(id).ok_or("附件不存在")?;
        if upload.owner != owner
            || upload.session != session
            || upload.total != total
            || upload.received != offset
            || offset + data.len() as u64 > total
        {
            return Err("附件传输顺序不一致，请重新选择".into());
        }
        fs::OpenOptions::new()
            .append(true)
            .open(&upload.path)
            .map_err(|e| e.to_string())?
            .write_all(&data)
            .map_err(|e| e.to_string())?;
        upload.received += data.len() as u64;
        Ok(json!({"received":upload.received,"complete":upload.received==total}))
    }
    fn path(&self, id: &str, session: &str, owner: &str) -> Result<String, String> {
        let upload = self
            .files
            .get(id)
            .filter(|u| u.owner == owner && u.session == session && u.received == u.total)
            .ok_or("附件尚未传输完成")?;
        Ok(upload.path.to_string_lossy().into_owned())
    }
    fn cancel(&mut self, id: &str, session: &str, owner: &str) {
        if self
            .files
            .get(id)
            .is_some_and(|u| u.session == session && u.owner == owner)
        {
            if let Some(upload) = self.files.remove(id) {
                if upload.received < upload.total {
                    let _ = fs::remove_file(upload.path);
                }
            }
        }
    }
}

#[derive(Default)]
pub struct Replay {
    bytes: VecDeque<u8>,
    base: u64,
    owner: String,
    expires: Option<Instant>,
    release_reason: &'static str,
    seen: VecDeque<String>,
    pub exit_code: Option<u32>,
    reader_done: bool,
    pending_exit: Option<u32>,
}
impl Replay {
    pub fn process_exited(&mut self, code: u32) {
        self.pending_exit = Some(code);
        if self.reader_done {
            self.exit_code = Some(code);
        }
    }
    pub fn output_finished(&mut self) {
        self.reader_done = true;
        self.exit_code = self.pending_exit;
    }
    pub fn has_remote_control(&self) -> bool {
        !self.owner.is_empty() && self.expires.is_some_and(|t| t > Instant::now())
    }
    pub fn append(&mut self, bytes: &[u8]) {
        self.bytes.extend(bytes);
        while self.bytes.len() > 1_048_576 {
            self.bytes.pop_front();
            self.base += 1;
        }
    }
    pub fn release(&mut self) {
        self.expires = None;
        self.release_reason = "desktop";
    }
    fn detach(&mut self, client: &str) {
        if self.owner == client {
            self.expires = None;
            self.release_reason = "read_only";
        }
    }
    fn claim(&mut self, client: &str) {
        self.owner = client.into();
        self.expires = Some(Instant::now() + Duration::from_secs(30));
        self.release_reason = "";
    }
    fn require_control(&mut self, client: &str, input: Option<&str>) -> Result<bool, String> {
        if client.is_empty()
            || self.owner != client
            || self.expires.is_none_or(|t| t <= Instant::now())
        {
            return Err("当前为只读查看；请重新点接续并控制".into());
        }
        self.expires = Some(Instant::now() + Duration::from_secs(30));
        if let Some(id) = input {
            if id.is_empty() || id.len() > 80 {
                return Err("输入标识无效".into());
            }
            if self.seen.iter().any(|s| s == id) {
                return Ok(false);
            }
            self.seen.push_back(id.into());
            if self.seen.len() > 256 {
                self.seen.pop_front();
            }
        }
        Ok(true)
    }
    fn snapshot(&mut self, after: u64, client: &str) -> Value {
        let control = !client.is_empty()
            && self.owner == client
            && self.expires.is_some_and(|t| t > Instant::now());
        let state = if control {
            self.expires = Some(Instant::now() + Duration::from_secs(30));
            "controlled"
        } else if !client.is_empty() && self.owner == client {
            if self.release_reason.is_empty() { "expired" } else { self.release_reason }
        } else if self.has_remote_control() { "other_device" } else { "read_only" };
        let start = after
            .max(self.base)
            .min(self.base + self.bytes.len() as u64);
        let bytes: Vec<u8> = self
            .bytes
            .iter()
            .skip((start - self.base) as usize)
            .take(65_536)
            .copied()
            .collect();
        json!({"data":STANDARD.encode(&bytes),"after":start+bytes.len() as u64,"truncated":after<self.base,"can_write":control,"control_state":state,"exit_code":self.exit_code})
    }
}

#[derive(Default)]
pub struct Service {
    current: Mutex<Option<(Arc<AtomicBool>, PathBuf, String)>>,
    discovery: crate::discovery::Publisher,
}
impl Service {
    pub fn set(&self, app: tauri::AppHandle, enabled: bool) -> Result<(), String> {
        let mut current = self.current.lock().map_err(|_| "接续服务状态不可用")?;
        if let Some((running, path, _)) = current.take() {
            running.store(false, Ordering::SeqCst);
            let _ = fs::remove_file(path);
        }
        if !enabled {
            self.discovery.set(false, 22)?;
            return Ok(());
        }
        let listener = TcpListener::bind("127.0.0.1:0").map_err(|e| e.to_string())?;
        listener.set_nonblocking(true).map_err(|e| e.to_string())?;
        let port = listener.local_addr().map_err(|e| e.to_string())?.port();
        let token = format!("{}{}", uuid::Uuid::new_v4(), uuid::Uuid::new_v4());
        let path = PathBuf::from(std::env::var_os("USERPROFILE").ok_or("找不到当前电脑账号")?)
            .join(".agent-workbench/remote.json");
        fs::create_dir_all(path.parent().unwrap()).map_err(|e| e.to_string())?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            fs::set_permissions(path.parent().unwrap(), fs::Permissions::from_mode(0o700))
                .map_err(|e| e.to_string())?;
        }
        fs::write(
            &path,
            json!({"schema":1,"port":port,"token":token}).to_string(),
        )
        .map_err(|e| e.to_string())?;
        let running = Arc::new(AtomicBool::new(true));
        *current = Some((running.clone(), path, token.clone()));
        let ssh_port = app.state::<AppState>().config.lock().map(|c| c.remote_ssh_port).unwrap_or(22);
        let _ = self.discovery.set(true, if ssh_port == 0 { 22 } else { ssh_port });
        thread::spawn(move || {
            while running.load(Ordering::SeqCst) {
                match listener.accept() {
                    Ok((stream, _)) => {
                        let app = app.clone();
                        let token = token.clone();
                        let running = running.clone();
                        thread::spawn(move || serve(stream, &token, app, running));
                    }
                    Err(error) if error.kind() == std::io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(100))
                    }
                    Err(_) => break,
                }
            }
        });
        Ok(())
    }
}

#[tauri::command]
pub fn set_remote_discovery_port(state: tauri::State<'_, AppState>, port: u16) -> Result<(), String> {
    if port == 0 { return Err("SSH 端口无效".into()); }
    let enabled = state.config.lock().map_err(|_| "配置不可用")?.remote_enabled;
    state.remote.discovery.set(enabled, port)?;
    let mut config = state.config.lock().map_err(|_| "配置不可用")?;
    config.remote_ssh_port = port;
    crate::write_config(&state.config_path, &config)
}

fn read_request(stream: &mut TcpStream, token: &str) -> Result<Value, String> {
    stream
        .set_read_timeout(Some(Duration::from_secs(10)))
        .map_err(|e| e.to_string())?;
    stream
        .set_write_timeout(Some(Duration::from_secs(10)))
        .map_err(|e| e.to_string())?;
    let mut bytes = Vec::new();
    let mut buffer = [0; 8192];
    let header_end = loop {
        let count = stream.read(&mut buffer).map_err(|_| "请求未完成")?;
        if count == 0 {
            return Err("请求未完成".into());
        }
        bytes.extend_from_slice(&buffer[..count]);
        if let Some(index) = bytes.windows(4).position(|s| s == b"\r\n\r\n") {
            break index + 4;
        }
        if bytes.len() > 8192 {
            return Err("请求头过大".into());
        }
    };
    let header = std::str::from_utf8(&bytes[..header_end]).map_err(|_| "请求头无效")?;
    let mut lines = header.split("\r\n");
    if lines.next() != Some("POST /rpc HTTP/1.1") {
        return Err("请求无效".into());
    }
    let mut headers = std::collections::HashMap::new();
    for line in lines.filter(|s| !s.is_empty()) {
        let (key, value) = line.split_once(':').ok_or("请求头无效")?;
        if headers
            .insert(key.to_ascii_lowercase(), value.trim().to_owned())
            .is_some()
        {
            return Err("重复请求头".into());
        }
    }
    if headers.get("authorization").map(String::as_str) != Some(format!("Bearer {token}").as_str())
        || headers.contains_key("transfer-encoding")
    {
        return Err("未授权".into());
    }
    let length: usize = headers
        .get("content-length")
        .ok_or("请求缺少长度")?
        .parse()
        .map_err(|_| "请求长度无效")?;
    if length == 0 || length > 1_048_576 {
        return Err("请求过大".into());
    }
    while bytes.len() < header_end + length {
        let count = stream.read(&mut buffer).map_err(|_| "请求未完成")?;
        if count == 0 {
            return Err("请求未完成".into());
        }
        bytes.extend_from_slice(&buffer[..count]);
    }
    serde_json::from_slice(&bytes[header_end..header_end + length])
        .map_err(|_| "请求格式无效".into())
}
fn serve(mut stream: TcpStream, token: &str, app: tauri::AppHandle, running: Arc<AtomicBool>) {
    let result = read_request(&mut stream, token).and_then(|request| {
        if !running.load(Ordering::SeqCst) {
            return Err("电脑已关闭接续服务".into());
        }
        operation(&app, request)
    });
    let body = match result {
        Ok(value) => json!({"ok":true,"value":value}),
        Err(error) => json!({"ok":false,"error":error}),
    }
    .to_string();
    let _ = write!(
        stream,
        "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nCache-Control: no-store\r\nConnection: close\r\nContent-Length: {}\r\n\r\n{}",
        body.len(),
        body
    );
}

fn operation(app: &tauri::AppHandle, request: Value) -> Result<Value, String> {
    let text = |key: &str| request.get(key).and_then(Value::as_str).unwrap_or("");
    let op = text("op");
    if op == "ping" {
        return Ok(json!({"schema":1}));
    }
    let client_id = text("client_id");
    uuid::Uuid::parse_str(client_id).map_err(|_| "控制端标识无效")?;
    let state = app.state::<AppState>();
    if op == "state" {
        let config = state.config.lock().map_err(|_| "配置不可用")?.clone();
        let sessions: Vec<_> = state
            .remote_sessions
            .lock()
            .map_err(|_| "会话不可用")?
            .values()
            .cloned()
            .collect();
        return Ok(
            json!({"schema":1,"platform":"windows","capabilities":["sessions","launch","system-cli","history","history-archive","reasoning-effort","models","attachments"],
            "profiles":config.profiles,"workspace":config.workspace,"workspaces":[],"profile_id":config.selected_profile_id,
            "client":config.client,"model":config.model,"permissions":config.remote_permissions,"sessions":sessions}),
        );
    }
    let root = app.path().app_config_dir().map_err(|e| e.to_string())?;
    if op == "history" {
        let rows = if text("workspace").is_empty() {
            crate::history::scan_all(&root)
        } else {
            crate::history::scan_project(&root, &PathBuf::from(text("workspace")))
        };
        let rows: Vec<_> = rows
            .into_iter()
            .filter(|r| text("client").is_empty() || r.client == text("client"))
            .filter(|r| request["include_archived"] == true || !r.archived)
            .collect();
        return Ok(json!({"sessions":rows}));
    }
    if op == "history-archive" {
        let archived = request["archived"].as_bool().ok_or("归档状态无效")?;
        crate::history::set_archived(&root, text("history_ref"), text("history_id"), text("client"), archived)?;
        return Ok(json!({"archived":archived}));
    }
    if op == "models" {
        let models = tauri::async_runtime::block_on(crate::models_for_profile(
            text("profile_id").into(),
            state,
        ))?;
        return Ok(json!({"models":models}));
    }
    if op == "launch" {
        let client = text("client").to_string();
        let workspace = text("workspace").to_string();
        let info = if text("scope") == "system" && text("history_action").is_empty() {
            crate::start_global_terminal(client, workspace, app.clone(), state)?
        } else {
            let action = text("history_action");
            let rows = if action.is_empty() {
                vec![]
            } else {
                crate::history::scan_project(&root, &PathBuf::from(&workspace))
            };
            let source = rows.into_iter().find(|r| {
                r.client == client && (if action == "recent" { !r.archived } else { r.history_ref == text("history_ref") })
            });
            if !action.is_empty() && source.is_none() {
                return Err("电脑当前项目找不到这条会话".into());
            }
            if let Some(row) = &source {
                if matches!(action, "recent" | "original") {
                    let existing = state
                        .remote_sessions
                        .lock()
                        .map_err(|_| "会话不可用")?
                        .values()
                        .find(|r| {
                            r.get("history_ref").and_then(Value::as_str) == Some(&row.history_ref)
                        })
                        .cloned();
                    if let Some(existing) = existing {
                        return Ok(existing);
                    }
                    if state
                        .remote_sessions
                        .lock()
                        .map_err(|_| "会话不可用")?
                        .values()
                        .any(|r| {
                            r["running"] == true
                                && r["workspace"].as_str() == Some(workspace.as_str())
                                && r["client"].as_str() == Some(client.as_str())
                                && r.get("history_ref").is_none()
                        })
                    {
                        return Err("此项目有正在运行的会话，请从电脑运行列表接续；要恢复其他历史，请先确认原会话已结束".into());
                    }
                }
            }
            let permission = state
                .config
                .lock()
                .map_err(|_| "配置不可用")?
                .remote_permissions
                .get(&client)
                .cloned()
                .ok_or("请先在电脑确认 Agent 权限设置")?;
            let permission_mode = serde_json::from_value::<AgentPermission>(json!(permission))
                .map_err(|_| "权限无效")?;
            let profile_id = text("profile_id").to_string();
            let gateway = state
                .config
                .lock()
                .map_err(|_| "配置不可用")?
                .profiles
                .iter()
                .find(|p| p.id == profile_id)
                .map(|p| p.gateway.clone());
            let info = crate::start_terminal(
                StartTerminalRequest {
                    profile_id,
                    client,
                    model: text("model").into(),
                    workspace,
                    session_mode: if source.is_some() { "resume" } else { "new" }.into(),
                    history_ref: source.as_ref().map(|r| r.history_ref.clone()),
                    history_id: source.as_ref().map(|r| r.id.clone()),
                    confirmed_gateway: gateway,
                    history_action: if source.is_some() {
                        Some(if action == "fork" { "fork" } else { "original" }.into())
                    } else {
                        None
                    },
                    permission_mode,
                    reasoning_effort: request.get("reasoning_effort").map(|value| value.as_str().map(str::to_owned).ok_or("思考强度无效")).transpose()?,
                },
                app.clone(),
                state,
            )?;
            if matches!(action, "original" | "recent") {
                if let Some(source) = source {
                    if let Ok(mut rows) = app.state::<AppState>().remote_sessions.lock() {
                        if let Some(row) = rows.get_mut(&info.id) {
                            row["history_ref"] = json!(source.history_ref);
                        }
                    }
                }
            }
            info
        };
        let _ = app.emit("remote-terminal-created", &info);
        return Ok(serde_json::to_value(info).map_err(|e| e.to_string())?);
    }
    let id = text("session_id");
    if op == "output" {
        let output = state
            .remote_output
            .lock()
            .map_err(|_| "终端输出不可用")?
            .get(id)
            .cloned()
            .ok_or("会话已不存在")?;
        let value = output
            .lock()
            .map_err(|_| "终端输出不可用")?
            .snapshot(request["after"].as_u64().unwrap_or(0), client_id);
        return Ok(value);
    }
    let handle = state
        .terminals
        .lock()
        .map_err(|_| "会话不可用")?
        .get(id)
        .cloned()
        .ok_or("电脑会话已结束或不存在，请刷新")?;
    let mut replay = handle.replay.lock().map_err(|_| "终端输出不可用")?;
    match op {
        "attachment" => {
            replay.require_control(client_id, None)?;
            state
                .remote_uploads
                .lock()
                .map_err(|_| "附件状态不可用")?
                .receive(root.join("attachments/remote"), &request, id, client_id)
        }
        "attachment-cancel" => {
            state
                .remote_uploads
                .lock()
                .map_err(|_| "附件状态不可用")?
                .cancel(text("upload_id"), id, client_id);
            Ok(json!({"ok":true}))
        }
        "attachment-paste" => {
            let path = state
                .remote_uploads
                .lock()
                .map_err(|_| "附件状态不可用")?
                .path(text("upload_id"), id, client_id)?;
            if replay.require_control(client_id, Some(text("upload_id")))? {
                let bytes = format!("\u{1b}[200~{} \u{1b}[201~", crate::quote_powershell(&path));
                handle
                    .writer
                    .lock()
                    .map_err(|_| "终端不可用")?
                    .as_mut()
                    .ok_or("终端已退出")?
                    .write_all(bytes.as_bytes())
                    .map_err(|e| e.to_string())?;
            }
            Ok(json!({"attached":true}))
        }
        "attach" => {
            replay.claim(client_id);
            Ok(state
                .remote_sessions
                .lock()
                .map_err(|_| "会话不可用")?
                .get(id)
                .cloned()
                .ok_or("会话不存在")?)
        }
        "output" => Ok(replay.snapshot(request["after"].as_u64().unwrap_or(0), client_id)),
        "detach" => {
            replay.detach(client_id);
            Ok(json!({"detached":true}))
        }
        "input" => {
            if !replay.require_control(client_id, Some(text("input_id")))? {
                return Ok(json!({"accepted":true}));
            }
            let bytes = STANDARD.decode(text("data")).map_err(|_| "输入格式无效")?;
            if bytes.len() > 65_536 {
                return Err("输入过大".into());
            }
            handle
                .writer
                .lock()
                .map_err(|_| "终端不可用")?
                .as_mut()
                .ok_or("终端已退出")?
                .write_all(&bytes)
                .map_err(|e| e.to_string())?;
            Ok(json!({"accepted":true}))
        }
        "resize" => {
            replay.require_control(client_id, None)?;
            drop(replay);
            handle
                .master
                .lock()
                .map_err(|_| "终端不可用")?
                .as_ref()
                .ok_or("终端已退出")?
                .resize(portable_pty::PtySize {
                    cols: request["cols"].as_u64().unwrap_or(80).clamp(10, 400) as u16,
                    rows: request["rows"].as_u64().unwrap_or(24).clamp(2, 200) as u16,
                    pixel_width: 0,
                    pixel_height: 0,
                })
                .map_err(|e| e.to_string())?;
            Ok(json!({"ok":true}))
        }
        "stop" => {
            replay.require_control(client_id, None)?;
            drop(replay);
            tauri::async_runtime::block_on(crate::stop_terminal(id.into(), state))?;
            Ok(json!({"stopping":true}))
        }
        _ => Err("电脑版本尚不支持此功能".into()),
    }
}

#[tauri::command]
pub fn sync_remote_permissions(
    state: tauri::State<'_, AppState>,
    permissions: std::collections::HashMap<String, String>,
) -> Result<(), String> {
    if permissions.iter().any(|(k, v)| {
        !matches!(k.as_str(), "codex" | "claude")
            || !matches!(v.as_str(), "restricted" | "standard" | "full" | "dangerous")
    }) {
        return Err("权限设置无效".into());
    }
    let mut config = state.config.lock().map_err(|_| "配置不可用")?;
    config.remote_permissions = permissions;
    crate::write_config(&state.config_path, &config)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn attachment_is_ordered_and_bound_to_session() {
        let root = std::env::temp_dir().join(uuid::Uuid::new_v4().to_string());
        let mut uploads = Uploads::default();
        let id = uuid::Uuid::new_v4().to_string();
        let mut request = json!({"upload_id":id,"name":"../../image.png","size":4,"offset":0,"data":STANDARD.encode([1,2])});
        assert_eq!(
            uploads
                .receive(root.clone(), &request, "session", "phone")
                .unwrap()["complete"],
            false
        );
        assert!(
            uploads
                .receive(root.clone(), &request, "other", "phone")
                .is_err()
        );
        request["offset"] = json!(2);
        request["data"] = json!(STANDARD.encode([3, 4]));
        assert_eq!(
            uploads
                .receive(root.clone(), &request, "session", "phone")
                .unwrap()["complete"],
            true
        );
        let path = PathBuf::from(uploads.path(&id, "session", "phone").unwrap());
        assert_eq!(path.parent().unwrap(), root);
        assert_eq!(fs::read(&path).unwrap(), vec![1, 2, 3, 4]);
        assert!(uploads.path(&id, "other", "phone").is_err());
        fs::remove_dir_all(root).unwrap();
    }
    #[test]
    fn control_state_distinguishes_expiry_takeover_and_detach() {
        let mut buffer = Replay::default();
        assert_eq!(buffer.snapshot(0, "phone")["control_state"], "read_only");
        buffer.claim("phone");
        assert_eq!(buffer.snapshot(0, "phone")["control_state"], "controlled");
        buffer.expires = Some(Instant::now() - Duration::from_secs(1));
        assert_eq!(buffer.snapshot(0, "phone")["control_state"], "expired");
        assert!(!buffer.has_remote_control());
        buffer.claim("phone");
        buffer.release();
        assert_eq!(buffer.snapshot(0, "phone")["control_state"], "desktop");
        buffer.claim("other");
        assert_eq!(buffer.snapshot(0, "phone")["control_state"], "other_device");
        buffer.detach("phone");
        assert_eq!(buffer.snapshot(0, "other")["control_state"], "controlled");
        buffer.detach("other");
        assert_eq!(buffer.snapshot(0, "other")["control_state"], "read_only");
    }
    #[test]
    fn replay_caps_memory_and_tracks_a_single_writer() {
        let mut buffer = Replay::default();
        buffer.append(&vec![65; 1_048_580]);
        assert_eq!(buffer.bytes.len(), 1_048_576);
        let first = buffer.snapshot(0, "phone");
        assert_eq!(first["truncated"], true);
        assert_eq!(first["after"], 65_540);
        buffer.claim("phone");
        assert!(buffer.require_control("phone", Some("one")).unwrap());
        assert!(!buffer.require_control("phone", Some("one")).unwrap());
        assert!(buffer.require_control("other", Some("two")).is_err());
        buffer.release();
        assert!(buffer.require_control("phone", Some("two")).is_err());
        assert_eq!(buffer.snapshot(u64::MAX, "phone")["after"], 1_048_580);
        buffer.process_exited(0);
        assert!(buffer.exit_code.is_none());
        buffer.output_finished();
        assert_eq!(buffer.exit_code, Some(0));
    }
    #[test]
    fn http_rejects_missing_token_and_accepts_framed_request() {
        for authorized in [false, true] {
            let listener = TcpListener::bind("127.0.0.1:0").unwrap();
            let address = listener.local_addr().unwrap();
            let sender = thread::spawn(move || {
                let mut stream = TcpStream::connect(address).unwrap();
                let auth = if authorized {
                    "Authorization: Bearer token\r\n"
                } else {
                    ""
                };
                write!(
                    stream,
                    "POST /rpc HTTP/1.1\r\n{auth}Content-Length: 13\r\n\r\n{{\"op\":\"ping\"}}"
                )
                .unwrap();
            });
            let (mut stream, _) = listener.accept().unwrap();
            let result = read_request(&mut stream, "token");
            assert_eq!(result.is_ok(), authorized);
            sender.join().unwrap();
        }
    }
}

#[tauri::command]
pub fn set_remote_enabled(
    app: tauri::AppHandle,
    state: tauri::State<'_, AppState>,
    enabled: bool,
    permissions: Option<std::collections::HashMap<String, String>>,
) -> Result<bool, String> {
    if let Some(ref permissions) = permissions {
        if permissions.iter().any(|(k, v)| {
            !matches!(k.as_str(), "codex" | "claude")
                || !matches!(v.as_str(), "restricted" | "standard" | "full" | "dangerous")
        }) {
            return Err("权限设置无效".into());
        }
    }
    state.remote.set(app, enabled)?;
    let mut config = state.config.lock().map_err(|_| "配置不可用")?;
    config.remote_enabled = enabled;
    if let Some(permissions) = permissions {
        config.remote_permissions = permissions;
    }
    crate::write_config(&state.config_path, &config)?;
    Ok(enabled)
}
