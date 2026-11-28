"""Read local Claude Code and Codex session metadata without contacting a gateway."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
from urllib.parse import urlsplit
from typing import Iterable
from uuid import UUID, uuid4

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None

def gateway_from_api_base(base):
    base = base.rstrip("/")
    return base[:-3] if urlsplit(base).path == "/v1" else base


MAX_SESSION_FILE_BYTES = 64 * 1024 * 1024
MAX_SESSION_ROWS = 2000
MAX_TITLE_CHARS = 180
MAX_PROJECT_NAME_CHARS = 120
METADATA_VERSION = 1


def _text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(part for part in (_text(item) for item in value) if part)
    if isinstance(value, dict):
        if isinstance(value.get("text"), str):
            return value["text"]
        if value.get("type") in {"text", "input_text", "output_text"}:
            return _text(value.get("content"))
    return ""


def _title(value: str) -> str:
    for tag in ("environment_context", "system-reminder", "local-command-caveat", "INSTRUCTIONS", "skills_instructions"):
        value = re.sub(rf"<{tag}>.*?</{tag}>", " ", value, flags=re.DOTALL)
    value = re.sub(r"(?im)^\s*#+\s*AGENTS\.md instructions\s*", "", value)
    cleaned = " ".join(value.replace("\x00", " ").split())
    if cleaned.casefold().strip(" .:;()[]") in {"none", "null", "undefined", "n/a", "untitled"}:
        return ""
    return cleaned[:MAX_TITLE_CHARS]


def _profile_manifest(home: Path) -> dict:
    path = home / "workbench-profile.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _is_imported(manifest: dict, session_id: str) -> bool:
    ids = manifest.get("imported_sessions")
    return session_id == manifest.get("imported_session") or isinstance(ids, list) and session_id in ids


def branch_usage_context(home: Path, path: Path) -> tuple[bool, dict]:
    if not home.name.startswith("branch-"):
        return False, {}
    manifest = _profile_manifest(home)
    return _is_imported(manifest, path.stem[-36:]), manifest


def _session_ref(client: str, path: Path) -> str:
    return hashlib.sha256((client + "\0" + str(path.resolve())).encode("utf-8")).hexdigest()[:32]


def _valid_id(value: str) -> bool:
    try:
        return str(UUID(value)) == value
    except (ValueError, AttributeError, TypeError):
        return False


def _safe_file(path: Path) -> bool:
    try:
        return not path.is_symlink() and path.is_file() and path.stat().st_size <= MAX_SESSION_FILE_BYTES
    except OSError:
        return False


def _read_codex(path: Path, home: Path, profile_id: str, profile_name: str) -> dict | None:
    session_id = workspace = title = preview = model = ""
    manifest = _profile_manifest(home)
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(row, dict):
                    continue
                kind = row.get("type")
                payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
                if kind == "session_meta":
                    session_id = str(payload.get("session_id") or payload.get("id") or "")
                    workspace = str(payload.get("cwd") or "")
                elif kind == "turn_context":
                    model = str(payload.get("model") or model)
                    candidate = _title(str(payload.get("summary") or ""))
                    if candidate and not title:
                        title = candidate
                elif kind == "response_item":
                    messages = (payload.get("message"), payload)
                    for message in messages:
                        if isinstance(message, dict) and message.get("role") == "user":
                            candidate = _title(_text(message.get("content")))
                            if candidate:
                                preview = candidate
                                title = title or candidate
    except (OSError, RecursionError):
        return None
    title = title or _title(str(manifest.get("branch_title") or ""))
    gateway = str(manifest.get("gateway") or "")
    if not gateway and tomllib is not None:
        try:
            config = tomllib.loads((home / "config.toml").read_text(encoding="utf-8"))
            provider = config.get("model_provider")
            providers = config.get("model_providers")
            provider_config = providers.get(provider, {}) if isinstance(providers, dict) else {}
            base_url = provider_config.get("base_url", "") if isinstance(provider_config, dict) else ""
            if isinstance(base_url, str):
                gateway = gateway_from_api_base(base_url)
        except (OSError, ValueError, TypeError):
            pass
    if (not _valid_id(session_id) or _is_imported(manifest, session_id)
            or not path.stem.endswith("-" + session_id)):
        return None
    title = title or preview or "未命名 Codex 会话"
    stat = path.stat()
    return {
        "id": session_id,
        "history_ref": _session_ref("codex", path),
        "client": "codex",
        "title": title,
        "preview": preview,
        "workspace": workspace,
        "model": model or str(manifest.get("model") or ""),
        "profile_id": profile_id or str(manifest.get("profile_id") or ""),
        "profile_name": profile_name or str(manifest.get("profile_name") or ""),
        "gateway": gateway,
        "_key_fingerprint": str(manifest.get("key_fingerprint") or ""),
        "_configured_model": str(manifest.get("model") or model),
        "updated_at": int(stat.st_mtime),
        "_home": str(home),
        "_path": str(path),
    }


def _read_claude(path: Path, home: Path, profile_id: str, profile_name: str) -> dict | None:
    session_id = workspace = title = preview = model = ""
    manifest = _profile_manifest(home)
    if _is_imported(manifest, path.stem):
        return None
    try:
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if not isinstance(row, dict):
                    continue
                kind = row.get("type")
                if row.get("isSidechain") or row.get("sessionId") != path.stem:
                    continue
                session_id = path.stem
                workspace = str(row.get("cwd") or workspace)
                message = row.get("message") if isinstance(row.get("message"), dict) else {}
                if kind == "user" and not row.get("isMeta"):
                    candidate = _title(_text(message.get("content")))
                    if candidate:
                        preview = candidate
                        title = title or candidate
                if kind == "last-prompt":
                    candidate = _title(_text(row.get("lastPrompt")))
                    if candidate and not title:
                        title = candidate
                if kind == "assistant":
                    model = str(message.get("model") or model)
    except OSError:
        return None
    if not _valid_id(session_id):
        return None
    title = title or preview or "未命名 Claude 会话"
    stat = path.stat()
    return {
        "id": session_id,
        "history_ref": _session_ref("claude", path),
        "client": "claude",
        "title": title,
        "preview": preview,
        "workspace": workspace,
        "model": model or str(manifest.get("model") or ""),
        "profile_id": profile_id or str(manifest.get("profile_id") or ""),
        "profile_name": profile_name or str(manifest.get("profile_name") or ""),
        "gateway": str(manifest.get("gateway") or ""),
        "_key_fingerprint": str(manifest.get("key_fingerprint") or ""),
        "_configured_model": str(manifest.get("model") or model),
        "updated_at": int(stat.st_mtime),
        "_home": str(home),
        "_path": str(path),
    }


def scan_history(
    codex_roots: Iterable[tuple[Path, str, str]],
    claude_roots: Iterable[tuple[Path, str, str]],
    limit: int = MAX_SESSION_ROWS,
    workspace: str | Path | None = None,
    client: str | None = None,
) -> list[dict]:
    """Return newest local conversations across the supplied isolated client homes.

    Each root tuple is (home, profile_id, profile_name). No transcript is returned
    except the short local title preview needed by the history browser.
    """
    rows = []
    for _, kind, path, home, profile_id, profile_name in _candidates(codex_roots, claude_roots):
        if client is not None and kind != client:
            continue
        row = _read(kind, path, home, profile_id, profile_name)
        if row and (workspace is None or _same_workspace(row["workspace"], workspace)):
            rows.append(row)
        if len(rows) >= min(limit, MAX_SESSION_ROWS):
            break
    return rows


def _same_workspace(first: str, second: str | Path) -> bool:
    if not first or not str(second):
        return False
    try:
        first_path = Path(first).expanduser()
        second_path = Path(second).expanduser()
        return os.path.normcase(os.path.realpath(first_path)) == os.path.normcase(os.path.realpath(second_path))
    except (OSError, TypeError, ValueError):
        return False


def mark_scope(rows: Iterable[dict], scope: str) -> list[dict]:
    """Annotate rows with the source scope used by the explicit import UI."""
    return [{**row, "scope": scope} for row in rows]


def scan_all_history(
    codex_roots: Iterable[tuple[Path, str, str]],
    claude_roots: Iterable[tuple[Path, str, str]],
    global_codex_roots: Iterable[tuple[Path, str, str]],
    global_claude_roots: Iterable[tuple[Path, str, str]],
    limit: int = MAX_SESSION_ROWS,
    workspace: str | Path | None = None,
    client: str | None = None,
) -> list[dict]:
    local = mark_scope(scan_history(codex_roots, claude_roots, limit, workspace, client), "workbench")
    global_rows = mark_scope(scan_history(global_codex_roots, global_claude_roots, limit, workspace, client), "global")
    rows = sorted(local + global_rows, key=lambda row: row["updated_at"], reverse=True)
    return rows[:min(limit, MAX_SESSION_ROWS)]


def _candidates(codex_roots, claude_roots):
    result = []
    for client, roots, folder, pattern in (
        ("codex", codex_roots, "sessions", "rollout-*.jsonl"),
        ("claude", claude_roots, "projects", "*/*.jsonl"),
    ):
        for home, profile_id, profile_name in roots:
            root = home / folder
            if home.is_symlink() or root.is_symlink():
                continue
            try:
                paths = root.rglob(pattern) if client == "codex" else root.glob(pattern)
                for path in paths:
                    if (any(p.is_symlink() for p in path.parents if p != home and home in p.parents)
                            or not _safe_file(path)):
                        continue
                    result.append((path.stat().st_mtime, client, path, home, profile_id, profile_name))
            except OSError:
                continue
    return sorted(result, reverse=True)


def _read(client, path, home, profile_id, profile_name):
    try:
        reader = _read_codex if client == "codex" else _read_claude
        return reader(path, home, profile_id, profile_name)
    except (OSError, RecursionError):
        return None


def resolve_history(
    history_ref: str,
    session_id: str,
    client: str,
    codex_roots: Iterable[tuple[Path, str, str]],
    claude_roots: Iterable[tuple[Path, str, str]],
    workspace: str | Path | None = None,
) -> dict:
    if not isinstance(history_ref, str) or len(history_ref) != 32 or not _valid_id(session_id) or client not in {"codex", "claude"}:
        raise ValueError("历史会话标识无效。")
    for _, kind, path, home, profile_id, profile_name in _candidates(codex_roots, claude_roots):
        if kind == client and _session_ref(kind, path) == history_ref:
            row = _read(kind, path, home, profile_id, profile_name)
            if (row and row["id"] == session_id
                    and (workspace is None or _same_workspace(row["workspace"], workspace))):
                return row
    raise ValueError("找不到这条本地会话；请刷新历史列表。")


def public_rows(rows: Iterable[dict]) -> list[dict]:
    return [
        {**{key: value for key, value in row.items() if not key.startswith("_")},
         "configured_model": row.get("_configured_model") or row.get("model", "")}
        for row in rows
    ]


def _metadata_path(root: Path) -> Path:
    return root / "history-metadata.json"


def load_metadata(root: Path) -> dict:
    """Read app-owned history labels and project relationships."""
    path = _metadata_path(root)
    try:
        if path.is_symlink():
            return {"version": METADATA_VERSION, "sessions": {}, "projects": []}
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        value = {}
    if not isinstance(value, dict):
        value = {}
    sessions = value.get("sessions") if isinstance(value.get("sessions"), dict) else {}
    projects = value.get("projects") if isinstance(value.get("projects"), list) else []
    valid_projects = []
    project_ids = set()
    for project in projects:
        if not isinstance(project, dict):
            continue
        project_id = project.get("id")
        name = project.get("name")
        parent_id = project.get("parent_id")
        if not (_valid_id(project_id) and isinstance(name, str) and name.strip()
                and len(name.strip()) <= MAX_PROJECT_NAME_CHARS):
            continue
        if parent_id is not None and not _valid_id(parent_id):
            parent_id = None
        if project_id in project_ids:
            continue
        project_ids.add(project_id)
        valid_projects.append({"id": project_id, "name": name.strip(), "parent_id": parent_id})
    valid_sessions = {}
    for reference, entry in sessions.items():
        if not isinstance(reference, str) or len(reference) != 32 or not isinstance(entry, dict):
            continue
        title = entry.get("title")
        project_id = entry.get("project_id")
        clean = {}
        if isinstance(title, str) and title.strip():
            clean["title"] = title.strip()[:MAX_TITLE_CHARS]
        if project_id in project_ids:
            clean["project_id"] = project_id
        if clean:
            valid_sessions[reference] = clean
    return {"version": METADATA_VERSION, "sessions": valid_sessions, "projects": valid_projects}


def save_metadata(root: Path, metadata: dict) -> None:
    if root.is_symlink():
        raise ValueError("历史元数据目录无效。")
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = _metadata_path(root)
    fd, temporary = tempfile.mkstemp(prefix=".history-metadata-", dir=root)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(metadata, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply_metadata(rows: Iterable[dict], root: Path) -> list[dict]:
    metadata = load_metadata(root)
    projects = {project["id"]: project for project in metadata["projects"]}
    paths = {}

    def project_path(project_id: str, seen: set[str] | None = None) -> str:
        if project_id in paths:
            return paths[project_id]
        seen = set() if seen is None else seen
        if project_id in seen or project_id not in projects:
            return ""
        seen.add(project_id)
        project = projects[project_id]
        parent = project.get("parent_id")
        prefix = project_path(parent, seen) if isinstance(parent, str) else ""
        result = "/".join(part for part in (prefix, project["name"]) if part)
        paths[project_id] = result
        return result

    result = []
    for row in rows:
        entry = metadata["sessions"].get(row.get("history_ref"), {})
        merged = dict(row)
        if entry.get("title"):
            merged["title"] = entry["title"]
            merged["custom_title"] = True
        project_id = entry.get("project_id")
        if project_id in projects:
            merged["project_id"] = project_id
            merged["project_path"] = project_path(project_id)
        else:
            merged["project_id"] = None
            merged["project_path"] = ""
        result.append(merged)
    return result


def _require_reference(reference: str) -> str:
    if not isinstance(reference, str) or len(reference) != 32 or not re.fullmatch(r"[0-9a-f]{32}", reference):
        raise ValueError("历史会话标识无效。")
    return reference


def rename_session(reference: str, title: str, root: Path) -> None:
    reference = _require_reference(reference)
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > MAX_TITLE_CHARS:
        raise ValueError("会话名称不能为空且不能超过 180 个字符。")
    metadata = load_metadata(root)
    current = metadata["sessions"].setdefault(reference, {})
    current["title"] = title.strip()
    save_metadata(root, metadata)


def _require_project(metadata: dict, project_id: str) -> dict:
    if not _valid_id(project_id):
        raise ValueError("项目标识无效。")
    for project in metadata["projects"]:
        if project["id"] == project_id:
            return project
    raise ValueError("找不到该项目。")


def create_project(name: str, parent_id: str | None, root: Path) -> dict:
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > MAX_PROJECT_NAME_CHARS:
        raise ValueError("项目名称不能为空且不能超过 120 个字符。")
    metadata = load_metadata(root)
    if parent_id is not None:
        _require_project(metadata, parent_id)
    project = {"id": str(uuid4()), "name": name.strip(), "parent_id": parent_id}
    metadata["projects"].append(project)
    save_metadata(root, metadata)
    return project


def rename_project(project_id: str, name: str, root: Path) -> dict:
    if not isinstance(name, str) or not name.strip() or len(name.strip()) > MAX_PROJECT_NAME_CHARS:
        raise ValueError("项目名称不能为空且不能超过 120 个字符。")
    metadata = load_metadata(root)
    project = _require_project(metadata, project_id)
    project["name"] = name.strip()
    save_metadata(root, metadata)
    return project


def delete_project(project_id: str, root: Path) -> None:
    metadata = load_metadata(root)
    _require_project(metadata, project_id)
    children = {project["id"] for project in metadata["projects"] if project.get("parent_id") == project_id}
    if children:
        raise ValueError("请先删除或移动子项目。")
    metadata["projects"] = [project for project in metadata["projects"] if project["id"] != project_id]
    for entry in metadata["sessions"].values():
        if entry.get("project_id") == project_id:
            entry.pop("project_id", None)
    save_metadata(root, metadata)


def assign_session_project(reference: str, project_id: str | None, root: Path) -> None:
    reference = _require_reference(reference)
    metadata = load_metadata(root)
    if project_id is not None:
        _require_project(metadata, project_id)
    entry = metadata["sessions"].setdefault(reference, {})
    if project_id is None:
        entry.pop("project_id", None)
    else:
        entry["project_id"] = project_id
    if not entry:
        metadata["sessions"].pop(reference, None)
    save_metadata(root, metadata)


def _archive_marker(row: dict, root: Path) -> Path:
    # Derive the filename from a resolved, trusted session, never from request paths.
    return root / (_session_ref(row["client"], Path(row["_path"])) + ".json")


def archive_rows(rows: Iterable[dict], root: Path, include_archived: bool = False) -> list[dict]:
    result = []
    for row in rows:
        marker = _archive_marker(row, root)
        archived = not marker.is_symlink() and marker.is_file()
        if include_archived or not archived:
            result.append({**row, "archived": archived})
    return result


def set_archived(row: dict, root: Path, archived: bool) -> None:
    """Keep app-owned visibility state without writing the original conversation."""
    if not isinstance(archived, bool):
        raise ValueError("归档状态无效。")
    marker = _archive_marker(row, root)
    if root.is_symlink() or marker.is_symlink():
        raise ValueError("归档状态路径无效。")
    if not archived:
        marker.unlink(missing_ok=True)
        return
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".archive-", dir=root)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump({"version": 1, "archived_at": int(time.time())}, stream)
        os.replace(temporary, marker)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def key_fingerprint(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def can_resume_in_place(row: dict, profile_id: str, model: str, gateway: str = "",
                        fingerprint: str = "") -> bool:
    """Only continue a workbench session when its key profile and model are known to match."""
    source_profile = row.get("profile_id")
    source_model = row.get("_configured_model") or row.get("model")
    if (row.get("scope") == "global" or not source_profile or source_profile == "global"
            or source_profile != profile_id or not source_model or source_model != model):
        return False
    source_gateway = row.get("gateway")
    if not source_gateway or source_gateway != gateway:
        return False
    # Old snapshots predate fingerprints. A recorded fingerprint must match;
    # the replaceable default Key needs one even for older histories.
    source_fingerprint = row.get("_key_fingerprint")
    if source_fingerprint or profile_id == "free":
        return bool(fingerprint and source_fingerprint == fingerprint)
    return True


def stage_branch(row: dict, parent: Path, profile_id: str, profile_name: str, model: str, gateway: str,
                 fingerprint: str = ""):
    """Give the native fork a private input copy and an empty, target-specific home."""
    source = Path(row["_path"])
    source_home = Path(row["_home"])
    inputs = [(row["id"], source)]
    if row["client"] == "codex":
        inputs.extend(_codex_ancestors(source_home, row["id"], source))
    home = parent / ("branch-" + uuid4().hex)
    home.mkdir(parents=True, mode=0o700)
    seed = home / source.relative_to(source_home)
    try:
        for _, path in inputs:
            destination = home / path.relative_to(source_home)
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            with path.open("rb") as original, open(destination, "xb", opener=_private_open) as replica:
                shutil.copyfileobj(original, replica)
        (home / "workbench-profile.json").write_text(json.dumps({
            "profile_id": profile_id, "profile_name": profile_name,
            "model": model, "gateway": gateway, "branch_title": row["title"],
            "key_fingerprint": fingerprint,
            "imported_sessions": [session_id for session_id, _ in inputs],
            "inherited_through": _latest_claude_timestamp(seed) if row["client"] == "claude" else "",
        }, ensure_ascii=False), encoding="utf-8")
    except BaseException:
        shutil.rmtree(home)
        raise
    return home, seed


def _latest_claude_timestamp(path: Path) -> str:
    latest = ""
    with path.open(encoding="utf-8", errors="replace") as stream:
        for line in stream:
            try:
                row = json.loads(line)
            except (ValueError, RecursionError):
                continue
            if isinstance(row, dict) and row.get("type") == "assistant" and isinstance(row.get("timestamp"), str):
                latest = max(latest, row["timestamp"])
    return latest


def _codex_ancestors(home: Path, session_id: str, path: Path) -> list[tuple[str, Path]]:
    ancestors = []
    seen = {session_id}
    while True:
        with path.open(encoding="utf-8", errors="replace") as stream:
            first = json.loads(stream.readline())
        base = first.get("payload", {}).get("history_base")
        if not isinstance(base, dict):
            return ancestors
        parent_id = base.get("thread_id")
        if not _valid_id(parent_id) or parent_id in seen or len(seen) >= 100:
            raise ValueError("Codex 会话的父历史无法解析。")
        seen.add(parent_id)
        choices = [candidate for _, _, candidate, _, _, _ in _candidates([(home, "", "")], [])
                   if candidate.stem.endswith("-" + parent_id)]
        if len(choices) != 1:
            raise ValueError("Codex 分支缺少可读取的父历史；已停止恢复。")
        path = choices[0]
        ancestors.append((parent_id, path))


def _private_open(path, flags):
    return os.open(path, flags, 0o600)
