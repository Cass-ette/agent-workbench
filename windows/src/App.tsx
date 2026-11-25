import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";
import { listen } from "@tauri-apps/api/event";
import { getCurrentWebview } from "@tauri-apps/api/webview";
import { open } from "@tauri-apps/plugin-dialog";
import { FitAddon } from "@xterm/addon-fit";
import { Terminal } from "@xterm/xterm";
import {
  Activity,
  Check,
  ChevronDown,
  CircleHelp,
  FolderOpen,
  KeyRound,
  Maximize2,
  Minimize2,
  Plus,
  RefreshCw,
  Search,
  Settings2,
  Square,
  TerminalSquare,
  Trash2,
  X,
  Zap,
} from "lucide-react";
import "@xterm/xterm/css/xterm.css";
import "./App.css";
import { CurrentReasoningDialog, ReasoningSlider, effortKey, readEffortPreferences } from "./ReasoningEffort";
import type { Effort } from "./ReasoningEffort";
import { UsageStatus } from "./UsagePanel";
import { WorkbenchUpdate } from "./WorkbenchUpdate";
import { SSHConnection } from "./SSHConnection";
import { AgentPermissionDialog, permissionLabel, permissionStorageKey, readAgentPermissions } from "./AgentPermissions";
import { ToolBridge } from "./ToolBridge";
import { installImagePaste } from "./TerminalClipboard";
import { reorderSessions, tabDropTarget } from "./SessionOrder";
import { AppearancePicker, terminalTheme, useAppearance } from "./Appearance";
import type { ResolvedTheme } from "./Appearance";
import type { AgentPermissions } from "./AgentPermissions";
import type { UsageSnapshot } from "./UsagePanel";

type ClientKind = "claude" | "codex";
type SessionMode = "new" | "continue" | "resume";
type LaunchScope = "workbench" | "system";

interface KeyProfile {
  id: string;
  name: string;
  gateway: string;
  model_catalog_url?: string;
}

interface AppConfig {
  profiles: KeyProfile[];
  selected_profile_id: string;
  client: string;
  model: string;
  workspace: string;
}

interface AppStateResponse {
  config: AppConfig;
  config_path: string;
}

interface ModelCatalog { models: string[]; catalog_notice?: string; }
interface AddProfileResponse extends ModelCatalog { profile: KeyProfile; }

interface TerminalInfo {
  id: string;
  title: string;
  workspace: string;
}

interface LocalConversation {
  id: string;
  history_ref: string;
  client: ClientKind;
  title: string;
  preview?: string;
  workspace: string;
  model: string;
  configured_model?: string;
  profile_id: string;
  profile_name: string;
  updated_at: number;
  scope?: "local" | "workbench" | "global";
  archived?: boolean;
}

interface Session extends TerminalInfo {
  client: ClientKind;
  model: string;
  profileName: string;
  mode: SessionMode;
  exitCode: number | null;
  originalHistoryRef?: string;
  permissions?: string;
}

interface TerminalOutput {
  session_id: string;
  data: number[];
}

interface TerminalExit {
  session_id: string;
  exit_code: number | null;
}

const emptyConfig: AppConfig = {
  profiles: [],
  selected_profile_id: "",
  client: "claude",
  model: "",
  workspace: "",
};

function App() {
  const appearance = useAppearance();
  const [effortPreferences, setEffortPreferences] = useState(readEffortPreferences);
  const [reasoningTarget, setReasoningTarget] = useState<{ id: string; client: ClientKind } | null>(null);
  const [showTools, setShowTools] = useState(false);
  const [agentPermissions, setAgentPermissions] = useState(readAgentPermissions);
  const [showPermissions, setShowPermissions] = useState(() => readAgentPermissions() === null);
  const [config, setConfig] = useState<AppConfig>(emptyConfig);
  const [configPath, setConfigPath] = useState("");
  const [loaded, setLoaded] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [modelQuery, setModelQuery] = useState("");
  const [isLoadingModels, setIsLoadingModels] = useState(false);
  const [modelLoadError, setModelLoadError] = useState("");
  const [catalogNotice, setCatalogNotice] = useState("");
  const [modelReload, setModelReload] = useState(0);
  const [sessionMode, setSessionMode] = useState<SessionMode>("new");
  const [launchScope, setLaunchScope] = useState<LaunchScope>(() =>
    window.localStorage.getItem("launchScope") === "system" ? "system" : "workbench",
  );
  const [history, setHistory] = useState<LocalConversation[]>([]);
  const [showSSHConnection, setShowSSHConnection] = useState(false);
  const [historyByDirectory, setHistoryByDirectory] = useState(false);
  const [historyScope, setHistoryScope] = useState<"workspace" | "global">("workspace");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyQuery, setHistoryQuery] = useState("");
  const [historyError, setHistoryError] = useState("");
  const [historyArchived, setHistoryArchived] = useState(false);
  const [historyArchiveBusy, setHistoryArchiveBusy] = useState("");
  const [selectedHistory, setSelectedHistory] = useState<LocalConversation | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const sessionsRef = useRef<Session[]>([]);
  const [draggedSessionId, setDraggedSessionId] = useState("");
  const [dropTargetId, setDropTargetId] = useState("");
  const [dropAfter, setDropAfter] = useState(false);
  const tabPointer = useRef<{ id: string; x: number; y: number; moving: boolean; target: string; after: boolean } | null>(null);
  const suppressTabClick = useRef(false);
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const currentSessionId = useRef("");
  const [isStarting, setIsStarting] = useState(false);
  const starting = useRef(false);
  const closing = useRef(new Map<string, Promise<boolean>>());
  const [closingIds, setClosingIds] = useState<string[]>([]);
  const [showProfileDialog, setShowProfileDialog] = useState(false);
  const [editingConnection, setEditingConnection] = useState<KeyProfile | null>(null);
  const [terminalFullscreen, setTerminalFullscreen] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [usage, setUsage] = useState<UsageSnapshot | null>(null);
  const [isLoadingUsage, setIsLoadingUsage] = useState(false);
  const [usageError, setUsageError] = useState("");
  const usageBusy = useRef(false);
  const terminalRefs = useRef(new Map<string, Terminal>());
  const pendingOutput = useRef(new Map<string, Uint8Array[]>());
  const addedCatalog = useRef<{ id: string; reload: number; catalog: ModelCatalog } | null>(null);
  const pendingExits = useRef(new Map<string, number>());

  const selectedProfile = useMemo(
    () => config.profiles.find((profile) => profile.id === config.selected_profile_id),
    [config.profiles, config.selected_profile_id],
  );
  sessionsRef.current = sessions;
  const startupEffort = effortPreferences[effortKey(config.client, config.selected_profile_id, config.model)] ?? "default";
  function setStartupEffort(value: Effort) {
    const next = { ...effortPreferences, [effortKey(config.client, config.selected_profile_id, config.model)]: value };
    try { window.localStorage.setItem("reasoning.preferences", JSON.stringify(next)); setEffortPreferences(next); }
    catch { setError("无法保存思考强度设置，请重试。"); }
  }

  const matchingHistory = history.filter((entry) => Boolean(entry.archived) === historyArchived && [entry.title, entry.preview ?? "", entry.workspace, entry.client, entry.model, entry.profile_name].some(
    (value) => value.toLocaleLowerCase().includes(historyQuery.trim().toLocaleLowerCase()),
  ));

  const filteredModels = useMemo(() => {
    const query = modelQuery.trim().toLocaleLowerCase();
    if (!query) return models;
    return models.filter((model) => model.toLocaleLowerCase().includes(query));
  }, [modelQuery, models]);

  const selectedModelOutsideFilter = !!config.model && !filteredModels.includes(config.model);
  const canStart = !!config.workspace && !isStarting
    && (launchScope === "system" || (!!selectedProfile && !!config.model));

  function selectSession(id: string) {
    currentSessionId.current = id;
    setSelectedSessionId(id);
  }

  useEffect(() => {
    window.localStorage.setItem("launchScope", launchScope);
  }, [launchScope]);

  useEffect(() => {
    if (!isTauri()) {
      setLoaded(true);
      return;
    }
    let cancelled = false;
    invoke<AppStateResponse>("load_app_state")
      .then((response) => {
        if (cancelled) return;
        setConfig({
          ...emptyConfig,
          ...response.config,
          client: response.config.client === "codex" ? "codex" : "claude",
        });
        setConfigPath(response.config_path);
      })
      .catch((cause: unknown) => setError(errorMessage(cause)))
      .finally(() => {
        if (!cancelled) setLoaded(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (!isTauri() || !loaded) return;
    let cancelled = false;
    const unlisteners: Array<() => void> = [];
    listen<TerminalInfo & {client: ClientKind; model: string; scope: string}>("remote-terminal-created", event => {
      const info = event.payload;
      setSessions(current => current.some(s => s.id === info.id) ? current : [...current, {
        ...info, mode: "new", exitCode: null, profileName: info.scope === "system" ? "系统 CLI" : "电脑工作台配置",
      }]);
    }).then(unlisten => { if (cancelled) unlisten(); else unlisteners.push(unlisten); });
    listen<TerminalOutput>("terminal-output", (event) => {
      const { session_id, data } = event.payload;
      const bytes = new Uint8Array(data);
      const terminal = terminalRefs.current.get(session_id);
      if (terminal) {
        terminal.write(bytes);
      } else {
        const queue = pendingOutput.current.get(session_id) ?? [];
        queue.push(bytes);
        pendingOutput.current.set(session_id, queue);
      }
    }).then((unlisten) => {
      if (cancelled) unlisten();
      else unlisteners.push(unlisten);
    });
    listen<TerminalExit>("terminal-exit", (event) => {
      const { session_id, exit_code } = event.payload;
      if (starting.current) pendingExits.current.set(session_id, exit_code ?? 1);
      setSessions((current) => current.map((session) => session.id === session_id ? { ...session, exitCode: exit_code ?? 1 } : session));
    }).then((unlisten) => {
      if (cancelled) unlisten();
      else unlisteners.push(unlisten);
    });
    listen<{ session_id: string; error: string }>("terminal-stop-error", (event) => {
      setError(event.payload.error);
    }).then((unlisten) => {
      if (cancelled) unlisten();
      else unlisteners.push(unlisten);
    });
    return () => {
      cancelled = true;
      unlisteners.forEach((unlisten) => unlisten());
    };
  }, [loaded]);

  useEffect(() => {
    if (!isNativeTauri() || !loaded) return;
    let cancelled = false;
    let unlisten: (() => void) | undefined;
    getCurrentWebview().onDragDropEvent((event) => {
      if (cancelled || event.payload.type !== "drop") return;
      const { paths } = event.payload;
      if (!paths.length) return;
      const sessionId = currentSessionId.current;
      if (!sessionId) {
        setNotice("请先启动一个 Codex 或 Claude 终端，再拖入文件。");
        return;
      }
      const text = `${paths.map(shellQuotePowerShell).join(" ")} `;
      invoke("write_terminal", {
        sessionId,
        data: Array.from(new TextEncoder().encode(text)),
      })
        .then(() => setNotice(`已将 ${paths.length} 个路径插入当前终端。`))
        .catch((cause: unknown) => setError(errorMessage(cause)));
    }).then((remove) => {
      if (cancelled) remove();
      else unlisten = remove;
    }).catch((cause: unknown) => setError(errorMessage(cause)));
    return () => {
      cancelled = true;
      unlisten?.();
    };
  }, [loaded]);

  const refreshUsage = useCallback(async () => {
    if (!isTauri() || usageBusy.current) return;
    usageBusy.current = true;
    setIsLoadingUsage(true);
    try {
      setUsage(await invoke<UsageSnapshot>("usage_snapshot"));
      setUsageError("");
    } catch {
      setUsageError("用量读取失败；保留上次结果");
    } finally {
      usageBusy.current = false;
      setIsLoadingUsage(false);
    }
  }, []);

  useEffect(() => {
    if (!loaded || !isTauri()) return;
    void refreshUsage();
    const timer = window.setInterval(() => void refreshUsage(), 10000);
    return () => window.clearInterval(timer);
  }, [loaded, refreshUsage]);

  useEffect(() => {
    if (!loaded || !isTauri()) return;
    const timer = window.setTimeout(() => {
      invoke("save_preferences", {
        request: { client: config.client, model: config.model, workspace: config.workspace },
      }).catch((cause: unknown) => setError(errorMessage(cause)));
    }, 250);
    return () => window.clearTimeout(timer);
  }, [config.client, config.model, config.workspace, loaded]);

  useEffect(() => {
    if (!loaded || launchScope === "system" || !selectedProfile || !isTauri()) {
      setModels([]);
      setIsLoadingModels(false);
      setModelLoadError(""); setCatalogNotice("");
      return;
    }
    const added = addedCatalog.current;
    if (added?.id === selectedProfile.id && added.reload === modelReload) {
      setModels(added.catalog.models);
      setCatalogNotice(added.catalog.catalog_notice ?? "");
      setModelLoadError("");
      setIsLoadingModels(false);
      return;
    }
    let cancelled = false;
    setIsLoadingModels(true);
    setModelLoadError(""); setCatalogNotice("");
    invoke<ModelCatalog>("models_for_profile", { profileId: selectedProfile.id })
      .then((catalog) => {
        if (cancelled) return;
        const items = catalog.models;
        setCatalogNotice(catalog.catalog_notice ?? "");
        setModels((previous) => items.length ? items : previous);
        setConfig((current) => ({
          ...current,
          model: current.model || items[0] || "",
        }));
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setModelLoadError(errorMessage(cause));
        }
      })
      .finally(() => {
        if (!cancelled) setIsLoadingModels(false);
      });
    return () => {
      cancelled = true;
    };
  }, [launchScope, loaded, selectedProfile?.id, selectedProfile?.gateway, selectedProfile?.model_catalog_url, modelReload]);

  const registerTerminal = useCallback((id: string, terminal: Terminal) => {
    terminalRefs.current.set(id, terminal);
    const queued = pendingOutput.current.get(id) ?? [];
    queued.forEach((chunk) => terminal.write(chunk));
    pendingOutput.current.delete(id);
  }, []);

  const unregisterTerminal = useCallback((id: string, terminal: Terminal) => {
    if (terminalRefs.current.get(id) === terminal) terminalRefs.current.delete(id);
  }, []);

  const canReceiveInput = useCallback((id: string) =>
    currentSessionId.current === id && !closing.current.has(id), []);

  async function selectProfile(profile: KeyProfile) {
    if (profile.id === config.selected_profile_id) return;
    setError("");
    setModelQuery("");
    setModels([]);
    setModelLoadError(""); setCatalogNotice("");
    setConfig((current) => ({ ...current, selected_profile_id: profile.id, model: "" }));
    try {
      await invoke("select_profile", { profileId: profile.id });
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }

  async function chooseWorkspace() {
    if (!isTauri()) return;
    const result = await open({ directory: true, multiple: false, title: "选择项目工作目录" });
    if (typeof result === "string") {
      setConfig((current) => ({ ...current, workspace: result }));
      if (selectedHistory && selectedHistory.workspace !== result) {
        setSelectedHistory(null);
        setSessionMode("new");
      }
    }
  }

  async function startSession(mode: SessionMode = sessionMode, original?: LocalConversation) {
    if (starting.current) return;
    if (!original && !canStart) return;
    if (!original && launchScope === "system") {
      return startGlobalSession(config.client as ClientKind);
    }
    if (!original && (!selectedProfile || !config.model || !config.workspace)) return;
    const source = original ?? (mode === "resume" ? selectedHistory : null);
    if (mode === "resume" && !source) return openHistory();
    if (!original && source && !window.confirm(
      "从「" + source.title + "」创建分支？\n调用配置：" + selectedProfile?.name +
      "\n网关：" + selectedProfile?.gateway + "\n模型：" + config.model +
      "\n历史上下文会发往该网关，原对话保留。"
    )) return;
    const client = source?.client ?? config.client as ClientKind;
    const model = original ? (original.configured_model || original.model) : config.model;
    const profileName = original
      ? (original.scope === "global" ? "系统 CLI" : config.profiles.find((profile) => profile.id === original.profile_id)?.name ?? original.profile_name)
      : selectedProfile?.name ?? "";
    const existing = original && sessions.find((session) =>
      session.originalHistoryRef === original.history_ref && session.exitCode === null);
    if (existing) {
      const closingSession = closing.current.get(existing.id);
      if (!closingSession) {
        selectSession(existing.id);
        setError("");
        setNotice("已切回正在运行的对话。");
        return;
      }
      if (!await closingSession || starting.current) return;
    }
    if (original?.scope !== "global" && !agentPermissions) {
      setShowPermissions(true);
      return;
    }
    setError("");
    setNotice("");
    starting.current = true;
    setIsStarting(true);
    try {
      const info = await invoke<TerminalInfo>("start_terminal", {
        request: {
          profile_id: original ? "" : selectedProfile?.id,
          client,
          model: original ? "" : model,
          workspace: original?.workspace ?? config.workspace,
          session_mode: mode,
          history_ref: source?.history_ref,
          history_id: source?.id,
          confirmed_gateway: source && !original ? selectedProfile?.gateway : undefined,
          history_action: original ? "original" : source ? "fork" : undefined,
          permission_mode: agentPermissions?.[client] ?? "restricted",
          reasoning_effort: effortPreferences[effortKey(client, original?.profile_id ?? config.selected_profile_id,
            original?.configured_model ?? original?.model ?? model)] ?? "default",
        },
      });
      const session: Session = {
        ...info,
        client,
        model,
        profileName,
        mode,
        exitCode: pendingExits.current.get(info.id) ?? null,
        originalHistoryRef: original?.history_ref,
        permissions: original?.scope === "global" ? "系统 CLI 权限" : permissionLabel(client, agentPermissions![client]),
      };
      setSessions((current) => [...current, session]);
      selectSession(info.id);
      if (session.exitCode === null) setNotice(source
        ? original ? `已用原配置恢复「${source.title}」。` : `已从「${source.title}」创建分支。`
        : `${client === "codex" ? "Codex" : "Claude Code"} 已在独立终端中启动。`);
      if (source && !original) {
        setSelectedHistory(null);
        setSessionMode("new");
      }
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      starting.current = false;
      pendingExits.current.clear();
      setIsStarting(false);
    }
  }

  async function startGlobalSession(client: ClientKind) {
    if (starting.current || !config.workspace) return;
    setError("");
    setNotice("");
    starting.current = true;
    setIsStarting(true);
    try {
      const info = await invoke<TerminalInfo>("start_global_terminal", {
        client,
        workspace: config.workspace,
      });
      const session: Session = {
        ...info,
        client,
        model: "系统配置",
        profileName: "系统全局",
        mode: "new",
        exitCode: pendingExits.current.get(info.id) ?? null,
        permissions: "系统 CLI 权限",
      };
      setSessions((current) => [...current, session]);
      selectSession(info.id);
      if (session.exitCode === null) setNotice(`已启动系统全局 ${client === "codex" ? "Codex" : "Claude Code"}；沿用系统配置，不会修改工作台配置。`);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      starting.current = false;
      pendingExits.current.clear();
      setIsStarting(false);
    }
  }

  async function openHistory(scope: "workspace" | "global" = "workspace") {
    if (scope === "workspace" && !config.workspace.trim()) { setError("请先选择项目目录。"); return; }
    setHistoryScope(scope);
    setHistoryByDirectory(false);
    setHistoryArchived(false);
    setHistoryQuery("");
    setHistoryOpen(true);
    setHistoryLoading(true);
    setHistoryError("");
    try {
      const rows = scope === "global"
        ? await invoke<LocalConversation[]>("list_all_history")
        : await invoke<LocalConversation[]>("list_history", { workspace: config.workspace });
      setHistory(rows);
    } catch (cause) {
      setHistoryError(errorMessage(cause));
    } finally {
      setHistoryLoading(false);
    }
  }

  async function restoreRecentInProject() {
    if (isStarting || !config.workspace.trim()) { setError("请先选择项目目录。"); return; }
    try {
      const rows = await invoke<LocalConversation[]>("list_history", { workspace: config.workspace });
      const latest = rows.find((entry) => entry.client === config.client && !entry.archived);
      if (!latest) { setError("当前项目暂无该客户端的对话可恢复。"); return; }
      await startSession("resume", latest);
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }

  async function archiveHistory(entry: LocalConversation) {
    if (historyArchiveBusy) return;
    setHistoryArchiveBusy(entry.history_ref);
    try {
      await invoke("set_history_archived", { historyRef: entry.history_ref, historyId: entry.id, client: entry.client, archived: !entry.archived });
      setHistory((rows) => rows.map((row) => row.history_ref === entry.history_ref ? { ...row, archived: !entry.archived } : row));
      setNotice(entry.archived ? "已取消归档。" : "已归档；可在「已归档」中查找和取消归档。");
    } catch (cause) { setHistoryError(errorMessage(cause)); }
    finally { setHistoryArchiveBusy(""); }
  }

  function selectHistory(entry: LocalConversation) {
    setHistoryOpen(false);
    if (historyScope === "global") {
      void startSession("resume", entry);
      return;
    }
    setSelectedHistory(entry);
    setSessionMode("resume");
    setConfig((current) => ({ ...current, client: entry.client }));
  }

  function selectClient(client: ClientKind) {
    if (selectedHistory && selectedHistory.client !== client) {
      setSelectedHistory(null);
      setSessionMode("new");
    }
    setConfig((current) => ({ ...current, client }));
  }

  async function closeSession(session: Session) {
    if (closing.current.has(session.id)) return;
    setError("");
    setClosingIds((ids) => [...ids, session.id]);
    const completion = (async () => {
      try {
        if (isTauri() && session.exitCode === null) {
          await invoke("stop_terminal", { sessionId: session.id });
        }
        const closingWasSelected = currentSessionId.current === session.id;
        setSessions((current) => current.filter((item) => item.id !== session.id));
        if (closingWasSelected) {
          const remaining = sessionsRef.current.filter((item) => item.id !== session.id);
          selectSession(remaining[remaining.length - 1]?.id ?? "");
        }
        pendingOutput.current.delete(session.id);
        return true;
      } catch (cause) {
        setError("关闭终端失败：" + errorMessage(cause));
        return false;
      }
    })();
    closing.current.set(session.id, completion);
    await completion;
    closing.current.delete(session.id);
    setClosingIds((ids) => ids.filter((id) => id !== session.id));
  }

  async function removeProfile(profile: KeyProfile) {
    if (!window.confirm(`删除“${profile.name}”及其本机调用 Key？`)) return;
    setError("");
    try {
      await invoke("remove_profile", { profileId: profile.id });
      const remaining = config.profiles.filter((item) => item.id !== profile.id);
      setConfig((current) => ({
        ...current,
        profiles: remaining,
        selected_profile_id: current.selected_profile_id === profile.id ? (remaining[0]?.id ?? "") : current.selected_profile_id,
        model: current.selected_profile_id === profile.id ? "" : current.model,
      }));
      setNotice("已删除本机 Key 配置。");
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }

  const activeSession = sessions.find((session) => session.id === selectedSessionId);

  return (
    <div className={`app-shell ${terminalFullscreen ? "terminal-fullscreen" : ""}`}>
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark"><Activity size={17} strokeWidth={2.2} /></div>
          <div>
            <div className="brand-name">Agent Workbench</div>
            <div className="brand-caption">项目与终端会话</div>
          </div>
        </div>
        <div className="topbar-status">
          <span className="status-dot" />本机工作区
          <AppearancePicker value={appearance.mode} change={appearance.changeMode} />
          <WorkbenchUpdate running={sessions.filter((session) => session.exitCode === null).length} />
        </div>
      </header>

      <div className="app-body">
        <aside className="sidebar">
          <div className="sidebar-heading">
            <div>
              <span className="eyebrow">调用配置</span>
              <h2>工作台调用配置</h2>
            </div>
            <button className="icon-button sidebar-add" title="添加调用配置" onClick={() => setShowProfileDialog(true)}>
              <Plus size={16} />
            </button>
          </div>

          <div className="profile-list">
            {config.profiles.map((profile) => (
              <div key={profile.id} className={`profile-row ${profile.id === config.selected_profile_id ? "selected" : ""}`}>
                <button className="profile-select" onClick={() => selectProfile(profile)}>
                  <span className="profile-indicator"><KeyRound size={14} /></span>
                  <span className="profile-copy">
                    <strong>{profile.name}</strong>
                    <small>{gatewayLabel(profile.gateway)}</small>
                  </span>
                  {profile.id === config.selected_profile_id && <Check size={14} className="profile-check" />}
                </button>
                <button className="icon-button" title={`连接设置 ${profile.name}`} onClick={() => setEditingConnection(profile)}><Settings2 size={13} /></button>
                <button className="profile-remove" title={`删除 ${profile.name}`} onClick={() => removeProfile(profile)}>
                  <Trash2 size={13} />
                </button>
              </div>
            ))}
            {!config.profiles.length && (
              <button className="empty-profile" onClick={() => setShowProfileDialog(true)}>
                <KeyRound size={17} />
                <span>添加本机调用 Key</span>
                <Plus size={15} />
              </button>
            )}
          </div>

          <div className="sidebar-bottom">
            <button className="sidebar-link" onClick={() => setShowPermissions(true)}>权限设置</button>
            <button className="sidebar-link" onClick={() => setShowTools(true)}>工具与插件</button>
            <button className="sidebar-link" onClick={() => void openHistory("global")}>恢复最近对话</button>
            <button className="sidebar-link" onClick={() => setShowSSHConnection(true)}>手机连接</button>
            <div className="security-note"><span className="security-dot" />密钥保存在 Windows 凭据管理器</div>
            <button className="sidebar-link" onClick={() => setShowProfileDialog(true)}>
              <Settings2 size={15} />管理调用配置
            </button>
          </div>
        </aside>

        <main className="main-pane">
          <section className="setup-panel">
            <div className="section-heading">
              <div>
                <span className="eyebrow">TASK SETUP</span>
                <h1>启动工作会话</h1>
              </div>
              <div className="profile-current">
                <span>当前配置</span>
                <strong>{launchScope === "system" ? "系统配置" : (selectedProfile?.name ?? "尚未配置")}</strong>
              </div>
            </div>

            <div className={`setup-grid ${launchScope === "system" ? "system-scope" : ""}`}>
              <div className="field client-field">
                <label>客户端</label>
                <div className="segmented-control" role="group" aria-label="选择客户端">
                  <button aria-pressed={config.client === "claude"} onClick={() => selectClient("claude")}>Claude Code</button>
                  <button aria-pressed={config.client === "codex"} onClick={() => selectClient("codex")}>Codex</button>
                </div>
              </div>

              <div className="field scope-field">
                <label>启动方式</label>
                <div className="segmented-control" role="group" aria-label="选择启动方式">
                  <button aria-pressed={launchScope === "workbench"} onClick={() => setLaunchScope("workbench")}>工作台配置</button>
                  <button aria-pressed={launchScope === "system"} onClick={() => setLaunchScope("system")}>系统 CLI</button>
                </div>
              </div>

              {launchScope === "workbench" && (
                <div className="field model-field">
                  <label htmlFor="model-query">模型 <span>{models.length ? `${models.length} 个可用` : ""}</span></label>
                  <div className="model-picker">
                    <div className="search-box">
                      <Search size={14} />
                      <input id="model-query" value={modelQuery} onChange={(event) => setModelQuery(event.currentTarget.value)} placeholder="搜索模型" />
                    </div>
                    <div className="model-select-wrap">
                      <select aria-label="选择模型" value={config.model} onChange={(event) => {
                        const model = event.currentTarget.value;
                        setConfig((current) => ({ ...current, model }));
                      }} disabled={!selectedProfile || isLoadingModels || !filteredModels.length}>
                        {isLoadingModels && <option value="">正在读取模型…</option>}
                        {!isLoadingModels && !selectedProfile && <option value="">先添加调用配置</option>}
                        {!isLoadingModels && selectedProfile && !models.length && !config.model && <option value="">{modelLoadError ? "模型列表读取失败" : "网关未返回可用模型"}</option>}
                        {!isLoadingModels && models.length > 0 && !filteredModels.length && <option value="" disabled>没有匹配模型</option>}
                        {selectedModelOutsideFilter && <option value={config.model}>{config.model}（{models.includes(config.model) ? "当前" : "已保存"}）</option>}
                        {filteredModels.map((model) => <option key={model} value={model}>{model}</option>)}
                      </select>
                      <ChevronDown size={14} className="select-chevron" />
                    </div>
                    <button className="model-refresh" aria-label="刷新模型列表" title="刷新模型列表" onClick={() => setModelReload((value) => value + 1)} disabled={!selectedProfile || isLoadingModels}>
                      <RefreshCw size={14} className={isLoadingModels ? "spinning" : ""} />
                    </button>
                  </div>
                  <div className="manual-model">
                    <label htmlFor="manual-model-id">模型 ID（可手动填写）</label>
                    <input id="manual-model-id" value={config.model} maxLength={250} disabled={!selectedProfile} placeholder="输入网关支持的模型 ID" onChange={(event) => {
                      const model = event.currentTarget.value.trim();
                      setConfig((current) => ({ ...current, model }));
                    }} />
                  </div>
                  {modelLoadError && <span className="model-load-error" role="status">模型列表读取失败：{modelLoadError}</span>}
                  <span className="model-load-note">Claude 需 Messages，Codex 需 Responses；仅支持 Chat Completions 的网关不能直接供 Codex 使用。</span>
                  {catalogNotice && <span className="model-load-note" role="status">{catalogNotice}</span>}
                  {!catalogNotice && !modelLoadError && !isLoadingModels && selectedProfile && !models.length && <span className="model-load-note">网关未返回可用模型</span>}
                </div>
              )}

              <div className="field workspace-field">
                <label htmlFor="workspace">项目目录</label>
                <div className="path-control">
                  <input id="workspace" value={config.workspace} onChange={(event) => {
                    const workspace = event.currentTarget.value;
                    setConfig((current) => ({ ...current, workspace }));
                    if (selectedHistory && selectedHistory.workspace !== workspace) {
                      setSelectedHistory(null);
                      setSessionMode("new");
                    }
                  }} placeholder="选择或输入项目目录" />
                  <button className="browse-button" onClick={chooseWorkspace} title="浏览项目目录"><FolderOpen size={16} /><span>浏览</span></button>
                </div>
              </div>

              {launchScope === "workbench" && (
                <div className="field mode-field">
                  <label htmlFor="session-mode">会话</label>
                  <div className="mode-select-wrap">
                    <select id="session-mode" value={sessionMode} onChange={(event) => {
                      const mode = event.currentTarget.value as SessionMode;
                      if (mode === "resume") void openHistory("workspace");
                      else if (mode === "continue") void restoreRecentInProject();
                      else { setSelectedHistory(null); setSessionMode(mode); }
                    }}>
                      <option value="new">新会话</option>
                      <option value="continue" title="使用原 Key、模型与项目目录续接">恢复最近对话</option>
                      <option value="resume">{selectedHistory ? "分支 · " + selectedHistory.title : "从历史创建分支"}</option>
                    </select>
                    <ChevronDown size={14} className="select-chevron" />
                  </div>
                </div>
              )}

              <button className="launch-button" disabled={!canStart} onClick={() => startSession()}>
                <Zap size={16} fill="currentColor" />
                {isStarting ? "正在启动" : sessionMode === "resume" && launchScope === "workbench" ? "启动分支" : "启动"}
              </button>
            </div>
            {launchScope === "workbench" && <div className="startup-reasoning">
              <ReasoningSlider value={startupEffort} onChange={setStartupEffort} label="启动默认思考强度" />
              <small>按客户端、调用配置和模型分别保存；当前会话使用终端旁的入口调整。</small>
            </div>}
            <div className="setup-footnote">
              <span><CircleHelp size={13} />{launchScope === "system" ? "沿用系统已有命令、Key 和配置" : "每个标签使用独立终端进程"}</span>
              {launchScope === "workbench" && selectedProfile && <span className="gateway-address">{selectedProfile.gateway}</span>}
            </div>
          </section>

          <section className="terminal-panel">
            <div className="terminal-toolbar">
              <div className="terminal-heading">
                <TerminalSquare size={16} />
                <strong>终端工作区</strong>
                <span className="session-count">{sessions.length}</span>
                {activeSession?.permissions && <small>{activeSession.permissions}</small>}
              </div>
              <div className="terminal-toolbar-actions">
                {activeSession && activeSession.exitCode === null && <button className="secondary-button"
                  onClick={() => setReasoningTarget({ id: activeSession.id, client: activeSession.client })}>思考强度</button>}
                {activeSession && activeSession.exitCode === null && (
                  <button className="icon-button stop-button" title="停止并关闭此终端" disabled={closingIds.includes(activeSession.id)} onClick={() => closeSession(activeSession)}><Square size={13} fill="currentColor" /></button>
                )}
                <button className="icon-button fullscreen-button" title={terminalFullscreen ? "退出终端全屏" : "终端全屏"} onClick={() => setTerminalFullscreen((value) => !value)}>
                  {terminalFullscreen ? <Minimize2 size={15} /> : <Maximize2 size={15} />}
                </button>
                <button className="new-terminal-button" disabled={!canStart} onClick={() => startSession("new")}><Plus size={14} />新建会话</button>
              </div>
            </div>

            {sessions.length > 0 && (
              <div className="terminal-tabs" role="tablist">
                {sessions.map((session) => (
                  <div key={session.id} data-session-id={session.id}
                    className={`terminal-tab ${session.id === selectedSessionId ? "active" : ""} ${draggedSessionId === session.id ? "dragging" : ""} ${dropTargetId === session.id ? "drop-target" : ""} ${dropTargetId === session.id && dropAfter ? "drop-after" : ""}`}
                    onPointerDown={(event) => {
                      if (event.button !== 0 || (event.target as Element).closest(".tab-close")) return;
                      suppressTabClick.current = false;
                      tabPointer.current = { id: session.id, x: event.clientX, y: event.clientY, moving: false, target: "", after: false };
                      event.currentTarget.setPointerCapture?.(event.pointerId);
                    }}
                    onPointerMove={(event) => {
                      const drag = tabPointer.current;
                      if (!drag) return;
                      if (!drag.moving && Math.hypot(event.clientX - drag.x, event.clientY - drag.y) < 6) return;
                      drag.moving = true;
                      setDraggedSessionId(drag.id);
                      const row = event.currentTarget.closest<HTMLElement>(".terminal-tabs");
                      if (!row) return;
                      const rect = row.getBoundingClientRect();
                      if (event.clientX < rect.left + 32) row.scrollLeft -= 20;
                      else if (event.clientX > rect.right - 32) row.scrollLeft += 20;
                      const target = tabDropTarget(event.clientX, Array.from(row.querySelectorAll<HTMLElement>(".terminal-tab")).map(tab => {
                        const bounds = tab.getBoundingClientRect(); return { id: tab.dataset.sessionId!, left: bounds.left, right: bounds.right };
                      }), drag.id);
                      drag.target = target?.id ?? "";
                      drag.after = target?.after ?? false;
                      setDropTargetId(drag.target);
                      setDropAfter(drag.after);
                    }}
                    onPointerUp={(event) => {
                      const drag = tabPointer.current;
                      tabPointer.current = null;
                      if (drag?.moving) {
                        suppressTabClick.current = true;
                        setSessions((current) => reorderSessions(current, drag.id, drag.target, drag.after));
                      }
                      event.currentTarget.releasePointerCapture?.(event.pointerId);
                      setDraggedSessionId("");
                      setDropTargetId("");
                    }}
                    onPointerCancel={() => { tabPointer.current = null; setDraggedSessionId(""); setDropTargetId(""); }}>
                    <button className="terminal-tab-select" role="tab" aria-selected={session.id === selectedSessionId} onClick={() => {
                      if (suppressTabClick.current) { suppressTabClick.current = false; return; }
                      selectSession(session.id);
                    }}>
                      <span className={`tab-state ${session.exitCode === null ? "running" : "ended"}`} />
                      <span className="tab-label" title={`${session.title}\n${session.workspace}\n配置：${session.profileName}`}>
                        {session.client === "codex" ? "Codex" : "Claude Code"} · {modeLabel(session.mode)}
                        <small>{pathBasename(session.workspace)} · {session.model}</small>
                      </span>
                    </button>
                    <button className="tab-close" title={closingIds.includes(session.id) ? "正在关闭…" : "关闭会话标签"} disabled={closingIds.includes(session.id)} onClick={() => closeSession(session)}><X size={13} /></button>
                  </div>
                ))}
              </div>
            )}

            <div className="terminal-stage">
              {!sessions.length && (
                <div className="terminal-empty">
                  <div className="empty-icon"><TerminalSquare size={22} /></div>
                  <strong>准备运行</strong>
                  <span>{launchScope === "system" ? "选择项目目录，使用系统配置启动。" : "选择模型和项目目录，启动一个终端工作会话。"}</span>
                  {launchScope === "workbench" && !selectedProfile && <button onClick={() => setShowProfileDialog(true)}><Plus size={14} />添加调用配置</button>}
                </div>
              )}
              {sessions.map((session) => (
                <TerminalPane
                  key={session.id}
                  session={session}
                  active={session.id === selectedSessionId}
                  theme={appearance.resolved}
                  register={registerTerminal}
                  unregister={unregisterTerminal}
                  canReceiveInput={canReceiveInput}
                />
              ))}
            </div>
          </section>

          <footer className="statusbar">
            <div className={`status-message ${error ? "error" : ""}`}>
              <span className="status-dot" />{error || (closingIds.length > 0 ? "正在关闭终端…" : activeSession?.exitCode ? "终端退出码 " + activeSession.exitCode : notice) || (loaded ? "就绪" : "正在读取本机配置…")}
            </div>
            {launchScope === "workbench" && <UsageStatus usage={usage} error={usageError} busy={isLoadingUsage} refresh={() => void refreshUsage()} profileId={config.selected_profile_id} />}
            <span className="config-location">{configPath ? `配置文件 · ${configPath}` : "本机配置"}</span>
          </footer>
        </main>
      </div>

      {reasoningTarget && <CurrentReasoningDialog client={reasoningTarget.client} close={() => setReasoningTarget(null)} insert={(command) => {
        const session = sessionsRef.current.find((item) => item.id === reasoningTarget.id);
        const terminal = terminalRefs.current.get(reasoningTarget.id);
        if (currentSessionId.current !== reasoningTarget.id || !session || session.exitCode !== null || !terminal || closing.current.has(session.id)) {
          setError("目标终端已切换或结束，请重新打开思考强度入口。"); return false;
        }
        terminal.paste(command);
        terminal.focus?.();
        setNotice(`已填入 ${command}；确认输入框内容后按回车，生效情况由 CLI 确认。`);
        return true;
      }} />}
      {showTools && <ToolBridge close={() => setShowTools(false)} />}
      {loaded && showPermissions && <AgentPermissionDialog settings={agentPermissions} close={() => setShowPermissions(false)} save={(settings: AgentPermissions) => {
        try {
          window.localStorage.setItem(permissionStorageKey, JSON.stringify(settings));
          setAgentPermissions(settings);
          void invoke("sync_remote_permissions", { permissions: settings }).catch(cause => setError(String(cause)));
          setShowPermissions(false);
          setNotice("已保存权限设置；之后新建、恢复和分支的工作台会话使用此设置。");
        } catch { setError("无法保存权限设置，请重试。"); }
      }} />}
      {historyOpen && (
        <div className="dialog-backdrop" onKeyDown={(event) => { if (event.key === "Escape") setHistoryOpen(false); }}>
          <section className="profile-dialog history-dialog" role="dialog" aria-label="本地对话历史" aria-modal="true">
            <div className="history-heading-row">
              <div>
                <h2>{historyScope === "global" ? "恢复最近对话" : "从当前项目的历史创建分支"}</h2>
                <small>{historyScope === "workspace" ? config.workspace + " · 分支使用当前调用配置与模型" : "点击任意对话，用原配置恢复。"}</small>
              </div>
            </div>
            <div className="segmented-control history-view-switch" role="group" aria-label="归档状态">
              <button aria-pressed={!historyArchived} onClick={() => setHistoryArchived(false)}>未归档</button>
              <button aria-pressed={historyArchived} onClick={() => setHistoryArchived(true)}>已归档</button>
            </div>
            {historyScope === "global" && <div className="segmented-control history-view-switch" role="group" aria-label="浏览方式">
              <button aria-pressed={!historyByDirectory} onClick={() => setHistoryByDirectory(false)}>最近</button>
              <button aria-pressed={historyByDirectory} onClick={() => setHistoryByDirectory(true)}>按目录</button>
            </div>}
            <label className="dialog-field">
              搜索对话
              <input autoFocus value={historyQuery} onChange={(event) => setHistoryQuery(event.currentTarget.value)}
                placeholder="标题、客户端、模型或项目" />
            </label>
            <div className="history-list">
              {historyLoading ? <p>正在读取本地会话…</p> : historyError ? <p role="alert">{historyError}</p>
                : <HistoryOutline
                    rows={matchingHistory.map((entry) => ({ entry, path: historyByDirectory ? historyPathParts(entry.workspace) : [] }))}
                    profiles={config.profiles}
                    onSelect={selectHistory}
                    onArchive={(entry) => void archiveHistory(entry)}
                    archiving={Boolean(historyArchiveBusy)}
                  />}
              {!historyLoading && !historyError && !matchingHistory.length && <p>暂无可恢复的本地对话。</p>}
            </div>
            <div className="dialog-actions"><button className="secondary-button" onClick={() => setHistoryOpen(false)}>关闭</button></div>
          </section>
        </div>
      )}
      {showSSHConnection && <SSHConnection close={() => setShowSSHConnection(false)} />}
      {editingConnection && <ProfileDialog initial={editingConnection}
        onClose={() => setEditingConnection(null)} onError={setError}
        onSave={async (_name, gateway, _key, read_models, model_catalog_url) => {
          const response = await invoke<AddProfileResponse>("update_profile_connection", { request: {
            profile_id: editingConnection.id, gateway, model_catalog_url, read_models,
          } });
          const selected = config.selected_profile_id === response.profile.id;
          if (selected) {
            addedCatalog.current = { id: response.profile.id, reload: modelReload, catalog: response };
            setModels(response.models); setCatalogNotice(response.catalog_notice ?? ""); setModelLoadError("");
          }
          setConfig((current) => ({ ...current, profiles: current.profiles.map((p) => p.id === response.profile.id ? response.profile : p) }));
          setEditingConnection(null);
          setNotice("连接设置已保存；后续启动使用新调用地址，已运行终端保留原配置。" + (response.catalog_notice || ""));
        }} />}
      {showProfileDialog && (
        <ProfileDialog
          onClose={() => setShowProfileDialog(false)}
          onSave={async (name, gateway, key, read_models, model_catalog_url) => {
            setError("");
            const response = await invoke<AddProfileResponse>("add_profile", { request: { name, gateway, key, read_models, model_catalog_url } });
            addedCatalog.current = { id: response.profile.id, reload: modelReload, catalog: response };
            setConfig((current) => ({
              ...current,
              profiles: [...current.profiles, response.profile],
              selected_profile_id: response.profile.id,
              model: response.models[0] ?? "",
            }));
            setModels(response.models);
            setCatalogNotice(response.catalog_notice ?? "");
            setModelQuery("");
            setShowProfileDialog(false);
            setNotice(response.catalog_notice || `已保存调用配置，读取到 ${response.models.length} 个模型；未发起推理。`);
          }}
          onError={(message) => setError(message)}
        />
      )}
    </div>
  );
}

function historyPathParts(workspace: string): string[] {
  // Keep the server and share together as the root for UNC paths.
  const unc = workspace.startsWith("\\\\");
  const parts = workspace.split(/[\\/]+/).filter(Boolean);
  return unc && parts.length >= 2 ? ["\\\\" + parts[0] + "\\" + parts[1], ...parts.slice(2)] : parts;
}

function HistoryOutline({ rows, profiles, onSelect, onArchive, archiving }: {
  rows: { entry: LocalConversation; path: string[] }[];
  profiles: KeyProfile[];
  onSelect: (entry: LocalConversation) => void;
  onArchive: (entry: LocalConversation) => void;
  archiving: boolean;
}) {
  const folders = new Map<string, typeof rows>();
  for (const row of rows) {
    const folder = row.path[0];
    if (!folder) continue;
    const group = folders.get(folder) ?? [];
    group.push(row);
    folders.set(folder, group);
  }
  return <>
    {[...folders].sort(([a], [b]) => a.localeCompare(b)).map(([folder, group]) => {
      const parts = [folder];
      for (const part of group[0].path.slice(1)) {
        if (!group.every((row) => row.path[parts.length] === part)) break;
        parts.push(part);
      }
      const label = parts.join("\\");
      return <details key={label} className="history-directory">
        <summary title={label}><FolderOpen size={16} /> <strong>{label}</strong></summary>
        <div className="history-directory-children">
          <HistoryOutline rows={group.map((row) => ({ ...row, path: row.path.slice(parts.length) }))}
            profiles={profiles} onSelect={onSelect} onArchive={onArchive} archiving={archiving} />
        </div>
      </details>;
    })}
    {rows.filter((row) => !row.path.length).map(({ entry }) => (
      <div className="history-row" key={entry.history_ref}><button onClick={() => onSelect(entry)}>
        <strong>{entry.title}</strong>
        <small>{entry.client === "codex" ? "Codex" : "Claude"} · {entry.model || "模型未知"} · {entry.scope === "global" ? "系统 CLI" : (profiles.find((profile) => profile.id === entry.profile_id)?.name ?? (entry.profile_name || "来源配置未知"))} · {new Date(entry.updated_at * 1000).toLocaleString()}</small>
        {entry.preview && <small>{entry.preview}</small>}
        <small title={entry.workspace}>{entry.workspace || "项目目录未知"}</small>
      </button>
      <button className="history-archive-button" disabled={archiving} onClick={() => onArchive(entry)}
        aria-label={`${entry.archived ? "取消归档" : "归档"}对话：${entry.title}`}
        title="只改变历史列表显示，保留对话、原配置和运行终端">{entry.archived ? "取消归档" : "归档"}</button></div>
    ))}
  </>;
}

function TerminalPane({
  session,
  active,
  theme,
  register,
  unregister,
  canReceiveInput,
}: {
  session: Session;
  active: boolean;
  theme: ResolvedTheme;
  register: (id: string, terminal: Terminal) => void;
  unregister: (id: string, terminal: Terminal) => void;
  canReceiveInput: (id: string) => boolean;
}) {
  const host = useRef<HTMLDivElement>(null);
  const selected = useRef(active);
  selected.current = active;
  const running = useRef(session.exitCode === null);
  running.current = session.exitCode === null;
  const [pasteError, setPasteError] = useState("");
  const terminalInstance = useRef<Terminal | null>(null);
  const initialTheme = useRef(theme);

  useEffect(() => {
    if (terminalInstance.current) terminalInstance.current.options.theme = terminalTheme(theme);
  }, [theme]);

  useEffect(() => {
    if (!active) host.current?.dispatchEvent(new Event("workbench-input-revoked"));
  }, [active]);

  useEffect(() => {
    if (!host.current) return;
    const terminal = new Terminal({
      allowProposedApi: false,
      cursorBlink: true,
      cursorStyle: "block",
      fontFamily: "Cascadia Code, Consolas, 'Courier New', monospace",
      fontSize: 13,
      minimumContrastRatio: 4.5,
      lineHeight: 1.18,
      scrollback: 5000,
      theme: terminalTheme(initialTheme.current),
    });
    terminalInstance.current = terminal;
    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(host.current);
    const removePaste = installImagePaste(host.current, {
      isCurrent: () => selected.current && running.current && canReceiveInput(session.id),
      stage: (data) => invoke<string>("stage_clipboard_image", { data }),
      paste: (text) => { setPasteError(""); terminal.paste(text); },
      onError: setPasteError,
    });
    register(session.id, terminal);
    const resize = () => {
      if (!host.current || host.current.clientWidth === 0 || host.current.clientHeight === 0) return;
      fitAddon.fit();
      if (isTauri() && terminal.cols > 0 && terminal.rows > 0) {
        invoke("resize_terminal", { sessionId: session.id, cols: terminal.cols, rows: terminal.rows }).catch(() => undefined);
      }
    };
    const observer = new ResizeObserver(resize);
    observer.observe(host.current);
    const input = terminal.onData((data) => {
      if (isTauri()) {
        invoke("write_terminal", { sessionId: session.id, data: Array.from(new TextEncoder().encode(data)) }).catch(() => undefined);
      }
    });
    terminal.writeln(`\x1b[2m${session.title}  ·  ${session.workspace}\x1b[0m`);
    terminal.writeln("");
    if (session.permissions) terminal.writeln("权限：" + session.permissions);
    resize();
    return () => {
      removePaste();
      observer.disconnect();
      input.dispose();
      unregister(session.id, terminal);
      terminalInstance.current = null;
      terminal.dispose();
    };
  }, [register, session.id, session.title, session.workspace, unregister, canReceiveInput]);

  return <div className={`terminal-pane ${active ? "visible" : "hidden"}`} aria-hidden={!active}>
    {pasteError && <div className="terminal-paste-error" role="alert">粘贴图片失败：{pasteError}</div>}
    <div ref={host} style={{ width: "100%", height: "100%" }} />
  </div>;
}

function shellQuotePowerShell(path: string): string {
  return `'${path.replace(/'/g, "''")}'`;
}

function isNativeTauri(): boolean {
  return isTauri()
    && typeof window !== "undefined"
    && "__TAURI_INTERNALS__" in window;
}

function ProfileDialog({
  initial,
  onClose,
  onSave,
  onError,
}: {
  initial?: KeyProfile;
  onClose: () => void;
  onSave: (name: string, gateway: string, key: string, readModels: boolean, modelCatalogURL: string) => Promise<void>;
  onError: (message: string) => void;
}) {
  const [name, setName] = useState(initial?.name ?? "");
  const [gateway, setGateway] = useState(initial?.gateway ?? "http://127.0.0.1:8081");
  const [sharedCatalog, setSharedCatalog] = useState(!initial?.model_catalog_url);
  const [catalogURL, setCatalogURL] = useState(initial?.model_catalog_url ?? "");
  const [key, setKey] = useState("");
  const [readModels, setReadModels] = useState(true);
  const [busy, setBusy] = useState(false);
  const [dialogError, setDialogError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setDialogError("");
    try {
      await onSave(name.trim(), gateway.trim(), key.trim(), readModels, sharedCatalog ? "" : catalogURL.trim());
    } catch (cause) {
      const message = errorMessage(cause);
      setDialogError(message);
      onError(message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="dialog-backdrop" role="presentation" onMouseDown={(event) => event.target === event.currentTarget && !busy && onClose()}>
      <form className="profile-dialog" role="dialog" aria-label={initial ? "连接设置" : "添加调用配置"} onSubmit={submit}>
        <div className="dialog-header">
          <div className="dialog-icon"><KeyRound size={18} /></div>
          <div><h2>{initial ? "连接设置" : "添加调用配置"}</h2><p>{initial ? initial.name + " · 保留已有 Key 和历史" : "使用兼容网关创建可复用的调用配置。"}</p></div>
          <button type="button" className="icon-button" title="关闭" onClick={onClose} disabled={busy}><X size={17} /></button>
        </div>
        {!initial && <label className="dialog-field">配置名称<input autoFocus value={name} onChange={(event) => setName(event.currentTarget.value)} placeholder="例如：AutoBits 免费" maxLength={80} required /></label>}
        <label className="dialog-field">调用 API 基础地址（可含路径）<input value={gateway} onChange={(event) => setGateway(event.currentTarget.value)} placeholder="http://127.0.0.1:8081" required /></label>
        <label><input type="checkbox" checked={sharedCatalog} onChange={(event) => setSharedCatalog(event.currentTarget.checked)} />模型目录与调用共用地址</label>
        {!sharedCatalog && <><label className="dialog-field">模型目录完整 URL<input value={catalogURL} onChange={(event) => setCatalogURL(event.currentTarget.value)} placeholder="https://服务商/api/paas/v4/models" required /></label><p>只向此地址读取模型列表，使用同一把调用 Key；请填写服务商可信地址。推理仍使用上方调用地址。</p></>}
        {!initial && <label className="dialog-field">下游调用 Key<input type="password" value={key} onChange={(event) => setKey(event.currentTarget.value)} autoComplete="new-password" placeholder="不会写入普通配置文件" required /></label>}
        <label><input type="checkbox" checked={readModels} onChange={(event) => setReadModels(event.currentTarget.checked)} />保存时读取模型列表</label>
        <p>支持 /proxy/v1 等 API 前缀；未提供模型列表时可手动填写模型 ID。取消读取时不校验 Key。</p>
        {dialogError && <div className="dialog-error" role="alert">{dialogError}</div>}
        <div className="dialog-security"><KeyRound size={14} />Key 将保存到 Windows 凭据管理器。可选读取模型列表，不发起推理。</div>
        <div className="dialog-actions"><button type="button" className="secondary-button" onClick={onClose} disabled={busy}>取消</button><button type="submit" className="primary-button" disabled={busy || !name.trim() || (!initial && !key.trim()) || (!sharedCatalog && !catalogURL.trim())}>{busy ? "保存中…" : "保存配置"}</button></div>
      </form>
    </div>
  );
}

function gatewayLabel(gateway: string) {
  try {
    const url = new URL(gateway);
    return url.host;
  } catch {
    return gateway;
  }
}

function pathBasename(path: string) {
  const parts = path.split(/[\\/]/).filter(Boolean);
  return parts[parts.length - 1] ?? path;
}

function modeLabel(mode: SessionMode) {
  if (mode === "continue") return "继续";
  if (mode === "resume") return "恢复";
  return "新建";
}

function errorMessage(cause: unknown) {
  if (typeof cause === "string") return cause;
  if (cause instanceof Error) return cause.message;
  return "操作失败，请检查配置后重试。";
}

export default App;
