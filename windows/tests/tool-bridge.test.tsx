import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ToolBridge } from "../src/ToolBridge";

const { invoke } = vi.hoisted(() => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({ invoke }));
vi.mock("@tauri-apps/plugin-dialog", () => ({ open: vi.fn() }));
afterEach(() => { cleanup(); invoke.mockReset(); });

it("工具保存须明确确认，修改配置后重新确认；不会操作系统客户端设置", async () => {
  const value = { config: '{"mcpServers":{}}', screen: false, items: [] };
  invoke.mockResolvedValue(value);
  render(<ToolBridge close={() => {}} />);
  const editor = await screen.findByLabelText("MCP 服务 JSON");
  const save = screen.getByRole<HTMLButtonElement>("button", { name: "保存工具设置" });
  expect(save.disabled).toBe(true);
  fireEvent.click(screen.getByLabelText("我已核对服务程序、地址和组件，允许后续工作台会话加载它们。"));
  expect(save.disabled).toBe(false);
  fireEvent.change(editor, { target: { value: '{"mcpServers":{"demo":{"command":"test"}}}' } });
  expect(save.disabled).toBe(true);
  fireEvent.click(screen.getByLabelText("我已核对服务程序、地址和组件，允许后续工作台会话加载它们。"));
  fireEvent.click(save);
  await screen.findByText("已保存；只影响之后新建、恢复和分支的工作台会话。");
  expect(invoke).toHaveBeenCalledWith("tools_save", { client: "codex", config: '{"mcpServers":{"demo":{"command":"test"}}}', screen: false, disabled: [] });
  expect(invoke.mock.calls.every(([name]) => ["tools_get", "tools_save"].includes(name))).toBe(true);
});
