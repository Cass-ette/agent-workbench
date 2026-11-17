//! Summarize isolated client records. Never emit prompts, responses, keys, or guessed prices.
use serde::Serialize;
use serde_json::Value;
use std::{
    collections::BTreeMap,
    fs,
    io::{BufRead, BufReader},
    path::{Path, PathBuf},
};

#[derive(Clone, Serialize, Default)]
pub struct Counts {
    input_tokens: Option<u64>,
    output_tokens: Option<u64>,
    cached_input_tokens: Option<u64>,
    cache_creation_input_tokens: Option<u64>,
    reasoning_output_tokens: Option<u64>,
}

fn number(v: &Value, key: &str) -> Option<u64> {
    v.get(key)?.as_u64().filter(|v| *v <= 9_007_199_254_740_991)
}

fn maximum(a: Option<u64>, b: Option<u64>) -> Option<u64> {
    match (a, b) {
        (Some(a), Some(b)) => Some(a.max(b)),
        (a, b) => a.or(b),
    }
}

fn sum(a: Option<u64>, b: Option<u64>) -> Option<u64> {
    match (a, b) {
        (Some(a), Some(b)) => Some(a.saturating_add(b)),
        (a, b) => a.or(b),
    }
}

impl Counts {
    fn parse(v: &Value, client: &str) -> Option<Self> {
        let mut incoming = number(v, "input_tokens")?;
        let outgoing = number(v, "output_tokens")?;
        let cached = number(
            v,
            if client == "claude" {
                "cache_read_input_tokens"
            } else {
                "cached_input_tokens"
            },
        );
        let created = number(
            v,
            if client == "claude" {
                "cache_creation_input_tokens"
            } else {
                "cache_write_input_tokens"
            },
        );
        if client == "claude" {
            incoming = incoming
                .saturating_add(cached.unwrap_or(0))
                .saturating_add(created.unwrap_or(0));
        }
        Some(Self {
            input_tokens: Some(incoming),
            output_tokens: Some(outgoing),
            cached_input_tokens: cached,
            cache_creation_input_tokens: created,
            reasoning_output_tokens: number(v, "reasoning_output_tokens"),
        })
    }

    fn merge(&mut self, other: &Self, combine: fn(Option<u64>, Option<u64>) -> Option<u64>) {
        self.input_tokens = combine(self.input_tokens, other.input_tokens);
        self.output_tokens = combine(self.output_tokens, other.output_tokens);
        self.cached_input_tokens = combine(self.cached_input_tokens, other.cached_input_tokens);
        self.cache_creation_input_tokens = combine(
            self.cache_creation_input_tokens,
            other.cache_creation_input_tokens,
        );
        self.reasoning_output_tokens =
            combine(self.reasoning_output_tokens, other.reasoning_output_tokens);
    }
}

#[derive(Serialize, Default)]
pub struct UsageSession {
    id: String,
    client: String,
    model: String,
    workspace: String,
    updated_at: String,
    #[serde(flatten)]
    counts: Counts,
    requests: usize,
    counts_from_cumulative: bool,
    estimated_cost: Option<f64>,
    #[serde(skip)]
    records: BTreeMap<String, Counts>,
    #[serde(skip)]
    record_models: BTreeMap<String, String>,
    #[serde(skip)]
    routes: Vec<Value>,
    #[serde(skip)]
    cumulative: Option<Counts>,
}

#[derive(Serialize, Default)]
pub struct UsageSnapshot {
    #[serde(flatten)]
    counts: Counts,
    requests: usize,
    by_source: BTreeMap<String, usize>,
    pricing_status: String,
    estimated_cost: Option<f64>,
    sessions: Vec<UsageSession>,
    unreadable_files: usize,
    scanned_at: u64,
}

fn text(v: &Value, key: &str) -> String {
    v.get(key).and_then(Value::as_str).unwrap_or("").to_string()
}

fn files(root: &Path, found: &mut Vec<PathBuf>) {
    if root.is_symlink() {
        return;
    }
    let Ok(entries) = fs::read_dir(root) else {
        return;
    };
    for entry in entries.flatten() {
        let path = entry.path();
        if path.is_symlink() {
            continue;
        }
        if path.is_dir() {
            files(&path, found);
        } else if path.extension().and_then(|s| s.to_str()) == Some("jsonl") {
            found.push(path);
        }
    }
}

fn parse_file(path: &Path, client: &str) -> Result<UsageSession, std::io::Error> {
    let file = fs::File::open(path)?;
    let mut item = UsageSession {
        client: client.into(),
        id: path
            .file_stem()
            .unwrap_or_default()
            .to_string_lossy()
            .into(),
        ..Default::default()
    };
    for (ordinal, line) in BufReader::new(file).lines().enumerate() {
        let line = line?;
        let Ok(row) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        if !row.is_object() {
            continue;
        }
        let kind = text(&row, "type");
        let payload = &row["payload"];
        if kind == "session_meta" {
            let id = text(payload, "id");
            let alternate = text(payload, "session_id");
            if !id.is_empty() {
                item.id = id;
            } else if !alternate.is_empty() {
                item.id = alternate;
            }
            let cwd = text(payload, "cwd");
            if !cwd.is_empty() {
                item.workspace = cwd;
            }
        }
        if client == "claude" {
            let id = text(&row, "sessionId");
            let cwd = text(&row, "cwd");
            if !id.is_empty() {
                item.id = id;
            }
            if !cwd.is_empty() {
                item.workspace = cwd;
            }
        }
        if kind == "turn_context" {
            let model = text(payload, "model");
            if !model.is_empty() {
                item.model = model;
            }
        }
        let message = &row["message"];
        let record = if client == "claude" && kind == "assistant" {
            let model = text(message, "model");
            if !model.is_empty() {
                item.model = model;
            }
            let identity = [text(message, "id"), text(&row, "uuid")]
                .into_iter()
                .find(|s| !s.is_empty())
                .unwrap_or_else(|| ordinal.to_string());
            Counts::parse(&message["usage"], client).map(|u| (identity, u))
        } else if client == "codex" && kind == "token_usage_record" {
            let response = text(payload, "response_id");
            let identity = if !response.is_empty() {
                response
            } else {
                format!(
                    "{}:{}",
                    text(payload, "turn_id"),
                    row.get("ordinal")
                        .map(Value::to_string)
                        .unwrap_or_else(|| ordinal.to_string())
                )
            };
            Counts::parse(&payload["usage"], client).map(|u| (identity, u))
        } else {
            None
        };
        if let Some((id, usage)) = record {
            item.record_models.insert(id.clone(), item.model.clone());
            item.records.entry(id).or_default().merge(&usage, maximum);
            item.updated_at = item.updated_at.max(text(&row, "timestamp"));
        }
        if client == "codex" && kind == "event_msg" && text(payload, "type") == "token_count" {
            if let Some(usage) = Counts::parse(&payload["info"]["total_token_usage"], client) {
                item.cumulative
                    .get_or_insert_with(Counts::default)
                    .merge(&usage, maximum);
                item.updated_at = item.updated_at.max(text(&row, "timestamp"));
            }
        }
        // This explicitly named field is a client estimate in USD, not a billing receipt.
        if kind == "result" {
            if let Some(cost) = row
                .get("total_cost_usd")
                .and_then(Value::as_f64)
                .filter(|v| v.is_finite() && *v >= 0.0)
            {
                item.estimated_cost = Some(item.estimated_cost.unwrap_or(0.0).max(cost));
            }
        }
    }
    item.id = format!("{client}:{}", item.id);
    Ok(item)
}

pub fn scan(claude_root: &Path, codex_root: &Path) -> UsageSnapshot {
    let mut grouped: BTreeMap<String, UsageSession> = BTreeMap::new();
    let mut snapshot = UsageSnapshot {
        pricing_status: "unavailable".into(),
        ..Default::default()
    };
    for (client, root) in [("claude", claude_root), ("codex", codex_root)] {
        let mut paths = Vec::new();
        files(root, &mut paths);
        paths.sort();
        for path in paths {
            if client == "codex"
                && !path
                    .strip_prefix(root)
                    .unwrap_or(&path)
                    .components()
                    .any(|c| c.as_os_str() == "sessions")
            {
                continue;
            }
            let file = match parse_file(&path, client) {
                Ok(file) => file,
                Err(_) => {
                    snapshot.unreadable_files += 1;
                    continue;
                }
            };
            if file.records.is_empty() && file.cumulative.is_none() {
                continue;
            }
            let entry = grouped
                .entry(file.id.clone())
                .or_insert_with(|| UsageSession {
                    id: file.id.clone(),
                    client: client.into(),
                    ..Default::default()
                });
            if let Some(profile) = path
                .strip_prefix(root)
                .ok()
                .and_then(|p| p.components().next())
            {
                let mut route =
                    serde_json::json!({"profile_id": profile.as_os_str().to_string_lossy()});
                for parent in path.ancestors().skip(1).take_while(|p| p.starts_with(root)) {
                    if let Ok(config) = fs::read_to_string(parent.join("config.toml")) {
                        for line in config.lines() {
                            if let Some((name, value)) = line.split_once('=') {
                                if name.trim() == "base_url" {
                                    if let Ok(url) = serde_json::from_str::<String>(value.trim()) {
                                        route["gateway"] = Value::String(
                                            url.trim_end_matches("/v1")
                                                .trim_end_matches('/')
                                                .into(),
                                        );
                                    }
                                }
                            }
                        }
                        break;
                    }
                }
                if !entry.routes.contains(&route) {
                    entry.routes.push(route);
                }
            }
            for (id, model) in file.record_models {
                entry.record_models.insert(id, model);
            }
            for (id, counts) in file.records {
                entry.records.entry(id).or_default().merge(&counts, maximum);
            }
            if let Some(counts) = file.cumulative {
                entry
                    .cumulative
                    .get_or_insert_with(Counts::default)
                    .merge(&counts, maximum);
            }
            if let Some(cost) = file.estimated_cost {
                entry.estimated_cost = Some(entry.estimated_cost.unwrap_or(0.0).max(cost));
            }
            if !file.model.is_empty() {
                entry.model = file.model;
            }
            if !file.workspace.is_empty() {
                entry.workspace = file.workspace;
            }
            entry.updated_at = entry.updated_at.clone().max(file.updated_at);
        }
    }
    let mut priced = 0;
    for (_, mut session) in grouped {
        for counts in session.records.values() {
            session.counts.merge(counts, sum);
        }
        if let Some(counts) = &session.cumulative {
            session.counts.merge(counts, maximum);
        }
        session.requests = session.records.len();
        session.counts_from_cumulative = session.requests == 0 && session.cumulative.is_some();
        snapshot.requests += session.requests;
        *snapshot
            .by_source
            .entry(session.client.clone())
            .or_default() += session.requests;
        snapshot.counts.merge(&session.counts, sum);
        if let Some(cost) = session.estimated_cost {
            snapshot.estimated_cost = Some(snapshot.estimated_cost.unwrap_or(0.0) + cost);
            priced += 1;
        }
        snapshot.sessions.push(session);
    }
    for client in ["claude", "codex"] {
        snapshot.by_source.entry(client.into()).or_default();
    }
    snapshot.pricing_status = if priced == 0 {
        "unavailable"
    } else if priced < snapshot.sessions.len() {
        "partial_estimate"
    } else {
        "client_estimate"
    }
    .into();
    snapshot
        .sessions
        .sort_by(|a, b| b.updated_at.cmp(&a.updated_at).then(b.id.cmp(&a.id)));
    snapshot.scanned_at = std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs();
    snapshot
}

pub fn scan_for_pricing(claude_root: &Path, codex_root: &Path) -> Value {
    let snapshot = scan(claude_root, codex_root);
    let mut result = serde_json::to_value(&snapshot).unwrap_or_default();
    for (output, session) in result["sessions"]
        .as_array_mut()
        .into_iter()
        .flatten()
        .zip(&snapshot.sessions)
    {
        let mut detailed = Counts::default();
        let records: Vec<Value> = session
            .records
            .iter()
            .map(|(id, counts)| {
                detailed.merge(counts, sum);
                let mut row = serde_json::to_value(counts).unwrap_or_default();
                row["model"] = Value::String(
                    session
                        .record_models
                        .get(id)
                        .filter(|m| !m.is_empty())
                        .unwrap_or(&session.model)
                        .clone(),
                );
                row["context_known"] = Value::Bool(true);
                row
            })
            .collect();
        let total = serde_json::to_value(&session.counts).unwrap_or_default();
        let detail = serde_json::to_value(&detailed).unwrap_or_default();
        let mut remainder = serde_json::json!({});
        for key in [
            "input_tokens",
            "output_tokens",
            "cached_input_tokens",
            "cache_creation_input_tokens",
            "reasoning_output_tokens",
        ] {
            remainder[key] = Value::from(
                total[key]
                    .as_u64()
                    .unwrap_or(0)
                    .saturating_sub(detail[key].as_u64().unwrap_or(0)),
            );
        }
        output["_pricing_records"] = Value::Array(records);
        output["_pricing_contexts"] = Value::Array(session.routes.clone());
        output["_pricing_remainder"] = remainder;
    }
    result
}

#[cfg(test)]
mod tests {
    use super::*;
    struct Fixture(PathBuf);
    impl Fixture {
        fn new() -> Self {
            let root =
                std::env::temp_dir().join(format!("workbench-usage-{}", uuid::Uuid::new_v4()));
            fs::create_dir_all(&root).unwrap();
            Self(root)
        }
        fn write(&self, name: &str, rows: &[Value]) {
            let path = self.0.join(name);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            let mut data = rows
                .iter()
                .map(Value::to_string)
                .collect::<Vec<_>>()
                .join("\n");
            data.push_str("\n{\"unfinished\":");
            fs::write(path, data).unwrap();
        }
        fn scan(&self) -> UsageSnapshot {
            scan(&self.0.join("claude"), &self.0.join("codex"))
        }
    }
    impl Drop for Fixture {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    #[test]
    fn claude_normalizes_cache_and_deduplicates_final_chunks_and_copies() {
        let f = Fixture::new();
        let rows = [
            serde_json::json!({"type":"assistant","sessionId":"a","uuid":"one","message":{"id":"reply","usage":{"input_tokens":10,"output_tokens":2,"cache_read_input_tokens":5,"cache_creation_input_tokens":3}}}),
            serde_json::json!({"type":"assistant","sessionId":"a","uuid":"two","message":{"id":"reply","usage":{"input_tokens":10,"output_tokens":8,"cache_read_input_tokens":5,"cache_creation_input_tokens":3}}}),
        ];
        f.write("claude/projects/a.jsonl", &rows);
        f.write("claude/projects/copy.jsonl", &rows);
        let s = f.scan();
        assert_eq!(
            (s.counts.input_tokens, s.counts.output_tokens, s.requests),
            (Some(18), Some(8), 1)
        );
        assert_eq!(s.sessions.len(), 1);
        assert_eq!(s.counts.reasoning_output_tokens, None);
    }

    #[test]
    fn codex_preserves_multiple_responses_in_one_turn_without_readding_cumulative_events() {
        let f = Fixture::new();
        let rows = [
            serde_json::json!({"type":"session_meta","payload":{"id":"thread"}}),
            serde_json::json!({"type":"token_usage_record","payload":{"response_id":"r1","turn_id":"t","usage":{"input_tokens":100,"output_tokens":10,"cached_input_tokens":60}}}),
            serde_json::json!({"type":"token_usage_record","payload":{"response_id":"r2","turn_id":"t","usage":{"input_tokens":100,"output_tokens":10,"cached_input_tokens":60}}}),
            serde_json::json!({"type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":200,"output_tokens":20,"cached_input_tokens":120}}}}),
        ];
        f.write("codex/p/sessions/a.jsonl", &rows);
        f.write("codex/copy/sessions/b.jsonl", &rows);
        let s = f.scan();
        assert_eq!(
            (s.counts.input_tokens, s.counts.output_tokens, s.requests),
            (Some(200), Some(20), 2)
        );
        assert_eq!(s.counts.cached_input_tokens, Some(120));
    }

    #[test]
    fn older_cumulative_records_work_and_missing_price_differs_from_zero_estimate() {
        let f = Fixture::new();
        f.write("codex/p/sessions/a.jsonl", &[serde_json::json!({"type":"event_msg","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":123,"output_tokens":4}}}})]);
        let s = f.scan();
        assert_eq!(s.counts.input_tokens, Some(123));
        assert!(s.sessions[0].counts_from_cumulative);
        assert_eq!(s.counts.cached_input_tokens, None);
        assert_eq!(s.estimated_cost, None);
        f.write("claude/projects/b.jsonl", &[
            serde_json::json!({"type":"assistant","sessionId":"b","message":{"id":"b","usage":{"input_tokens":0,"output_tokens":0,"cost":999}}}),
            serde_json::json!({"type":"result","total_cost_usd":0})]);
        let s = f.scan();
        assert_eq!(s.estimated_cost, Some(0.0));
        assert_eq!(s.pricing_status, "partial_estimate");
    }

    #[test]
    fn invalid_records_do_not_become_zero_usage() {
        let f = Fixture::new();
        f.write("claude/projects/a.jsonl", &[
            serde_json::json!({"type":"assistant","message":{"usage":{"input_tokens":true,"output_tokens":-1}}}),
            serde_json::json!({"type":"assistant","message":{"usage":{}}}), serde_json::json!([])]);
        let s = f.scan();
        assert!(s.sessions.is_empty());
        assert_eq!(s.counts.input_tokens, None);
        assert_eq!(s.estimated_cost, None);
    }
}
