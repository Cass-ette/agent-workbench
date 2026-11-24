pub const LEVELS: &[&str] = &["low", "medium", "high", "xhigh", "max"];

pub fn normalize(value: Option<&str>) -> Result<Option<&str>, String> {
    match value {
        None | Some("" | "default") => Ok(None),
        Some(value) if LEVELS.contains(&value) => Ok(Some(value)),
        _ => Err("思考强度无效，请选择客户端默认或明确的档位。".into()),
    }
}

pub fn args(client: &str, value: Option<&str>) -> Result<Vec<String>, String> {
    let Some(effort) = normalize(value)? else { return Ok(vec![]) };
    match client {
        "codex" => Ok(vec!["-c".into(), format!("model_reasoning_effort={}", serde_json::json!(effort))]),
        "claude" => Ok(vec!["--effort".into(), effort.into()]),
        _ => Err("此终端不是 Codex 或 Claude。".into()),
    }
}

pub fn config_line(value: Option<&str>, existing: &str) -> Result<String, String> {
    let saved = existing.lines().find_map(|line| {
        let (key, value) = line.split_once('=')?;
        if key.trim() != "model_reasoning_effort" { return None; }
        let value = serde_json::from_str::<String>(value.trim()).ok()?;
        (!value.is_empty() && value.len() <= 80 && !value.chars().any(char::is_control)).then_some(value)
    });
    Ok(normalize(value)?.or(saved.as_deref()).map(|effort| format!("model_reasoning_effort = {}\n", serde_json::json!(effort))).unwrap_or_default())
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn default_preserves_native_effort_and_overrides_never_change_model() {
        assert!(args("codex", None).unwrap().is_empty());
        assert!(config_line(None, "").unwrap().is_empty());
        assert_eq!(config_line(None, "model_reasoning_effort = \"high\"\n").unwrap(), "model_reasoning_effort = \"high\"\n");
        assert_eq!(config_line(None, "model_reasoning_effort = \"ultra\"\n").unwrap(), "model_reasoning_effort = \"ultra\"\n");
        assert_eq!(config_line(Some("low"), "model_reasoning_effort = \"high\"\n").unwrap(), "model_reasoning_effort = \"low\"\n");
        for level in LEVELS {
            assert_eq!(args("codex", Some(level)).unwrap(), vec!["-c".to_string(), format!("model_reasoning_effort=\"{level}\"")]);
            assert_eq!(args("claude", Some(level)).unwrap(), vec!["--effort".to_string(), level.to_string()]);
        }
        assert!(args("codex", Some("high\n/model other")).is_err());
        assert!(args("claude", Some("none")).is_err());
    }
}
