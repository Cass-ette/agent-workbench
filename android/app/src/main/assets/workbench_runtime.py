"""Fixed dependency installer with persistent local status and bounded output."""
import fcntl
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import time
from linux_vm import LinuxVM

ROOT = Path.home() / ".local/share/agent-workbench/runtime"
SENSITIVE = ("WORKBENCH_GATEWAY_KEY", "CCH_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
             "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CODEX_HOME", "CLAUDE_CONFIG_DIR")

class RuntimeInstaller:
    def __init__(self, root=None, bundle=None):
        self.root = Path(root or ROOT)
        self.bundle = Path(bundle or self.root)
        self.guard = threading.Lock()

    def save(self, data):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        tmp = self.root / "status.tmp"
        tmp.write_text(json.dumps(data, ensure_ascii=False))
        tmp.chmod(0o600)
        tmp.replace(self.root / "status.json")

    def status(self):
        try: data = json.loads((self.root / "status.json").read_text())
        except (OSError, ValueError): data = {"state": "idle", "stage": "尚未准备本地 Agent 环境"}
        if data.get("state") == "running" and data.get("pid"):
            try: os.kill(data["pid"], 0)
            except ProcessLookupError:
                # The collector can be saving a successful exit after waitpid
                # has reaped the child. Its lock is authoritative until saved.
                with (self.root / "install.lock").open("a") as lock:
                    try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError: data["state"] = "running"
                    else: data.update(state="interrupted", stage="安装已中断，可点击重试")
        try:
            with (self.root / "install.log").open("rb") as stream:
                stream.seek(0, 2); stream.seek(max(0, stream.tell() - 12000))
                data["log"] = stream.read().decode("utf-8", "replace")
        except OSError: data["log"] = ""
        data["vm"] = LinuxVM().status()
        data["architecture"] = LinuxVM.host_architecture()
        data["local_agent_supported"] = data["architecture"] in LinuxVM.PLATFORMS
        prefix = Path(os.environ.get("PREFIX", "/data/data/com.termux/files/usr"))
        data["debian_installed"] = any((prefix / "var/lib/proot-distro" / layout / "debian").is_dir()
                                        for layout in ("containers", "installed-rootfs"))
        return data

    def start(self, clients, environment="debian"):
        if environment not in ("debian", "vm"):
            raise ValueError("请选择 Debian 轻量环境或完整 Linux 虚拟机")
        if clients is None and environment == "vm":
            clients = []
        if not isinstance(clients, list) or any(c not in ("codex", "claude") for c in clients):
            raise ValueError("请选择 Codex 或 Claude")
        clients = sorted(set(clients))
        if environment == "debian" and not clients:
            raise ValueError("请选择 Codex 或 Claude")
        if clients and LinuxVM.host_architecture() not in LinuxVM.PLATFORMS:
            raise ValueError("本地 Codex/Claude 需要 64 位 ARM64 或 x86_64；此设备仍可使用 Shell 或 SSH 电脑")
        with self.guard:
            self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            lock = (self.root / "install.lock").open("a")
            try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                lock.close()
                return self.status()
            log = (self.root / "install.log").open("w")
            (self.root / "install.log").chmod(0o600)
            env = {k: v for k, v in os.environ.items() if k not in SENSITIVE}
            # A later APK may refresh the bundled recipe while this installation
            # is running. Execute an immutable snapshot, preserving retry state.
            fd, recipe = tempfile.mkstemp(prefix="recipe-", suffix=".sh", dir=self.bundle)
            try:
                with os.fdopen(fd, "wb") as stream:
                    stream.write((self.bundle / "runtime_setup.sh").read_bytes())
                install_args = (["--vm", *clients] if environment == "vm" else clients)
                child = subprocess.Popen(["bash", recipe, *install_args],
                    env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                    start_new_session=True, pass_fds=(lock.fileno(),))
            except Exception:
                Path(recipe).unlink(missing_ok=True)
                lock.close(); log.close(); raise
            data = {"state": "running", "stage": "正在检查已有环境", "clients": clients, "environment": environment,
                    "pid": child.pid, "started": time.time()}
            self.save(data)
            def collect():
                try:
                    for line in child.stdout:
                        log.write(line); log.flush()
                        if line.startswith("WB_STAGE:"):
                            data["stage"] = line[len("WB_STAGE:"):].strip(); self.save(data)
                    code = child.wait()
                    data.update(state="ready" if code == 0 else "failed", exit_code=code,
                                stage="安装检查通过，可回工作区启动" if code == 0 else "安装未完成，请查看日志并重试")
                    self.save(data)
                finally:
                    Path(recipe).unlink(missing_ok=True)
                    child.stdout.close(); log.close(); lock.close()
            threading.Thread(target=collect, daemon=True).start()
            return data
