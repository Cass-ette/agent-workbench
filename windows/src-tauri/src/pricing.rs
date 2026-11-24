//! Public prices plus disclosed Key ratios. All amounts are labelled estimates.
use reqwest::{Client, Url};
use serde_json::{Value, json};
use std::{
    collections::BTreeMap,
    fs,
    path::Path,
    time::{Duration, SystemTime, UNIX_EPOCH},
};

const REFERENCE: &str = include_str!("../../../backend/pricing_reference.json");
const DEEPSEEK: &str = include_str!("../../../backend/deepseek_reference.json");
const DS_URL: &str = "https://api-docs.deepseek.com/quick_start/pricing";
const KEYS: [&str; 4] = ["prompt", "completion", "cache_read", "cache_write"];

pub fn now() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_secs()
}
fn num(v: &Value) -> Option<f64> {
    v.as_f64()
        .or_else(|| v.as_str()?.parse().ok())
        .filter(|v| v.is_finite() && *v >= 0.0)
}
fn read(path: &Path) -> Option<Value> {
    if path.is_symlink() {
        return None;
    }
    serde_json::from_slice(&fs::read(path).ok()?).ok()
}
fn save(path: &Path, data: &Value) -> Result<(), String> {
    fs::create_dir_all(path.parent().ok_or("价格缓存目录无效")?).map_err(|e| e.to_string())?;
    let temp = path.with_extension(format!("{}.tmp", uuid::Uuid::new_v4()));
    fs::write(&temp, serde_json::to_vec(data).map_err(|e| e.to_string())?)
        .map_err(|e| e.to_string())?;
    fs::rename(temp, path).map_err(|e| e.to_string())
}
fn client() -> Result<Client, String> {
    Client::builder()
        .no_proxy()
        .redirect(reqwest::redirect::Policy::none())
        .timeout(Duration::from_secs(15))
        .build()
        .map_err(|_| "价格查询初始化失败".into())
}
pub fn validate_source(source: &str) -> Result<Url, String> {
    let url = Url::parse(source).map_err(|_| "公开价格地址格式无效")?;
    let local = url
        .host_str()
        .map(|h| {
            h == "localhost"
                || h.trim_matches(['[', ']'])
                    .parse::<std::net::IpAddr>()
                    .is_ok_and(|a| a.is_loopback())
        })
        .unwrap_or(false);
    if url.as_str().contains('@')
        || url.query().is_some()
        || url.fragment().is_some()
        || !(url.scheme() == "https" || local && url.scheme() == "http")
    {
        return Err("公开价格需无凭据的 HTTPS，或本机 HTTP 地址".into());
    }
    Ok(url)
}
async fn public_get(url: &str) -> Result<Vec<u8>, String> {
    let mut response = client()?
        .get(validate_source(url)?)
        .header("User-Agent", "AgentWorkbench-PublicPricing/1.0")
        .send()
        .await
        .map_err(|_| "价格源暂不可达")?;
    if !response.status().is_success() {
        return Err("价格源未成功返回".into());
    }
    let mut bytes = Vec::new();
    while let Some(chunk) = response.chunk().await.map_err(|_| "读取价格源失败")? {
        if bytes.len() + chunk.len() > 8_000_000 {
            return Err("价格源过大".into());
        }
        bytes.extend_from_slice(&chunk);
    }
    Ok(bytes)
}
pub async fn verify_source(url: &str) -> Result<(), String> {
    let data: Value =
        serde_json::from_slice(&public_get(url).await?).map_err(|_| "价格源未返回 JSON")?;
    normalize(&data).ok_or("未识别到 CCHP 公开文本价目表")?;
    Ok(())
}
fn normalize(raw: &Value) -> Option<Value> {
    let mut models = serde_json::Map::new();
    for row in raw["models"].as_array()? {
        let Some(name) = row["modelName"].as_str() else {
            continue;
        };
        if row["hasPricing"] == false {
            continue;
        }
        let mut charges = serde_json::Map::new();
        for key in KEYS {
            if row["charges"][key]["unit"] == "per_M_tokens" {
                if let Some(value) = num(&row["charges"][key]["price"]) {
                    charges.insert(key.into(), json!(value.to_string()));
                }
            }
        }
        if !charges.contains_key("prompt") || !charges.contains_key("completion") {
            continue;
        }
        let tracks: Vec<Value> = row["tracks"]
            .as_array()
            .into_iter()
            .flatten()
            .map(|t| json!({"factors":t["chargeFactors"], "triggers":t["triggers"]}))
            .collect();
        models.insert(name.into(), json!({"model":name, "charges":charges, "tracks":tracks, "group_ids":row["availableGroupIds"]}));
    }
    if models.is_empty() {
        None
    } else {
        Some(Value::Object(models))
    }
}
fn plain(html: &str) -> String {
    let mut inside = false;
    let mut out = String::new();
    for c in html.chars() {
        match c {
            '<' => {
                inside = true;
                out.push(' ');
            }
            '>' => inside = false,
            _ if !inside => out.push(c),
            _ => {}
        }
    }
    out.split_whitespace().collect::<Vec<_>>().join(" ")
}
fn official(html: &str) -> Option<Value> {
    let mut names = Vec::<String>::new();
    let mut rows: BTreeMap<String, Value> = BTreeMap::new();
    let mut current = "";
    for tr in html.split("<tr").skip(1) {
        let content = tr.split("</tr>").next()?;
        let cells: Vec<String> = content
            .split("<td")
            .skip(1)
            .filter_map(|c| {
                c.split_once('>')
                    .map(|(_, s)| plain(s.split("</td>").next().unwrap_or(s)))
            })
            .collect();
        if cells.first().is_some_and(|s| s == "MODEL") {
            names = cells
                .iter()
                .skip(1)
                .map(|s| s.split_whitespace().next().unwrap_or("").to_string())
                .collect();
            if names.iter().any(|s| !s.starts_with("deepseek-")) {
                return None;
            }
            for name in &names {
                rows.insert(name.clone(), json!({"model":name,"charges":{},"upper_charges":{},"tracks":[],"group_ids":[]}));
            }
        }
        let text = cells.join(" ").to_uppercase();
        if text.contains("1M INPUT TOKENS (CACHE HIT)") {
            current = "cache_read";
        } else if text.contains("1M INPUT TOKENS (CACHE MISS)") {
            current = "prompt";
        } else if text.contains("1M OUTPUT TOKENS") {
            current = "completion";
        }
        let prices: Vec<f64> = cells
            .iter()
            .filter_map(|c| c.strip_prefix('$').and_then(|s| s.parse().ok()))
            .collect();
        if !current.is_empty() && !names.is_empty() && names.len() == prices.len() {
            for (name, value) in names.iter().zip(prices) {
                let row = rows.get_mut(name)?;
                row["charges"][current] = json!(
                    num(&row["charges"][current])
                        .unwrap_or(value)
                        .min(value)
                        .to_string()
                );
                row["upper_charges"][current] = json!(
                    num(&row["upper_charges"][current])
                        .unwrap_or(value)
                        .max(value)
                        .to_string()
                );
            }
        }
    }
    if rows.is_empty()
        || rows
            .values()
            .any(|r| KEYS[..3].iter().any(|k| r["charges"][k].is_null()))
    {
        None
    } else {
        Some(json!(rows))
    }
}
pub fn cached(root: &Path, id: &str, gateway: &str) -> Value {
    let direct = Url::parse(gateway)
        .ok()
        .is_some_and(|u| u.host_str() == Some("api.deepseek.com"));
    let mut context = read(&root.join(format!("ctx-{id}.json"))).filter(|c| c["gateway"] == gateway).unwrap_or_else(|| json!({"profile_id":id,"gateway":gateway,"ratio":if direct {json!("1")} else {Value::Null},"catalog":serde_json::from_str::<Value>(if direct {DEEPSEEK} else {REFERENCE}).unwrap()}));
    if now().saturating_sub(context["catalog"]["fetched_at"].as_u64().unwrap_or(0)) >= 21600 {
        context["catalog"]["stale"] = json!(true);
    }
    context
}
pub async fn refresh(
    root: &Path,
    id: &str,
    gateway: &str,
    key: Option<&str>,
    source: &str,
    force: bool,
) -> Value {
    let mut context = cached(root, id, gateway);
    let direct = Url::parse(gateway)
        .ok()
        .is_some_and(|u| u.host_str() == Some("api.deepseek.com"));
    let expired = force
        || context["source_override"] != source
        || now().saturating_sub(context["catalog"]["fetched_at"].as_u64().unwrap_or(0))
            >= if direct { 86400 } else { 21600 }
        || context["catalog"]["source_kind"] == "reference";
    if expired {
        let mut urls = if direct {
            vec![DS_URL.into()]
        } else if !source.is_empty() {
            vec![source.into()]
        } else {
            vec![format!("{gateway}/api/public/catalog/models")]
        };
        if !direct && source.is_empty() {
            if let Ok(mut u) = Url::parse(gateway) {
                let local = u.host_str().is_some_and(|h| {
                    h == "localhost"
                        || h.trim_matches(['[', ']'])
                            .parse::<std::net::IpAddr>()
                            .is_ok_and(|a| a.is_loopback())
                });
                if local {
                    if let Some(port) = u.port().filter(|p| *p > 1) {
                        let _ = u.set_port(Some(port - 1));
                        u.set_path("/api/public/catalog/models");
                        urls.push(u.to_string());
                    }
                }
            }
        }
        let mut fresh = false;
        for url in urls {
            if let Ok(bytes) = public_get(&url).await {
                let models = if direct {
                    official(&String::from_utf8_lossy(&bytes))
                } else {
                    serde_json::from_slice::<Value>(&bytes)
                        .ok()
                        .and_then(|r| normalize(&r))
                };
                if let Some(models) = models {
                    context["catalog"] = json!({"models":models,"source_url":url,"source_kind":if direct {"official"} else {"gateway"},"fetched_at":now(),"stale":false});
                    fresh = true;
                    break;
                }
            }
        }
        if !fresh {
            context["catalog"]["stale"] = json!(true);
        }
    }
    context["source_override"] = json!(source);
    if direct {
        context["ratio"] = json!("1");
    } else if let Some(key) = key {
        if let Ok(c) = client() {
            if let Ok(response) = c
                .get(format!("{gateway}/v1/cchp/account"))
                .bearer_auth(key)
                .send()
                .await
            {
                if response.status().is_success() {
                    if let Ok(raw) = response.json::<Value>().await {
                        context["ratio"] = if raw["ratio"]["disclosed"] == true {
                            raw["ratio"]["value"].clone()
                        } else {
                            Value::Null
                        };
                        context["group_id"] = raw["ratio"]["groupId"].clone();
                        context["ratio_fetched_at"] = json!(now());
                    }
                } else {
                    context["ratio"] = Value::Null;
                }
            }
        }
    }
    let _ = save(&root.join(format!("ctx-{id}.json")), &context);
    context
}
fn model_key(model: &str) -> String {
    model
        .rsplit('/')
        .next()
        .unwrap_or(model)
        .to_lowercase()
        .replace(['.', '_'], "-")
}
pub fn quote(context: &Value, model: &str) -> Option<Value> {
    let mut catalog = context["catalog"].clone();
    let lookup = |c: &Value| -> Option<Value> {
        let models = c["models"].as_object()?;
        if let Some(row) = models.get(model) {
            return Some(row.clone());
        }
        let matches: Vec<_> = models
            .iter()
            .filter(|(m, _)| model_key(m) == model_key(model))
            .collect();
        if matches.len() == 1 {
            Some(matches[0].1.clone())
        } else {
            None
        }
    };
    let mut q = if let Some(q) = lookup(&catalog) {
        q
    } else {
        catalog = serde_json::from_str(DEEPSEEK).ok()?;
        lookup(&catalog)?
    };
    let factor = num(&context["ratio"]);
    q["model"] = json!(model);
    q["base_charges"] = q["charges"].clone();
    q["ratio"] = factor.map(|f| json!(f.to_string())).unwrap_or(Value::Null);
    for field in ["charges", "upper_charges"] {
        if let Some(charges) = q[field].as_object_mut() {
            for value in charges.values_mut() {
                if let (Some(v), Some(f)) = (num(value), factor) {
                    *value = json!((v * f).to_string());
                }
            }
        }
    }
    q["basis"] = json!(if catalog["source_kind"] == "official" {
        "DeepSeek 官方参考价"
    } else if factor.is_some() && catalog["source_kind"] == "gateway" {
        "网关目录 × 分组倍率"
    } else {
        "参考估算（倍率未公开）"
    });
    q["source_kind"] = catalog["source_kind"].clone();
    q["is_gateway_price"] = json!(catalog["source_kind"] == "gateway" && factor.is_some());
    q["source_url"] = catalog["source_url"].clone();
    q["stale"] = catalog["stale"].clone();
    Some(q)
}
pub fn snapshot(context: &Value, models: &[String]) -> Value {
    let quotes: Vec<_> = models.iter().filter_map(|m| quote(context, m)).collect();
    json!({"profile_id":context["profile_id"],"gateway":context["gateway"],"source_url":context["catalog"]["source_url"],"source_override":context["source_override"],"fetched_at":context["catalog"]["fetched_at"],"stale":context["catalog"]["stale"],"ratio":context["ratio"],"quotes":quotes,"missing_models":models.len().saturating_sub(quotes.len()),"disclaimer":"单位 USD／百万 Token。当前网关目录基础价 × 此 Key 公开倍率，只乘一次。按该售价估算 Token 费用，最终实扣以 CCHP 账单为准；参考值不代表网关承诺售价。"})
}
fn estimate(counts: &Value, q: &Value, upper: bool) -> Option<f64> {
    let incoming = num(&counts["input_tokens"])?;
    let outgoing = num(&counts["output_tokens"])?;
    let cached = num(&counts["cached_input_tokens"]).unwrap_or(0.0);
    let created = num(&counts["cache_creation_input_tokens"]).unwrap_or(0.0);
    if cached + created > incoming {
        return None;
    }
    let rates = if upper && q["upper_charges"].is_object() {
        &q["upper_charges"]
    } else {
        &q["charges"]
    };
    let mut tracks: Vec<_> = q["tracks"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|t| {
            t["triggers"]
                .as_array()
                .into_iter()
                .flatten()
                .all(|c| c["kind"] == "input_tokens_above")
        })
        .collect();
    tracks.sort_by_key(|t| std::cmp::Reverse(t["triggers"].as_array().map_or(0, |a| a.len())));
    let known = counts["context_known"] == true;
    let matched = tracks.iter().find(|t| {
        t["triggers"].as_array().into_iter().flatten().all(|c| {
            known
                && if c["inclusive"] == true {
                    incoming >= num(&c["threshold"]).unwrap_or(0.0)
                } else {
                    incoming > num(&c["threshold"]).unwrap_or(0.0)
                }
        })
    });
    let mut cost = 0.0;
    for (key, quantity, fallback) in [
        ("prompt", incoming - cached - created, "prompt"),
        ("cache_read", cached, "prompt"),
        ("cache_write", created, "prompt"),
        ("completion", outgoing, "completion"),
    ] {
        if quantity == 0.0 {
            continue;
        }
        let rate = num(&rates[key]).or_else(|| num(&rates[fallback]))?;
        let mut factor = matched.and_then(|t| num(&t["factors"][key])).unwrap_or(1.0);
        if upper && !known {
            for t in &tracks {
                factor = factor.max(num(&t["factors"][key]).unwrap_or(1.0));
            }
        }
        cost += quantity * rate * factor / 1_000_000.0;
    }
    Some(cost)
}
fn resolve(contexts: &[Value], session: &Value, model: &str) -> Option<Value> {
    let routes = session["_pricing_contexts"].as_array();
    if let Some(routes) = routes.filter(|r| !r.is_empty()) {
        let mut matches = Vec::new();
        for route in routes {
            for c in contexts {
                if c["profile_id"] == route["profile_id"]
                    && (route["gateway"].is_null() || route["gateway"] == c["gateway"])
                {
                    if let Some(q) = quote(c, model) {
                        matches.push(q);
                    }
                }
            }
        }
        if !matches.is_empty()
            && matches.iter().all(|q| {
                q["charges"] == matches[0]["charges"]
                    && q["upper_charges"] == matches[0]["upper_charges"]
            })
        {
            let mut q = matches[0].clone();
            q["is_gateway_price"] = json!(
                matches.len() == routes.len()
                    && matches.iter().all(|q| q["is_gateway_price"] == true)
            );
            if q["is_gateway_price"] != true {
                q["basis"] = json!(format!(
                    "{}（历史售价未完全确认，仅供参考）",
                    q["basis"].as_str().unwrap_or("参考估算")
                ));
            }
            return Some(q);
        }
        let mut reference = contexts.first()?.clone();
        reference["ratio"] = Value::Null;
        let mut q = quote(&reference, model)?;
        q["basis"] = json!("基础价参考估算（历史网关或 Key 配置已改变）");
        q["is_gateway_price"] = json!(false);
        return Some(q);
    }
    let quotes: Vec<_> = contexts
        .iter()
        .filter_map(|c| {
            let q = quote(c, model)?;
            if let (Some(group), Some(groups)) = (c["group_id"].as_str(), q["group_ids"].as_array())
            {
                if !groups.is_empty() && !groups.iter().any(|g| g == group) {
                    return None;
                }
            }
            Some(q)
        })
        .collect();
    if !quotes.is_empty()
        && quotes.iter().all(|q| {
            q["charges"] == quotes[0]["charges"]
                && q["upper_charges"] == quotes[0]["upper_charges"]
                && q["ratio"] == quotes[0]["ratio"]
        })
    {
        let mut q = quotes[0].clone();
        q["is_gateway_price"] = json!(quotes.iter().all(|q| q["is_gateway_price"] == true));
        q["basis"] = json!(format!(
            "{}{}",
            q["basis"].as_str().unwrap_or("参考估算"),
            if q["is_gateway_price"] == true {
                "（历史 Key 未记录，匹配配置同价）"
            } else {
                "（含未确认售价，仅供参考）"
            }
        ));
        return Some(q);
    }
    let mut reference = contexts.first()?.clone();
    reference["ratio"] = Value::Null;
    let mut q = quote(&reference, model)?;
    q["basis"] = json!("基础价参考估算（历史 Key／倍率无法归属）");
    q["is_gateway_price"] = json!(false);
    Some(q)
}
pub fn apply_usage(mut usage: Value, contexts: &[Value]) -> Value {
    let mut total = 0.0;
    let mut high = 0.0;
    let mut priced = 0;
    let mut unpriced = 0;
    for session in usage["sessions"].as_array_mut().into_iter().flatten() {
        let reported = num(&session["estimated_cost"]);
        let mut records = session["_pricing_records"]
            .as_array()
            .cloned()
            .unwrap_or_default();
        if records.is_empty() {
            let mut record = session.clone();
            record["context_known"] = json!(false);
            records.push(record);
        } else if num(&session["_pricing_remainder"]["input_tokens"]).unwrap_or(0.0) > 0.0
            || num(&session["_pricing_remainder"]["output_tokens"]).unwrap_or(0.0) > 0.0
        {
            let mut extra = session["_pricing_remainder"].clone();
            extra["model"] = session["model"].clone();
            extra["context_known"] = json!(false);
            records.push(extra);
        }
        let mut low_cost = 0.0;
        let mut high_cost = 0.0;
        let mut valid = true;
        let mut gateway_only = true;
        let mut notes = Vec::new();
        for record in records {
            let model = record["model"]
                .as_str()
                .filter(|m| !m.is_empty())
                .or_else(|| session["model"].as_str())
                .unwrap_or("");
            let Some(q) = resolve(contexts, session, model) else {
                valid = false;
                break;
            };
            if let (Some(lo), Some(hi)) =
                (estimate(&record, &q, false), estimate(&record, &q, true))
            {
                low_cost += lo;
                high_cost += hi;
                gateway_only &= q["is_gateway_price"] == true;
                notes.push(q["basis"].as_str().unwrap_or("参考估算").to_string());
            } else {
                valid = false;
                break;
            }
        }
        if let Some(row) = session.as_object_mut() {
            row.remove("_pricing_records");
            row.remove("_pricing_contexts");
            row.remove("_pricing_remainder");
        }
        if valid {
            session["estimated_cost"] = json!(low_cost);
            session["estimated_cost_upper"] = json!(high_cost);
            session["cost_source"] = json!(if gateway_only { "catalog" } else { "reference" });
            notes.sort();
            notes.dedup();
            session["pricing_note"] = json!(notes.join("；"));
            total += low_cost;
            high += high_cost;
            priced += 1;
        } else if let Some(value) = reported {
            total += value;
            high += value;
            priced += 1;
            session["cost_source"] = json!("client");
        } else {
            unpriced += 1;
            session["cost_source"] = json!("pending");
            session["pricing_note"] = json!("待匹配单价，可设置公开价格来源");
        }
    }
    usage["estimated_cost"] = if priced > 0 {
        json!(total)
    } else {
        Value::Null
    };
    usage["estimated_cost_upper"] = if priced > 0 { json!(high) } else { Value::Null };
    usage["priced_sessions"] = json!(priced);
    usage["unpriced_sessions"] = json!(unpriced);
    let sources: std::collections::HashSet<_> = usage["sessions"]
        .as_array()
        .into_iter()
        .flatten()
        .filter(|s| num(&s["estimated_cost"]).is_some())
        .filter_map(|s| s["cost_source"].as_str())
        .collect();
    let status = if sources.contains("reference") {
        "reference_estimate"
    } else if sources.len() > 1 {
        "mixed_estimate"
    } else if sources.contains("catalog") {
        "catalog_estimate"
    } else {
        "client_estimate"
    };
    usage["pricing_status"] = json!(if priced == 0 {
        "pending_price".to_string()
    } else {
        format!("{}{status}", if unpriced > 0 { "partial_" } else { "" })
    });
    usage["pricing_notice"] = json!(
        "按当前单价重估历史，并非历史实扣。缺缓存明细按普通输入计价；累计差额按末模型估算，不含工具费及未知服务档位。"
    );
    usage
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn mac_windows_share_cost_contract_and_official_parser() {
        let cases: Vec<Value> = serde_json::from_str(include_str!(
            "../../../tests/fixtures/pricing-contracts.json"
        ))
        .unwrap();
        for case in cases {
            let low = estimate(&case["counts"], &case["quote"], false).unwrap();
            let high = estimate(&case["counts"], &case["quote"], true).unwrap();
            assert!(
                (low - num(&case["low"]).unwrap()).abs() < 1e-12,
                "{}",
                case["name"]
            );
            assert!(
                (high - num(&case["high"]).unwrap()).abs() < 1e-12,
                "{}",
                case["name"]
            );
        }
        let models = official(include_str!(
            "../../../tests/fixtures/deepseek-pricing.html"
        ))
        .unwrap();
        assert_eq!(
            num(&models["deepseek-flash"]["upper_charges"]["prompt"]),
            Some(0.3)
        );
        assert_eq!(
            num(&models["deepseek-v4-pro"]["charges"]["completion"]),
            Some(1.98)
        );
    }
    #[test]
    fn free_and_hidden_ratios_differ_and_cache_is_not_counted_twice() {
        let mut c = json!({"catalog":{"source_kind":"gateway","models":{"test":{"charges":{"prompt":"2","completion":"10","cache_read":"0.2","cache_write":"2.5"},"tracks":[]}}},"ratio":"0.5"});
        let q = quote(&c, "test").unwrap();
        assert_eq!(num(&q["charges"]["prompt"]), Some(1.0));
        let counts = json!({"input_tokens":1000,"output_tokens":100,"cached_input_tokens":600,"cache_creation_input_tokens":100,"reasoning_output_tokens":90});
        assert!((estimate(&counts, &q, false).unwrap() - 0.000985).abs() < 1e-12);
        c["ratio"] = json!("0");
        assert_eq!(quote(&c, "test").unwrap()["is_gateway_price"], true);
        assert_eq!(
            estimate(&counts, &quote(&c, "test").unwrap(), false),
            Some(0.0)
        );
        c["ratio"] = Value::Null;
        assert!(quote(&c, "test").unwrap()["ratio"].is_null());
        assert_eq!(quote(&c, "test").unwrap()["is_gateway_price"], false);
    }
    #[test]
    fn usage_labels_gateway_reference_client_and_pending_separately() {
        let context = json!({"profile_id":"one","gateway":"https://a.test","ratio":"1","catalog":{
            "source_kind":"gateway","models":{"test":{"charges":{"prompt":"2","completion":"10"},"tracks":[]}}}});
        let session =
            json!({"model":"test","input_tokens":1000000,"output_tokens":0,"estimated_cost":null});
        for (kind, status) in [
            ("gateway", "catalog_estimate"),
            ("official", "reference_estimate"),
            ("reference", "reference_estimate"),
        ] {
            let mut c = context.clone();
            c["catalog"]["source_kind"] = json!(kind);
            let result = apply_usage(json!({"sessions":[session.clone()]}), &[c]);
            assert_eq!(result["pricing_status"], status);
        }
        let reported =
            json!({"model":"client-only","input_tokens":1,"output_tokens":0,"estimated_cost":0});
        let mixed = apply_usage(
            json!({"sessions":[session.clone(),reported.clone()]}),
            &[context.clone()],
        );
        assert_eq!(mixed["pricing_status"], "mixed_estimate");
        let client = apply_usage(json!({"sessions":[reported]}), &[context.clone()]);
        assert_eq!(client["pricing_status"], "client_estimate");
        let pending = apply_usage(
            json!({"sessions":[{"model":"unknown","input_tokens":1,"output_tokens":0}]}),
            &[context.clone()],
        );
        assert_eq!(pending["pricing_status"], "pending_price");
        assert!(pending["estimated_cost"].is_null());
        let mut changed = session.clone();
        changed["_pricing_contexts"] = json!([{"profile_id":"one","gateway":"https://old.test"}]);
        assert_eq!(
            resolve(&[context.clone()], &changed, "test").unwrap()["is_gateway_price"],
            false
        );
        changed["_pricing_contexts"] = json!([{"profile_id":"one"},{"profile_id":"deleted"}]);
        assert_eq!(
            resolve(&[context], &changed, "test").unwrap()["is_gateway_price"],
            false
        );
    }
    #[test]
    fn public_source_never_accepts_remote_http_or_credentials() {
        assert!(validate_source("http://example.test").is_err());
        assert!(validate_source("https://u:p@example.test/prices").is_err());
        assert!(validate_source("http://localhost:1234/prices").is_ok());
    }
    #[test]
    fn official_snapshot_has_both_deepseek_models_and_ranges() {
        let c = json!({"catalog":serde_json::from_str::<Value>(DEEPSEEK).unwrap(),"ratio":"1"});
        let q = quote(&c, "deepseek-flash").unwrap();
        assert!(
            num(&q["upper_charges"]["prompt"]).unwrap() > num(&q["charges"]["prompt"]).unwrap()
        );
        assert!(quote(&c, "deepseek-chat").is_none());
    }
}

pub fn apply_upstream_usage(mut usage: Value) -> Value {
    let mut all = Vec::new();
    let mut complete = 0; let mut missing_sessions = 0;
    for session in usage["sessions"].as_array_mut().into_iter().flatten() {
        let mut records = session["_pricing_records"].as_array().cloned().unwrap_or_default();
        if records.is_empty() { let mut r = session.clone(); r["date"] = json!(""); records.push(r); }
        let mut missing = 0;
        for record in &mut records {
            let reported = num(&record["upstream_cost_usd"]);
            let cost = reported.or_else(|| {
                let quote = record["unit_prices"].as_array()?.first()?;
                let incoming = num(&record["input_tokens"])?;
                let outgoing = num(&record["output_tokens"])?;
                let cached = num(&record["cached_input_tokens"]).unwrap_or(0.0);
                let created = num(&record["cache_creation_input_tokens"]).unwrap_or(0.0);
                if cached + created > incoming { return None; }
                let mut cost = 0.0;
                for (key, quantity) in [("prompt",incoming-cached-created),("completion",outgoing),("cache_read",cached),("cache_write",created)] {
                    if quantity > 0.0 { cost += num(&quote["charges"][key])? * quantity / 1_000_000.0; }
                }
                Some(cost)
            });
            record["cost_source"] = json!(if reported.is_some() { "upstream" } else if cost.is_some() { "upstream_rates" } else { "unavailable" });
            missing += usize::from(cost.is_none());
            record["estimated_cost"] = cost.map(|c| json!(c)).unwrap_or(Value::Null);
            record["unpriced_records"] = json!(usize::from(cost.is_none()));
        }
        let costs: Vec<_> = records.iter().filter_map(|r| r["estimated_cost"].as_f64()).collect();
        session["estimated_cost"] = if costs.is_empty() { Value::Null } else { json!(costs.iter().sum::<f64>()) };
        let source = if records.iter().any(|r| r["cost_source"] == "upstream_rates") { "upstream_rates" } else { "upstream" };
        session["cost_source"] = json!(if costs.is_empty() { "unavailable".to_string() } else { format!("{}{source}", if missing > 0 { "partial_" } else { "" }) });
        session["unpriced_records"] = json!(missing);
        session["pricing_note"] = json!(if missing > 0 { "部分记录缺少费用或单价" } else { "只显示上游提供的费用或单价" });
        session["timeline"] = json!(crate::usage::daily(&records));
        session["usage_records"] = json!(records);
        if missing == 0 { complete += 1; } else { missing_sessions += 1; }
        all.extend(records);
        if let Some(row) = session.as_object_mut() { row.remove("_pricing_records"); row.remove("_pricing_contexts"); row.remove("_pricing_remainder"); }
    }
    let costs: Vec<_> = all.iter().filter_map(|r| r["estimated_cost"].as_f64()).collect();
    usage["estimated_cost"] = if costs.is_empty() { Value::Null } else { json!(costs.iter().sum::<f64>()) };
    let source = if all.iter().any(|r| r["cost_source"] == "upstream_rates") { "upstream_rates" } else { "upstream" };
    usage["pricing_status"] = json!(if costs.is_empty() { "unavailable".to_string() } else { format!("{}{source}", if missing_sessions > 0 { "partial_" } else { "" }) });
    usage["priced_sessions"] = json!(complete); usage["unpriced_sessions"] = json!(missing_sessions);
    usage["timeline"] = json!(crate::usage::daily(&all));
    usage["usage_records"] = json!(all);
    usage["schema_version"] = json!(2);
    usage["pricing_notice"] = json!("费用仅显示日志明确提供的上游 USD 金额，或按上游单价计算；未提供不套参考价。CLI 未保留的信息无法追溯。日期按 UTC，日期未知仅纳入全部；缓存／推理已含在输入／输出中。");
    usage
}
