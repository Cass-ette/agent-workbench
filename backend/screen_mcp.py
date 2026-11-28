"""On-demand macOS screen reading over MCP stdio. No input control."""
import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def capture():
    if sys.platform != "darwin":
        raise RuntimeError("本机截图工具仅支持 macOS；请连接当前系统的截图 MCP")
    fd, name = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        capture_result = subprocess.run(
            ["/usr/sbin/screencapture", "-x", "-m", "-t", "png", name],
            check=False, capture_output=True, timeout=20)
        if capture_result.returncode != 0:
            detail = capture_result.stderr.decode(errors="replace").strip()
            if "permission" in detail.lower() or "not permitted" in detail.lower():
                raise RuntimeError("macOS 屏幕录制权限未授予。请在系统设置 → 隐私与安全性 → 屏幕录制中允许 Agent 工作台，然后重新检查工具权限。")
            raise RuntimeError("macOS 没有返回屏幕图像，可能是桌面已锁定、当前没有可捕获的显示器，或截图进程未获得屏幕录制权限。请先在工作台工具设置中检查系统权限。")
        resize_result = subprocess.run(
            ["/usr/bin/sips", "-Z", "1600", name],
            check=False, capture_output=True, timeout=20)
        if resize_result.returncode != 0:
            raise RuntimeError("截图已生成，但缩放失败；请重试屏幕读取。")
        data = Path(name).read_bytes()
        if not data or len(data) > 12 * 1024 * 1024:
            raise RuntimeError("截图为空或过大")
        return {"content": [{"type": "image", "mimeType": "image/png",
                              "data": base64.b64encode(data).decode()}]}
    except RuntimeError:
        raise
    except (subprocess.SubprocessError, OSError):
        raise RuntimeError("无法启动 macOS 截图服务。请确认桌面已解锁，并在工作台工具设置中检查屏幕录制权限。") from None
    finally:
        Path(name).unlink(missing_ok=True)


def response(request):
    method = request.get("method")
    if method == "initialize":
        return {"protocolVersion": request.get("params", {}).get("protocolVersion", "2024-11-05"),
                "capabilities": {"tools": {}}, "serverInfo": {"name": "workbench-screen", "version": "1.0.0"}}
    if method == "ping":
        return {}
    if method == "tools/list":
        return {"tools": [{"name": "capture_screen", "description": "按用户请求读取当前主屏幕，返回图片。需要系统屏幕录制授权和支持图片的模型。不会点击或输入。",
                "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
                "annotations": {"readOnlyHint": True, "destructiveHint": False}}]}
    if method == "tools/call" and request.get("params", {}).get("name") == "capture_screen":
        try:
            return capture()
        except RuntimeError as exc:
            return {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
    raise ValueError("不支持此 MCP 方法")


def main():
    for line in sys.stdin:
        try:
            request = json.loads(line)
            if "id" not in request:
                continue
            try:
                result = {"jsonrpc": "2.0", "id": request["id"], "result": response(request)}
            except ValueError:
                result = {"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": "Method not found"}}
            print(json.dumps(result, ensure_ascii=False), flush=True)
        except (ValueError, TypeError):
            print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}), flush=True)


if __name__ == "__main__":
    main()
