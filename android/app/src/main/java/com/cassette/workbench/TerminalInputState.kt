package com.cassette.workbench

enum class RemoteControlState(val label: String) {
    Checking("正在确认输入控制权"),
    Controlled("控制电脑会话"),
    Desktop("只读 · 电脑端输入已接管"),
    Expired("只读 · 输入控制权已超时，请重新接续"),
    OtherDevice("只读 · 另一设备正在控制"),
    ReadOnly("只读 · 尚未取得输入控制权"),
    Unavailable("暂时无法连接电脑，正在重试"),
}

/** Input feedback belongs to its terminal, including when another tab is selected. */
data class TerminalInputState(
    val control: RemoteControlState = RemoteControlState.Controlled,
    val error: String = "",
    val epoch: Int = 0,
) {
    val canWrite get() = control == RemoteControlState.Controlled

    fun claimed() = TerminalInputState(epoch = epoch + 1)

    fun snapshot(canWrite: Boolean, reason: String, expectedEpoch: Int = epoch): TerminalInputState {
        if (expectedEpoch != epoch) return this
        // Older computers only return can_write. Never infer a desktop takeover.
        val next = if (canWrite) RemoteControlState.Controlled else when (reason) {
            "desktop" -> RemoteControlState.Desktop
            "expired" -> RemoteControlState.Expired
            "other_device" -> RemoteControlState.OtherDevice
            else -> RemoteControlState.ReadOnly
        }
        return copy(control = next, error = if (next != control || !canWrite) "" else error)
    }

    fun failed(message: String, expectedEpoch: Int = epoch): TerminalInputState =
        if (expectedEpoch != epoch) this
        else if (message.contains("只读查看") || message.contains("接续并控制")) {
            copy(control = RemoteControlState.ReadOnly, error = "")
        } else copy(error = "终端输入未送达：$message")
}

// Some IMEs commit LF instead of a key event. Raw CLI menus expect CR (Enter);
// LF is Ctrl+J and can insert a draft newline. Preserve multiline pasted text.
fun terminalImeCommit(text: String): String = if (text == "\n" || text == "\r\n") "\r" else text
