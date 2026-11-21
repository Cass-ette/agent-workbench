"""An optional, independently booted Linux guest. Never changes Android's kernel."""
import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import struct
import subprocess
import sys
import time
import uuid

RELEASE = "3.24.4"
RELEASE_BRANCH = ".".join(RELEASE.split(".")[:2])
PLATFORMS = {
    "aarch64": {
        "qemu": "qemu-system-aarch64", "package": "qemu-system-aarch64-headless",
        "machine": "virt", "cpu": "cortex-a72", "console": "ttyAMA0",
        "iso_arch": "aarch64", "musl_loader": "ld-musl-aarch64.so.1",
        "sha256": "cf88d8ba67389d20d9ed307366b52e487c15d7d3ee3233b62b95f709daa1d430",
    },
    "x86_64": {
        "qemu": "qemu-system-x86_64", "package": "qemu-system-x86-64-headless",
        "machine": "q35", "cpu": "max", "console": "ttyS0",
        "iso_arch": "x86_64", "musl_loader": "ld-musl-x86_64.so.1",
        "sha256": "157d353f8e54252cad2d9812a0d56bb7fa2231fc154a110e1d180d7480ef8b4f",
    },
}
SSH_PORT = 22220
DOCKER_SMOKE = '''set -eu
for attempt in $(seq 1 60); do
    if docker info --format '{{.ServerVersion}}' 2>/dev/null; then break; fi
    if [ "$attempt" = 60 ]; then tail -30 /var/log/docker.log; exit 1; fi
    sleep 2
done
uname -r
mountpoint -q /projects
test -w /projects
probe=$(mktemp -d)
image=
cleanup() {
    if [ -n "$image" ]; then docker image rm "$image" >/dev/null 2>&1 || true; fi
    rm -rf "$probe"
}
trap cleanup EXIT
# Build a disposable image from this guest's BusyBox and musl. Installation
# can verify the container runtime without depending on Docker Hub access.
tar -chf "$probe/rootfs.tar" -C / bin/busybox lib/__MUSL_LOADER__
image=$(docker import "$probe/rootfs.tar")
docker run --rm --pull never "$image" /bin/busybox echo 'Docker container started successfully'
'''

AGENT_PACKAGES = {
    "codex": "@openai/codex@0.169.0",
    "claude": "@anthropic-ai/claude-code@2.1.326",
}

AGENT_RUNNER = r'''import json
import os
from pathlib import Path
import shutil
import sys

path = Path(sys.argv[1])
try:
    data = json.loads(path.read_text())
finally:
    path.unlink(missing_ok=True)

client = data["client"]
if client not in ("codex", "claude"):
    raise SystemExit("不支持的客户端")
workspace = Path(data["workspace"]).resolve()
try:
    workspace.relative_to(Path("/projects").resolve())
except ValueError:
    raise SystemExit("项目目录必须位于 /projects 下")
if not workspace.is_dir():
    raise SystemExit("VM 项目目录不存在；请检查手机项目共享")

scope = data["scope"]
if len(scope) != 24 or any(char not in "0123456789abcdef" for char in scope):
    raise SystemExit("调用配置标识无效")
home = Path.home()
env = {key: value for key, value in os.environ.items()
       if key not in ("WORKBENCH_GATEWAY_KEY", "CCH_API_KEY", "OPENAI_API_KEY",
                      "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
                      "CODEX_HOME", "CLAUDE_CONFIG_DIR", "ANTHROPIC_MODEL",
                      "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
                      "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_SMALL_FAST_MODEL")}
env.update(TERM="xterm-256color", COLORTERM="truecolor")
env["PATH"] = "/usr/local/bin:/usr/bin:/bin:" + env.get("PATH", "")
os.chdir(workspace)

model = data["model"]
gateway = data["gateway"].rstrip("/")
key = data["key"]
mode = data.get("mode", "new")
if client == "codex":
    config = home / ".local/share/agent-workbench/codex" / scope
    config.mkdir(parents=True, exist_ok=True, mode=0o700)
    config.chmod(0o700)
    (config / "config.toml").write_text(
        "model = " + json.dumps(model) + '\nmodel_provider = "workbench_gateway"\n'
        'sandbox_mode = "workspace-write"\n\n[model_providers.workbench_gateway]\n'
        'name = "Workbench gateway"\nwire_api = "responses"\n'
        'env_key = "WORKBENCH_GATEWAY_KEY"\nbase_url = '
        + json.dumps(gateway + "/v1") + "\n")
    (config / "config.toml").chmod(0o600)
    env["CODEX_HOME"] = str(config)
    env["WORKBENCH_GATEWAY_KEY"] = key
    executable = shutil.which("codex", path=env["PATH"])
    if not executable:
        raise SystemExit("VM 中未安装 Codex，请到运行环境安装 / 检查 Linux VM")
    args = [executable]
    if mode == "continue":
        args += ["resume", "--last"]
    elif mode == "resume":
        args += ["resume"]
    # The VM is the isolation boundary; Codex needs guest-wide access for Docker
    # and system package tasks. The phone still exposes only the shared project.
    args += ["--sandbox", "danger-full-access", "--ask-for-approval", "on-request"]
    args += ["--model", model, "--cd", str(workspace), "--no-alt-screen"]
else:
    config = home / ".local/share/agent-workbench/claude" / scope
    config.mkdir(parents=True, exist_ok=True, mode=0o700)
    config.chmod(0o700)
    settings = config / "settings.json"
    if not settings.exists():
        settings.write_text("{}\n")
    settings.chmod(0o600)
    env.update(CLAUDE_CONFIG_DIR=str(config), ANTHROPIC_BASE_URL=gateway,
               ANTHROPIC_API_KEY=key, ANTHROPIC_MODEL=model,
               ANTHROPIC_DEFAULT_OPUS_MODEL=model,
               ANTHROPIC_DEFAULT_SONNET_MODEL=model,
               ANTHROPIC_DEFAULT_HAIKU_MODEL=model,
               ANTHROPIC_SMALL_FAST_MODEL=model,
               CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC="1",
               CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS="1", DISABLE_AUTOUPDATER="1")
    executable = shutil.which("claude", path=env["PATH"])
    if not executable:
        raise SystemExit("VM 中未安装 Claude，请到运行环境安装 / 检查 Linux VM")
    args = [executable, "--bare", "--add-dir", str(workspace), "--model", model,
            "--settings", str(settings), "--append-system-prompt", "全程使用简体中文。"]
    if mode == "continue":
        args += ["--continue"]
    elif mode == "resume":
        args += ["--resume"]

os.execvpe(args[0], args, env)
'''


def stage(text):
    print("WB_STAGE:" + text, flush=True)


def checked_download(url, destination, digest):
    destination = Path(destination)
    if destination.is_file() and hashlib.sha256(destination.read_bytes()).hexdigest() == digest:
        return
    partial = destination.with_suffix(".download")
    subprocess.run(["curl", "-fL", "--retry", "3", "--connect-timeout", "20", "-o", str(partial), url], check=True)
    if hashlib.sha256(partial.read_bytes()).hexdigest() != digest:
        partial.unlink(missing_ok=True)
        raise ValueError("Linux 镜像 SHA256 校验失败，未启动或安装")
    partial.replace(destination)


class LinuxVM:
    PLATFORMS = PLATFORMS

    def __init__(self, home=None):
        self.home = Path(home) if home else (Path("/data/data/com.termux/files/home")
            if Path("/data/data/com.termux/files/home").is_dir() else Path.home())
        self.root = self.home / ".local/share/tablet-linux"

    @staticmethod
    def host_architecture():
        machine = os.uname().machine.lower()
        machine = {"arm64": "aarch64", "amd64": "x86_64", "x64": "x86_64"}.get(machine, machine)
        # A 32-bit Termux can run on a 64-bit Android kernel. The bridge's
        # userspace ABI, rather than uname alone, determines usable packages.
        if struct.calcsize("P") == 4:
            machine = {"aarch64": "arm", "x86_64": "i686"}.get(machine, machine)
        return machine

    def platform(self):
        machine = self.host_architecture()
        try:
            return PLATFORMS[machine]
        except KeyError:
            raise ValueError("完整 Linux VM 需要 64 位 ARM64 或 x86_64；可继续使用 Debian 轻量环境或 SSH") from None

    @staticmethod
    def kvm_available():
        try:
            descriptor = os.open("/dev/kvm", os.O_RDWR | getattr(os, "O_CLOEXEC", 0))
            try:
                return fcntl.ioctl(descriptor, 0xAE00, 0) == 12
            finally:
                os.close(descriptor)
        except OSError:
            return False

    def pid(self):
        try:
            pid = int((self.root / "qemu.pid").read_text())
            argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
            joined = b" ".join(argv)
            if b"qemu-system-" in joined and str(self.root / "disk.raw").encode() in joined:
                return pid
        except (OSError, ValueError):
            pass
        return None

    def status(self):
        try:
            installation = json.loads((self.root / "installed.json").read_text())
        except (OSError, ValueError):
            installation = {}
        installed = bool(installation)
        pid = self.pid()
        architecture = self.host_architecture()
        # Before architecture metadata was added, the only shipped VM image was ARM64.
        supported = architecture in PLATFORMS and (
            not installed or installation.get("architecture", "aarch64") == architecture)
        acceleration = "kvm" if self.kvm_available() else "tcg"
        if pid:
            try:
                argv = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
                if b"kvm" in argv:
                    acceleration = "kvm"
                elif b"tcg,thread=multi" in argv:
                    acceleration = "tcg"
            except OSError:
                pass
        return {"installed": installed, "supported": supported, "architecture": architecture,
                "state": "running" if pid and installed else "installing" if pid else "stopped",
                "pid": pid, "acceleration": acceleration, "memory_mb": 1536, "disk_gb": 12,
                "distribution": "Alpine " + RELEASE, "docker": installed,
                "clients": installation.get("clients", [])}

    def ssh(self, command=None, terminal=False, **kwargs):
        args = ["ssh", "-i", str(self.root / "client_key"), "-p", str(SSH_PORT),
                "-o", "UserKnownHostsFile=" + str(self.root / "known_hosts"),
                "-o", "StrictHostKeyChecking=yes", "-o", "IdentitiesOnly=yes",
                "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=30"]
        if terminal:
            args.append("-tt")
        args.append("root@127.0.0.1")
        if command:
            args.append(command)
        return subprocess.run(args, **kwargs)

    def start(self, installer=False, verifying=False):
        metadata = self.root / ("prepared.json" if verifying else "installed.json")
        if metadata.is_file():
            installation = json.loads(metadata.read_text())
            if installation.get("architecture", "aarch64") != self.host_architecture():
                raise ValueError("现有 VM 磁盘与当前 Termux 架构不匹配；数据已保留，不会启动或覆盖")
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.root / "control.lock").open("a") as guard:
            fcntl.flock(guard, fcntl.LOCK_EX)
            if self.pid():
                return self.status()
            if not installer and not (self.root / ("prepared.json" if verifying else "installed.json")).is_file():
                raise ValueError("请先在运行环境页选装完整 Linux 虚拟机")
            for name in ["serial.sock", "qmp.sock", "qemu.pid"]:
                (self.root / name).unlink(missing_ok=True)
            projects = self.home / "projects"
            projects.mkdir(exist_ok=True)
            platform = self.platform()
            kvm = self.kvm_available()
            args = [platform["qemu"], "-name", "workbench-linux", "-machine", platform["machine"],
                    "-accel", "kvm" if kvm else "tcg,thread=multi", "-cpu", "host" if kvm else platform["cpu"],
                    "-smp", "2", "-m", "1536",
                    "-kernel", str(self.root / ("vmlinuz-installer" if installer else "vmlinuz")),
                    "-initrd", str(self.root / ("initramfs-installer" if installer else "initramfs")),
                    "-append", f"console={platform['console']} modules=loop,squashfs,virtio_pci,virtio_blk,virtio_net" if installer
                        else f"console={platform['console']} root=/dev/vda rootfstype=ext4 rw",
                    "-drive", "file=" + str(self.root / "disk.raw") + ",if=none,id=system,format=raw",
                    "-device", "virtio-blk-pci,drive=system", "-netdev",
                    f"user,id=net0,hostfwd=tcp:127.0.0.1:{SSH_PORT}-:22", "-device", "virtio-net-pci,netdev=net0",
                    "-fsdev", "local,id=projects,path=" + str(projects) + ",security_model=none",
                    "-device", "virtio-9p-pci,fsdev=projects,mount_tag=projects",
                    "-display", "none", "-serial", "unix:" + str(self.root / "serial.sock") + ",server=on,wait=off",
                    "-qmp", "unix:" + str(self.root / "qmp.sock") + ",server=on,wait=off",
                    "-pidfile", str(self.root / "qemu.pid"), "-rtc", "base=utc", "-no-reboot"]
            if installer:
                args += ["-drive", "file=" + str(self.root / "installer.iso") + ",if=none,id=install,format=raw,readonly=on",
                         "-device", "virtio-blk-pci,drive=install"]
            env = {k: v for k, v in os.environ.items() if not any(part in k.upper() for part in ("TOKEN", "KEY", "SECRET", "PASSWORD"))}
            with (self.root / "qemu.log").open("ab") as log:
                process = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                           env=env, start_new_session=True)
            time.sleep(0.2)
            if process.poll() is not None:
                raise ValueError("Linux 虚拟机启动失败：" + (self.root / "qemu.log").read_text()[-1200:])
            return self.status()

    def stop(self):
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.root / "control.lock").open("a") as guard:
            fcntl.flock(guard, fcntl.LOCK_EX)
            if self.pid():
                result = self.ssh("sync; poweroff", stdin=subprocess.DEVNULL, capture_output=True, timeout=20)
                if result.returncode not in (0, 255):
                    raise ValueError("虚拟机未能正常关机；请先在 Linux Shell 中执行 poweroff")
                self.wait_stopped()
            return self.status()

    def wait_stopped(self, timeout=90):
        until = time.monotonic() + timeout
        while self.pid() and time.monotonic() < until:
            time.sleep(1)
        if self.pid():
            raise ValueError("虚拟机尚未关机；已有任务保留，请在 Linux Shell 检查后重试")

    def wait_ready(self, timeout=300):
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            if not self.pid():
                raise ValueError("Linux 虚拟机已提前退出，请查看 qemu.log")
            result = self.ssh("true", stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
            if result.returncode == 0:
                return
            time.sleep(2)
        raise ValueError("Linux 启动尚未完成，请稍后重试；磁盘与现有环境已保留")

    def console(self, command, timeout=900):
        with socket.socket(socket.AF_UNIX) as connection:
            connection.settimeout(1)
            connection.connect(str(self.root / "serial.sock"))
            connection.sendall(b"\r")
            prompt = self._collect(connection, 300, rb"(login:|[~ /]# )")
            if b"login:" in prompt:
                connection.sendall(b"root\r")
                prompt = self._collect(connection, 20, rb"[~ /]# ")
            if not re.search(rb"[~ /]# ", prompt):
                raise ValueError("Linux 安装终端尚未就绪")
            # Canonical TTY input is capped at about 4 KiB per line. Disable
            # echo separately, then transfer bounded lines before executing.
            connection.sendall(b"stty -echo; printf '\\n__WB_ECHO_OFF__\\n'\r")
            if b"\n__WB_ECHO_OFF__" not in self._collect(connection, 20, rb"\r?\n__WB_ECHO_OFF__\r?\n"):
                raise ValueError("Linux 安装终端未响应，尚未发送安装脚本")
            encoded = base64.b64encode(command.encode()).decode()
            connection.sendall(b"cat > /tmp/wb-provision.b64 <<'__WB_PAYLOAD__'\r")
            for offset in range(0, len(encoded), 512):
                connection.sendall(encoded[offset:offset + 512].encode() + b"\r")
                time.sleep(0.02)
            connection.sendall(b"__WB_PAYLOAD__\r")
            connection.sendall(b"base64 -d /tmp/wb-provision.b64 > /tmp/wb-provision.sh && sh /tmp/wb-provision.sh; r=$?; rm -f /tmp/wb-provision.b64 /tmp/wb-provision.sh; stty echo; printf '\\n__WB_DONE_%s__\\n' \"$r\"\r")
            output = self._collect(connection, timeout, rb"\r?\n__WB_DONE_\d+__\r?\n", stream=True)
            match = re.search(rb"\r?\n__WB_DONE_(\d+)__", output)
            if not match or int(match[1]):
                raise ValueError("Linux 安装步骤未完成；磁盘已保留，可查看日志后重试")
            return output.decode(errors="replace")

    @staticmethod
    def _collect(connection, timeout, pattern, stream=False):
        result = bytearray()
        until = time.monotonic() + timeout
        while time.monotonic() < until:
            try:
                chunk = connection.recv(65536)
            except socket.timeout:
                continue
            if not chunk:
                break
            result.extend(chunk)
            if stream:
                print(chunk.decode(errors="replace"), end="", flush=True)
            if re.search(pattern, result):
                break
        return bytes(result)

    def install(self, clients=()):
        self.platform()
        if not isinstance(clients, (list, tuple)) or any(client not in AGENT_PACKAGES for client in clients):
            raise ValueError("Linux VM 客户端选项无效")
        clients = sorted(set(clients))
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        with (self.root / "install.lock").open("a") as guard:
            try:
                fcntl.flock(guard, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError("完整 Linux 正在安装，请等待当前安装完成")
            self._install()
            if clients:
                self.install_agents(clients)

    def _install(self):
        platform = self.platform()
        if (self.root / "installed.json").is_file():
            stage("完整 Linux 已安装；已有系统和数据保留")
            return
        if (self.root / "prepared.json").is_file():
            self._verify()
            return
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        stage("准备 QEMU、解包工具与 SSH（独立安装，不替换 Debian）")
        packages = [pkg for binary, pkg in [(platform["qemu"], platform["package"]),
                    ("bsdtar", "bsdtar"), ("ssh", "openssh"), ("curl", "curl")]
                    if not shutil.which(binary)]
        if packages:
            subprocess.run(["pkg", "update", "-y"], check=True)
            subprocess.run(["pkg", "install", "-y", *packages], check=True)
        stage("下载并校验 Alpine " + platform["iso_arch"] + " 镜像（约 90 MB）")
        iso_url = (f"https://dl-cdn.alpinelinux.org/alpine/v{RELEASE_BRANCH}/releases/"
                   f"{platform['iso_arch']}/alpine-virt-{RELEASE}-{platform['iso_arch']}.iso")
        checked_download(iso_url, self.root / "installer.iso", platform["sha256"])
        subprocess.run(["bsdtar", "-xf", str(self.root / "installer.iso"), "-C", str(self.root),
                        "boot/vmlinuz-virt", "boot/initramfs-virt"], check=True)
        shutil.copyfile(self.root / "boot/vmlinuz-virt", self.root / "vmlinuz-installer")
        shutil.copyfile(self.root / "boot/initramfs-virt", self.root / "initramfs-installer")
        disk = self.root / "disk.raw"
        if not disk.exists():
            with disk.open("xb") as stream:
                stream.truncate(12 * 1024 ** 3)
            (self.root / "install-owned.json").write_text(json.dumps({"inode": disk.stat().st_ino}))
        try:
            owned = json.loads((self.root / "install-owned.json").read_text())
        except (OSError, ValueError):
            owned = {}
        if owned.get("inode") != disk.stat().st_ino:
            raise ValueError("检测到来源未确认的现有虚拟磁盘；已保留，不会格式化")
        if not (self.root / "client_key").is_file():
            subprocess.run(["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(self.root / "client_key")], check=True)
        public = (self.root / "client_key.pub").read_text().strip()
        stage("启动独立 Linux 内核并安装 Docker；软件模拟首次需要等待")
        self.start(installer=True)
        self.console(self._provision(public), timeout=1800)
        key_output = self.console("cat /mnt/etc/ssh/ssh_host_ed25519_key.pub")
        key = re.search(r"ssh-ed25519 [A-Za-z0-9+/=]+", key_output)
        if not key:
            raise ValueError("无法验证虚拟机 SSH 主机身份")
        (self.root / "known_hosts").write_text(f"[127.0.0.1]:{SSH_PORT} " + key[0] + "\n")
        self.wait_ready()
        for remote, local in [("vmlinuz-virt", "vmlinuz"), ("initramfs-virt", "initramfs")]:
            with (self.root / local).open("wb") as stream:
                self.ssh("cat /mnt/boot/" + remote, stdout=stream, check=True)
        self.ssh("sync; poweroff", capture_output=True, timeout=30)
        self.wait_stopped()
        (self.root / "prepared.json").write_text(json.dumps({"distribution": "Alpine", "version": RELEASE,
                                                              "architecture": self.host_architecture()}))
        self._verify()

    def _verify(self):
        stage("启动已安装系统，验证 Docker 容器和项目共享")
        already_running = bool(self.pid())
        self.start(verifying=True)
        self.wait_ready()
        smoke = DOCKER_SMOKE.replace("__MUSL_LOADER__", self.platform()["musl_loader"])
        self.ssh(smoke, stdin=subprocess.DEVNULL, timeout=480, check=True)
        wrapper = self.home / ".local/bin/wvm"
        wrapper.parent.mkdir(parents=True, exist_ok=True)
        wrapper.write_text("#!" + sys.executable + "\nimport runpy,sys\nsys.path.insert(0," + repr(str(Path(__file__).parent)) + ")\nrunpy.run_path(" + repr(str(Path(__file__).resolve())) + ",run_name='__main__')\n")
        wrapper.chmod(0o700)
        if not already_running:
            self.stop()
        (self.root / "prepared.json").replace(self.root / "installed.json")
        stage("完整 Linux 与 Docker 已验证；默认停止，可按需启动")

    def install_agents(self, clients):
        installation_path = self.root / "installed.json"
        try:
            installation = json.loads(installation_path.read_text())
        except (OSError, ValueError):
            raise ValueError("请先安装完整 Linux 虚拟机") from None
        requested = sorted(set(clients))
        if not requested:
            return
        was_running = bool(self.pid())
        stage("启动 Linux VM 并准备 Agent 运行依赖")
        self.start()
        self.wait_ready()
        package_script = [
            "set -eu",
            "ip link set lo up; ip link set eth0 up",
            "ip addr show eth0 | grep -q 'inet ' || udhcpc -i eth0 -q",
            "apk add --no-progress nodejs npm python3 ca-certificates gcompat libstdc++",
            "node -e 'process.exit(Number(process.versions.node.split(\".\")[0]) >= 22 ? 0 : 1)' || { echo 'Alpine Node.js 版本低于 22' >&2; exit 1; }",
            "wb_npm_root=$(npm root -g)",
        ]
        for client in requested:
            package_name, package_version = AGENT_PACKAGES[client].rsplit("@", 1)
            package_json = "$wb_npm_root/" + package_name + "/package.json"
            version_check = (
                "node -e 'const fs=require(\"fs\");const p=JSON.parse(fs.readFileSync(process.argv[1],\"utf8\"));"
                "process.exit(p.version===process.argv[2]?0:1)' "
                + '"' + package_json + '" ' + shlex.quote(package_version) + " 2>/dev/null"
            )
            package_script.append(
                "if ! command -v " + shlex.quote(client) + " >/dev/null 2>&1 || ! " + version_check + "; then "
                "npm install -g --no-audit --no-fund " + shlex.quote(AGENT_PACKAGES[client]) + "; fi"
            )
            package_script.append(shlex.quote(client) + " --version")
        package_script.append("mkdir -p /usr/local/bin")
        package_script.append("base64 -d > /usr/local/bin/wb-agent-runner.py <<'__WB_RUNNER__'\n" +
                              base64.b64encode(AGENT_RUNNER.encode()).decode() + "\n__WB_RUNNER__")
        package_script.append("chmod 755 /usr/local/bin/wb-agent-runner.py")
        try:
            self.ssh("\n".join(package_script), stdin=subprocess.DEVNULL, timeout=1800, check=True)
            installation["clients"] = sorted(set(installation.get("clients", [])) | set(requested))
            temporary = installation_path.with_suffix(".tmp")
            temporary.write_text(json.dumps(installation))
            temporary.chmod(0o600)
            temporary.replace(installation_path)
            stage("Linux VM Agent 已安装：" + "、".join(requested))
        finally:
            if not was_running and self.pid():
                self.stop()

    def run_agent(self, payload):
        client = payload.get("client")
        if client not in AGENT_PACKAGES:
            raise ValueError("请选择 Codex 或 Claude")
        if not self.status()["installed"]:
            raise ValueError("请先在运行环境页安装完整 Linux 虚拟机")
        home_projects = (self.home / "projects").resolve()
        workspace = Path(payload.get("workspace") or "").resolve()
        try:
            relative = workspace.relative_to(home_projects)
        except ValueError:
            raise ValueError("Linux VM Agent 仅支持手机 ~/projects 下的项目目录") from None
        if not workspace.is_dir():
            raise ValueError("项目目录不存在；请先把项目放到手机 ~/projects 下")
        guest_workspace = Path("/projects") / relative
        model = str(payload.get("model") or "").strip()
        key = str(payload.get("key") or "")
        gateway = str(payload.get("gateway") or "").rstrip("/")
        mode = payload.get("mode", "new")
        if not model or not key or mode not in ("new", "continue", "resume"):
            raise ValueError("VM Agent 启动参数无效")
        scope = hashlib.sha256(json.dumps([payload.get("profile_id"), gateway,
                                           str(guest_workspace)], ensure_ascii=False).encode()).hexdigest()[:24]
        data = {"client": client, "model": model, "key": key, "gateway": gateway,
                "workspace": str(guest_workspace), "scope": scope, "mode": mode,
                "codex_compatibility": payload.get("codex_compatibility") is True}
        self.start()
        self.wait_ready()
        shared_workspace = self.ssh("test -d " + shlex.quote(str(guest_workspace)) +
                                    " && test -w " + shlex.quote(str(guest_workspace)),
                                    stdin=subprocess.DEVNULL, capture_output=True, timeout=15)
        if shared_workspace.returncode:
            raise ValueError("VM 看不到可写的项目目录；请检查手机 ~/projects 共享挂载")
        check = self.ssh("command -v " + shlex.quote(client), stdin=subprocess.DEVNULL,
                         capture_output=True, timeout=15)
        if check.returncode:
            raise ValueError("VM 中尚未安装 " + client + "；请到运行环境页安装 / 检查 Linux VM")
        remote = "/tmp/wb-agent-" + uuid.uuid4().hex + ".json"
        transfer = self.ssh("umask 077; cat > " + shlex.quote(remote),
                            input=json.dumps(data, ensure_ascii=False).encode(),
                            capture_output=True, timeout=30)
        if transfer.returncode:
            raise ValueError("未能安全传递 VM Agent 启动配置")
        try:
            result = self.ssh("exec python3 /usr/local/bin/wb-agent-runner.py " + shlex.quote(remote), terminal=True)
            return result.returncode
        finally:
            self.ssh("rm -f -- " + shlex.quote(remote), stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)

    @staticmethod
    def _provision(public):
        return ('''set -eu
ip link set lo up
ip link set eth0 up
ip addr show eth0 | grep -q 'inet ' || udhcpc -i eth0 -q
printf '__WB_APK_REPOSITORIES__' > /etc/apk/repositories
apk add --no-progress e2fsprogs openssh
if ! blkid /dev/vda | grep -q 'LABEL="WB_LINUX"'; then
  existing=$(blkid /dev/vda || true)
  if [ -n "$existing" ]; then echo 'Existing filesystem preserved; refusing to format'; exit 1; fi
  mkfs.ext4 -F -L WB_LINUX /dev/vda
fi
modprobe ext4
mkdir -p /mnt
mountpoint -q /mnt || mount /dev/vda /mnt
mkdir -p /mnt/etc/apk/keys /mnt/dev /mnt/proc /mnt/sys
cp /etc/apk/keys/* /mnt/etc/apk/keys/
cp /etc/apk/repositories /mnt/etc/apk/repositories
cp /etc/resolv.conf /mnt/etc/resolv.conf
mountpoint -q /mnt/dev || mount --bind /dev /mnt/dev
mountpoint -q /mnt/proc || mount -t proc proc /mnt/proc
mountpoint -q /mnt/sys || mount -t sysfs sysfs /mnt/sys
apk --root /mnt --initdb add --no-progress alpine-base linux-virt openssh docker docker-cli docker-cli-compose bash
cp -a /etc/runlevels /mnt/etc/
printf 'workbench-linux\n' > /mnt/etc/hostname
printf 'auto lo\niface lo inet loopback\nauto eth0\niface eth0 inet dhcp\n' > /mnt/etc/network/interfaces
printf '/dev/vda / ext4 defaults 0 1\nprojects /projects 9p trans=virtio,version=9p2000.L,msize=262144,nofail 0 0\n' > /mnt/etc/fstab
mkdir -p /mnt/root/.ssh /mnt/projects /mnt/etc/docker
chmod 700 /mnt/root/.ssh
printf '%s\n' ''' + shlex.quote(public) + ''' > /mnt/root/.ssh/authorized_keys
chmod 600 /mnt/root/.ssh/authorized_keys
printf 'PermitRootLogin prohibit-password\nPasswordAuthentication no\nKbdInteractiveAuthentication no\n' > /mnt/etc/ssh/sshd_config
printf '{"log-driver":"json-file","log-opts":{"max-size":"10m","max-file":"2"}}\n' > /mnt/etc/docker/daemon.json
chroot /mnt ssh-keygen -A
chroot /mnt passwd -d root
for service in networking hostname modules; do chroot /mnt rc-update add "$service" boot; done
for service in sshd cgroups docker; do chroot /mnt rc-update add "$service" default; done
cp /mnt/etc/ssh/sshd_config /etc/ssh/sshd_config
cp /mnt/etc/ssh/ssh_host_* /etc/ssh/
mkdir -p /root/.ssh
cp /mnt/root/.ssh/authorized_keys /root/.ssh/authorized_keys
chmod 700 /root/.ssh
chmod 600 /root/.ssh/authorized_keys
/usr/sbin/sshd
sync
''').replace("__WB_APK_REPOSITORIES__", (
            f"https://dl-cdn.alpinelinux.org/alpine/v{RELEASE_BRANCH}/main\n"
            f"https://dl-cdn.alpinelinux.org/alpine/v{RELEASE_BRANCH}/community\n"))


def main():
    parser = argparse.ArgumentParser()
    actions = parser.add_subparsers(dest="action", required=True)
    install = actions.add_parser("install")
    install.add_argument("--clients", nargs="*", choices=sorted(AGENT_PACKAGES), default=[])
    for action in ("start", "stop", "status", "shell"):
        actions.add_parser(action)
    docker = actions.add_parser("docker")
    docker.add_argument("arguments", nargs=argparse.REMAINDER)
    agent = actions.add_parser("agent")
    agent.add_argument("launch_file")
    args = parser.parse_args()
    vm = LinuxVM()
    if args.action == "install":
        vm.install(args.clients)
    elif args.action == "start":
        print(json.dumps(vm.start(), ensure_ascii=False))
    elif args.action == "stop":
        print(json.dumps(vm.stop(), ensure_ascii=False))
    elif args.action == "status":
        print(json.dumps(vm.status(), ensure_ascii=False))
    elif args.action == "agent":
        launch_file = Path(args.launch_file)
        try:
            payload = json.loads(launch_file.read_text())
        finally:
            launch_file.unlink(missing_ok=True)
        raise SystemExit(vm.run_agent(payload))
    else:
        vm.start()
        print("正在连接平板本机 Linux 虚拟机…", file=sys.stderr)
        vm.wait_ready()
        command = "cd /projects; exec bash -l" if args.action == "shell" else shlex.join(["docker", *args.arguments])
        raise SystemExit(vm.ssh(command, terminal=sys.stdin.isatty()).returncode)


if __name__ == "__main__":
    main()
