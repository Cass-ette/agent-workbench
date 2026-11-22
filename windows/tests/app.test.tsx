import { StrictMode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import App from "../src/App";
import { permissionStorageKey } from "../src/AgentPermissions";

const { invoke, onDragDropEvent, terminalPaste } = vi.hoisted(() => ({ invoke: vi.fn(), onDragDropEvent: vi.fn(), terminalPaste: vi.fn() }));

vi.mock("@tauri-apps/api/core", () => ({ isTauri: () => true, invoke }));
vi.mock("@tauri-apps/api/event", () => ({ listen: vi.fn(async () => () => undefined) }));
vi.mock("@tauri-apps/api/webview", () => ({ getCurrentWebview: () => ({ onDragDropEvent }) }));
vi.mock("@tauri-apps/plugin-dialog", () => ({ open: vi.fn() }));
vi.mock("@xterm/xterm", () => ({
  Terminal: class {
    loadAddon() {}
    open() {}
    onData() { return { dispose() {} }; }
    writeln() {}
    paste(text: string) { terminalPaste(text); }
    dispose() {}
  },
}));
vi.mock("@xterm/addon-fit", () => ({ FitAddon: class {} }));

const models = Array.from({ length: 430 }, (_, index) => `test-model-${String(index + 1).padStart(3, "0")}`);

beforeEach(() => {
  window.localStorage.clear();
  window.localStorage.setItem(permissionStorageKey, JSON.stringify({ codex: "standard", claude: "standard" }));
  onDragDropEvent.mockReset().mockResolvedValue(() => undefined);
  terminalPaste.mockReset();
  invoke.mockReset();
  invoke.mockImplementation(async (command: string) => {
    if (command === "load_app_state") {
      return {
        config: {
          profiles: [{ id: "fixture-profile", name: "测试配置", gateway: "http://127.0.0.1:8081" }],
          selected_profile_id: "fixture-profile",
          client: "claude",
          model: models[0],
          workspace: "C:\\test-project",
        },
        config_path: "test-settings.json",
      };
    }
    if (command === "models_for_profile") return models;
    if (command === "list_history" || command === "list_all_history") return [];
    return undefined;
  });
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

async function openApp() {
  render(<StrictMode><App /></StrictMode>);
  await screen.findByText("430 个可用");
  return screen.getAllByRole<HTMLSelectElement>("combobox")[0];
}

describe("工作会话配置", () => {
  it("首次设置可以取消，局部启动仍需明确选择权限", async () => {
    window.localStorage.removeItem(permissionStorageKey);
    await openApp();
    expect(await screen.findByRole("dialog", { name: "Agent 权限设置" })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    fireEvent.click(screen.getByRole("button", { name: "新建会话" }));
    expect(await screen.findByRole("dialog", { name: "Agent 权限设置" })).toBeTruthy();
    expect(invoke.mock.calls.some(([command]) => command === "start_terminal")).toBe(false);
    fireEvent.click(screen.getByRole("button", { name: "保存权限设置" }));
    expect(JSON.parse(window.localStorage.getItem(permissionStorageKey)!)).toEqual({ codex: "standard", claude: "standard" });
  });

  it("提高两客户端权限需要确认，取消不会保存", async () => {
    await openApp();
    fireEvent.click(screen.getByRole("button", { name: "权限设置" }));
    fireEvent.change(screen.getByLabelText("Codex 权限"), { target: { value: "full" } });
    const save = screen.getByRole<HTMLButtonElement>("button", { name: "保存权限设置" });
    expect(save.disabled).toBe(true);
    fireEvent.click(screen.getByRole("checkbox"));
    expect(save.disabled).toBe(false);
    fireEvent.change(screen.getByLabelText("Claude 权限"), { target: { value: "full" } });
    expect(save.disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(JSON.parse(window.localStorage.getItem(permissionStorageKey)!)).toEqual({ codex: "standard", claude: "standard" });
  });
  it("切回较早的对话时已注册的拖放回调立即使用新选中的终端", async () => {
    vi.stubGlobal("__TAURI_INTERNALS__", {});
    vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
    let nextId = 0;
    const normalInvoke = invoke.getMockImplementation()!;
    invoke.mockImplementation(async (command: string, args: unknown) => {
      if (command === "start_terminal") {
        return { id: String(++nextId), title: "Terminal", workspace: "C:\\test-project" };
      }
      return normalInvoke(command, args);
    });
    await openApp();
    const drop = onDragDropEvent.mock.lastCall?.[0];
    expect(drop).toBeTypeOf("function");

    const addTab = screen.getByRole("button", { name: "新建会话" });
    fireEvent.click(addTab);
    await screen.findByRole("tab", { selected: true });
    fireEvent.click(addTab);
    await waitFor(() => expect(screen.getAllByRole("tab")).toHaveLength(2));
    const [first, second] = screen.getAllByRole("tab");
    expect(second.getAttribute("aria-selected")).toBe("true");
    fireEvent.click(first);
    const path = "C:\\test-project\\file name.png";
    await act(async () => drop({ payload: { type: "drop", paths: [path] } }));
    expect(invoke.mock.calls.filter(([command]) => command === "write_terminal")).toEqual([
      ["write_terminal", { sessionId: "1", data: Array.from(new TextEncoder().encode(`'${path}' `)) }],
    ]);
    expect(onDragDropEvent).toHaveBeenCalledTimes(1);
  });

  it("侧栏历史跨目录打开全部会话", async () => {
    const normalInvoke = invoke.getMockImplementation()!;
    invoke.mockImplementation(async (command: string, args: unknown) => {
      if (command === "list_all_history") return [];
      return normalInvoke(command, args);
    });
    await openApp();

    fireEvent.click(screen.getByRole("button", { name: "恢复最近对话" }));
    await screen.findByRole("heading", { name: "恢复最近对话" });
    expect(invoke).toHaveBeenCalledWith("list_all_history");
  });

  it("截图粘贴在当前标签完成，保存期间切换标签则取消插入", async () => {
    vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
    let nextId = 0;
    let finish!: (path: string) => void;
    const normalInvoke = invoke.getMockImplementation()!;
    invoke.mockImplementation(async (command: string, args: unknown) => {
      if (command === "start_terminal") return { id: String(++nextId), title: "Terminal", workspace: "C:\\test-project" };
      if (command === "stage_clipboard_image") return new Promise<string>((resolve) => { finish = resolve; });
      return normalInvoke(command, args);
    });
    await openApp();
    const add = screen.getByRole("button", { name: "新建会话" });
    fireEvent.click(add);
    await screen.findByRole("tab", { selected: true });
    fireEvent.click(add);
    await waitFor(() => expect(screen.getAllByRole("tab")).toHaveLength(2));
    const clipboardData = { items: [{ kind: "file", type: "image/png", getAsFile: () => ({
      type: "image/png", size: 4, arrayBuffer: async () => new Uint8Array([137, 80, 78, 71]).buffer,
    }) }] };
    const activeHost = document.querySelector(".terminal-pane.visible > div:last-child")!;
    fireEvent.paste(activeHost, { clipboardData });
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("stage_clipboard_image", { data: [137, 80, 78, 71] }));
    const [first, second] = screen.getAllByRole("tab");
    fireEvent.click(first);
    fireEvent.click(second);
    await act(async () => finish("C:\\old.png"));
    expect(terminalPaste).not.toHaveBeenCalled();
    fireEvent.paste(activeHost, { clipboardData });
    await waitFor(() => expect(invoke.mock.calls.filter(([command]) => command === "stage_clipboard_image")).toHaveLength(2));
    await act(async () => finish("C:\\new.png"));
    expect(terminalPaste).toHaveBeenCalledExactlyOnceWith("'C:\\new.png' ");
  });

  it("原对话仍在运行时切回已有标签，关闭后再启动同一条历史", async () => {
    vi.stubGlobal("ResizeObserver", class { observe() {} disconnect() {} });
    const row = {
      id: "8123cb5e-1984-435a-9803-976796d3075c",
      history_ref: "original-conversation",
      title: "测试 Codex 历史",
      workspace: "C:\\test-project",
      client: "codex",
      model: "test-model",
      profile_id: "fixture-profile",
      profile_name: "测试配置",
      updated_at: 1,
    };
    let finishStop!: () => void;
    const stopping = new Promise<void>((resolve) => { finishStop = resolve; });
    let launches = 0;
    const normalInvoke = invoke.getMockImplementation()!;
    invoke.mockImplementation(async (command: string, args: unknown) => {
      if (command === "list_all_history") return [row];
      if (command === "start_terminal") return { id: String(++launches), title: row.title, workspace: row.workspace };
      if (command === "stop_terminal") return stopping;
      return normalInvoke(command, args);
    });
    await openApp();
    const restore = async () => {
      fireEvent.click(screen.getByRole("button", { name: "恢复最近对话" }));
      fireEvent.click(await screen.findByRole("button", { name: /测试 Codex 历史/ }));
    };

    await restore();
    await screen.findByRole("tab", { selected: true });
    expect(invoke.mock.calls.find(([command]) => command === "start_terminal")?.[1].request.permission_mode).toBe("standard");
    fireEvent.click(screen.getByRole("button", { name: "新建会话" }));
    await waitFor(() => expect(screen.getAllByRole("tab")).toHaveLength(2));
    await restore();
    expect(screen.getByRole("tab", { selected: true }).textContent).toContain("Codex");
    expect(launches).toBe(2);

    fireEvent.click(screen.getByTitle("停止并关闭此终端"));
    await restore();
    await restore();
    expect(launches).toBe(2);
    expect(screen.getByTitle("正在关闭…")).toBeTruthy();
    await act(async () => finishStop());
    await waitFor(() => {
      expect(launches).toBe(3);
      expect(screen.getAllByRole("tab")).toHaveLength(2);
    });
    expect(screen.getByRole("tab", { selected: true }).textContent).toContain("Codex");
  });

  it("恢复选择器只请求当前项目并展示最近消息预览", async () => {
    const row = {
      id: "8123cb5e-1984-435a-9803-976796d3075c",
      history_ref: "source-ref",
      client: "claude",
      title: "会话标题",
      preview: "最后一条用户消息",
      workspace: "C:\\test-project",
      model: "test-model",
      profile_id: "fixture-profile",
      profile_name: "测试配置",
      updated_at: 1,
      scope: "workbench",
    };
    const normalInvoke = invoke.getMockImplementation()!;
    invoke.mockImplementation(async (command: string, args: unknown) => {
      if (command === "list_history") return [row];
      return normalInvoke(command, args);
    });
    await openApp();

    fireEvent.change(screen.getByLabelText("会话"), { target: { value: "resume" } });
    await screen.findByText("最后一条用户消息");
    expect(invoke).toHaveBeenCalledWith("list_history", { workspace: "C:\\test-project" });
  });

  it("未添加工作台 Key 时，仍可用同一个启动按钮运行所选的系统客户端", async () => {
    const normalInvoke = invoke.getMockImplementation()!;
    invoke.mockImplementation(async (command: string, args: unknown) => {
      if (command === "load_app_state") {
        const state = await normalInvoke(command, args);
        return {
          ...state,
          config: { ...state.config, profiles: [], selected_profile_id: "", model: "" },
        };
      }
      if (command === "start_global_terminal") throw "测试：系统客户端不可用";
      return normalInvoke(command, args);
    });
    render(<StrictMode><App /></StrictMode>);

    await screen.findByText("尚未配置");
    const launch = screen.getByRole<HTMLButtonElement>("button", { name: "启动" });
    expect(launch.disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Codex" }));
    fireEvent.click(screen.getByRole("button", { name: "系统 CLI" }));
    expect(screen.queryByRole("combobox", { name: "选择模型" })).toBeNull();

    await waitFor(() => expect(launch.disabled).toBe(false));
    fireEvent.click(launch);
    await screen.findByText("测试：系统客户端不可用");
    expect(invoke).toHaveBeenCalledWith("start_global_terminal", {
      client: "codex",
      workspace: "C:\\test-project",
    });
    expect(invoke.mock.calls.some(([command]) => command === "start_terminal")).toBe(false);
  });

  it("已有配置但模型列表读取失败时保留原模型，并可刷新重试", async () => {
    const normalInvoke = invoke.getMockImplementation()!;
    let reachable = false;
    invoke.mockImplementation(async (command: string, args: unknown) => {
      if (command === "models_for_profile" && !reachable) throw "连接网关失败";
      return normalInvoke(command, args);
    });
    render(<StrictMode><App /></StrictMode>);

    await screen.findByText("模型列表读取失败：连接网关失败");
    expect(screen.queryByRole("option", { name: "先添加调用配置" })).toBeNull();
    expect(screen.getByRole<HTMLSelectElement>("combobox", { name: "选择模型" }).value).toBe(models[0]);
    expect(screen.getAllByText("测试配置", { selector: "strong" }).length).toBeGreaterThan(0);

    reachable = true;
    fireEvent.click(screen.getByRole("button", { name: "刷新模型列表" }));
    await screen.findByText("430 个可用");
    expect(screen.queryByText("模型列表读取失败：连接网关失败")).toBeNull();
  });

  it("网关返回空列表时提示无模型，并保留已保存的模型", async () => {
    const normalInvoke = invoke.getMockImplementation()!;
    invoke.mockImplementation(async (command: string, args: unknown) =>
      command === "models_for_profile" ? [] : normalInvoke(command, args));
    render(<StrictMode><App /></StrictMode>);

    await screen.findByText("网关未返回可用模型");
    expect(screen.queryByRole("option", { name: "先添加调用配置" })).toBeNull();
    expect(screen.getByRole<HTMLSelectElement>("combobox", { name: "选择模型" }).value).toBe(models[0]);
  });

  it("刷新失败时保留上次成功读取的列表与当前选择", async () => {
    const picker = await openApp();
    fireEvent.change(picker, { target: { value: models[10] } });
    const normalInvoke = invoke.getMockImplementation()!;
    invoke.mockImplementation(async (command: string, args: unknown) => {
      if (command === "models_for_profile") throw "临时网络故障";
      return normalInvoke(command, args);
    });
    fireEvent.click(screen.getByRole("button", { name: "刷新模型列表" }));
    await screen.findByText("模型列表读取失败：临时网络故障");
    expect(picker.value).toBe(models[10]);
    expect(screen.getByText("430 个可用")).toBeTruthy();
    expect(picker.options.length).toBe(430);
  });

  it("连续切换模型后仍显示工作区，并保存最后所选模型", async () => {
    const picker = await openApp();
    fireEvent.change(picker, { target: { value: models[1] } });
    fireEvent.change(picker, { target: { value: models[2] } });

    expect(screen.getByRole("heading", { name: "启动工作会话" })).toBeTruthy();
    expect(picker.value).toBe(models[2]);
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("save_preferences", {
      request: { client: "claude", model: models[2], workspace: "C:\\test-project" },
    }));
  });

  it("编辑项目路径后仍显示工作区，并保存新路径", async () => {
    await openApp();
    fireEvent.change(screen.getByLabelText("项目目录"), { target: { value: "C:\\another-project" } });

    expect(screen.getByRole("heading", { name: "启动工作会话" })).toBeTruthy();
    await waitFor(() => expect(invoke).toHaveBeenCalledWith("save_preferences", {
      request: { client: "claude", model: models[0], workspace: "C:\\another-project" },
    }));
  });

  it("搜索并选择列表后部的模型后，清空或改变搜索仍保留当前模型", async () => {
    const picker = await openApp();
    const search = screen.getByPlaceholderText("搜索模型");
    fireEvent.change(search, { target: { value: models[399] } });
    fireEvent.change(picker, { target: { value: models[399] } });
    fireEvent.change(search, { target: { value: "" } });
    expect(picker.value).toBe(models[399]);

    fireEvent.change(search, { target: { value: "no-matching-model" } });
    expect(picker.value).toBe(models[399]);
    expect(screen.getByRole("heading", { name: "启动工作会话" })).toBeTruthy();
  });
});
