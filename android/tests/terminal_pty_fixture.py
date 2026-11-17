"""Loopback-only real PTY for TerminalInputInstrumentation. No Termux or model API required."""
import argparse
import hmac
import importlib.util
import json
import shutil
import sys
from pathlib import Path
from urllib.parse import urlsplit

TOKEN = "workbench-ui-fixture-token-only-00000000"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--port", type=int, default=19887)
    options = parser.parse_args()
    root = Path(options.workspace).resolve()
    root.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(Path(__file__).with_name("terminal_scroll_tui.py"), root / "scroll_tui.py")
    source = Path(__file__).resolve().parents[1] / "app/src/main/assets/termux_bridge.py"
    spec = importlib.util.spec_from_file_location("bridge", source)
    bridge = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bridge)

    def shell_only(payload, home=None):
        if payload.get("client") != "shell":
            raise ValueError("Only Shell is allowed in this test")
        return ["/bin/bash", "--noprofile", "--norc", "-i"], {
            "PATH": "/usr/bin:/bin", "HOME": str(root), "TERM": "xterm-256color", "PS1": "WB_TEST> ",
            "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8", "BASH_SILENCE_DEPRECATION_WARNING": "1",
        }, str(root)

    bridge.launch_spec = shell_only
    counters = {"reads": 0, "health": 0, "creates": 0, "inputs": 0}
    failures = {"reads": 0, "health": 0}

    class TestHandler(bridge.Handler):
        def handle_request(self):
            path = urlsplit(self.path).path
            if path == "/test/gateway/v1/models":
                if (self.headers.get("x-api-key") != "metadata-test-key" or
                    self.headers.get("Authorization") != "Bearer metadata-test-key" or
                    self.headers.get("anthropic-version") != "2023-06-01"):
                    return self.reply(401, {"error": "Missing compatible gateway authentication"})
                return self.reply(200, {"data": [{"id": "synthetic-model"}]})
            if self.headers.get("x-api-key"):
                return self.reply(400, {"error": "Gateway header leaked into local bridge request"})
            if not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + TOKEN):
                return super().handle_request()
            if path == "/test/state":
                with self.server.guard:
                    if self.command == "POST":
                        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
                        for key in failures:
                            failures[key] = min(max(int(data.get(key, 0)), 0), 20)
                    return self.reply(200, {**counters, "python": sys.executable, "sessions": {
                        key: {"pid": session.pid, "exit_code": session.exit_code}
                        for key, session in self.server.sessions.items()
                    }})
            key = "health" if path == "/health" else "reads" if self.command == "GET" and path.startswith("/sessions/") else "creates" if path == "/sessions" and self.command == "POST" else "inputs" if self.command == "POST" and path.endswith("/input") else None
            if key is not None:
                with self.server.guard:
                    counters[key] += 1
                    fail = failures.get(key, 0) > 0
                    if fail: failures[key] -= 1
                if fail: return self.reply(503, {"error": "Synthetic temporary failure"})
            return super().handle_request()

        do_GET = handle_request
        do_POST = handle_request
        do_DELETE = handle_request

    server = bridge.BridgeServer(("127.0.0.1", options.port), TOKEN)
    server.RequestHandlerClass = TestHandler
    print(f"Loopback test PTY ready on {options.port}", flush=True)
    try:
        server.serve_forever()
    finally:
        for session in server.sessions.values():
            session.stop()
        server.server_close()


if __name__ == "__main__":
    main()
