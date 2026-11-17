import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
    expect(screen.getAllByText("待匹配单价 · 可在网关售价中设置来源").length).toBeGreaterThan(0);
    expect(screen.getAllByText("—").length).toBe(2);
    expect(screen.getByText("Claude · test-model")).toBeTruthy();
    expect(screen.queryByText(/\$0\.0000/)).toBeNull();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("明确标注部分会话估算，保留客户端提供的零估算", () => {
    const refresh = vi.fn();
    render(<UsageStatus usage={{ ...usage, estimated_cost: 0, pricing_status: "partial_estimate" }} error="" busy={false} refresh={refresh} />);
    expect(screen.getByText(/部分会话估算 \$0\.0000/)).toBeTruthy();
    fireEvent.click(screen.getByTitle("刷新 Token 用量"));
    expect(refresh).toHaveBeenCalledOnce();
  });

  it("读取失败保留之前的统计并说明状态", () => {
    render(<UsageStatus usage={usage} error="用量读取失败；保留上次结果" busy={false} refresh={vi.fn()} />);
    fireEvent.click(screen.getByTitle("查看隔离会话历史累计"));
    expect(screen.getAllByText("用量读取失败；保留上次结果").length).toBe(2);
    expect(screen.getByText("Claude · test-model")).toBeTruthy();
  });

  it("显示价目表估算区间、覆盖率及价格入口", () => {
    render(<UsageStatus usage={{ ...usage, estimated_cost: 0.1, estimated_cost_upper: 0.2, priced_sessions: 1, pricing_status: "catalog_estimate" }} error="" busy={false} refresh={vi.fn()} />);
    fireEvent.click(screen.getByTitle("查看隔离会话历史累计"));
    expect(screen.getAllByText("网关单价估算 $0.1000–$0.2000").length).toBeGreaterThan(0);
    expect(screen.getByText("已计算 1／1 个会话")).toBeTruthy();
    fireEvent.click(screen.getByText("网关售价"));
    expect(screen.getByText("网关售价目录地址（高级，无需管理 Token）")).toBeTruthy();
  });

  it("参考来源不会展示成确认的网关单价", () => {
    render(<UsageStatus usage={{ ...usage, estimated_cost: 0.1, pricing_status: "reference_estimate",
      sessions: [{ ...usage.sessions[0], estimated_cost: 0.1, cost_source: "reference" }] }} error="" busy={false} refresh={vi.fn()} />);
    fireEvent.click(screen.getByTitle("查看隔离会话历史累计"));
    expect(screen.getAllByText("含参考值估算 $0.1000").length).toBeGreaterThan(0);
    expect(screen.getByText("参考估算 $0.1000")).toBeTruthy();
    expect(screen.queryByText(/网关单价估算/)).toBeNull();
  });

  it("客户端金额和网关计算的混合总额另作标注", () => {
    render(<UsageStatus usage={{ ...usage, estimated_cost: 0.1, pricing_status: "mixed_estimate" }} error="" busy={false} refresh={vi.fn()} />);
    expect(screen.getByText(/混合来源估算 \$0\.1000/)).toBeTruthy();
  });
});
