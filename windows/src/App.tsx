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
import { UsageStatus } from "./UsagePanel";
import { WorkbenchUpdate } from "./WorkbenchUpdate";
import { SSHConnection } from "./SSHConnection";
import { AgentPermissionDialog, permissionLabel, permissionStorageKey, readAgentPermissions } from "./AgentPermissions";
import { ToolBridge } from "./ToolBridge";
import { installImagePaste } from "./TerminalClipboard";
import type { AgentPermissions } from "./AgentPermissions";
import type { UsageSnapshot } from "./UsagePanel";

type ClientKind = "claude" | "codex";
type SessionMode = "new" | "continue" | "resume";
type LaunchScope = "workbench" | "system";

interface KeyProfile {
  id: string;
  name: string;
  gateway: string;
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

interface AddProfileResponse {
  profile: KeyProfile;
  models: string[];
}

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
  const [selectedHistory, setSelectedHistory] = useState<LocalConversation | null>(null);
  const [sessions, setSessions] = useState<Session[]>([]);
  const [selectedSessionId, setSelectedSessionId] = useState("");
  const currentSessionId = useRef("");
  const [isStarting, setIsStarting] = useState(false);
  const starting = useRef(false);
  const closing = useRef(new Map<string, Promise<boolean>>());
  const [closingIds, setClosingIds] = useState<string[]>([]);
  const [showProfileDialog, setShowProfileDialog] = useState(false);
  const [terminalFullscreen, setTerminalFullscreen] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [usage, setUsage] = useState<UsageSnapshot | null>(null);
  const [isLoadingUsage, setIsLoadingUsage] = useState(false);
  const [usageError, setUsageError] = useState("");
  const usageBusy = useRef(false);
  const terminalRefs = useRef(new Map<string, Terminal>());
  const pendingOutput = useRef(new Map<string, Uint8Array[]>());
  const pendingExits = useRef(new Map<string, number>());

  const selectedProfile = useMemo(
    () => config.profiles.find((profile) => profile.id === config.selected_profile_id),
    [config.profiles, config.selected_profile_id],
  );

  const matchingHistory = history.filter((entry) => [entry.title, entry.preview ?? "", entry.workspace, entry.client, entry.model, entry.profile_name].some(
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
      setModelLoadError("");
      return;
    }
    let cancelled = false;
    setIsLoadingModels(true);
    setModelLoadError("");
    invoke<string[]>("models_for_profile", { profileId: selectedProfile.id })
      .then((items) => {
        if (cancelled) return;
        setModels(items);
        setConfig((current) => ({
          ...current,
          model: !items.length || items.includes(current.model) ? current.model : items[0],
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
  }, [launchScope, loaded, selectedProfile?.id, modelReload]);

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
    setModelLoadError("");
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
      const latest = rows.find((entry) => entry.client === config.client);
      if (!latest) { setError("当前项目暂无该客户端的对话可恢复。"); return; }
      await startSession("resume", latest);
    } catch (cause) {
      setError(errorMessage(cause));
    }
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
        setSessions((current) => current.filter((item) => item.id !== session.id));
        if (currentSessionId.current === session.id) selectSession("");
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
            <div className="brand-caption">LOCAL SESSION MANAGER</div>
          </div>
        </div>
        <div className="topbar-status">
          <span className="status-dot" />本机工作区
          <WorkbenchUpdate running={sessions.filter((session) => session.exitCode === null).length} />
        </div>
      </header>

      <div className="app-body">
        <aside className="sidebar">
          <div className="sidebar-heading">
            <div>
              <span className="eyebrow">CONNECTIONS</span>
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
                      <input id="model-query" value={modelQuery} onChange={(event) => setModelQuery(event.currentTarget.value)} placeholder="搜索模型" disabled={!models.length} />
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
                  {modelLoadError && <span className="model-load-error" role="status">模型列表读取失败：{modelLoadError}</span>}
                  {!modelLoadError && !isLoadingModels && selectedProfile && !models.length && <span className="model-load-note">网关未返回可用模型</span>}
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
                  <div key={session.id} className={`terminal-tab ${session.id === selectedSessionId ? "active" : ""}`}>
                    <button className="terminal-tab-select" role="tab" aria-selected={session.id === selectedSessionId} onClick={() => selectSession(session.id)}>
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
                  />}
              {!historyLoading && !historyError && !matchingHistory.length && <p>暂无可恢复的本地对话。</p>}
            </div>
            <div className="dialog-actions"><button className="secondary-button" onClick={() => setHistoryOpen(false)}>关闭</button></div>
          </section>
        </div>
      )}
      {showSSHConnection && <SSHConnection close={() => setShowSSHConnection(false)} />}
      {showProfileDialog && (
        <ProfileDialog
          onClose={() => setShowProfileDialog(false)}
          onSave={async (name, gateway, key) => {
            setError("");
            const response = await invoke<AddProfileResponse>("add_profile", { request: { name, gateway, key } });
            setConfig((current) => ({
              ...current,
              profiles: [...current.profiles, response.profile],
              selected_profile_id: response.profile.id,
              model: response.models[0] ?? "",
            }));
            setModels(response.models);
            setModelQuery("");
            setShowProfileDialog(false);
            setNotice(`已保存调用配置，读取到 ${response.models.length} 个模型；未发起推理。`);
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

function HistoryOutline({ rows, profiles, onSelect }: {
  rows: { entry: LocalConversation; path: string[] }[];
  profiles: KeyProfile[];
  onSelect: (entry: LocalConversation) => void;
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
            profiles={profiles} onSelect={onSelect} />
        </div>
      </details>;
    })}
    {rows.filter((row) => !row.path.length).map(({ entry }) => (
      <button key={entry.history_ref} onClick={() => onSelect(entry)}>
        <strong>{entry.title}</strong>
        <small>{entry.client === "codex" ? "Codex" : "Claude"} · {entry.model || "模型未知"} · {entry.scope === "global" ? "系统 CLI" : (profiles.find((profile) => profile.id === entry.profile_id)?.name ?? (entry.profile_name || "来源配置未知"))} · {new Date(entry.updated_at * 1000).toLocaleString()}</small>
        {entry.preview && <small>{entry.preview}</small>}
        <small title={entry.workspace}>{entry.workspace || "项目目录未知"}</small>
      </button>
    ))}
  </>;
}

function TerminalPane({
  session,
  active,
  register,
  unregister,
  canReceiveInput,
}: {
  session: Session;
  active: boolean;
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
      lineHeight: 1.18,
      scrollback: 5000,
      theme: {
        background: "#17191c",
        foreground: "#e5e7eb",
        cursor: "#93c5fd",
        selectionBackground: "#3c4658",
        black: "#202329",
        red: "#e16d6d",
        green: "#77b58d",
        yellow: "#d4ad69",
        blue: "#7098c7",
        magenta: "#b38ac4",
        cyan: "#61a9a3",
        white: "#c9cdd4",
        brightBlack: "#666b75",
        brightRed: "#ef8a8a",
        brightGreen: "#91cba4",
        brightYellow: "#e7c783",
        brightBlue: "#8aaedb",
        brightMagenta: "#caa0d8",
        brightCyan: "#80c3bc",
        brightWhite: "#f3f4f6",
      },
    });
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
  onClose,
  onSave,
  onError,
}: {
  onClose: () => void;
  onSave: (name: string, gateway: string, key: string) => Promise<void>;
  onError: (message: string) => void;
}) {
  const [name, setName] = useState("");
  const [gateway, setGateway] = useState("http://127.0.0.1:8081");
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [dialogError, setDialogError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setBusy(true);
    setDialogError("");
    try {
      await onSave(name.trim(), gateway.trim(), key.trim());
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
      <form className="profile-dialog" onSubmit={submit}>
        <div className="dialog-header">
          <div className="dialog-icon"><KeyRound size={18} /></div>
          <div><h2>添加调用配置</h2><p>使用本机网关创建一个可复用的 Key 配置。</p></div>
          <button type="button" className="icon-button" title="关闭" onClick={onClose} disabled={busy}><X size={17} /></button>
        </div>
        <label className="dialog-field">配置名称<input autoFocus value={name} onChange={(event) => setName(event.currentTarget.value)} placeholder="例如：AutoBits 免费" maxLength={80} required /></label>
        <label className="dialog-field">本机或远程网关<input value={gateway} onChange={(event) => setGateway(event.currentTarget.value)} placeholder="http://127.0.0.1:8081" required /></label>
        <label className="dialog-field">下游调用 Key<input type="password" value={key} onChange={(event) => setKey(event.currentTarget.value)} autoComplete="new-password" placeholder="不会写入普通配置文件" required /></label>
        {dialogError && <div className="dialog-error" role="alert">{dialogError}</div>}
        <div className="dialog-security"><KeyRound size={14} />Key 将保存到 Windows 凭据管理器。添加时只读取模型列表，不发起推理。</div>
        <div className="dialog-actions"><button type="button" className="secondary-button" onClick={onClose} disabled={busy}>取消</button><button type="submit" className="primary-button" disabled={busy || !name.trim() || key.trim().length < 20}>{busy ? "校验并保存…" : "校验并保存"}</button></div>
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
