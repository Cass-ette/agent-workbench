const maxBytes = 20 * 1024 * 1024;

async function pngBytes(file: File): Promise<number[]> {
  if (file.size > maxBytes) throw new Error("截图超过 20 MB，请保存文件后再拖入。");
  if (file.type === "image/png") return Array.from(new Uint8Array(await file.arrayBuffer()));
  const bitmap = await createImageBitmap(file);
  try {
    if (bitmap.width > 16384 || bitmap.height > 16384 || bitmap.width * bitmap.height > 40_000_000) {
      throw new Error("截图尺寸过大，请保存文件后再拖入。");
    }
    const canvas = document.createElement("canvas");
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("无法转换剪贴板图片。");
    context.drawImage(bitmap, 0, 0);
    const png = await new Promise<Blob>((resolve, reject) => canvas.toBlob(
      (blob) => blob ? resolve(blob) : reject(new Error("无法转换剪贴板图片。")), "image/png"));
    if (png.size > maxBytes) throw new Error("截图超过 20 MB，请保存文件后再拖入。");
    return Array.from(new Uint8Array(await png.arrayBuffer()));
  } finally { bitmap.close(); }
}

/** Capture before xterm's textarea handles text; bind the async result to this tab. */
export function installImagePaste(host: HTMLElement, options: {
  isCurrent: () => boolean;
  stage: (data: number[]) => Promise<string>;
  paste: (text: string) => void;
  onError: (message: string) => void;
}): () => void {
  let disposed = false;
  let generation = 0;
  const handle = (event: ClipboardEvent) => {
    const item = Array.from(event.clipboardData?.items ?? []).find(
      (entry) => entry.kind === "file" && entry.type.startsWith("image/"));
    const file = item?.getAsFile();
    if (!file) return; // Keep normal text paste on xterm's existing path.
    event.preventDefault();
    event.stopImmediatePropagation();
    if (disposed || !options.isCurrent()) return;
    const action = generation;
    const current = () => !disposed && generation === action && options.isCurrent();
    void (async () => {
      try {
        const bytes = await pngBytes(file);
        if (!current()) return;
        const path = await options.stage(bytes);
        if (current()) options.paste(`'${path.replace(/'/g, "''")}' `);
      } catch (cause) {
        if (current()) options.onError(cause instanceof Error ? cause.message : String(cause));
      }
    })();
  };
  // Invalidate even if the user switches away and then back before staging completes.
  const switched = () => { generation++; };
  host.addEventListener("paste", handle, true);
  host.addEventListener("workbench-input-revoked", switched);
  return () => {
    disposed = true;
    host.removeEventListener("paste", handle, true);
    host.removeEventListener("workbench-input-revoked", switched);
  };
}
