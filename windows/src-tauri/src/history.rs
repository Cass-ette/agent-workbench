use serde::Serialize;
use serde_json::Value;
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
    pub workspace: String,
    pub model: String,
    pub profile_id: String,
    pub profile_name: String,
    pub gateway: String,
    pub updated_at: u64,
    #[serde(skip)]
    pub home: PathBuf,
    #[serde(skip)]
    pub source: PathBuf,
}

fn text(value: &Value) -> &str {
    value.as_str().unwrap_or("")
}

fn preview(content: &Value) -> String {
    let joined;
    let raw = if let Some(parts) = content.as_array() {
        joined = parts.iter().map(|part| text(&part["text"])).collect::<Vec<_>>().join(" ");
        &joined
    } else {
        text(content)
    };
    let raw = raw.rsplit_once("</environment_context>").map_or(raw, |(_, tail)| tail);
    raw.split_whitespace().collect::<Vec<_>>().join(" ").chars().take(180).collect()
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

fn read(client: &str, home: PathBuf, path: &Path, updated: SystemTime, profile_id: &str) -> Option<Conversation> {
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
        client: client.into(), title: String::new(), workspace: String::new(),
        model: String::new(),
        profile_id: manifest["profile_id"].as_str().unwrap_or(profile_id).into(),
        profile_name: text(&manifest["profile_name"]).into(),
        gateway: text(&manifest["gateway"]).into(),
        updated_at: updated.duration_since(UNIX_EPOCH).unwrap_or_default().as_secs(), home,
        source: path.to_owned(),
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
                "turn_context" if payload["model"].is_string() => row.model = text(&payload["model"]).into(),
                "response_item" if row.title.is_empty() && payload["role"] == "user" => {
                    row.title = preview(&payload["content"]);
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
            } else if kind == "user" && row.title.is_empty() && data["isMeta"] != true {
                row.title = preview(&data["message"]["content"]);
            }
        }
    }
    if row.title.is_empty() && client == "codex" {
        row.title = text(&manifest["branch_title"]).into();
    }
    let uuid = Uuid::parse_str(&row.id).ok()?;
    if uuid.to_string() != row.id || row.title.is_empty()
        || (client == "codex" && (!stem.starts_with("rollout-") || !stem.ends_with(&format!("-{}", row.id)))) {
        return None;
    }
    if row.model.is_empty() {
        row.model = text(&manifest["model"]).into();
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
    model: &str, gateway: &str,
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
        read(client, home, &path, updated, &profile.to_string_lossy())
    }).take(1000).collect()
}
