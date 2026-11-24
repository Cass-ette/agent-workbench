import { useId, useState } from "react";

export const effortLevels = ["default", "low", "medium", "high", "xhigh", "max"] as const;
export type Effort = typeof effortLevels[number];
export function isEffort(value: unknown): value is Effort { return effortLevels.includes(value as Effort); }
export const effortLabel = (value: Effort) => value === "default" ? "客户端默认" : value;
export const effortKey = (client: string, profile: string, model: string) => JSON.stringify([client, profile, model]);
export function effortCommand(client: "codex" | "claude", value: Effort): string {
  if (!isEffort(value)) throw new Error("思考强度无效");
  if (value === "default") return client === "codex" ? "/model" : "/effort";
  return client === "codex" ? `/model --reasoning=${value}` : `/effort ${value}`;
}
export function readEffortPreferences(): Record<string, Effort> {
  try {
    const value = JSON.parse(window.localStorage.getItem("reasoning.preferences") ?? "{}");
    if (!value || typeof value !== "object" || Array.isArray(value)) return {};
    return Object.fromEntries(Object.entries(value).filter(([, effort]) => isEffort(effort))) as Record<string, Effort>;
  } catch { return {}; }
}

export function ReasoningSlider({ value, onChange, label }: { value: Effort; onChange: (value: Effort) => void; label: string }) {
  const id = useId();
  return <div className="reasoning-control">
    <label htmlFor={id}>{label}<strong>{effortLabel(value)}</strong></label>
    <input id={id} type="range" min="0" max={effortLevels.length - 1} step="1" value={effortLevels.indexOf(value)}
      aria-valuetext={effortLabel(value)} onChange={(event) => onChange(effortLevels[Number(event.currentTarget.value)])} />
    <div className="reasoning-ticks">{effortLevels.map((level) => <button key={level} type="button" aria-pressed={value === level}
      onClick={() => onChange(level)}>{level === "default" ? "默认" : level}</button>)}</div>
    <small>通用档位；具体支持范围由客户端、模型和上游决定。较高强度通常需要更多时间和 Token。</small>
  </div>;
}

export function CurrentReasoningDialog({ client, insert, close }: { client: "codex" | "claude"; insert: (command: string) => boolean; close: () => void }) {
  const [value, setValue] = useState<Effort>("high");
  const command = effortCommand(client, value);
  return <div className="dialog-backdrop"><section className="profile-dialog" role="dialog" aria-label="当前会话思考强度">
    <h2>当前会话 · {client === "codex" ? "Codex" : "Claude"}</h2>
    <ReasoningSlider value={value} onChange={setValue} label="选择思考强度" />
    <p>明确档位只调整强度，保留模型；客户端默认会打开原生设置。命令会填入当前终端，请在 CLI 输入框确认后按回车；由客户端确认生效。</p>
    <code>{command}</code>
    {client === "codex" && <p>Codex 原生目录未收录的第三方模型可能拒绝 /model。若提示 Model unavailable，请设置该模型的启动默认强度，再自行结束并按原配置恢复对话；无需创建分支。</p>}
    {client === "claude" && <p>Claude 可能把 /effort 选择保存为其默认值；系统 CLI 会话也遵循客户端自身规则。</p>}
    <div className="dialog-actions"><button className="secondary-button" onClick={close}>关闭</button>
      <button className="primary-button" onClick={() => { if (insert(command)) close(); }}>填入当前终端</button></div>
  </section></div>;
}
