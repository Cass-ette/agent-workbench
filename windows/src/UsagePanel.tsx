import { useEffect, useState } from "react";
import { RefreshCw, X } from "lucide-react";
import { ModelPrices, useModelPrices } from "./ModelPrices";

interface UsageCounts {
  input_tokens: number | null;
  output_tokens: number | null;
  cached_input_tokens: number | null;
  cache_creation_input_tokens: number | null;
  reasoning_output_tokens: number | null;
}

interface UsageSession extends UsageCounts {
  id: string;
  client: string;
  model: string;
  workspace: string;
  estimated_cost: number | null;
  estimated_cost_upper?: number | null;
  pricing_note?: string;
  cost_source?: string;
}

export interface UsageSnapshot extends UsageCounts {
  requests: number;
  by_source: Record<string, number>;
  pricing_status: string;
  estimated_cost: number | null;
  sessions: UsageSession[];
  unreadable_files: number;
  scanned_at: number;
  estimated_cost_upper?: number | null;
  pricing_notice?: string;
  priced_sessions?: number;
  unpriced_sessions?: number;
}

function tokens(value: number | null | undefined): string {
  return value == null ? "—" : value.toLocaleString();
}

function costLabel(cost: number | null | undefined, status = "", upper?: number | null): string {
  const label = status.includes("partial") ? "部分会话估算"
    : status === "reference" ? "参考估算"
    : status.includes("reference") ? "含参考值估算"
    : status.includes("mixed") ? "混合来源估算"
    : status.includes("catalog") ? "网关单价估算" : "客户端估算";
  if (cost == null) return "待匹配单价 · 可在网关售价中设置来源";
  if (upper != null && upper > cost + 1e-8) return `${label} $${cost.toFixed(4)}–$${upper.toFixed(4)}`;
  return `${label} ${cost > 0 && cost < 0.0001 ? "<$0.0001" : `$${cost.toFixed(4)}`}`;
}

export function UsageStatus({ usage, error, busy, refresh, profileId = "" }: {
  usage: UsageSnapshot | null;
  error: string;
  busy: boolean;
  refresh: () => void;
  profileId?: string;
}) {
  const [expanded, setExpanded] = useState(false);
  const [page, setPage] = useState("usage");
  const prices = useModelPrices(profileId, refresh);
  useEffect(() => {
    if (!expanded) return;
    const escape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", escape);
    return () => window.removeEventListener("keydown", escape);
  }, [expanded]);
  const cost = costLabel(usage?.estimated_cost, usage?.pricing_status, usage?.estimated_cost_upper);
  return <div className="usage-status">
    <button className="usage-summary" title="查看隔离会话历史累计" onClick={() => setExpanded(true)}>
      {error || (!usage?.sessions.length ? "Token · 尚无本地记录" : `历史累计 · 输入 ${tokens(usage.input_tokens)} · 输出 ${tokens(usage.output_tokens)} · ${cost}`)}
    </button>
    <button className="usage-refresh" title="刷新 Token 用量" onClick={refresh} disabled={busy}>
      <RefreshCw size={12} className={busy ? "spinning" : ""} />
    </button>
    {expanded && <div className="dialog-backdrop" onMouseDown={(event) => {
      if (event.target === event.currentTarget) setExpanded(false);
    }}>
      <section className="usage-dialog" role="dialog" aria-modal="true" aria-labelledby="usage-title">
        <div className="usage-dialog-heading">
          <h2 id="usage-title">Token 与费用</h2>
          <button className="secondary-button" disabled={busy} onClick={refresh}>刷新</button>
          <button className="icon-button" title="关闭用量详情" onClick={() => setExpanded(false)} autoFocus><X size={16} /></button>
        </div>
        <div className="usage-tabs">
          <button className="secondary-button" onClick={() => setPage("usage")} aria-pressed={page === "usage"}>本地 Token 记录</button>
          <button className="secondary-button" onClick={() => setPage("prices")} aria-pressed={page === "prices"}>网关售价</button>
        </div>
        {page === "prices" ? <ModelPrices {...prices} /> : <>
        <p className="usage-note">隔离会话历史累计 · 恢复会话包含恢复前的记录</p>
        <dl className="usage-totals">
          <div><dt>输入（含缓存）</dt><dd>{tokens(usage?.input_tokens)}</dd></div>
          <div><dt>输出</dt><dd>{tokens(usage?.output_tokens)}</dd></div>
          <div><dt>缓存读取</dt><dd>{tokens(usage?.cached_input_tokens)}</dd></div>
          <div><dt>缓存写入</dt><dd>{tokens(usage?.cache_creation_input_tokens)}</dd></div>
          <div><dt>推理输出</dt><dd>{tokens(usage?.reasoning_output_tokens)}</dd></div>
        </dl>
        <p className="usage-cost">{cost}</p>
        <p className="usage-note">{usage?.pricing_notice || "缓存是输入的细分，推理是输出的细分，无需重复相加。按当前单价估算，实际扣费以网关账单为准。"}</p>
        {usage?.priced_sessions != null && <p className="usage-note">已计算 {usage.priced_sessions}／{usage.sessions.length} 个会话</p>}
        {error && <p className="dialog-error">{error}</p>}
        {!!usage?.unreadable_files && <p className="dialog-error">{usage.unreadable_files} 个记录文件无法读取，统计可能不完整。</p>}
        <div className="usage-sessions">
          {!usage?.sessions.length && <p className="usage-note">尚无可用的本地用量记录</p>}
          {usage?.sessions.map((session) => <article className="usage-session" key={session.id}>
            <strong>{session.client === "codex" ? "Codex" : "Claude"} · {session.model || "模型未记录"}</strong>
            <p className="usage-workspace" title={session.workspace}>{session.workspace || "项目未记录"}</p>
            <p className="usage-session-id">{session.id}</p>
            <p>输入 {tokens(session.input_tokens)} · 输出 {tokens(session.output_tokens)} · 缓存读 {tokens(session.cached_input_tokens)} / 写 {tokens(session.cache_creation_input_tokens)} · 推理 {tokens(session.reasoning_output_tokens)}</p>
            <p className="usage-note">{costLabel(session.estimated_cost, session.cost_source, session.estimated_cost_upper)}</p>
            {session.pricing_note && <p className="usage-note">{session.pricing_note}</p>}
          </article>)}
        </div>
        </>}
      </section>
    </div>}
  </div>;
}
