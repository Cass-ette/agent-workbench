"""Exercise the shipped PowerShell relay on Windows, without a model or SSH server."""
import ast
import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import queue
import subprocess
import tempfile
import threading
import unittest


@unittest.skipUnless(os.name == "nt", "Windows PowerShell integration")
class WindowsRemoteHelperTests(unittest.TestCase):
    def test_authenticated_rpc_stays_on_loopback_and_keeps_token_local(self):
        module = ast.parse((Path(__file__).parents[1] / "android/app/src/main/assets/remote_workbench.py").read_text(encoding="utf-8"))
        function = next(node for node in module.body if isinstance(node, ast.FunctionDef) and node.name == "remote_command")
        scope = {"base64": base64}
        exec(compile(ast.Module(body=[function], type_ignores=[]), "remote_command", "exec"), scope)
        command = scope["remote_command"]("windows", "WB1:test:")
        encoded = command.split("-EncodedCommand ", 1)[1]
        seen = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): return
            def do_POST(self):
                if self.headers.get("Authorization") != "Bearer test-desktop-token":
                    self.send_error(403); return
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                seen.append(body)
                payload = json.dumps({"ok": True, "value": {"schema": 1, "op": body["op"]}}).encode()
                self.send_response(200); self.send_header("Content-Length", str(len(payload))); self.end_headers(); self.wfile.write(payload)
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as folder:
                endpoint = Path(folder) / ".agent-workbench/remote.json"
                endpoint.parent.mkdir()
                endpoint.write_text(json.dumps({"schema": 1, "port": server.server_port, "token": "test-desktop-token"}))
                env = {**os.environ, "USERPROFILE": folder, "HTTP_PROXY": "http://127.0.0.1:1", "NO_PROXY": ""}
                process = subprocess.Popen(["powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                                           stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
                lines = queue.Queue()
                def read():
                    for line in process.stdout: lines.put(line)
                threading.Thread(target=read, daemon=True).start()
                try:
                    ready = lines.get(timeout=20)
                    self.assertTrue(json.loads(base64.b64decode(ready.split("WB1:test:", 1)[1]))["ready"])
                    request = {"id": "one", "body": {"op": "state", "client_id": "test-phone"}}
                    process.stdin.write(base64.b64encode(json.dumps(request).encode()).decode() + "\n"); process.stdin.flush()
                    response = lines.get(timeout=20)
                    payload = json.loads(base64.b64decode(response.split("WB1:test:", 1)[1]))
                    self.assertEqual(payload["result"]["value"]["op"], "state")
                    self.assertNotIn("test-desktop-token", json.dumps(payload))
                    self.assertEqual(seen[-1]["client_id"], "test-phone")
                finally:
                    process.stdin.close()
                    try: process.wait(timeout=10)
                    except subprocess.TimeoutExpired: process.kill(); process.wait()
        finally:
            server.shutdown(); server.server_close()
