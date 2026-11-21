#!/data/data/com.termux/files/usr/bin/bash
# Fixed, idempotent recipe. No model requests and no API credentials.
set -euo pipefail
if [[ "${1:-}" == --vm ]]; then
  shift
  if (( $# )); then
    exec python3 "$(dirname "$0")/linux_vm.py" install --clients "$@"
  fi
  exec python3 "$(dirname "$0")/linux_vm.py" install
fi
stage() { printf '\nWB_STAGE:%s\n' "$1"; }
stage '检查 Termux 依赖'
missing=()
for pair in 'python3:python' 'bash:bash' 'git:git' 'ssh:openssh' 'proot-distro:proot-distro'; do
  command -v "${pair%%:*}" >/dev/null 2>&1 || missing+=("${pair#*:}")
done
if (( ${#missing[@]} )); then
  pkg update -y
  pkg install -y -o Dpkg::Options::=--force-confold "${missing[@]}"
fi
stage '检查并准备 Debian'
if ! proot-distro login debian -- /bin/true; then
  # A broken existing container must not be reset or overwritten.
  pd_root="${PREFIX}/var/lib/proot-distro"
  if [[ -e "$pd_root/containers/debian" || -e "$pd_root/installed-rootfs/debian" ]]; then
    echo '现有 Debian 无法启动，已保留文件。请检查上方错误，不会自动重装。' >&2
    exit 1
  fi
  proot-distro install debian
fi
stage '准备 Debian 工具、Node.js 和 Agent'
proot-distro login debian -- /bin/bash -s -- "$@" <<'DEBIAN_SETUP'
set -euo pipefail
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export DEBIAN_FRONTEND=noninteractive
stage() { printf '\nWB_STAGE:%s\n' "$1"; }
missing=()
for pair in 'curl:curl' 'git:git' 'xz:xz-utils' 'python3:python3' 'useradd:passwd'; do
  command -v "${pair%%:*}" >/dev/null 2>&1 || missing+=("${pair#*:}")
done
[[ -s /etc/ssl/certs/ca-certificates.crt ]] || missing+=(ca-certificates)
if (( ${#missing[@]} )); then
  apt-get update
  apt-get install -y --no-install-recommends "${missing[@]}"
fi
stage '检查 Node.js 22 或更新版本'
if ! node -e 'process.exit(Number(process.versions.node.split(".")[0]) >= 22 ? 0 : 1)' 2>/dev/null || ! command -v npm >/dev/null; then
  case "$(dpkg --print-architecture)" in
    arm64) wb_arch=arm64; wb_sha=2babb9eb5264860d540849f4b064a6fbdebcdc323657d9d382ce23d313fd5e09 ;;
    amd64) wb_arch=x64; wb_sha=cdeb8670a0c81f51b4dc4ac61919f9b1ad917b304bf8221eb939570663d240f8 ;;
    *) echo '暂不支持此 Debian 架构' >&2; exit 1 ;;
  esac
  wb_tmp=$(mktemp -d)
  trap 'rm -rf -- "$wb_tmp"' EXIT
  wb_file="node-v22.23.5-linux-$wb_arch.tar.xz"
  curl -fL --retry 3 --connect-timeout 20 -o "$wb_tmp/$wb_file" "https://nodejs.org/dist/v22.23.5/$wb_file"
  printf '%s  %s\n' "$wb_sha" "$wb_tmp/$wb_file" | sha256sum -c -
  tar -xJf "$wb_tmp/$wb_file" -C /usr/local --strip-components=1
fi
stage '准备工作台普通用户'
if ! id workbench >/dev/null 2>&1; then
  useradd --create-home --shell /bin/bash workbench
fi
for cli in "$@"; do
  case "$cli" in
    codex) wb_package='@openai/codex@0.169.0' ;;
    claude) wb_package='@anthropic-ai/claude-code@2.1.326' ;;
    *) echo '安装选项无效' >&2; exit 1 ;;
  esac
  stage "检查并安装 $cli"
  if ! "$cli" --version; then
    npm install -g "$wb_package"
  fi
done
DEBIAN_SETUP
stage '以普通用户验证客户端版本'
proot-distro login debian --user workbench -- /bin/bash -s -- "$@" <<'VERIFY'
set -euo pipefail
export PATH=/usr/local/bin:/usr/bin:/bin
[[ "$(id -u)" != 0 ]]
node --version
for cli in "$@"; do
  "$cli" --version
done
VERIFY
stage '安装检查完成，可以在工作区启动 Agent'
