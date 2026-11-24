import { useEffect, useState } from "react";
import { RefreshCw, X } from "lucide-react";
import { UsageAnalytics, type UsagePoint } from "./UsageAnalytics";

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
  profile_id?: string;
  profile_name?: string;
  estimated_cost: number | null;
  estimated_cost_upper?: number | null;
  pricing_note?: string;
  cost_source?: string;
  timeline?: UsagePoint[];
  usage_records?: UsagePoint[];
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
  const label = status.includes("partial") ? "已提供部分费用"
    : status.includes("upstream_rates") ? "按上游单价计算" : "上游报告费用";
  if (cost == null) return "费用未提供";
  if (upper != null && upper > cost + 1e-8) return `${label} $${cost.toFixed(4)}–$${upper.toFixed(4)}`;
  return `${label} ${cost > 0 && cost < 0.0001 ? "<$0.0001" : `$${cost.toFixed(4)}`}`;
}

export function UsageStatus({ usage, error, busy, refresh, profileId: _profileId = "" }: {
  usage: UsageSnapshot | null;
  error: string;
  busy: boolean;
  refresh: () => void;
  profileId?: string;
}) {
  const [expanded, setExpanded] = useState(false);
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
        <UsageAnalytics sessions={usage?.sessions ?? []} />
        <p className="usage-note">{usage?.pricing_notice || "费用只显示上游明确提供的信息；没有则未提供。日期按 UTC，缓存与推理不重复计入总量。"}</p>
        {error && <p className="dialog-error">{error}</p>}
        {!!usage?.unreadable_files && <p className="dialog-error">{usage.unreadable_files} 个记录文件无法读取，统计可能不完整。</p>}
      </section>
    </div>}
  </div>;
}
