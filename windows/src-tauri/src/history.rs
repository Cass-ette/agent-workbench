use serde::Serialize;
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::{
    collections::HashSet,
    fs,
    hash::{DefaultHasher, Hash, Hasher},
    io::{BufRead, BufReader},
    path::{Path, PathBuf},
    time::{SystemTime, UNIX_EPOCH},
};
use uuid::Uuid;

#[derive(Serialize)]
pub struct Conversation {
    pub id: String,
    pub history_ref: String,
    pub client: String,
    pub title: String,
    pub preview: String,
    pub workspace: String,
    pub model: String,
    pub profile_id: String,
    pub profile_name: String,
    pub gateway: String,
    pub updated_at: u64,
    pub scope: String,
    #[serde(skip)]
    pub home: PathBuf,
    #[serde(skip)]
    pub source: PathBuf,
    #[serde(skip)]
    pub key_fingerprint: String,
    #[serde(skip)]
    pub configured_model: String,
}

fn text(value: &Value) -> &str {
    value.as_str().unwrap_or("")
}

fn content_text(value: &Value) -> String {
    match value {
        Value::String(value) => value.clone(),
        Value::Array(parts) => parts.iter().map(content_text).filter(|part| !part.is_empty()).collect::<Vec<_>>().join(" "),
        Value::Object(fields) => fields.get("text").and_then(Value::as_str).map(str::to_owned)
            .or_else(|| fields.get("content").map(content_text)).unwrap_or_default(),
        _ => String::new(),
    }
}

fn preview(content: &Value) -> String {
    let mut raw = content_text(content);
    for tag in ["environment_context", "system-reminder", "local-command-caveat"] {
        let open = format!("<{tag}>");
        let close = format!("</{tag}>");
        while let Some(start) = raw.find(&open) {
            let end = raw[start..].find(&close).map(|offset| start + offset + close.len()).unwrap_or(raw.len());
            raw.replace_range(start..end, " ");
        }
    }
    let cleaned = raw.split_whitespace().collect::<Vec<_>>().join(" ");
    let lowercase = cleaned.to_ascii_lowercase();
    let normalized = lowercase.trim_matches(|character: char| " .:;()[]".contains(character));
    if ["none", "null", "undefined", "n/a", "untitled"].contains(&normalized) {
        return String::new();
    }
    cleaned.chars().take(180).collect()
}

fn candidates(directory: &Path, depth: u8, files: &mut Vec<(SystemTime, PathBuf)>) {
    if directory.is_symlink() {
        return;
    }
    let Ok(entries) = fs::read_dir(directory) else { return };
    for entry in entries.flatten() {
        let path = entry.path();
        let Ok(kind) = entry.file_type() else { continue };
        if kind.is_dir() && depth > 0 {
            candidates(&path, depth - 1, files);
        } else if kind.is_file() && path.extension().is_some_and(|ext| ext == "jsonl") {
            if let Some(metadata) = entry.metadata().ok().filter(|metadata| metadata.len() <= 64 * 1024 * 1024) {
                if let Ok(modified) = metadata.modified() {
                    files.push((modified, path));
                }
            }
        }
    }
}

fn read(client: &str, home: PathBuf, path: &Path, updated: SystemTime,
        profile_id: &str, scope: &str) -> Option<Conversation> {
    let file = fs::File::open(path).ok()?;
    let manifest = profile_manifest(&home);
    let stem = path.file_stem()?.to_str()?;
    if is_imported(&manifest, stem) {
        return None;
    }
    let mut hash = DefaultHasher::new();
    (client, path).hash(&mut hash);
    let mut row = Conversation {
        id: String::new(), history_ref: format!("{:016x}", hash.finish()),
        client: client.into(), title: String::new(), preview: String::new(), workspace: String::new(),
        model: String::new(),
        profile_id: if scope == "global" { "global" } else { manifest["profile_id"].as_str().unwrap_or(profile_id) }.into(),
        profile_name: if scope == "global" { "系统全局".into() } else { text(&manifest["profile_name"]).into() },
        gateway: text(&manifest["gateway"]).into(),
        updated_at: updated.duration_since(UNIX_EPOCH).unwrap_or_default().as_secs(), home,
        source: path.to_owned(),
        scope: scope.into(),
        key_fingerprint: text(&manifest["key_fingerprint"]).into(),
        configured_model: text(&manifest["model"]).into(),
    };
    for line in BufReader::new(file).lines().map_while(Result::ok) {
        let Ok(data) = serde_json::from_str::<Value>(&line) else { continue };
        let kind = text(&data["type"]);
        if client == "codex" {
            let payload = &data["payload"];
            match kind {
                "session_meta" if row.id.is_empty() => {
                    row.id = payload["id"].as_str().or(payload["session_id"].as_str())?.into();
                    row.workspace = text(&payload["cwd"]).into();
                }
                "turn_context" => {
                    if payload["model"].is_string() { row.model = text(&payload["model"]).into(); }
                    if row.title.is_empty() {
                        let summary = preview(&payload["summary"]);
                        if !summary.is_empty() { row.title = summary; }
                    }
                }
                "response_item" | "event_msg" => {
                    let message = payload.get("message").unwrap_or(payload);
                    let is_user = message["role"] == "user" || payload["role"] == "user"
                        || (kind == "event_msg" && payload["type"] == "user_message");
                    if is_user {
                        let content = message.get("content").or_else(|| payload.get("content"))
                            .or_else(|| payload.get("message")).unwrap_or(&Value::Null);
                        let candidate = preview(content);
                        if !candidate.is_empty() {
                            row.preview = candidate.clone();
                            if row.title.is_empty() { row.title = candidate; }
                        }
                    }
                }
                _ => {}
            }
        } else if data["sessionId"] == stem && data["isSidechain"] != true {
            row.id = stem.into();
            if row.workspace.is_empty() {
                row.workspace = text(&data["cwd"]).into();
            }
            if kind == "assistant" && data["message"]["model"].is_string() {
                row.model = text(&data["message"]["model"]).into();
            } else if kind == "user" && data["isMeta"] != true {
                let candidate = preview(&data["message"]["content"]);
                if !candidate.is_empty() {
                    row.preview = candidate.clone();
                    if row.title.is_empty() { row.title = candidate; }
                }
            }
        }
    }
    if row.title.is_empty() && client == "codex" {
        row.title = preview(&manifest["branch_title"]);
    }
    let uuid = Uuid::parse_str(&row.id).ok()?;
    if uuid.to_string() != row.id || row.title.is_empty()
        || (client == "codex" && (!stem.starts_with("rollout-") || !stem.ends_with(&format!("-{}", row.id)))) {
        return None;
    }
    if row.model.is_empty() {
        row.model = text(&manifest["model"]).into();
    }
    if row.configured_model.is_empty() {
        row.configured_model = row.model.clone();
    }
    Some(row)
}

fn profile_manifest(home: &Path) -> Value {
    fs::read(home.join("workbench-profile.json")).ok()
        .and_then(|bytes| serde_json::from_slice(&bytes).ok()).unwrap_or_default()
}

fn is_imported(manifest: &Value, stem: &str) -> bool {
    manifest["imported_sessions"].as_array().into_iter().flatten()
        .filter_map(Value::as_str).any(|id| stem.ends_with(id))
}

pub fn branch_usage_context(home: &Path, path: &Path) -> (bool, Value) {
    if !home.file_name().and_then(|name| name.to_str()).is_some_and(|name| name.starts_with("branch-")) {
        return (false, Value::Null);
    }
    let manifest = profile_manifest(home);
    let imported = path.file_stem().and_then(|stem| stem.to_str())
        .is_some_and(|stem| is_imported(&manifest, stem));
    (imported, manifest)
}

pub fn stage_branch(
    entry: &Conversation, root: &Path, profile_id: &str, profile_name: &str,
    model: &str, gateway: &str, fingerprint: &str,
) -> Result<(PathBuf, PathBuf), String> {
    let relative = entry.source.strip_prefix(&entry.home).map_err(|_| "历史路径无效")?;
    let mut inputs = vec![(entry.id.clone(), entry.source.clone())];
    if entry.client == "codex" {
        let mut files = Vec::new();
        candidates(&entry.home.join("sessions"), 3, &mut files);
        let mut seen = HashSet::from([entry.id.clone()]);
        let mut current = entry.source.clone();
        loop {
            let first = BufReader::new(fs::File::open(&current).map_err(|_| "无法读取 Codex 历史")?)
                .lines().next().and_then(Result::ok)
                .and_then(|line| serde_json::from_str::<Value>(&line).ok())
                .ok_or("Codex 会话元数据无效")?;
            let base = &first["payload"]["history_base"];
            if base.is_null() {
                break;
            }
            let id = base["thread_id"].as_str().ok_or("Codex 父会话标识无效")?;
            if Uuid::parse_str(id).is_err() || seen.len() >= 100 || !seen.insert(id.to_string()) {
                return Err("Codex 会话的父历史无法解析".into());
            }
            let suffix = format!("-{id}");
            let mut matches = files.iter().filter(|(_, path)| {
                path.file_stem().and_then(|stem| stem.to_str())
                    .is_some_and(|stem| stem.starts_with("rollout-") && stem.ends_with(&suffix))
            });
            let (_, path) = matches.next().ok_or("Codex 分支缺少可读取的父历史")?;
            if matches.next().is_some() {
                return Err("发现多条 Codex 父历史，已停止恢复".into());
            }
            current = path.clone();
            inputs.push((id.to_owned(), current.clone()));
        }
    }
    let branch = root.join(&entry.client).join(format!("branch-{}", Uuid::new_v4()));
    let seed = branch.join(relative);
    let result = (|| -> std::io::Result<()> {
        for (_, path) in &inputs {
            let destination = branch.join(path.strip_prefix(&entry.home).map_err(|_| std::io::ErrorKind::InvalidInput)?);
            fs::create_dir_all(destination.parent().ok_or(std::io::ErrorKind::InvalidInput)?)?;
            fs::copy(path, destination)?;
        }
        let inherited_through = if entry.client == "claude" {
            BufReader::new(fs::File::open(&seed)?).lines().map_while(Result::ok)
                .filter_map(|line| serde_json::from_str::<Value>(&line).ok())
                .filter(|row| row["type"] == "assistant")
                .filter_map(|row| row["timestamp"].as_str().map(str::to_owned))
                .max().unwrap_or_default()
        } else { String::new() };
        let manifest = serde_json::json!({
            "profile_id": profile_id, "profile_name": profile_name, "model": model,
            "gateway": gateway, "branch_title": entry.title, "inherited_through": inherited_through,
            "key_fingerprint": fingerprint,
            "imported_sessions": inputs.iter().map(|(id, _)| id).collect::<Vec<_>>(),
        });
        fs::write(branch.join("workbench-profile.json"), manifest.to_string())
    })();
    if let Err(error) = result {
        let _ = fs::remove_dir_all(&branch);
        return Err(format!("无法准备对话分支：{error}"));
    }
    Ok((branch, seed))
}

pub fn scan(root: &Path) -> Vec<Conversation> {
    scan_matching(root, None)
}

fn scan_matching(root: &Path, workspace: Option<&Path>) -> Vec<Conversation> {
    let target = workspace.and_then(|path| fs::canonicalize(path).ok());
    if workspace.is_some() && target.is_none() { return Vec::new() }
    let mut found = Vec::new();
    for (client, folder, depth) in [("codex", "sessions", 3), ("claude", "projects", 1)] {
        let root = root.join(client);
        if root.is_symlink() { continue; }
        let Ok(homes) = root.read_dir() else { continue };
        for home in homes.flatten() {
            if !home.file_type().is_ok_and(|kind| kind.is_dir()) { continue; }
            let mut files = Vec::new();
            candidates(&home.path().join(folder), depth, &mut files);
            for (updated, path) in files {
                if client == "claude" && path.parent().and_then(Path::parent) != Some(home.path().join(folder).as_path()) {
                    continue;
                }
                found.push((updated, client, home.path(), path, home.file_name()));
            }
        }
    }
    found.sort_unstable_by(|a, b| b.0.cmp(&a.0).then_with(|| a.3.cmp(&b.3)));
    found.into_iter().filter_map(|(updated, client, home, path, profile)| {
        let row = read(client, home, &path, updated, &profile.to_string_lossy(), "workbench")?;
        let matches = target.as_ref().map_or(true, |target| {
            fs::canonicalize(&row.workspace).is_ok_and(|path| canonical_paths_equal(&path, target))
        });
        matches.then_some(row)
    }).take(1000).collect()
}

fn user_home() -> Option<PathBuf> {
    std::env::var_os("USERPROFILE").or_else(|| std::env::var_os("HOME")).map(PathBuf::from)
}

/// Scan system-level Claude/Codex homes only for the explicit import action.
pub fn scan_global() -> Vec<Conversation> {
    let Some(home) = user_home() else { return Vec::new() };
    let mut found = Vec::new();
    for (client, client_home, folder, depth) in [
        ("codex", home.join(".codex"), "sessions", 3u8),
        ("claude", home.join(".claude"), "projects", 1u8),
    ] {
        if client_home.is_symlink() { continue; }
        let mut files = Vec::new();
        candidates(&client_home.join(folder), depth, &mut files);
        for (updated, path) in files {
            if client == "claude" && path.parent().and_then(Path::parent) != Some(client_home.join(folder).as_path()) {
                continue;
            }
            if let Some(row) = read(client, client_home.clone(), &path, updated, "global", "global") {
                found.push(row);
            }
        }
    }
    found.sort_unstable_by(|a, b| b.updated_at.cmp(&a.updated_at).then_with(|| a.source.cmp(&b.source)));
    found.into_iter().take(1000).collect()
}

pub fn scan_all(root: &Path) -> Vec<Conversation> {
    let mut rows = scan(root);
    rows.extend(scan_global());
    rows.sort_unstable_by(|a, b| b.updated_at.cmp(&a.updated_at).then_with(|| a.source.cmp(&b.source)));
    rows.into_iter().take(1000).collect()
}

pub fn scan_workspace(root: &Path, workspace: &Path) -> Vec<Conversation> {
    scan_matching(root, Some(workspace))
}

pub fn same_workspace(first: &Path, second: &Path) -> bool {
    let (Ok(first), Ok(second)) = (fs::canonicalize(first), fs::canonicalize(second)) else { return false };
    canonical_paths_equal(&first, &second)
}

pub fn can_resume_in_place(entry: &Conversation, profile_id: &str, model: &str, gateway: &str,
                            fingerprint: &str) -> bool {
    entry.scope == "workbench"
        && !profile_id.is_empty()
        && entry.profile_id == profile_id
        && !entry.configured_model.is_empty()
        && entry.configured_model == model
        && !gateway.is_empty() && entry.gateway == gateway
        && (entry.key_fingerprint.is_empty() || entry.key_fingerprint == fingerprint)
}

pub fn configuration_home(root: &Path, client: &str, profile_id: &str, model: &str,
                          gateway: &str, workspace: &Path, fingerprint: &str) -> PathBuf {
    let identity = serde_json::json!([profile_id, model, gateway, workspace, fingerprint]);
    let hash = format!("{:x}", Sha256::digest(identity.to_string().as_bytes()));
    root.join(client).join(format!("scope-{}", &hash[..24]))
}

pub fn launch_home(source: Option<&Conversation>, branch: Option<&(PathBuf, PathBuf)>,
                   default: PathBuf) -> PathBuf {
    branch.map(|(home, _)| home.clone())
        .or_else(|| source.map(|entry| entry.home.clone())).unwrap_or(default)
}

pub fn record_profile(home: &Path, profile_id: &str, profile_name: &str, model: &str,
                      gateway: &str, fingerprint: &str) -> Result<(), String> {
    fs::create_dir_all(home).map_err(|error| error.to_string())?;
    let manifest = serde_json::json!({"profile_id": profile_id, "profile_name": profile_name,
        "model": model, "gateway": gateway, "key_fingerprint": fingerprint});
    fs::write(home.join("workbench-profile.json"), manifest.to_string())
        .map_err(|error| format!("保存会话来源失败：{error}"))
}

fn canonical_paths_equal(first: &Path, second: &Path) -> bool {
    #[cfg(windows)]
    { first.to_string_lossy().eq_ignore_ascii_case(&second.to_string_lossy()) }
    #[cfg(not(windows))]
    { first == second }
}

#[cfg(test)]
mod tests {
    use super::{can_resume_in_place, configuration_home, launch_home, preview, scan_workspace, Conversation};
    use serde_json::json;
    use std::{fs, path::PathBuf};
    use uuid::Uuid;

    #[test]
    fn preview_reads_structured_text_and_removes_injected_context() {
        let value = json!([
            {"type": "input_text", "text": "<environment_context>private context</environment_context>"},
            {"type": "input_text", "text": "A real user request"}
        ]);
        assert_eq!(preview(&value), "A real user request");
        assert_eq!(preview(&json!("null")), "");
    }

    #[test]
    fn matching_workbench_history_resumes_without_a_branch() {
        let entry = Conversation {
            id: Uuid::new_v4().to_string(), history_ref: "ref".into(), client: "codex".into(),
            title: "title".into(), preview: "preview".into(), workspace: "/work/project".into(),
            model: "model-a".into(), profile_id: "p-one".into(), profile_name: "One".into(),
            gateway: "https://gateway.test".into(), updated_at: 0, scope: "workbench".into(),
            home: PathBuf::from("original/branch-home"), source: PathBuf::new(), key_fingerprint: "key-one".into(),
            configured_model: "model-a".into(),
        };
        assert!(can_resume_in_place(&entry, "p-one", "model-a", "https://gateway.test", "key-one"));
        assert!(!can_resume_in_place(&entry, "p-two", "model-a", "https://gateway.test", "key-one"));
        assert!(!can_resume_in_place(&entry, "p-one", "model-b", "https://gateway.test", "key-one"));
        assert!(!can_resume_in_place(&entry, "p-one", "model-a", "https://other.test", "key-one"));
        assert!(!can_resume_in_place(&entry, "p-one", "model-a", "https://gateway.test", "key-two"));
        let mut alias = entry;
        alias.model = "upstream-model-snapshot".into();
        assert!(can_resume_in_place(&alias, "p-one", "model-a", "https://gateway.test", "key-one"));
        let entry = alias;
        let fallback = PathBuf::from("normal/profile-home");
        assert_eq!(launch_home(Some(&entry), None, fallback.clone()), entry.home);
        let branch = (PathBuf::from("new/branch"), PathBuf::from("new/seed"));
        assert_eq!(launch_home(Some(&entry), Some(&branch), fallback.clone()), branch.0);
        let mut external = entry;
        external.scope = "global".into();
        assert!(!can_resume_in_place(&external, "p-one", "model-a", "https://gateway.test", "key-one"));
        external.scope = "workbench".into();
        external.gateway.clear();
        assert!(!can_resume_in_place(&external, "p-one", "model-a", "https://gateway.test", "key-one"));
    }

    #[test]
    fn new_homes_keep_old_session_configuration_snapshots() {
        let root = PathBuf::from("workbench");
        let workspace = PathBuf::from("project");
        let first = configuration_home(&root, "codex", "p-one", "model-a", "https://gateway.test", &workspace, "key-one");
        assert_eq!(first, configuration_home(&root, "codex", "p-one", "model-a", "https://gateway.test", &workspace, "key-one"));
        assert_ne!(first, configuration_home(&root, "codex", "p-one", "model-b", "https://gateway.test", &workspace, "key-one"));
        assert_ne!(first, configuration_home(&root, "codex", "p-one", "model-a", "https://gateway.test", &workspace, "key-two"));
    }

    #[test]
    fn workspace_scan_excludes_other_project_histories() {
        let root = std::env::temp_dir().join(format!("history-scope-{}", Uuid::new_v4()));
        let selected = root.join("project-a");
        let other = root.join("project-b");
        let home = root.join("workbench/codex/profile");
        fs::create_dir_all(&selected).unwrap();
        fs::create_dir_all(&other).unwrap();
        for (workspace, id) in [(&selected, Uuid::new_v4()), (&other, Uuid::new_v4())] {
            let path = home.join(format!("sessions/2026/11/21/rollout-test-{id}.jsonl"));
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            let rows = [
                json!({"type": "session_meta", "payload": {"id": id.to_string(), "cwd": workspace.display().to_string()}}),
                json!({"type": "response_item", "payload": {"role": "user", "content": "first request"}}),
                json!({"type": "response_item", "payload": {"message": {"role": "user", "content": "latest request"}}}),
            ];
            fs::write(path, rows.iter().map(|value| value.to_string()).collect::<Vec<_>>().join("\n") + "\n").unwrap();
        }
        let rows = scan_workspace(&root.join("workbench"), &selected);
        assert_eq!(rows.len(), 1);
        assert_eq!(PathBuf::from(&rows[0].workspace), selected);
        assert_eq!(rows[0].title, "first request");
        assert_eq!(rows[0].preview, "latest request");
        let _ = fs::remove_dir_all(root);
    }
}
