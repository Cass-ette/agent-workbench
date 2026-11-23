import { useEffect, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { readAgentPermissions } from "./AgentPermissions";

interface ConnectionInfo {
  name: string;
  user: string;
  addresses: string[];
  host_key: string;
  remember: boolean;
}

export function SSHConnection({ close }: { close: () => void }) {
  const [info, setInfo] = useState<ConnectionInfo>();
  const [host, setHost] = useState("");
  const [port, setPort] = useState("22");
  const [status, setStatus] = useState("");
  const [enabled, setEnabled] = useState(false);
  async function readInfo() {
    try {
      const result = await invoke<ConnectionInfo>("ssh_connection_info");
      setInfo(result);
      if (!host) setHost(result.addresses[0] ?? "");
      setStatus("");
    } catch (error) {
      setStatus(String(error));
    }
  }
  useEffect(() => {
    void readInfo();
    void invoke<{config: {remote_enabled: boolean}}>("load_app_state").then(state => setEnabled(state.config.remote_enabled));
  }, []);
  async function setRemote(value: boolean) {
    try {
      await invoke("set_remote_enabled", { enabled: value, permissions: readAgentPermissions() });
      setEnabled(value);
      setStatus(value ? "手机接续已启用，请保持电脑和工作台运行。" : "已关闭手机接续，电脑终端继续运行。");
    } catch (error) { setStatus(String(error)); }
  }

  async function copyCode() {
    if (!info) return;
    const address = host.trim().replace(/^\[|\]$/g, "");
    if (!/^(?:[A-Za-z0-9][A-Za-z0-9._-]{0,252}|[0-9a-f:]*:[0-9a-f:]+)$/i.test(address)
        || !/^\d+$/.test(port) || Number(port) < 1 || Number(port) > 65535) {
      setStatus("请填写有效的地址和端口。");
      return;
    }
    const code = JSON.stringify({
      name: info.name, host: address, user: info.user, port: Number(port),
      host_key: info.host_key, platform: "windows", remember: info.remember,
    });
    try {
      await navigator.clipboard.writeText("workbench-ssh-v1:" + btoa(String.fromCharCode(...new TextEncoder().encode(code))));
      setStatus("已复制。到手机工作台的「运行环境 → 添加电脑」粘贴。");
    } catch {
      setStatus("无法写入剪贴板。");
    }
  }

  return <div className="dialog-backdrop" onKeyDown={(event) => { if (event.key === "Escape") close(); }}>
    <section className="profile-dialog" role="dialog" aria-modal="true" aria-label="手机连接这台电脑">
      <h2>手机连接这台电脑</h2>
      <p>在 Windows 可选功能中安装 OpenSSH 服务器，然后启动 sshd 服务。</p>
      <label><input type="checkbox" checked={enabled} onChange={e => void setRemote(e.target.checked)} />允许通过 SSH 接续电脑工作台</label>
      <p>手机可查看和接续电脑会话，断开不结束任务。复制内容可能被跨设备剪贴板同步。</p>
      {info && <>
        <label className="dialog-field">手机可达的地址
          <input list="ssh-addresses" value={host} onChange={(event) => setHost(event.target.value)} />
          <datalist id="ssh-addresses">{info.addresses.map((address) => <option key={address} value={address} />)}</datalist>
        </label>
        <label className="dialog-field">SSH 端口<input value={port} onChange={(event) => setPort(event.target.value)} /></label>
        <p>账号 {info.user}。同一网络可用局域网地址，异地可填 Tailscale 地址。</p>
        {!info.remember && <p>Windows 管理员账号通过密码登录。普通账号支持记住此手机。</p>}
      </>}
      {status && <p role="status">{status}</p>}
      <div className="dialog-actions">
        <button className="secondary-button" onClick={close}>关闭</button>
        {!info && <button className="secondary-button" onClick={() => void readInfo()}>重新读取</button>}
        <button className="primary-button" disabled={!info} onClick={() => void copyCode()}>复制连接码</button>
      </div>
    </section>
  </div>;
}
