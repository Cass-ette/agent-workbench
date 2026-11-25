package com.cassette.workbench

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp

@Composable
fun AgentPermissionsDialog(model: WorkbenchModel) {
    var codex by remember { mutableStateOf(if (model.options.permissionsReviewed && model.options.codexPermission != "dangerous") model.options.codexPermission else "standard") }
    var claude by remember { mutableStateOf(if (model.options.claudePermission != "dangerous") model.options.claudePermission else "standard") }
    var dangerous by remember { mutableStateOf(model.options.codexPermission == "dangerous" && model.options.claudePermission == "dangerous") }
    var confirmed by remember { mutableStateOf(false) }
    var helpError by remember { mutableStateOf(false) }
    val context = LocalContext.current
    val needsConfirmation = (codex == "full" && model.options.codexPermission != "full") ||
        (claude == "full" && model.options.claudePermission != "full")
    AlertDialog(
        onDismissRequest = { model.showAgentPermissions = false },
        title = { Text("Agent 权限设置") },
        text = {
            Column(Modifier.heightIn(max = 480.dp).verticalScroll(rememberScrollState())) {
                Text("允许联网统一适用于 Git、下载依赖和其他网络请求。权限仅作用于本地运行环境，不会获取手机 root。")
                Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                    Checkbox(checked = dangerous, onCheckedChange = { dangerous = it })
                    Text("跳过命令审批并关闭沙箱限制", color = MaterialTheme.colorScheme.error)
                }
                Text("高风险：命令直接执行且不受沙箱隔离，范围为当前运行环境可访问的数据。只影响之后新会话；SSH 电脑配置保持不变。",
                    color = MaterialTheme.colorScheme.error)
                Text("Codex · Debian", style = MaterialTheme.typography.titleSmall)
                listOf("standard" to "项目内写入，允许联网（推荐）", "restricted" to "项目内写入，限制联网",
                    "full" to "完整访问，保留按需审批").forEach { (value, label) ->
                    TextButton(enabled = !dangerous, onClick = { codex = value; confirmed = false }) { Text((if (codex == value) "● " else "○ ") + label) }
                }
                Text("项目模式保留沙箱；越界操作由 Codex 按需申请。Debian 兼容模式若已启用，会关闭沙箱并保留按需审批，可访问 Termux 和绑定目录。Linux VM 的 Codex 保持虚拟机内完整访问与按需审批，以支持系统任务与 Docker。")
                Text("Claude · Debian / Linux VM", style = MaterialTheme.typography.titleSmall)
                listOf("standard" to "标准审批（推荐）", "full" to "跳过工具权限确认").forEach { (value, label) ->
                    TextButton(enabled = !dangerous, onClick = { claude = value; confirmed = false }) { Text((if (claude == value) "● " else "○ ") + label) }
                }
                Text("标准模式按 Claude 自己的规则确认命令、联网与文件操作，不等同于 Codex 的文件沙箱。跳过确认会让工具直接执行，仍受运行环境限制。")
                if (needsConfirmation && !dangerous) {
                    Checkbox(checked = confirmed, onCheckedChange = { confirmed = it })
                    Text("我确认启用高权限，理解它可操作运行环境内、当前账号可读写的项目外文件。")
                }
                Text("只影响之后新建、恢复和分支的本地会话。SSH 沿用电脑权限。Termux 授权和系统文件访问按需由 Android 提示，不能靠这份设置取得 root。")
                TextButton(onClick = {
                    helpError = runCatching {
                        context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse("https://github.com/Cass-ette/agent-workbench/blob/main/docs/agent-permissions.zh-CN.md")))
                    }.isFailure
                }) { Text("权限与工具接入说明") }
                if (helpError) Text("无法打开浏览器，请到仓库 docs/agent-permissions.zh-CN.md 查看说明。")
            }
        },
        confirmButton = { TextButton(enabled = dangerous || !needsConfirmation || confirmed,
            onClick = { model.saveAgentPermissions(if (dangerous) "dangerous" else codex, if (dangerous) "dangerous" else claude) }) { Text("保存权限设置") } },
        dismissButton = { TextButton(onClick = { model.showAgentPermissions = false }) { Text("取消") } },
    )
}
