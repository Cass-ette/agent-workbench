import { useState } from "react";

export interface UsagePoint {
  date: string;
  model?: string;
  record_id?: string;
  timestamp?: string;
  session_id?: string;
  client?: string;
  project_path?: string;
  profile_id?: string;
  profile_name?: string;
  input_tokens?: number | null;
  output_tokens?: number | null;
  cached_input_tokens?: number | null;
  cache_creation_input_tokens?: number | null;
  reasoning_output_tokens?: number | null;
  estimated_cost?: number | null;
  unpriced_records?: number;
  cost_source?: string;
  unit_prices?: { model: string; currency: string; unit: string; charges: Record<string, number> }[];
}
interface Session extends Omit<UsagePoint, "date"> {
  id: string; client: string; model: string; workspace: string; profile_id?: string; profile_name?: string; timeline?: UsagePoint[]; usage_records?: UsagePoint[];
}
export function projectMatches(workspace: string, project: string, children = false): boolean {
  if (!project) return true;
  if (project === "__unknown") return !workspace;
  const normalize = (value: string) => value.replace(/\\/g, "/").replace(/\/+$/, "") || "/";
  const root = normalize(project), path = normalize(workspace);
  return !!workspace && (path === root || (children && path.startsWith(root === "/" ? "/" : root + "/")));
}
export function usageRows(session: Session): UsagePoint[] {
  return (session.usage_records ?? session.timeline ?? [{ ...session, date: "" }]).map((r) => ({
    ...r, model: r.model ?? session.model, session_id: r.session_id ?? session.id,
    client: r.client ?? session.client, project_path: r.project_path ?? session.workspace,
    profile_id: r.profile_id ?? session.profile_id ?? "", profile_name: r.profile_name ?? session.profile_name ?? "",
  }));
}
export function usagePoints(sessions: Session[], id: string, lower = "", upper = "", project = "", profile = "", client = "", model = "", children = false, group = "date"): UsagePoint[] {
  const groups = new Map<string, UsagePoint>();
  for (const session of sessions) {
    for (const row of usageRows(session)) {
      if (id && row.session_id !== id) continue;
      if (!projectMatches(row.project_path || "", project, children)) continue;
      if (profile && (profile === "__unknown" ? !!row.profile_id : row.profile_id !== profile)) continue;
      if (client && row.client !== client) continue;
      if (model && row.model !== model) continue;
      if ((lower || upper) && (!row.date || row.date < lower || (upper && row.date > upper))) continue;
      const key = group === "project" ? row.project_path || "未关联项目" : group === "profile" ? row.profile_id || "未识别配置"
        : group === "model" ? row.model || "模型未记录" : group === "client" ? row.client || "客户端未记录"
        : group === "session" ? row.session_id || "会话未记录" : row.date;
      const point = groups.get(key) || { date: key };
      for (const field of ["input_tokens", "output_tokens", "cached_input_tokens", "cache_creation_input_tokens", "reasoning_output_tokens", "estimated_cost", "unpriced_records"] as const) {
        if (row[field] != null) point[field] = (point[field] || 0) + row[field];
      }
      for (const price of row.unit_prices || []) {
        point.unit_prices ??= [];
        if (!point.unit_prices.some((p) => JSON.stringify(p) === JSON.stringify(price))) point.unit_prices.push(price);
      }
      groups.set(key, point);
    }
  }
  return [...groups.values()].sort((a, b) => a.date.localeCompare(b.date));
}
const usd = (cost: number | null) => cost == null ? "未提供" : cost > 0 && cost < .0001 ? "<$0.0001" : "$" + cost.toFixed(4);
export function UsageAnalytics({ sessions }: { sessions: Session[] }) {
  const [session, setSession] = useState("");
  const [project, setProject] = useState("");
  const [children, setChildren] = useState(false);
  const [profile, setProfile] = useState("");
  const [client, setClient] = useState("");
  const [model, setModel] = useState("");
  const [group, setGroup] = useState("date");
  const [range, setRange] = useState("all");
  const [money, setMoney] = useState(false);
  const [series, setSeries] = useState("both");
  const today = new Date().toISOString().slice(0, 10);
  const [start, setStart] = useState(new Date(Date.now() - 29 * 86400000).toISOString().slice(0, 10));
  const [end, setEnd] = useState(today);
  const lower = range === "all" ? "" : range === "custom" ? start : new Date(Date.now() - (Number(range) - 1) * 86400000).toISOString().slice(0, 10);
  const upper = range === "all" ? "" : range === "custom" ? end : today;
  const rows = sessions.flatMap(usageRows);
  const projects = [...new Set(rows.map((r) => r.project_path).filter((v): v is string => !!v))].sort();
  const profiles = [...new Map(rows.filter((r) => r.profile_id).map((r) => [r.profile_id!, r.profile_name || r.profile_id!])).entries()];
  const clients = [...new Set(rows.map((r) => r.client).filter((v): v is string => !!v))].sort();
  const models = [...new Set(rows.map((r) => r.model).filter((v): v is string => !!v))].sort();
  const scopedSessions = sessions.filter((s) => usagePoints([s], "", lower, upper, project, profile, client, model, children).length > 0);
  const points = usagePoints(sessions, session, lower, upper, project, profile, client, model, children, group);
  const hasMoney = points.some((p) => p.estimated_cost != null);
  const showMoney = money && hasMoney;
  const sum = (key: keyof UsagePoint): number | null => {
    const known = points.map((p) => p[key]).filter((v): v is number => typeof v === "number");
    return known.length ? known.reduce((a, b) => a + b, 0) : null;
  };
  const count = (key: keyof UsagePoint) => sum(key)?.toLocaleString() ?? "—";
  const dated = points.filter((p) => p.date);
  const value = (p: UsagePoint) => showMoney ? p.estimated_cost ?? 0 : Math.max(series === "output" ? 0 : p.input_tokens ?? 0, series === "input" ? 0 : p.output_tokens ?? 0);
  const max = Math.max(...dated.map(value), 1e-12);
  const missing = sum("unpriced_records") ?? 0;
  const unknown = group === "date" ? points.find((p) => !p.date) : undefined;
  const unitPrices = [...new Map(points.flatMap((p) => p.unit_prices || []).map((p) => [JSON.stringify(p), p])).values()];
  return <div className="usage-analytics">
    <div className="usage-filters">
      <label>项目目录<select aria-label="统计项目目录" value={project} onChange={(e) => { setProject(e.target.value); setSession(""); }}><option value="">全部项目</option><option value="__unknown">未关联项目</option>{projects.map((p) => <option key={p} value={p}>{p}</option>)}</select></label>
      {project && project !== "__unknown" && <label><span>目录范围</span><span><input type="checkbox" checked={children} onChange={(e) => { setChildren(e.target.checked); setSession(""); }} /> 包含子目录</span></label>}
      <label>配置档案<select aria-label="统计配置档案" value={profile} onChange={(e) => { setProfile(e.target.value); setSession(""); }}><option value="">全部配置</option><option value="__unknown">未识别配置</option>{profiles.map(([id, name]) => <option key={id} value={id}>{name}</option>)}</select></label>
      <label>客户端<select aria-label="统计客户端" value={client} onChange={(e) => { setClient(e.target.value); setSession(""); }}><option value="">全部客户端</option>{clients.map((c) => <option key={c} value={c}>{c}</option>)}</select></label>
      <label>模型<select aria-label="统计模型" value={model} onChange={(e) => { setModel(e.target.value); setSession(""); }}><option value="">全部模型</option>{models.map((m) => <option key={m} value={m}>{m}</option>)}</select></label>
      <label>会话<select aria-label="统计会话" value={session} onChange={(e) => setSession(e.target.value)}><option value="">全部会话</option>{scopedSessions.map((s) => <option key={s.id} value={s.id}>{s.client} · {s.workspace || "未关联项目"} · {s.profile_name || s.profile_id || "未识别配置"} · {s.model || "模型未记录"} · {s.id.slice(-8)}</option>)}</select></label>
      <label>统计视角<select aria-label="统计视角" value={group} onChange={(e) => setGroup(e.target.value)}><option value="date">每日趋势</option><option value="project">按项目</option><option value="profile">按配置</option><option value="model">按模型</option><option value="client">按客户端</option><option value="session">按会话</option></select></label>
      <label>时间<select aria-label="时间范围" value={range} onChange={(e) => setRange(e.target.value)}><option value="all">全部</option><option value="1">今天</option><option value="7">近7天</option><option value="30">近30天</option><option value="custom">自定义</option></select></label>
      {range === "custom" && <><label>开始（UTC）<input aria-label="开始日期" type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label><label>结束（UTC）<input aria-label="结束日期" type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label></>}
    </div>
    {lower && upper && lower > upper && <p className="dialog-error">开始日期不能晚于结束日期</p>}
    <dl className="usage-totals"><div><dt>输入（含缓存）</dt><dd>{count("input_tokens")}</dd></div><div><dt>输出</dt><dd>{count("output_tokens")}</dd></div><div><dt>缓存读取</dt><dd>{count("cached_input_tokens")}</dd></div><div><dt>缓存写入</dt><dd>{count("cache_creation_input_tokens")}</dd></div><div><dt>推理输出</dt><dd>{count("reasoning_output_tokens")}</dd></div></dl>
    <p className="usage-cost">{sum("estimated_cost") == null ? "费用未提供" : (missing ? "已提供部分费用 " : "上游费用／按上游单价计算 ") + usd(sum("estimated_cost"))}</p>
    <div className="usage-tabs"><button className="secondary-button" aria-pressed={!showMoney} onClick={() => setMoney(false)}>Token</button>{points.some((p) => p.estimated_cost != null) && <button className="secondary-button" aria-pressed={showMoney} onClick={() => setMoney(true)}>费用 USD</button>}</div>
    {!showMoney && <><div className="usage-filters"><label>Token 系列<select aria-label="图表系列" value={series} onChange={(e) => setSeries(e.target.value)}><option value="both">输入与输出</option><option value="input">仅看输入</option><option value="output">仅看输出</option></select></label></div><p className="usage-note">{series === "both" ? "输入蓝／输出橙，并列同刻度；量级相差较大时可仅看输出。" : "单独按所选系列缩放；上方用量汇总不变。"}</p></>}
    <div className="usage-chart" role="img" aria-label={(group === "date" ? "每日" : "分组") + (showMoney ? "费用柱状图" : (series === "input" ? "输入" : series === "output" ? "输出" : "输入输出") + "Token柱状图")}>
      <svg width={Math.max(510, dated.length * (group === "date" ? 42 : 95))} height="205" viewBox={"0 0 " + Math.max(510, dated.length * (group === "date" ? 42 : 95)) + " 205"}>
        <text x="0" y="12" fill="currentColor" fontSize="10">{showMoney ? "最高 " + usd(max) : "最高 " + Math.ceil(max).toLocaleString() + " Token"}</text>
        <line x1="0" y1="160" x2={Math.max(510, dated.length * (group === "date" ? 42 : 95))} y2="160" stroke="currentColor" opacity=".3" />
        {dated.map((p, i) => {
          const x = i * (group === "date" ? 42 : 95) + 8, h = 140 * value(p) / max;
          const input = 140 * (p.input_tokens ?? 0) / max, output = 140 * (p.output_tokens ?? 0) / max;
          return <g key={p.date}><title>{p.date} {group === "date" ? "UTC" : ""} · 输入 {p.input_tokens ?? "未知"} · 输出 {p.output_tokens ?? "未知"} · {usd(p.estimated_cost ?? null)}{p.unpriced_records ? " · " + p.unpriced_records + "条缺价" : ""}</title>
            {showMoney ? p.estimated_cost == null ? <text x={x} y="153" fontSize="11" fill="currentColor">未提供</text> : <rect x={x} y={160 - h} width="26" height={h} fill="var(--ctp-peach)" /> : <>
              {series !== "output" && p.input_tokens != null && <rect aria-label={p.date + " 输入 " + p.input_tokens} x={x} y={160 - input} width={series === "both" ? 11 : 26} height={input} fill="var(--ctp-blue)" />}
              {series !== "input" && p.output_tokens != null && <rect aria-label={p.date + " 输出 " + p.output_tokens} x={x + (series === "both" ? 15 : 0)} y={160 - output} width={series === "both" ? 11 : 26} height={output} fill="var(--ctp-peach)" />}
            </>}
            <text transform={"translate(" + (x + 6) + " 176) rotate(35)"} fontSize="9" fill="currentColor">{(group === "profile" ? profiles.find(([id]) => id === p.date)?.[1] || p.date : p.date).slice(-14)}</text>
          </g>;
        })}
      </svg>
    </div>
    {!dated.length && <p className="usage-note">所选范围没有可绘制的记录</p>}
    {unknown && <p className="usage-note">日期未知：输入 {unknown.input_tokens?.toLocaleString() ?? "—"} · 输出 {unknown.output_tokens?.toLocaleString() ?? "—"}；纳入全部，未绘入日期图。</p>}
    {group !== "date" && <div className="usage-group-rows">{points.map((p) => <p className="usage-note" key={p.date}>{group === "profile" ? profiles.find(([id]) => id === p.date)?.[1] || p.date : p.date} · 输入 {p.input_tokens?.toLocaleString() ?? "—"} · 输出 {p.output_tokens?.toLocaleString() ?? "—"} · 费用 {usd(p.estimated_cost ?? null)}</p>)}</div>}
    {session && <p className="usage-note">{sessions.find((s) => s.id === session)?.workspace}</p>}
    {!!unitPrices.length && <details><summary>上游提供的单价 · USD／百万 Token</summary>{unitPrices.map((p, i) => <p className="usage-note" key={i}>{p.model} · 输入 {p.charges.prompt ?? "未提供"} · 输出 {p.charges.completion ?? "未提供"} · 缓存读 {p.charges.cache_read ?? "未提供"}／写 {p.charges.cache_write ?? "未提供"}</p>)}</details>}
    {!!missing && <p className="dialog-error">{missing} 条记录缺少费用或单价；费用合计仅包含已知部分。</p>}
  </div>;
}
