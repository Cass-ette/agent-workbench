import { describe, expect, it, vi } from "vitest";
import { installImagePaste } from "../src/TerminalClipboard";

function paste(host: HTMLElement, type = "image/png") {
  const event = new Event("paste", { bubbles: true, cancelable: true });
  const file = { type, size: 4, arrayBuffer: async () => new Uint8Array([137, 80, 78, 71]).buffer };
  Object.defineProperty(event, "clipboardData", { value: {
    items: [{ kind: type.startsWith("image/") ? "file" : "string", type, getAsFile: () => file }],
  } });
  host.dispatchEvent(event);
  return event;
}

describe("截图粘贴", () => {
  it("拦截图像，把保存后的安全路径送入当前终端且不提交", async () => {
    const host = document.createElement("div");
    const textHandler = vi.fn();
    host.addEventListener("paste", textHandler);
    const stage = vi.fn(async () => "C:\\Users\\user's\\clipboard.png");
    const insert = vi.fn();
    const remove = installImagePaste(host, { isCurrent: () => true, stage, paste: insert, onError: vi.fn() });
    expect(paste(host).defaultPrevented).toBe(true);
    await vi.waitFor(() => expect(insert).toHaveBeenCalledWith("'C:\\Users\\user''s\\clipboard.png' "));
    expect(stage).toHaveBeenCalledWith([137, 80, 78, 71]);
    expect(textHandler).not.toHaveBeenCalled();
    remove();
  });

  it.each(["switch", "close"])("图片保存期间 %s 不会写到其他对话", async (action) => {
    const host = document.createElement("div");
    let finish!: (path: string) => void;
    const stage = vi.fn(() => new Promise<string>((resolve) => { finish = resolve; }));
    const insert = vi.fn();
    const remove = installImagePaste(host, { isCurrent: () => true, stage, paste: insert, onError: vi.fn() });
    paste(host);
    await vi.waitFor(() => expect(stage).toHaveBeenCalled());
    if (action === "switch") host.dispatchEvent(new Event("workbench-input-revoked"));
    else remove();
    finish("C:\\attachment.png");
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(insert).not.toHaveBeenCalled();
    remove();
  });

  it("普通文本走原有粘贴，非活动终端不保存图片", async () => {
    const host = document.createElement("div");
    const stage = vi.fn();
    const remove = installImagePaste(host, { isCurrent: () => false, stage, paste: vi.fn(), onError: vi.fn() });
    expect(paste(host, "text/plain").defaultPrevented).toBe(false);
    expect(paste(host).defaultPrevented).toBe(true);
    expect(stage).not.toHaveBeenCalled();
    remove();
  });
});
