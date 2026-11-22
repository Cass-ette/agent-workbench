import { useState } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";

export type PermissionMode = "restricted" | "standard" | "full";
export interface AgentPermissions { codex: PermissionMode; claude: PermissionMode }
export const permissionStorageKey = "agentPermissions.v1";

export function readAgentPermissions(): AgentPermissions | null {
  try {
    const value = JSON.parse(window.localStorage.getItem(permissionStorageKey) ?? "null");
    return value && ["restricted", "standard", "full"].includes(value.codex)
      && ["standard", "full"].includes(value.claude) ? value : null;
  } catch { return null; }
}

export function permissionLabel(client: "codex" | "claude", mode: PermissionMode): string {
  if (client === "claude") return mode === "full" ? "跳过工具确认" : "标准审批";
  return mode === "full" ? "完整访问 · 按需审批" : mode === "standard" ? "项目内写入 · 允许联网" : "项目内写入 · 限制联网";
}

export function AgentPermissionDialog({ settings, save, close }: {
  settings: AgentPermissions | null;
  save: (settings: AgentPermissions) => void;
  close: () => void;
}) {
  const [codex, setCodex] = useState<PermissionMode>(settings?.codex ?? "standard");
  const [claude, setClaude] = useState<PermissionMode>(settings?.claude ?? "standard");
  const [confirmed, setConfirmed] = useState(false);
  const [helpError, setHelpError] = useState("");
  const needsConfirmation = (codex === "full" && settings?.codex !== "full")
    || (claude === "full" && settings?.claude !== "full");
  return <div className="dialog-backdrop">
    <section className="profile-dialog permission-dialog" role="dialog" aria-modal="true" aria-label="Agent 权限设置">
      <h2>Agent 权限设置</h2>
      <p>为工作台里的 Codex 和 Claude 选择权限。允许联网统一适用于 Git、下载依赖和其他网络请求。</p>
      <label className="field">Codex 权限
        <select value={codex} onChange={(event) => { setCodex(event.target.value as PermissionMode); setConfirmed(false); }}>
          <option value="standard">项目内写入，允许联网（推荐）</option>
          <option value="restricted">项目内写入，限制联网</option>
          <option value="full">完整访问，保留按需审批</option>
        </select>
      </label>
      <p>项目模式保留文件沙箱，项目外写入等操作由 Codex 按需申请。完整访问关闭这层沙箱，普通命令可能直接操作项目外文件，并非逐条确认。</p>
      <label className="field">Claude 权限
        <select value={claude} onChange={(event) => { setClaude(event.target.value as PermissionMode); setConfirmed(false); }}>
          <option value="standard">标准审批（推荐）</option>
          <option value="full">跳过工具权限确认</option>
        </select>
      </label>
      <p>标准模式按 Claude 自己的规则确认命令、联网与文件操作；它与 Codex 的文件沙箱不同。跳过确认会允许工具直接执行。</p>
      {needsConfirmation && <label className="permission-confirm">
        <input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
        我确认启用上述高权限，理解它可访问当前账号可读写的项目外文件。
      </label>}
      <p>只影响之后新建、恢复和分支的工作台会话。系统 CLI 沿用外部设置。系统隐私目录、管理员权限仍由操作系统授权。</p>
      <button className="secondary-button" onClick={() => void openUrl("https://github.com/Cass-ette/claude-cchp-manager/blob/main/docs/agent-permissions.zh-CN.md").catch(() => setHelpError("无法打开浏览器，请在仓库 docs/agent-permissions.zh-CN.md 查看说明。"))}>权限与工具接入说明（浏览器、技能、MCP）</button>
      {helpError && <p role="alert">{helpError}</p>}
      <div className="dialog-actions">
        <button className="secondary-button" onClick={close}>取消</button>
        <button className="primary-button" disabled={needsConfirmation && !confirmed} onClick={() => save({ codex, claude })}>保存权限设置</button>
      </div>
    </section>
  </div>;
}
