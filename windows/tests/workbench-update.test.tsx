import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { WorkbenchUpdate } from "../src/WorkbenchUpdate";
import { version } from "../package.json";

const { invoke } = vi.hoisted(() => ({ invoke: vi.fn() }));
vi.mock("@tauri-apps/api/core", () => ({
  invoke,
  isTauri: () => true,
  Channel: class {},
}));
vi.mock("@tauri-apps/plugin-opener", () => ({ openUrl: vi.fn() }));

const update = { package: { version: "9.0.1", sha256: "a".repeat(64) }, notes: "新版说明" };
const cache = "workbench.update." + version;

beforeEach(() => {
  localStorage.clear();
  invoke.mockReset();
});
afterEach(cleanup);

it("没有新版或检查失败时仍保留常驻检查更新入口", async () => {
  invoke.mockResolvedValueOnce(null).mockRejectedValueOnce("无法读取更新（HTTP 403）。");
  render(<WorkbenchUpdate running={0} />);
  await waitFor(() => expect(localStorage.getItem(cache + ".next")).not.toBeNull());
  fireEvent.click(screen.getByRole("button", { name: "检查更新" }));
  await screen.findByText("无法读取更新（HTTP 403）。");
  fireEvent.click(screen.getByRole("button", { name: "稍后" }));
  expect(screen.getByRole("button", { name: "检查更新" })).toBeTruthy();
});

it("检查失败时保留已发现版本的更新入口", async () => {
  localStorage.setItem(cache + ".available", JSON.stringify(update));
  invoke.mockRejectedValueOnce("更新连接失败");
  render(<WorkbenchUpdate running={0} />);
  await waitFor(() => expect(localStorage.getItem(cache + ".next")).not.toBeNull());
  expect(screen.getByRole("button", { name: "新版 9.0.1" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "新版 9.0.1" }));
  expect(screen.getByText("新版说明")).toBeTruthy();
});

it("缓存到期后刷新已经显示的版本提示", async () => {
  localStorage.setItem(cache + ".available", JSON.stringify(update));
  localStorage.setItem(cache + ".next", String(Date.now() - 1));
  invoke.mockResolvedValueOnce({ ...update, package: { ...update.package, version: "9.0.2" } });

  render(<WorkbenchUpdate running={0} />);

  expect(screen.getByRole("button", { name: "新版 9.0.1" })).toBeTruthy();
  await screen.findByRole("button", { name: "新版 9.0.2" });
  expect(invoke).toHaveBeenCalledWith("check_workbench_update");
});

it("有运行中的终端时不安装，安装包丢失时可以重新下载", async () => {
  invoke.mockImplementation(async (command: string) => {
    if (command === "check_workbench_update") return update;
    if (command === "install_workbench_update") {
      throw { message: "安装包已丢失或校验失败，请重新下载。", redownload: true };
    }
  });
  const { rerender } = render(<WorkbenchUpdate running={1} />);
  fireEvent.click(await screen.findByRole("button", { name: "新版 9.0.1" }));
  fireEvent.click(screen.getByRole("button", { name: "下载更新" }));
  fireEvent.click(await screen.findByRole("button", { name: "退出并安装" }));
  await screen.findByText("还有 1 个终端正在运行。请在任务完成后回来安装。");
  expect(invoke.mock.calls.some(([command]) => command === "install_workbench_update")).toBe(false);

  rerender(<WorkbenchUpdate running={0} />);
  fireEvent.click(screen.getByRole("button", { name: "退出并安装" }));
  await screen.findByText("安装包已丢失或校验失败，请重新下载。");
  fireEvent.click(screen.getByRole("button", { name: "下载更新" }));
  await screen.findByRole("button", { name: "退出并安装" });
  expect(invoke.mock.calls.filter(([command]) => command === "download_workbench_update")).toHaveLength(2);
});

it("测试版显示标记并允许下载", async () => {
  invoke.mockResolvedValueOnce({ ...update, package: { ...update.package, version: "0.1.16", channel: "stable" } });
  render(<WorkbenchUpdate running={0} />);
  fireEvent.click(await screen.findByRole("button", { name: "测试版 0.1.16" }));
  expect(screen.getByText("测试版 · 可下载安装")).toBeTruthy();
  expect(screen.getByRole("button", { name: "下载更新" })).toBeTruthy();
});
