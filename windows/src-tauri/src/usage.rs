//! Summarize isolated client records. Never emit prompts, responses, keys, or guessed prices.
use serde::Serialize;
use serde_json::Value;
use chrono::{DateTime, Utc};
use sha2::{Digest, Sha256};
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
        let mut incoming = number(v, "input_tokens").or_else(|| number(v, "prompt_tokens"))?;
        let outgoing = number(v, "output_tokens").or_else(|| number(v, "completion_tokens"))?;
        let cached = number(
            v,
            if client == "claude" {
                "cache_read_input_tokens"
            } else {
                "cached_input_tokens"
            },
        ).or_else(|| if client == "claude" { None } else { number(&v["input_tokens_details"], "cached_tokens").or_else(|| number(&v["prompt_tokens_details"], "cached_tokens")) });
        let created = number(
            v,
            if client == "claude" {
                "cache_creation_input_tokens"
            } else {
                "cache_write_input_tokens"
            },
        ).or_else(|| if client == "claude" { None } else { number(&v["input_tokens_details"], "cache_write_tokens").or_else(|| number(&v["prompt_tokens_details"], "cache_write_tokens")) });
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
            reasoning_output_tokens: number(v, "reasoning_output_tokens").or_else(|| number(&v["output_tokens_details"], "reasoning_tokens")).or_else(|| number(&v["completion_tokens_details"], "reasoning_tokens")),
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
    profile_id: String,
    profile_name: String,
    updated_at: String,
    #[serde(flatten)]
    counts: Counts,
    requests: usize,
    counts_from_cumulative: bool,
    estimated_cost: Option<f64>,
    timeline: Vec<Value>,
    #[serde(skip)]
    records: BTreeMap<String, Counts>,
    #[serde(skip)]
    record_models: BTreeMap<String, String>,
    #[serde(skip)]
    record_times: BTreeMap<String, String>,
    #[serde(skip)]
    record_costs: BTreeMap<String, f64>,
    #[serde(skip)]
    record_prices: BTreeMap<String, Value>,
    #[serde(skip)]
    record_profiles: BTreeMap<String, Vec<(String, String)>>,
    #[serde(skip)]
    record_workspaces: BTreeMap<String, String>,
    #[serde(skip)]
    checkpoints: BTreeMap<String, (Counts, String, String)>,
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

fn stamp(s: &str) -> String {
    DateTime::parse_from_rfc3339(s).map(|d| d.with_timezone(&Utc).to_rfc3339()).unwrap_or_default()
}
pub fn daily(rows: &[Value]) -> Vec<Value> {
    let mut groups: BTreeMap<(String,String), Vec<&Value>> = BTreeMap::new();
    for row in rows { groups.entry((text(row, "date"),text(row,"model"))).or_default().push(row); }
    groups.into_iter().map(|((date,model), rows)| {
        let mut prices: Vec<Value> = Vec::new();
        let mut out = serde_json::json!({"date":date,"model":model,"requests":0,"unpriced_records":0,"estimated_cost":null});
        for row in rows {
            for price in row["unit_prices"].as_array().into_iter().flatten() { if !prices.contains(price) { prices.push(price.clone()); } }
            for key in ["input_tokens","output_tokens","cached_input_tokens","cache_creation_input_tokens","reasoning_output_tokens","requests","unpriced_records"] {
                if let Some(n) = row[key].as_u64() { out[key] = serde_json::json!(out[key].as_u64().unwrap_or(0).saturating_add(n)); }
            }
            if let Some(cost) = row["estimated_cost"].as_f64() { out["estimated_cost"] = serde_json::json!(out["estimated_cost"].as_f64().unwrap_or(0.0) + cost); }
        }
        out["unit_prices"] = serde_json::json!(prices);
        out
    }).collect()
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

fn parse_file(path: &Path, client: &str, inherited_through: &str) -> Result<UsageSession, std::io::Error> {
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
    let mut baseline = Counts::default();
    for (ordinal, line) in BufReader::new(file).lines().enumerate() {
        let line = line?;
        let Ok(row) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        if !row.is_object() {
            continue;
        }
        // Metadata precedes the inherited cutoff and is still needed to identify
        // the continued session and the new records' project/model.
        if text(&row, "type") == "session_meta" {
            let id = text(&row["payload"], "id");
            if !id.is_empty() { item.id = id; }
            let cwd = text(&row["payload"], "cwd");
            if !cwd.is_empty() { item.workspace = cwd; }
        }
        if text(&row, "type") == "turn_context" {
            let model = text(&row["payload"], "model");
            let cwd = text(&row["payload"], "cwd");
            if !model.is_empty() { item.model = model; }
            if !cwd.is_empty() { item.workspace = cwd; }
        }
        if !inherited_through.is_empty() && text(&row, "timestamp").as_str() <= inherited_through {
            if text(&row, "type") == "event_msg" && text(&row["payload"], "type") == "token_count" {
                if let Some(counts) = Counts::parse(&row["payload"]["info"]["total_token_usage"], client) { baseline.merge(&counts, maximum); }
            }
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
            if let Some(model) = payload["model"].as_str() { item.model = model.into(); }
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
            item.record_times.insert(id.clone(), stamp(&text(&row, "timestamp")));
            item.record_workspaces.insert(id.clone(),item.workspace.clone());
            let source = if client == "claude" { message } else { payload };
            let cost = source["upstream_cost_usd"].as_f64().or_else(|| source["cost_usd"].as_f64()).or_else(|| source["billing"]["cost_usd"].as_f64());
            if let Some(cost) = cost.filter(|c| c.is_finite() && *c >= 0.0) { item.record_costs.insert(id.clone(), cost); }
            let source = if client == "claude" { message } else { payload };
            let currency = source["currency"].as_str().or_else(|| source["billing"]["currency"].as_str());
            let unit = source["unit"].as_str().or_else(|| source["billing"]["unit"].as_str());
            let prices = source["upstream_prices"].as_object().or_else(|| source["upstream_prices_usd_per_million"].as_object()).or_else(|| source["billing"]["unit_prices"].as_object());
            if let Some(raw) = prices.filter(|_| currency == Some("USD") && matches!(unit,Some("per_M_tokens" | "USD_per_1M_tokens"))) {
                let charges: serde_json::Map<String,Value> = raw.iter().filter_map(|(k,v)| {
                    let name = match k.as_str() { "prompt" | "input" | "input_tokens" => "prompt", "completion" | "output" | "output_tokens" => "completion", "cache_read" | "cache_read_input_tokens" => "cache_read", "cache_write" | "cache_creation_input_tokens" => "cache_write", _ => return None };
                    v.as_f64().filter(|n| n.is_finite() && *n >= 0.0).map(|_| (name.into(),v.clone()))
                }).collect();
                if !charges.is_empty() { item.record_prices.insert(id.clone(), serde_json::json!({"model":item.model,"currency":"USD","unit":"per_M_tokens","charges":charges,"source":"upstream_log"})); }
            }
            item.record_models.insert(id.clone(), item.model.clone());
            item.records.entry(id).or_default().merge(&usage, maximum);
            item.updated_at = item.updated_at.max(text(&row, "timestamp"));
        }
        if client == "codex" && kind == "event_msg" && text(payload, "type") == "token_count" {
            if let Some(mut usage) = Counts::parse(&payload["info"]["total_token_usage"], client) {
                usage.merge(&baseline, |a,b| a.map(|n| n.saturating_sub(b.unwrap_or(0))));
                let checkpoint = item.checkpoints.entry(stamp(&text(&row,"timestamp"))).or_default();
                checkpoint.0.merge(&usage, maximum); checkpoint.1 = item.model.clone(); checkpoint.2 = item.workspace.clone();
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
            let branch_home = path.ancestors().take_while(|p| p.starts_with(root)).find(|p| p.file_name().and_then(|s| s.to_str()).is_some_and(|s| s.starts_with("branch-")))
                .or_else(|| path.ancestors().find(|candidate| candidate.parent() == Some(root)));
            let (imported, mut manifest) = branch_home.map(|home| crate::history::branch_usage_context(home, &path))
                .unwrap_or_default();
            if manifest["profile_id"].is_null() {
                for home in path.ancestors().skip(1).take_while(|p| p.starts_with(root)) {
                    let source = home.join("workbench-profile.json");
                    if !source.is_symlink() {
                        if let Ok(bytes) = fs::read(source) {
                            if let Ok(value) = serde_json::from_slice::<Value>(&bytes) { if value.is_object() { manifest = value; break; } }
                        }
                    }
                }
            }
            if imported {
                continue;
            }
            let file = match parse_file(&path, client, manifest["inherited_through"].as_str().unwrap_or("")) {
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
            let profile = if client == "codex" { path.strip_prefix(root).ok().and_then(|p| p.components().next()) } else { None };
            if profile.is_some() || manifest["profile_id"].is_string() {
                let identity = manifest["profile_id"].as_str().map(str::to_owned).unwrap_or_else(|| profile.map(|p| p.as_os_str().to_string_lossy().into_owned()).unwrap_or_default());
                let mut route = serde_json::json!({
                    "profile_id": identity
                });
                if !identity.is_empty() { entry.profile_id = identity; }
                if let Some(name) = manifest["profile_name"].as_str() { entry.profile_name = name.into(); }
                if let Some(gateway) = manifest["gateway"].as_str() {
                    route["gateway"] = Value::String(gateway.into());
                }
                for parent in path.ancestors().skip(1).take_while(|p| p.starts_with(root)) {
                    if let Ok(config) = fs::read_to_string(parent.join("config.toml")) {
                        for line in config.lines() {
                            if let Some((name, value)) = line.split_once('=') {
                                if name.trim() == "base_url" {
                                    if let Ok(url) = serde_json::from_str::<String>(value.trim()) {
                                        if route.get("gateway").is_none() {
                                            let base = url.trim_end_matches('/');
                                            let root_v1 = reqwest::Url::parse(base).map(|u| u.path() == "/v1").unwrap_or(false);
                                            route["gateway"] = Value::String(if root_v1 { base.trim_end_matches("/v1") } else { base }.into());
                                        }
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
            let file_identity = (manifest["profile_id"].as_str().map(str::to_owned).or_else(|| profile.map(|p| p.as_os_str().to_string_lossy().into_owned())).unwrap_or_default(),text(&manifest,"profile_name"));
            for id in file.records.keys() {
                let identity = file_identity.clone();
                let values = entry.record_profiles.entry(id.clone()).or_default();
                if !values.contains(&identity) { values.push(identity); }
            }
            for (id, model) in file.record_models {
                entry.record_models.insert(id, model);
            }
            entry.record_times.extend(file.record_times);
            entry.record_costs.extend(file.record_costs);
            entry.record_prices.extend(file.record_prices);
            entry.record_workspaces.extend(file.record_workspaces);
            for (date, (counts, model, workspace)) in file.checkpoints {
                let point = entry.checkpoints.entry(date).or_default(); point.0.merge(&counts, maximum); point.1 = model; point.2 = workspace;
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
        let profiles: std::collections::BTreeSet<_> = session.routes.iter().filter_map(|r| r["profile_id"].as_str().filter(|s| !s.is_empty())).collect();
        if profiles.len() > 1 { session.profile_id.clear(); session.profile_name.clear(); }
        session.timeline = daily(&pricing_records(&session));
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

fn pricing_records(session: &UsageSession) -> Vec<Value> {
    let mut rows = Vec::new();
    let mut detailed = Counts::default();
    for (id, counts) in &session.records {
        detailed.merge(counts, sum);
        let mut row = serde_json::to_value(counts).unwrap_or_default();
        row["model"] = Value::String(session.record_models.get(id).cloned().unwrap_or_default());
        row["record_id"] = Value::String(format!("{}:{id}",session.id));
        row["session_id"] = Value::String(session.id.clone());
        row["client"] = Value::String(session.client.clone());
        row["project_path"] = Value::String(session.record_workspaces.get(id).cloned().unwrap_or_default());
        let profiles = session.record_profiles.get(id);
        let profile = profiles.filter(|p| p.len() == 1).and_then(|p| p.first());
        row["profile_id"] = Value::String(profile.map(|p| p.0.clone()).unwrap_or_default());
        row["profile_name"] = Value::String(profile.map(|p| p.1.clone()).unwrap_or_default());
        row["timestamp"] = Value::String(session.record_times.get(id).cloned().unwrap_or_default());
        row["date"] = Value::String(session.record_times.get(id).map(|s| s.chars().take(10).collect()).unwrap_or_default());
        row["context_known"] = Value::Bool(true);
        row["requests"] = Value::from(1);
        row["upstream_cost_usd"] = session.record_costs.get(id).map(|c| Value::from(*c)).unwrap_or(Value::Null);
        row["unit_prices"] = serde_json::json!(session.record_prices.get(id).into_iter().collect::<Vec<_>>());
        rows.push(row);
    }
    if rows.is_empty() && !session.checkpoints.is_empty() {
        let mut previous = Counts::default();
        for (stamp, (counts, model, workspace)) in &session.checkpoints {
            let mut delta = counts.clone();
            delta.merge(&previous, |a,b| a.map(|n| n.saturating_sub(b.unwrap_or(0))));
            previous.merge(counts, maximum);
            let mut row = serde_json::to_value(delta).unwrap_or_default();
            row["date"] = Value::String(stamp.chars().take(10).collect());
            row["model"] = Value::String(model.clone());
            row["context_known"] = Value::Bool(false);
            row["record_id"] = serde_json::json!(format!("{}:cumulative:{stamp}",session.id));
            row["session_id"] = serde_json::json!(session.id);
            row["client"] = serde_json::json!(session.client);
            row["project_path"] = serde_json::json!(workspace);
            row["profile_id"] = serde_json::json!(session.profile_id);
            row["profile_name"] = serde_json::json!(session.profile_name);
            row["timestamp"] = serde_json::json!(stamp);
            rows.push(row);
        }
    } else {
        let mut extra = session.counts.clone();
        extra.merge(&detailed, |a,b| a.map(|n| n.saturating_sub(b.unwrap_or(0))));
        if extra.input_tokens.unwrap_or(0) > 0 || extra.output_tokens.unwrap_or(0) > 0 {
            let mut row = serde_json::to_value(extra).unwrap_or_default();
            row["date"] = Value::String(String::new());
            row["model"] = Value::String(String::new());
            row["context_known"] = Value::Bool(false);
            row["record_id"] = serde_json::json!(format!("{}:cumulative-remainder",session.id));
            row["session_id"] = serde_json::json!(session.id);
            row["client"] = serde_json::json!(session.client);
            row["project_path"] = serde_json::json!("");
            row["profile_id"] = serde_json::json!(session.profile_id);
            row["profile_name"] = serde_json::json!(session.profile_name);
            rows.push(row);
        }
    }
    rows
}

pub fn scan_for_pricing(claude_root: &Path, codex_root: &Path) -> Value {
    let snapshot = scan(claude_root, codex_root);
    let mut result = serde_json::to_value(&snapshot).unwrap_or_default();
    for (output, session) in result["sessions"].as_array_mut().into_iter().flatten().zip(&snapshot.sessions) {
        output["_pricing_records"] = Value::Array(pricing_records(session));
        output["_pricing_contexts"] = Value::Array(session.routes.clone());
        output["_pricing_remainder"] = serde_json::json!({});
    }
    result
}

pub fn scan_cached(claude_root: &Path, codex_root: &Path, cache: &Path) -> Value {
    fn signature(roots: &[&Path]) -> String {
        fn collect(path: &Path, digest: &mut Sha256) {
            if path.is_symlink() { return; }
            let Ok(meta) = fs::metadata(path) else { return; };
            if meta.is_dir() {
                let Ok(entries) = fs::read_dir(path) else { return; };
                let mut paths: Vec<_> = entries.flatten().map(|e| e.path()).collect();
                paths.sort();
                for path in paths { collect(&path,digest); }
            } else if path.extension().is_some_and(|s| s == "jsonl") || path.file_name().is_some_and(|s| s == "config.toml" || s == "workbench-profile.json") {
                digest.update(path.to_string_lossy().as_bytes());
                digest.update(meta.len().to_le_bytes());
                let stamp = meta.modified().ok().and_then(|v| v.duration_since(std::time::UNIX_EPOCH).ok()).map(|v| v.as_nanos()).unwrap_or_default();
                digest.update(stamp.to_le_bytes());
            }
        }
        let mut digest = Sha256::new(); digest.update(b"workbench-usage-v4");
        for root in roots { digest.update(root.to_string_lossy().as_bytes()); collect(root,&mut digest); }
        format!("{:x}",digest.finalize())
    }
    let roots = [claude_root,codex_root];
    let before = signature(&roots);
    let requested_path = cache.join("snapshot.json");
    let safe = !cache.is_symlink() && !requested_path.is_symlink();
    let canonical_cache = if cache.exists() {
        fs::canonicalize(cache).unwrap_or_else(|_| cache.to_path_buf())
    } else {
        cache.parent()
            .and_then(|parent| fs::canonicalize(parent).ok().map(|resolved| {
                resolved.join(cache.file_name().unwrap_or_default())
            }))
            .unwrap_or_else(|| cache.to_path_buf())
    };
    let path = canonical_cache.join("snapshot.json");
    if safe {
        if let Some(data) = fs::read(&path).ok().and_then(|b| serde_json::from_slice::<Value>(&b).ok()) {
            if data["signature"] == before && data["snapshot"].is_object() {
                let mut snapshot = data["snapshot"].clone();
                snapshot["scanned_at"] = serde_json::json!(crate::pricing::now());
                return snapshot;
            }
        }
    }
    let snapshot = scan_for_pricing(claude_root,codex_root);
    if safe && before == signature(&roots) && fs::create_dir_all(cache).is_ok() {
        let temporary = cache.join(format!(".usage-{}.json",uuid::Uuid::new_v4()));
        let data = serde_json::json!({"signature":before,"snapshot":snapshot});
        if fs::write(&temporary,data.to_string()).is_ok() { let _ = fs::rename(&temporary,&path); }
        let _ = fs::remove_file(&temporary);
    }
    snapshot
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

    #[test]
    fn canonical_records_keep_request_projects_and_upstream_rates_without_device_dimension() {
        let f = Fixture::new();
        f.write("codex/p/sessions/a.jsonl", &[
            serde_json::json!({"type":"session_meta","payload":{"id":"s","cwd":"/a"}}),
            serde_json::json!({"type":"turn_context","payload":{"model":"first","cwd":"/a"}}),
            serde_json::json!({"type":"token_usage_record","timestamp":"2026-11-21T23:00:00Z","payload":{"response_id":"one","usage":{"input_tokens":1000000,"output_tokens":0},"billing":{"currency":"USD","unit":"per_M_tokens","unit_prices":{"input":2}}}}),
            serde_json::json!({"type":"turn_context","payload":{"model":"last","cwd":"/b"}}),
            serde_json::json!({"type":"token_usage_record","timestamp":"2026-11-22T01:00:00+08:00","payload":{"response_id":"two","usage":{"input_tokens":100,"output_tokens":1},"upstream_cost_usd":0}}),
        ]);
        let result = crate::pricing::apply_upstream_usage(scan_for_pricing(&f.0.join("claude"), &f.0.join("codex")));
        let records = result["usage_records"].as_array().unwrap();
        assert_eq!(records.len(), 2);
        assert_eq!(records[0]["model"], "first");
        assert_eq!(records[0]["project_path"], "/a");
        assert_eq!(records[1]["project_path"], "/b");
        assert_eq!(records[0]["estimated_cost"], 2.0);
        assert_eq!(records[1]["estimated_cost"], 0.0);
        assert_eq!(result["pricing_status"], "upstream_rates");
        assert_eq!(result["schema_version"], 2);
        assert!(!result.to_string().contains("device_id"));
    }

    #[test]
    fn usage_cache_tracks_log_and_profile_changes_and_contains_no_messages() {
        let f = Fixture::new();
        f.write("claude/scope-a/projects/a.jsonl", &[serde_json::json!({"type":"assistant","sessionId":"s","cwd":"/a","message":{"id":"r1","model":"x","content":"secret-conversation-text","usage":{"input_tokens":100,"output_tokens":1}}})]);
        let manifest = f.0.join("claude/scope-a/workbench-profile.json");
        fs::write(&manifest,r#"{"profile_id":"p","profile_name":"first","key_fingerprint":"private-fingerprint"}"#).unwrap();
        let cache = f.0.join("usage");
        let first = scan_cached(&f.0.join("claude"),&f.0.join("codex"),&cache);
        let before = fs::metadata(cache.join("snapshot.json")).unwrap().modified().unwrap();
        let again = scan_cached(&f.0.join("claude"),&f.0.join("codex"),&cache);
        assert_eq!(first["input_tokens"],again["input_tokens"]);
        assert_eq!(before,fs::metadata(cache.join("snapshot.json")).unwrap().modified().unwrap());
        fs::write(&manifest,r#"{"profile_id":"p","profile_name":"changed"}"#).unwrap();
        let changed = scan_cached(&f.0.join("claude"),&f.0.join("codex"),&cache);
        assert_eq!(changed["sessions"][0]["profile_name"],"changed");
        let disk = fs::read_to_string(cache.join("snapshot.json")).unwrap();
        assert!(!disk.contains("secret-conversation-text"));
        assert!(!disk.contains("private-fingerprint"));
    }

    #[test]
    fn cumulative_history_keeps_each_project_and_unknown_model() {
        let f = Fixture::new();
        f.write("codex/p/sessions/a.jsonl", &[
            serde_json::json!({"type":"session_meta","payload":{"id":"s","cwd":"/first"}}),
            serde_json::json!({"type":"event_msg","timestamp":"2026-11-21T01:00:00Z","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":100,"output_tokens":0}}}}),
            serde_json::json!({"type":"turn_context","payload":{"model":"later-model","cwd":"/second"}}),
            serde_json::json!({"type":"event_msg","timestamp":"2026-11-22T01:00:00Z","payload":{"type":"token_count","info":{"total_token_usage":{"input_tokens":250,"output_tokens":0}}}}),
        ]);
        let result = crate::pricing::apply_upstream_usage(scan_for_pricing(&f.0.join("claude"), &f.0.join("codex")));
        let rows = result["usage_records"].as_array().unwrap();
        assert_eq!((rows[0]["project_path"].as_str(), rows[0]["model"].as_str(), rows[0]["input_tokens"].as_u64()), (Some("/first"),Some(""),Some(100)));
        assert_eq!((rows[1]["project_path"].as_str(), rows[1]["model"].as_str(), rows[1]["input_tokens"].as_u64()), (Some("/second"),Some("later-model"),Some(150)));
    }
}
