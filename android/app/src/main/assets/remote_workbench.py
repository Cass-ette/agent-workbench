"""Authenticated desktop RPC over an existing SSH login, without exporting its token.

The terminal shown during login belongs to SSH. After authentication the same
connection carries framed requests; desktop PTYs never belong to this process.
"""
import base64
import fcntl
import json
import os
import pty
import signal
import subprocess
import sys
import termios
import threading
import tty
import uuid

MAX_FRAME = 3 * 1024 * 1024


def remote_command(platform, marker):
    if platform == "macos":
        script = r'''
import base64,json,pathlib,sys,urllib.request
marker = MARKER
try:
    endpoint=json.loads((pathlib.Path.home()/'.agent-workbench/remote.json').read_text())
    assert endpoint['schema']==1 and 0<int(endpoint['port'])<65536
    direct=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    def request(value):
        req=urllib.request.Request('http://127.0.0.1:%d/rpc'%endpoint['port'],data=json.dumps(value).encode(),headers={'Authorization':'Bearer '+endpoint['token'],'Content-Type':'application/json'})
        with direct.open(req,timeout=60) as response:return json.load(response)
    assert request({'op':'ping'})['ok']
    print(marker+base64.b64encode(json.dumps({'ready':True,'schema':1}).encode()).decode(),flush=True)
    for line in sys.stdin:
        if len(line)>3145728:break
        req={}
        try:
            req=json.loads(base64.b64decode(line.strip(),validate=True))
            result=request(req['body'])
        except Exception:
            result={'ok':False,'error':'电脑工作台请求失败，请检查电脑服务状态'}
        print(marker+base64.b64encode(json.dumps({'id':req.get('id'),'result':result}).encode()).decode(),flush=True)
except Exception:
    print(marker+base64.b64encode(json.dumps({'ready':False,'error':'请在电脑工作台的手机连接中启用接续，并保持工作台运行'}).encode()).decode(),flush=True)
'''.replace("MARKER", repr(marker))
        import shlex
        # No dependency download and no user-specific installation path.
        return ("for wb_python in /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do "
                "if [ -x \"$wb_python\" ]; then exec \"$wb_python\" -u -c " + shlex.quote(script) + "; fi; done; exit 127")
    if platform != "windows":
        raise ValueError("此电脑尚不支持工作台接续，请使用普通 SSH")
    script = r'''
$ErrorActionPreference = 'Stop'
$marker = 'MARKER'
function Emit($value) {
    $json = ConvertTo-Json -InputObject $value -Depth 64 -Compress
    [Console]::WriteLine($marker + [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($json)))
}
try {
    $endpoint = Get-Content -LiteralPath (Join-Path $env:USERPROFILE '.agent-workbench/remote.json') -Raw | ConvertFrom-Json
    if ($endpoint.schema -ne 1 -or $endpoint.port -lt 1 -or $endpoint.port -gt 65535) { throw 'invalid endpoint' }
    $url = 'http://127.0.0.1:' + $endpoint.port + '/rpc'
    function Rpc($body) {
        $request = [Net.WebRequest]::Create($url)
        $request.Proxy = $null
        $request.Method = 'POST'
        $request.ContentType = 'application/json; charset=utf-8'
        $request.Headers['Authorization'] = 'Bearer ' + $endpoint.token
        $request.Timeout = 60000
        $request.ServicePoint.Expect100Continue = $false
        $request.KeepAlive = $false
        $bytes = [Text.Encoding]::UTF8.GetBytes((ConvertTo-Json -InputObject $body -Depth 64 -Compress))
        $request.ContentLength = $bytes.Length
        $stream = $request.GetRequestStream()
        try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
        $response = $request.GetResponse()
        try {
            $reader = [IO.StreamReader]::new($response.GetResponseStream(), [Text.Encoding]::UTF8)
            try { ConvertFrom-Json -InputObject $reader.ReadToEnd() } finally { $reader.Dispose() }
        } finally { $response.Dispose() }
    }
    if (!(Rpc @{op='ping'}).ok) { throw 'unavailable' }
    Emit @{ready=$true;schema=1}
    while ($null -ne ($line = [Console]::ReadLine())) {
        if ($line.Length -gt 3145728) { break }
        $request = $null
        try {
            $request = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($line)) | ConvertFrom-Json
            $result = Rpc $request.body
        } catch { $result = @{ok=$false;error='Desktop workbench request failed; check the computer.'} }
        Emit @{id=$request.id;result=$result}
    }
} catch { Emit @{ready=$false;error='Enable phone continuity in the desktop workbench and keep it running.'} }
'''.replace("MARKER", marker)
    return "powershell.exe -NoLogo -NoProfile -NonInteractive -EncodedCommand " + base64.b64encode(script.encode("utf-16le")).decode()


class RemoteConnection:
    @classmethod
    def open(cls, shell_command, owner, marker):
        # Build marker before spawning, so the reader cannot race its assignment.
        obj = cls.__new__(cls)
        obj.id, obj.owner, obj.marker = uuid.uuid4().hex, owner, marker.encode()
        obj.ready, obj.error, obj.exit_code = False, "", None
        obj.output, obj.base = bytearray(), 0
        obj.lock, obj.request_lock, obj.pending = threading.Lock(), threading.Lock(), {}
        obj.fd, slave = pty.openpty()
        try:
            # Run tty setup after exec, not Python code in a forked threaded host.
            obj.process = subprocess.Popen([sys.executable, str(__file__), "--ssh-child", shell_command],
                                           stdin=slave, stdout=slave, stderr=slave, start_new_session=True, close_fds=True)
            obj.pid = obj.process.pid
        except BaseException:
            os.close(obj.fd)
            raise
        finally:
            os.close(slave)
        threading.Thread(target=obj.read, daemon=True).start()
        return obj

    def _display(self, data):
        with self.lock:
            self.output.extend(data)
            if len(self.output) > 65536:
                removed = len(self.output) - 65536
                del self.output[:removed]
                self.base += removed

    def read(self):
        buffered = b""
        try:
            while True:
                chunk = os.read(self.fd, 65536)
                if not chunk:
                    break
                buffered += chunk
                if len(buffered) > MAX_FRAME:
                    raise ValueError("远端响应过大")
                while b"\n" in buffered:
                    line, buffered = buffered.split(b"\n", 1)
                    if self.marker in line:
                        frame = json.loads(base64.b64decode(line.split(self.marker, 1)[1].strip(), validate=True))
                        if "ready" in frame:
                            self.ready = frame["ready"] is True and frame.get("schema") == 1
                            self.error = str(frame.get("error", ""))[:200]
                            self._display(("\r\n" + ("电脑工作台已连接。返回配置任务可查看运行会话。" if self.ready else self.error) + "\r\n").encode())
                        else:
                            with self.lock:
                                item = self.pending.get(frame.get("id"))
                                if item:
                                    item[1].append(frame.get("result", {}))
                                    item[0].set()
                    elif not self.ready:
                        self._display(line + b"\r\n")
                # SSH password prompts have no trailing newline.
                if not self.ready and buffered and self.marker not in buffered:
                    keep = max((n for n in range(1, min(len(buffered), len(self.marker)) + 1)
                                if buffered.endswith(self.marker[:n])), default=0)
                    self._display(buffered[:-keep] if keep else buffered)
                    buffered = buffered[-keep:] if keep else b""
        except (OSError, ValueError, TypeError):
            if not self.error:
                self.error = "SSH 连接已断开；电脑任务不受影响，可重新连接"
            self.stop()
        finally:
            self.ready = False
            try:
                self.exit_code = self.process.wait()
            except ChildProcessError:
                self.exit_code = 1
            try:
                os.close(self.fd)
            except OSError:
                self.fd = -1
            with self.lock:
                for event, _ in self.pending.values():
                    event.set()

    def call(self, body):
        if not self.ready:
            raise ValueError(self.error or "请先在 SSH 登录终端完成电脑登录")
        with self.request_lock:
            identity = uuid.uuid4().hex
            event, result = threading.Event(), []
            value = {**body, "client_id": self.owner}
            frame = base64.b64encode(json.dumps({"id": identity, "body": value}).encode()) + b"\n"
            if len(frame) > MAX_FRAME:
                raise ValueError("请求过大，请分段传输附件")
            with self.lock:
                self.pending[identity] = (event, result)
            try:
                view = memoryview(frame)
                while view:
                    view = view[os.write(self.fd, view):]
                if not event.wait(65) or not result:
                    raise ValueError("电脑响应超时或连接中断；操作未自动重试，请核对会话状态")
                answer = result[0]
                if not answer.get("ok"):
                    raise ValueError(str(answer.get("error", "电脑拒绝了操作"))[:240])
                return answer.get("value", {})
            finally:
                with self.lock:
                    self.pending.pop(identity, None)

    def write(self, data):
        if self.ready:
            raise ValueError("登录已完成，请在电脑会话中输入")
        os.write(self.fd, data)

    def resize(self, cols, rows):
        return  # Only a login prompt is rendered here, not a desktop PTY.

    def snapshot(self, after):
        with self.lock:
            start = max(after, self.base)
            data = bytes(self.output[start-self.base:start-self.base+65536])
            return {"id": self.id, "data": base64.b64encode(data).decode(), "after": start + len(data),
                    "exit_code": self.exit_code, "truncated": after < self.base}

    def stop(self):
        try:
            os.killpg(self.pid, signal.SIGTERM)
        except ProcessLookupError:
            return


class RemoteSession:
    def __init__(self, connection, metadata):
        self.id = uuid.uuid4().hex
        self.connection, self.remote_id = connection, metadata["id"]
        self.exit_code = None

    def snapshot(self, after):
        value = self.connection.call({"op": "output", "session_id": self.remote_id, "after": after})
        self.exit_code = value.get("exit_code")
        return {**value, "id": self.id, "remote": True}

    def write(self, data):
        self.connection.call({"op": "input", "session_id": self.remote_id,
                              "input_id": uuid.uuid4().hex, "data": base64.b64encode(data).decode()})

    def resize(self, cols, rows):
        self.connection.call({"op": "resize", "session_id": self.remote_id, "cols": cols, "rows": rows})

    def stop(self):
        # Closing the phone tab only detaches; it must not stop the computer.
        self.connection.call({"op": "detach", "session_id": self.remote_id})

    def terminate(self):
        self.connection.call({"op": "stop", "session_id": self.remote_id})


if __name__ == "__main__" and len(sys.argv) == 3 and sys.argv[1] == "--ssh-child":
    fcntl.ioctl(0, termios.TIOCSCTTY, 0)
    tty.setraw(0)
    os.execvpe("bash", ["bash", "-c", sys.argv[2]], os.environ.copy())
