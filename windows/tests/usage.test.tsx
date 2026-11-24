import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { UsageAnalytics, usagePoints, projectMatches } from "../src/UsageAnalytics";
import { UsageStatus } from "../src/UsagePanel";
import type { UsageSnapshot } from "../src/UsagePanel";

afterEach(cleanup);
const usage: UsageSnapshot = {
  input_tokens: 100, output_tokens: 10, cached_input_tokens: 60,
  cache_creation_input_tokens: null, reasoning_output_tokens: null,
  requests: 1, by_source: { claude: 1 }, estimated_cost: null,
  pricing_status: "unavailable", unreadable_files: 0, scanned_at: 1,
  sessions: [{ id: "claude:test", client: "claude", model: "test-model", workspace: "C:\\test",
    input_tokens: 100, output_tokens: 10, cached_input_tokens: 60,
    cache_creation_input_tokens: null, reasoning_output_tokens: null, estimated_cost: null }],
};

describe("Token 与费用", () => {
  it("展开历史会话详情，缺失价格显示待匹配而不假装免费", () => {
    render(<UsageStatus usage={usage} error="" busy={false} refresh={vi.fn()} />);
    fireEvent.click(screen.getByTitle("查看隔离会话历史累计"));
    expect(screen.getByRole("dialog", { name: "Token 与费用" })).toBeTruthy();
    expect(screen.getAllByText("费用未提供").length).toBeGreaterThan(0);
    expect(screen.getAllByText("—").length).toBe(2);
    expect(screen.getByLabelText("统计会话")).toBeTruthy();
    expect(screen.queryByText(/\$0\.0000/)).toBeNull();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("明确标注已提供部分费用，保留上游提供的零费用", () => {
    const refresh = vi.fn();
    render(<UsageStatus usage={{ ...usage, estimated_cost: 0, pricing_status: "partial_upstream" }} error="" busy={false} refresh={refresh} />);
    expect(screen.getByText(/已提供部分费用 \$0\.0000/)).toBeTruthy();
    fireEvent.click(screen.getByTitle("刷新 Token 用量"));
    expect(refresh).toHaveBeenCalledOnce();
  });

  it("读取失败保留之前的统计并说明状态", () => {
    render(<UsageStatus usage={usage} error="用量读取失败；保留上次结果" busy={false} refresh={vi.fn()} />);
    fireEvent.click(screen.getByTitle("查看隔离会话历史累计"));
    expect(screen.getAllByText("用量读取失败；保留上次结果").length).toBe(2);
    expect(screen.getByLabelText("统计会话")).toBeTruthy();
  });

  it("上游明确提供的零费用与未提供不同", () => {
    render(<UsageAnalytics sessions={[{ ...usage.sessions[0], timeline: [{ date: "2026-11-22", model: "test-model", input_tokens: 100, output_tokens: 10, estimated_cost: 0, unpriced_records: 0 }] }]} />);
    expect(screen.getByText("上游费用／按上游单价计算 $0.0000")).toBeTruthy();
    fireEvent.click(screen.getByText("费用 USD"));
    expect(screen.getByRole("img", { name: "每日费用柱状图" })).toBeTruthy();
  });

  it("项目、配置和实际模型可组合筛选，同名目录不会混算", () => {
    const sessions = [
      { ...usage.sessions[0], id: "one", workspace: "/a/project", profile_id: "key-a", profile_name: "配置A", model: "last", timeline: [
        { date: "2026-11-21", model: "first", input_tokens: 100, output_tokens: 10, estimated_cost: null },
        { date: "2026-11-22", model: "last", input_tokens: 200, output_tokens: 20, estimated_cost: 0.2 },
        { date: "", model: "first", input_tokens: 3, output_tokens: 0 },
      ] },
      { ...usage.sessions[0], id: "two", workspace: "/b/project", profile_id: "key-b", profile_name: "配置B", timeline: [{ date: "2026-11-21", model: "first", input_tokens: 900, output_tokens: 90 }] },
    ];
    const rows = usagePoints(sessions, "", "2026-11-21", "2026-11-21", "/a/project", "key-a", "claude", "first");
    expect(rows).toHaveLength(1);
    expect(rows[0].input_tokens).toBe(100);
    expect(rows[0].estimated_cost).toBeUndefined();
    expect(usagePoints(sessions, "", "", "", "/a/project").map((r) => r.input_tokens)).toEqual([3,100,200]);
    render(<UsageAnalytics sessions={sessions} />);
    fireEvent.change(screen.getByLabelText("统计项目目录"), { target: { value: "/a/project" } });
    fireEvent.change(screen.getByLabelText("统计模型"), { target: { value: "first" } });
    expect(screen.getByText("103")).toBeTruthy();
    expect(screen.getByText("费用未提供")).toBeTruthy();
    fireEvent.change(screen.getByLabelText("时间范围"), { target: { value: "custom" } });
    fireEvent.change(screen.getByLabelText("开始日期"), { target: { value: "2026-11-21" } });
    fireEvent.change(screen.getByLabelText("结束日期"), { target: { value: "2026-11-21" } });
    expect(screen.getByText("100")).toBeTruthy();
  });

  it("未知项目与配置可以单独筛选，零费用不会丢失", () => {
    const rows = usagePoints([{ ...usage.sessions[0], workspace: "", profile_id: "", timeline: [{ date: "", input_tokens: 0, output_tokens: 0, estimated_cost: 0 }] }], "", "", "", "__unknown", "__unknown");
    expect(rows[0].estimated_cost).toBe(0);
    expect(projectMatches("C:\\root\\project\\sub", "C:\\root\\project", false)).toBe(false);
    expect(projectMatches("C:\\root\\project\\sub", "C:\\root\\project", true)).toBe(true);
    expect(projectMatches("C:\\root\\project-other", "C:\\root\\project", true)).toBe(false);
  });

  it("同一会话用统一调用明细按项目、配置或模型聚合，不归给末次配置", () => {
    const sessions = [{ ...usage.sessions[0], id: "s", model: "last", profile_id: "last-key", workspace: "/last", usage_records: [
      { date: "2026-11-21", record_id: "s:a", session_id: "s", client: "claude", project_path: "/a", profile_id: "a", profile_name: "配置A", model: "first", input_tokens: 100, output_tokens: 10, estimated_cost: 0 },
      { date: "2026-11-21", record_id: "s:b", session_id: "s", client: "claude", project_path: "/b", profile_id: "b", profile_name: "配置B", model: "last", input_tokens: 200, output_tokens: 20, estimated_cost: null },
    ] }];
    expect(usagePoints(sessions,"","","","/a","a","claude","first")[0].input_tokens).toBe(100);
    const grouped = usagePoints(sessions,"","","","","","","",false,"project");
    expect(grouped.map((p) => [p.date,p.input_tokens])).toEqual([["/a",100],["/b",200]]);
    render(<UsageAnalytics sessions={sessions} />);
    fireEvent.change(screen.getByLabelText("统计视角"), { target: { value: "profile" } });
    expect(screen.getByText(/配置A · 输入 100/)).toBeTruthy();
    expect(screen.getByText(/配置B · 输入 200/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("统计项目目录"), { target: { value: "/a" } });
    fireEvent.change(screen.getByLabelText("统计配置档案"), { target: { value: "a" } });
    fireEvent.change(screen.getByLabelText("统计模型"), { target: { value: "first" } });
    const picker = screen.getByLabelText("统计会话") as HTMLSelectElement;
    expect(Array.from(picker.options).map((o) => o.value)).toEqual(["", "s"]);
    fireEvent.change(picker, { target: { value: "s" } });
    expect(screen.getByText(/配置A · 输入 100/)).toBeTruthy();
  });
});
