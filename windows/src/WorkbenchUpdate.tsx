import { useCallback, useEffect, useRef, useState } from "react";
import { Channel, invoke, isTauri } from "@tauri-apps/api/core";
import { openUrl } from "@tauri-apps/plugin-opener";
import { version } from "../package.json";

const releasePage = "https://github.com/Cass-ette/agent-workbench/releases";
const key = "workbench.update." + version;

interface Available {
  package: { version: string; sha256: string; channel?: string };
  notes: string;
}

function cached(): Available | null {
  try {
    return JSON.parse(localStorage.getItem(key + ".available") || "null");
  } catch {
    return null;
  }
}

export function WorkbenchUpdate({ running }: { running: number }) {
  const [available, setAvailable] = useState(cached);
  const [visible, setVisible] = useState(false);
  const [state, setState] = useState<"idle" | "checking" | "downloading" | "ready" | "installing">("idle");
  const [progress, setProgress] = useState(0);
  const [status, setStatus] = useState("");
  const activity = useRef<typeof state>("idle");
  const showCheckResult = useRef(false);

  const change = useCallback((value: typeof state) => {
    activity.current = value;
    setState(value);
  }, []);

  const check = useCallback(async (manual = false) => {
    if (activity.current === "checking" && manual) {
      showCheckResult.current = true;
      setStatus("正在检查更新…");
      return;
    }
    if (activity.current !== "idle" || !isTauri()) return;
    if (!manual && (document.visibilityState === "hidden" || Number(localStorage.getItem(key + ".next")) > Date.now())) return;
    change("checking");
    showCheckResult.current = manual;
    if (manual) setStatus("正在检查更新…");
    try {
      const result = await invoke<Available | null>("check_workbench_update");
      setAvailable(result);
      localStorage.setItem(key + ".available", JSON.stringify(result));
      localStorage.setItem(key + ".next", String(Date.now() + 86_400_000));
      if (showCheckResult.current) setStatus(result ? "" : "当前已是最新版本。");
    } catch (error) {
      localStorage.setItem(key + ".next", String(Date.now() + 1_800_000));
      if (showCheckResult.current) setStatus(String(error));
    } finally {
      change("idle");
    }
  }, [change]);

  useEffect(() => {
    void check();
    const timer = window.setInterval(() => void check(), 900_000);
    const onVisibility = () => void check();
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [check]);

  async function download() {
    if (!available || activity.current !== "idle") return;
    const channel = new Channel<number>();
    channel.onmessage = setProgress;
    setProgress(0);
    setStatus("正在下载并校验更新…");
    change("downloading");
    try {
      await invoke("download_workbench_update", { expected: available.package, progress: channel });
      setStatus("安装包已校验，可以安装。");
      change("ready");
    } catch (error) {
      setStatus(String(error));
      change("idle");
    }
  }

  async function install() {
    if (!available || activity.current !== "ready") return;
    if (running > 0) {
      setStatus(`还有 ${running} 个终端正在运行。请在任务完成后回来安装。`);
      return;
    }
    change("installing");
    setStatus("正在打开 Windows 安装程序…");
    try {
      await invoke("install_workbench_update", { expected: available.package });
    } catch (error) {
      const issue = error as { message?: string; redownload?: boolean } | null;
      setStatus(issue?.message ?? String(error));
      change(issue?.redownload ? "idle" : "ready");
    }
  }

  return <>
    <button className="secondary-button update-entry" onClick={() => {
      setVisible(true);
      if (!available) void check(true);
    }}>{available ? `${available.package.version.startsWith("0.") ? "测试版" : "新版"} ${available.package.version}` : "检查更新"}</button>

    {visible && (
      <div className="dialog-backdrop" onKeyDown={(event) => {
        if (event.key === "Escape" && state !== "installing") setVisible(false);
      }}>
        <section className="profile-dialog update-dialog" role="dialog" aria-modal="true" aria-label="工作台更新">
          <h2>工作台更新</h2>
          {available?.package.version.startsWith("0.") && <p>测试版 · 可下载安装</p>}
          <p>当前版本 {version}{available && ` · 新版本 ${available.package.version}`}</p>
          {available && <div className="update-notes">{available.notes || "此版本暂无更新说明。"}</div>}
          {state === "downloading" && <progress aria-label="下载进度" max={100} value={progress} />}
          {state === "ready" && <p>{running > 0 ? `还有 ${running} 个终端正在运行，可以稍后安装。` : "安装程序打开后，工作台将退出。"}</p>}
          {status && <p role="status">{status}</p>}
          <div className="dialog-actions">
            <button className="secondary-button" onClick={() => void openUrl(releasePage).catch((error) => setStatus(String(error)))}>发布页</button>
            <button className="secondary-button" disabled={state === "installing"} onClick={() => setVisible(false)}>稍后</button>
            {state === "downloading" ? <button className="secondary-button" onClick={() => {
              setStatus("正在取消下载…");
              void invoke("cancel_workbench_update").catch((error) => setStatus(String(error)));
            }}>取消下载</button> : state === "ready" ? (
              <button className="primary-button" onClick={() => void install()}>退出并安装</button>
            ) : (
              <>
                <button className="secondary-button" disabled={state !== "idle"} onClick={() => void check(true)}>{available ? "重新检查" : "检查更新"}</button>
                {available && <button className="primary-button" disabled={state !== "idle"} onClick={() => void download()}>下载更新</button>}
              </>
            )}
          </div>
        </section>
      </div>
    )}
  </>;
}
