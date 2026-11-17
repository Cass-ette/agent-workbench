import { useCallback, useEffect, useRef, useState } from "react";
import { invoke, isTauri } from "@tauri-apps/api/core";

export interface PriceQuote {
  model: string;
  charges: Record<string, string>;
  base_charges: Record<string, string>;
  upper_charges?: Record<string, string>;
  ratio: string | null;
  basis: string;
  is_gateway_price?: boolean;
}
interface Prices {
  profile_id: string;
  source_url: string;
  source_override: string;
  fetched_at: number | null;
  stale: boolean;
  ratio: string | number | null;
  quotes: PriceQuote[];
  missing_models: number;
  disclaimer: string;
}
export function useModelPrices(profileId: string, onUpdate: () => void) {
  const [data, setData] = useState<Prices | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const request = useRef(0);
  const callback = useRef(onUpdate);
  callback.current = onUpdate;
  const refresh = useCallback(async (force = false) => {
    if (!profileId || !isTauri()) return;
    const token = ++request.current;
    setBusy(true);
    try {
      const next = await invoke<Prices>("model_pricing", { profileId, force });
      if (token !== request.current || next.profile_id !== profileId) return;
      setData(next); setError(""); callback.current();
    } catch {
      if (token === request.current) setError("价格刷新失败，保留缓存；可指定公开价格来源。");
    } finally { if (token === request.current) setBusy(false); }
  }, [profileId]);
  useEffect(() => {
    setData(null); setError(""); setBusy(false);
    void refresh();
    const timer = window.setInterval(() => void refresh(), 300000);
    return () => { ++request.current; window.clearInterval(timer); };
  }, [refresh]);
  const save = async (url: string) => {
    if (!profileId || !isTauri()) return;
    const token = ++request.current; setBusy(true);
    try {
      await invoke("save_pricing_source", { profileId, url });
      if (token === request.current) { setBusy(false); await refresh(true); }
    } catch { if (token === request.current) setError("公开地址校验失败，未改变价格来源。"); }
    finally { if (token === request.current) setBusy(false); }
  };
  return { data: data?.profile_id === profileId ? data : null, error, busy, refresh, save };
}

export function ModelPrices({ data, error, busy, refresh, save }: ReturnType<typeof useModelPrices>) {
  const [query, setQuery] = useState("");
  const [source, setSource] = useState("");
  useEffect(() => { setSource(data?.source_override || ""); }, [data?.profile_id]);
  const quotes = data?.quotes.filter((q) => q.model.toLowerCase().includes(query.toLowerCase())) || [];
  return <div className="model-prices">
    <div className="usage-dialog-heading"><h3>当前 Key 的网关售价 · USD／百万 Token</h3><button className="secondary-button" disabled={busy} onClick={() => void refresh(true)}>{busy ? "刷新中…" : "刷新价格"}</button></div>
    {data && <><p className="usage-note">{data.quotes.length} 个模型已匹配 · 倍率 {data.ratio == null ? "未公开，仅供参考" : `×${data.ratio}`}</p>
      <p className="usage-note">抓取：{data.fetched_at ? new Date(data.fetched_at * 1000).toLocaleString() : "参考快照"}{data.stale ? " · 缓存／参考价格" : ""}</p>
      <p className="usage-note">{data.source_url}</p>
      {!!data.missing_models && <p className="usage-note">{data.missing_models} 个模型暂无文本单价；图像等计费不按 Token 冒算。</p>}</>}
    <details><summary>网关售价目录地址（高级，无需管理 Token）</summary>
      <input aria-label="公开价格 URL" placeholder="留空自动发现；或填此网关公开售价目录的 URL" value={source} onChange={(e) => setSource(e.target.value)} />
      <button className="secondary-button" disabled={busy} onClick={() => void save(source)}>保存来源</button>
      <p className="usage-note">远程需 HTTPS，本机可用 HTTP；不会附带 Key 或对话。</p>
    </details>
    {error && <p className="dialog-error">{error}</p>}
    <input aria-label="搜索价格模型" placeholder="搜索模型" value={query} onChange={(e) => setQuery(e.target.value)} />
    <div className="price-rows">{quotes.map((q) => <article className="usage-session" key={q.model}>
      <strong>{q.model}</strong><p className="usage-note">{q.basis}</p>
      <table><thead><tr><th>类别</th><th>目录基础价</th><th>{q.is_gateway_price ? "× 倍率 → 网关单价" : "参考值 · 非确认售价"}</th></tr></thead><tbody>
        {([ ["prompt", "输入"], ["completion", "输出"], ["cache_read", "缓存读"], ["cache_write", "缓存写"] ]).map(([key, label]) => <tr key={key}>
          <td>{label}</td><td>{q.base_charges[key] == null ? "—" : `$${q.base_charges[key]}`}</td>
          <td>{q.ratio ?? "未知"} → {q.charges[key] == null ? "—" : `$${q.charges[key]}`}{q.upper_charges?.[key] && q.upper_charges[key] !== q.charges[key] ? `–$${q.upper_charges[key]}` : ""}</td>
        </tr>)}
      </tbody></table>
    </article>)}</div>
    <p className="usage-note">{data?.disclaimer || "正在读取公开单价；不会调用模型。"}</p>
  </div>;
}
