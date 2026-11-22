import { useEffect, useRef, useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { open } from "@tauri-apps/plugin-dialog";

interface Item { id: string; name: string; kind: string; enabled: boolean; command?: string }
interface Settings { config: string; screen: boolean; items: Item[] }

export function ToolBridge({ close }: { close: () => void }) {
  const dialog = useRef<HTMLElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    dialog.current?.focus();
    return () => { previous?.focus(); };
  }, []);
  const [client, setClient] = useState("codex");
  const [config, setConfig] = useState('{"mcpServers": {}}');
  const [screen, setScreen] = useState(false);
  const [items, setItems] = useState<Item[]>([]);
  const [disabled, setDisabled] = useState<string[]>([]);
  const [message, setMessage] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [loaded, setLoaded] = useState(false);
  function accept(value: Settings) {
    setConfig(value.config); setScreen(value.screen); setItems(value.items);
    setDisabled(value.items.filter(i => !i.enabled).map(i => i.id)); setConfirmed(false); setLoaded(true);
  }
  useEffect(() => {
    let active = true; setLoaded(false); setBusy(true); setMessage("");
    void invoke<Settings>("tools_get", { client }).then(value => { if (active) accept(value); })
      .catch(error => { if (active) setMessage(String(error)); }).finally(() => { if (active) setBusy(false); });
    return () => { active = false; };
  }, [client]);
  async function importDirectory(kind: string) {
    setBusy(true);
    try {
      const path = await open({ directory: true, multiple: false, title: kind === "skill" ? "选择包含 SKILL.md 的目录" : "选择包含 .claude-plugin/plugin.json 的目录" });
      if (typeof path !== "string") return;
      const value = await invoke<Settings>("tools_import", { client, path, kind });
      const added = value.items.filter(item => !items.some(known => known.id === item.id)).map(item => item.id);
      setItems(value.items); setDisabled(previous => [...previous, ...added]); setConfirmed(false);
      setMessage("已复制到私有目录，默认停用。勾选后保存才能加载。");
    }
    catch (error) { setMessage(String(error)); } finally { setBusy(false); }
  }
  return <div className="dialog-backdrop"><section ref={dialog} tabIndex={-1} className="profile-dialog permission-dialog" role="dialog" aria-modal="true" aria-label="工具与插件" onKeyDown={event => {
    if (event.key === "Escape" && !busy) { event.preventDefault(); close(); }
    if (event.key !== "Tab") return;
    const fields = Array.from(event.currentTarget.querySelectorAll<HTMLElement>("button, select, textarea, input")).filter(field => !field.matches(":disabled"));
    const first = fields[0], last = fields.at(-1);
    if (!first) { event.preventDefault(); event.currentTarget.focus(); }
    else if (event.shiftKey && (document.activeElement === first || document.activeElement === event.currentTarget)) { event.preventDefault(); last?.focus(); }
    else if (!event.shiftKey && (document.activeElement === last || document.activeElement === event.currentTarget)) { event.preventDefault(); first.focus(); }
  }}>
    <h2>工具与插件</h2>
    <p>仅配置工作台会话。外部客户端的模型、认证、历史和工具设置保持原样。</p>
    <fieldset disabled={busy} style={{ border: 0, padding: 0 }}>
      <label className="field">客户端<select value={client} onChange={event => setClient(event.target.value)}><option value="codex">Codex</option><option value="claude">Claude</option></select></label>
      <label className="field">MCP 服务 JSON<textarea aria-label="MCP 服务 JSON" rows={10} value={config} onChange={event => { setConfig(event.target.value); setConfirmed(false); }} style={{ fontFamily: "monospace", width: "100%" }} /></label>
      <p>支持 mcpServers / mcp_servers 对象。command/args 启动本机服务，url 连接 HTTP 服务。保存不执行程序；后续启动会话才加载。凭据优先用环境变量。</p>
      <label><input type="checkbox" checked={screen} onChange={event => { setScreen(event.target.checked); setConfirmed(false); }} />启用本机屏幕读取</label>
      <p>只按工具调用截取主屏幕，不点击或输入。截图会发给所选模型和网关，需支持图片。只可读取已解锁的交互桌面，不能读取 UAC 安全桌面。启用不代表模型或服务已验证。</p>
      <div className="dialog-actions"><button onClick={() => void importDirectory("skill")}>导入技能目录</button>{client === "claude" && <button onClick={() => void importDirectory("plugin")}>导入 Claude 插件目录</button>}</div>
      {items.map(item => <label key={item.id} style={{ display: "block" }}><input type="checkbox" checked={!disabled.includes(item.id)} onChange={event => { setDisabled(previous => event.target.checked ? previous.filter(id => id !== item.id) : [...previous, item.id]); setConfirmed(false); }} />{item.name} · {item.kind === "skill" ? "技能" : "Claude 插件"}{client === "claude" && item.command && <small> · 调用 /{item.command}</small>}</label>)}
      <p>导入复制单个组件。Claude 插件可能包含 hooks 和启动程序，最小模式的可用能力仍由 CLI 决定。Codex 暂支持 MCP 和 Skills，桌面宿主专属插件不能直接迁入。依赖与账号需在本机准备。停用只影响之后启动的会话。</p>
      <label><input type="checkbox" checked={confirmed} onChange={event => setConfirmed(event.target.checked)} />我已核对服务程序、地址和组件，允许后续工作台会话加载它们。</label>
    </fieldset>
    {message && <p role="status">{message}</p>}
    <div className="dialog-actions"><button onClick={close} disabled={busy}>关闭</button><button disabled={!loaded || busy || !confirmed} onClick={async () => {
      setBusy(true);
      try { accept(await invoke<Settings>("tools_save", { client, config, screen, disabled })); setMessage("已保存；只影响之后新建、恢复和分支的工作台会话。"); }
      catch (error) { setMessage(String(error)); } finally { setBusy(false); }
    }}>保存工具设置</button></div>
  </section></div>;
}
