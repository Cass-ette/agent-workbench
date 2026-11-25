import { useState } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";

export type PermissionMode = "restricted" | "standard" | "full" | "dangerous";
export interface AgentPermissions { codex: PermissionMode; claude: PermissionMode }
export const permissionStorageKey = "agentPermissions.v1";

export function readAgentPermissions(): AgentPermissions | null {
  try {
    const value = JSON.parse(window.localStorage.getItem(permissionStorageKey) ?? "null");
    return value && ["restricted", "standard", "full", "dangerous"].includes(value.codex)
      && ["standard", "full", "dangerous"].includes(value.claude) ? value : null;
  } catch { return null; }
}

export function permissionLabel(client: "codex" | "claude", mode: PermissionMode): string {
  if (mode === "dangerous") return "跳过审批 · 无沙箱（危险）";
  if (client === "claude") return mode === "full" ? "跳过工具确认" : "标准审批";
  return mode === "full" ? "完整访问 · 按需审批" : mode === "standard" ? "项目内写入 · 允许联网" : "项目内写入 · 限制联网";
}

export function AgentPermissionDialog({ settings, save, close }: {
  settings: AgentPermissions | null;
  save: (settings: AgentPermissions) => void;
  close: () => void;
}) {
  const [codex, setCodex] = useState<PermissionMode>(settings?.codex === "dangerous" ? "standard" : settings?.codex ?? "standard");
  const [claude, setClaude] = useState<PermissionMode>(settings?.claude === "dangerous" ? "standard" : settings?.claude ?? "standard");
  const [confirmed, setConfirmed] = useState(false);
  const [helpError, setHelpError] = useState("");
  const [dangerous, setDangerous] = useState(settings?.codex === "dangerous" && settings?.claude === "dangerous");
  const [safeCodex, setSafeCodex] = useState<PermissionMode>(settings?.codex === "dangerous" ? "standard" : settings?.codex ?? "standard");
  const [safeClaude, setSafeClaude] = useState<PermissionMode>(settings?.claude === "dangerous" ? "standard" : settings?.claude ?? "standard");
  const needsConfirmation = (codex === "full" && settings?.codex !== "full")
    || (claude === "full" && settings?.claude !== "full");
  return <div className="dialog-backdrop">
    <section className="profile-dialog permission-dialog" role="dialog" aria-modal="true" aria-label="Agent 权限设置">
      <h2>Agent 权限设置</h2>
      <p>为工作台里的 Codex 和 Claude 选择权限。允许联网统一适用于 Git、下载依赖和其他网络请求。</p>
      <label className="permission-confirm dangerous-permission">
        <input type="checkbox" checked={dangerous} onChange={(event) => setDangerous(event.target.checked)} />
        <strong>跳过命令审批并关闭沙箱限制</strong>
      </label>
      <p className="dangerous-warning">高风险：命令直接执行且不受沙箱隔离，范围为当前账号可访问的数据。只影响之后启动的工作台会话；系统 CLI 保持原设置。</p>
      <label className="field">Codex 权限
        <select disabled={dangerous} value={codex} onChange={(event) => { setSafeCodex(event.target.value as PermissionMode); setCodex(event.target.value as PermissionMode); setConfirmed(false); }}>
          <option value="standard">项目内写入，允许联网（推荐）</option>
          <option value="restricted">项目内写入，限制联网</option>
          <option value="full">完整访问，保留按需审批</option>
        </select>
      </label>
      <p>项目模式保留文件沙箱，项目外写入等操作由 Codex 按需申请。完整访问关闭这层沙箱，普通命令可能直接操作项目外文件，并非逐条确认。</p>
      <label className="field">Claude 权限
        <select disabled={dangerous} value={claude} onChange={(event) => { setSafeClaude(event.target.value as PermissionMode); setClaude(event.target.value as PermissionMode); setConfirmed(false); }}>
          <option value="standard">标准审批（推荐）</option>
          <option value="full">跳过工具权限确认</option>
        </select>
      </label>
      <p>标准模式按 Claude 自己的规则确认命令、联网与文件操作；它与 Codex 的文件沙箱不同。跳过确认会允许工具直接执行。</p>
      {needsConfirmation && !dangerous && <label className="permission-confirm">
        <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
        我确认启用上述高权限，理解它可访问当前账号可读写的项目外文件。
      </label>}
      <p>只影响之后新建、恢复和分支的工作台会话。系统 CLI 沿用外部设置。系统隐私目录、管理员权限仍由操作系统授权。</p>
      <button className="secondary-button" onClick={() => void openUrl("https://github.com/Cass-ette/agent-workbench/blob/main/docs/agent-permissions.zh-CN.md").catch(() => setHelpError("无法打开浏览器，请在仓库 docs/agent-permissions.zh-CN.md 查看说明。"))}>权限与工具接入说明（浏览器、技能、MCP）</button>
      {helpError && <p role="alert">{helpError}</p>}
      <div className="dialog-actions">
        <button className="secondary-button" onClick={close}>取消</button>
        <button className="primary-button" disabled={needsConfirmation && !dangerous && !confirmed} onClick={() => save(dangerous ? { codex: "dangerous", claude: "dangerous" } : { codex: safeCodex, claude: safeClaude })}>保存权限设置</button>
      </div>
    </section>
  </div>;
}
