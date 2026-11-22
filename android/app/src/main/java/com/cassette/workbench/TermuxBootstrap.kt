package com.cassette.workbench

import java.io.ByteArrayOutputStream
import java.util.Base64
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

// A single exec argument is limited to 32 memory pages (128 KiB on many
// Android devices). Also leave room in Binder's buffer for the Intent itself.
internal const val BOOTSTRAP_CHUNK_SIZE = 16 * 1024
private const val MAX_BOOTSTRAP_PAYLOAD = 192 * 1024

internal fun bootstrapArguments(script: String, assets: Map<String, ByteArray>): Array<String> {
    val archive = ByteArrayOutputStream()
    ZipOutputStream(archive).use { zip ->
        assets.forEach { (name, bytes) ->
            zip.putNextEntry(ZipEntry(name))
            zip.write(bytes)
            zip.closeEntry()
        }
    }
    val payload = Base64.getEncoder().encodeToString(archive.toByteArray())
    check(payload.length <= MAX_BOOTSTRAP_PAYLOAD) { "Termux 桥接安装包过大；请安装修复后的工作台版本。" }
    return (listOf("--noprofile", "--norc", "-c", script, "workbench-bootstrap") +
        payload.chunked(BOOTSTRAP_CHUNK_SIZE)).toTypedArray()
}

internal fun compileBootstrapScript(root: String, operation: String, port: Int, token: String, version: String, build: Int) = """
    set -euo pipefail
    umask 077
    export PREFIX=/data/data/com.termux/files/usr
    export HOME=/data/data/com.termux/files/home
    export PATH="${'$'}PREFIX/bin:/system/bin"
    export TMPDIR="${'$'}PREFIX/tmp"
    missing=()
    for pair in python3:python git:git ssh:openssh; do
        command -v "${'$'}{pair%%:*}" >/dev/null || missing+=("${'$'}{pair#*:}")
    done
    if (( ${'$'}{#missing[@]} )); then pkg update -y; pkg install -y -o Dpkg::Options::=--force-confold "${'$'}{missing[@]}"; fi
    mkdir -p '$root'
    chmod 700 '$root'
    printf '%s' "${'$'}@" | base64 -d | python3 -c '
    import io, pathlib, sys, zipfile
    root, operation = pathlib.Path(sys.argv[1]), sys.argv[2]
    with zipfile.ZipFile(io.BytesIO(sys.stdin.buffer.read())) as archive:
        for name in archive.namelist():
            if pathlib.PurePosixPath(name).name != name or name in ("", ".", ".."):
                raise ValueError("Invalid bridge asset name")
            temporary = root / (name + "." + operation + ".tmp")
            temporary.write_bytes(archive.read(name))
            temporary.replace(root / name)
    ' '$root' '$operation'
    exec python3 '$root/termux_bridge.py' --port $port --token '$token' --version '$version' --build $build
""".trimIndent()

internal fun bootstrapErrorMessage(stderr: String?, message: String?): String {
    if (sequenceOf(stderr, message).any { it?.contains("Argument list too long", ignoreCase = true) == true }) {
        return "Termux 启动参数超过系统限制。请更新工作台后，在「运行环境」重试连接。"
    }
    // Termux exceptions can contain the full command and its arguments. Avoid
    // exposing those (including the bridge credential) or filling the screen.
    return "Termux 准备未完成。请确认 Termux 已初始化、允许外部命令且可以下载依赖，再到「运行环境」重试连接。"
}
