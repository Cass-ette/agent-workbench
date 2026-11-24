import base64
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path
import shlex
import tempfile
import threading
import time
import socket
from types import SimpleNamespace
import unittest
import urllib.request
from unittest.mock import patch

MODULE = Path(__file__).parents[1] / "app/src/main/assets/remote_workbench.py"
spec = importlib.util.spec_from_file_location("remote_workbench", MODULE)
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)


class Handler(BaseHTTPRequestHandler):
    requests = []
    def log_message(self, *_):
        return
    def do_POST(self):
        if self.headers.get("Authorization") != "Bearer private-desktop-token":
            self.send_error(403); return
        value = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        type(self).requests.append(value)
        body = json.dumps({"ok": True, "value": {"schema": 1, "op": value["op"], "sessions": [{"id": "desktop-task"}]}}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)


class RemoteWorkbenchTests(unittest.TestCase):
    def test_failed_connection_keeps_output_and_a_safe_reason(self):
        connection = R.RemoteConnection.open("printf '%s\\n' 'ssh: connect to host old-address port 22: Connection refused'; exit 255", "phone", "WB1:test:")
        deadline = time.monotonic() + 5
        while connection.exit_code is None and time.monotonic() < deadline:
            time.sleep(.02)
        try:
            state = connection.snapshot(0)
            self.assertEqual(state["phase"], "failed")
            self.assertEqual(state["exit_code"], 255)
            self.assertIn("Connection refused", base64.b64decode(state["data"]).decode())
            self.assertIn("开启远程登录", state["error"])
            self.assertNotIn("old-address", state["error"])
        finally:
            connection.stop()

    def test_connection_failure_never_copies_arbitrary_command_to_message(self):
        self.assertIn("自动查找", R.connection_failure(b"ssh: No route to host"))
        self.assertIn("账号和密码", R.connection_failure(b"Permission denied (publickey,password)."))
        self.assertIn("不会跳过", R.connection_failure(b"Host key verification failed."))
        self.assertNotIn("private-token", R.connection_failure(b"failed command secret=private-token"))

    def test_discovery_candidates_require_pinned_key_and_lan_address_before_login(self):
        key = "ssh-ed25519 " + base64.b64encode(bytes(range(51))).decode()
        target = {"platform": "macos", "user": "tester", "host_key": key}
        def address(host): return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (host, 22))]
        good = SimpleNamespace(stdout=("192.168.2.9 " + key + "\n").encode())
        with patch.object(R.shutil, "which", return_value="/usr/bin/ssh-keyscan"), \
             patch.object(R.socket, "getaddrinfo", return_value=address("192.168.2.9")), \
             patch.object(R.subprocess, "run", return_value=good) as scan:
            self.assertEqual(R.verify_ssh_address(target, "new-computer.local", 22),
                             {"verified": True, "host": "192.168.2.9", "port": 22})
            self.assertNotIn("tester", str(scan.call_args))
            scan.return_value = SimpleNamespace(stdout=b"192.168.2.9 ssh-ed25519 wrong-key\n")
            self.assertEqual(R.verify_ssh_address(target, "192.168.2.9", 22)["reason"], "identity")
        for host in ("8.8.8.8", "127.0.0.1", "100.64.0.1", "169.254.1.3"):
            with patch.object(R.shutil, "which", return_value="/usr/bin/ssh-keyscan"), \
                 patch.object(R.socket, "getaddrinfo", return_value=address(host)), \
                 patch.object(R.subprocess, "run") as scan:
                self.assertEqual(R.verify_ssh_address(target, host, 22)["reason"], "outside_lan")
                scan.assert_not_called()
        with self.assertRaises(ValueError): R.verify_ssh_address(target, "host;id", 22)

    def test_only_network_failure_retries_and_explicit_close_cancels_retry(self):
        self.assertTrue(R.retryable_connection_failure(b"ssh: Connection timed out"))
        self.assertFalse(R.retryable_connection_failure(b"Permission denied"))
        self.assertFalse(R.retryable_connection_failure(b"Host key verification failed"))
        connection = R.RemoteConnection.__new__(R.RemoteConnection)
        connection.stopped, connection.ever_ready, connection.exit_code = False, True, 255
        connection.output = bytearray()
        self.assertTrue(connection.retryable)
        connection.stopped = True
        self.assertFalse(connection.retryable)

    def test_attachment_chunk_crosses_phone_bridge_request_limit(self):
        bridge_spec = importlib.util.spec_from_file_location("remote_test_bridge", MODULE.with_name("termux_bridge.py"))
        bridge = importlib.util.module_from_spec(bridge_spec)
        bridge_spec.loader.exec_module(bridge)
        with tempfile.TemporaryDirectory() as folder, patch.dict(os.environ, {"HOME": folder}):
            server = bridge.BridgeServer(("127.0.0.1", 0), "test-token" * 8)
        class Connection:
            def call(self, body): return {"received": len(base64.b64decode(body["data"]))}
        target = "00000000-0000-0000-0000-000000000001"
        server.remotes[target] = Connection()
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            body = json.dumps({"op": "attachment", "data": base64.b64encode(b"x" * 131072).decode()}).encode()
            req = urllib.request.Request("http://127.0.0.1:%d/remote/%s/rpc" % (server.server_port, target),
                data=body, headers={"Authorization": "Bearer " + server.token})
            with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req) as response:
                self.assertEqual(json.load(response)["received"], 131072)
        finally:
            server.shutdown(); server.server_close()

    def test_local_relay_authenticates_and_disconnect_does_not_stop_desktop(self):
        with tempfile.TemporaryDirectory() as folder:
            server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            endpoint = Path(folder) / ".agent-workbench/remote.json"
            endpoint.parent.mkdir(); endpoint.write_text(json.dumps({"schema": 1, "port": server.server_port, "token": "private-desktop-token"}))
            marker = "WB1:test:"
            with patch.dict(os.environ, {"HOME": folder, "HTTP_PROXY": "http://127.0.0.1:1", "http_proxy": "http://127.0.0.1:1", "NO_PROXY": "", "no_proxy": ""}):
                connection = R.RemoteConnection.open(R.remote_command("macos", marker), "test-phone", marker)
            try:
                deadline = time.monotonic() + 5
                while not connection.ready and connection.exit_code is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertTrue(connection.ready, connection.error)
                response = connection.call({"op": "state"})
                self.assertEqual(response["sessions"][0]["id"], "desktop-task")
                self.assertEqual(Handler.requests[-1]["client_id"], "test-phone")
                self.assertNotIn("private-desktop-token", json.dumps(response))
                display = base64.b64decode(connection.snapshot(0)["data"]).decode()
                self.assertNotIn("private-desktop-token", display)
            finally:
                connection.stop()
                deadline = time.monotonic() + 3
                while connection.exit_code is None and time.monotonic() < deadline:
                    time.sleep(.02)
                self.assertNotIn("stop", [r["op"] for r in Handler.requests])
                server.shutdown(); server.server_close()

    def test_phone_tab_only_detaches_and_stop_is_explicit(self):
        class Connection:
            def __init__(self): self.calls = []
            def call(self, body): self.calls.append(body); return {"exit_code": None, "after": 0, "data": ""}
        connection = Connection()
        session = R.RemoteSession(connection, {"id": "desktop"})
        session.stop()
        self.assertEqual(connection.calls[-1]["op"], "detach")
        session.terminate()
        self.assertEqual(connection.calls[-1]["op"], "stop")

    def test_command_never_embeds_desktop_secrets(self):
        for platform in ("macos", "windows"):
            command = R.remote_command(platform, "WB1:marker:")
            self.assertNotIn("private-desktop-token", command)
        self.assertIn("-NonInteractive", R.remote_command("windows", "WB1:marker:"))
        with self.assertRaises(ValueError): R.remote_command("unknown", "marker")
