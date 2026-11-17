"""Read a one-use private launch file inside Debian, then replace this process."""
import json
import os
from pathlib import Path
import shlex
import sys

SENSITIVE = ("WORKBENCH_GATEWAY_KEY", "CCH_API_KEY", "OPENAI_API_KEY", "ANTHROPIC_API_KEY",
             "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL", "CODEX_HOME", "CLAUDE_CONFIG_DIR",
             "ANTHROPIC_MODEL", "ANTHROPIC_DEFAULT_OPUS_MODEL", "ANTHROPIC_DEFAULT_SONNET_MODEL",
             "ANTHROPIC_DEFAULT_HAIKU_MODEL", "ANTHROPIC_SMALL_FAST_MODEL",
             "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX",
             "CLAUDE_CODE_USE_FOUNDRY", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
             "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS", "DISABLE_AUTOUPDATER", "CLAUDE_CODE_SIMPLE")

def main():
    path = Path(sys.argv[1])
    try:
        data = json.loads(path.read_text())
    finally:
        path.unlink(missing_ok=True)
    env = {k: v for k, v in os.environ.items() if k not in SENSITIVE}
    env.update(data["environment"])
    env.update(TERM="xterm-256color", COLORTERM="truecolor")
    env["PATH"] = "/usr/local/bin:/usr/bin:/bin:" + env.get("PATH", "")
    os.chdir(data["workspace"])
    command = shlex.join(data["args"])
    script = command + "; result=$?; printf '\\r\\n[Agent 已退出：%s；保留 Debian 终端]\\r\\n' \"$result\"; unset " + " ".join(SENSITIVE) + "; exec /bin/bash -l"
    os.execvpe("/bin/bash", ["/bin/bash", "--noprofile", "--norc", "-c", script], env)

if __name__ == "__main__":
    main()
