package com.cassette.workbench

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import org.json.JSONObject

data class RemoteProfile(val id: String, val name: String)
data class RemoteTask(val id: String, val title: String, val workspace: String, val running: Boolean)
data class RemoteWorkbench(
    val platform: String, val profiles: List<RemoteProfile>, val tasks: List<RemoteTask>,
    val workspace: String, val profile: String, val client: String, val model: String,
    val codexPermission: String, val claudePermission: String,
) {
    companion object {
        fun parse(value: JSONObject): RemoteWorkbench {
            require(value.getInt("schema") == 1) { "电脑工作台协议不兼容，请更新两端" }
            val profiles = value.getJSONArray("profiles")
            val sessions = value.getJSONArray("sessions")
            return RemoteWorkbench(value.getString("platform"), (0 until profiles.length()).map {
                val row = profiles.getJSONObject(it); RemoteProfile(row.getString("id"), row.getString("name"))
            }, (0 until sessions.length()).map {
                val row = sessions.getJSONObject(it)
                RemoteTask(row.getString("id"), row.getString("title"), row.getString("workspace"), row.optBoolean("running", true))
            }, value.optString("workspace"), value.optString("profile_id"), value.optString("client", "codex"), value.optString("model"),
                value.optJSONObject("permissions")?.optString("codex", "unreviewed") ?: "unreviewed",
                value.optJSONObject("permissions")?.optString("claude", "unreviewed") ?: "unreviewed")
        }
    }
}

@Composable internal fun RemoteWorkbenchPanel(model: WorkbenchModel, history: (Boolean) -> Unit) {
    val remote = model.remoteWorkbench
    Text("项目、配置、历史和 Agent 均在这台电脑。断开手机不结束电脑任务。", style = MaterialTheme.typography.bodySmall)
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(onClick = model::connectRemote, enabled = !model.busy) {
            Text(if (model.remoteConnecting) "打开电脑登录终端" else if (remote == null) "连接电脑工作台" else "重新连接")
        }
        TextButton(onClick = { model.launch(shell = true) }, enabled = model.connected && !model.busy) { Text("普通 SSH") }
        if (remote != null) TextButton(onClick = { model.refreshRemote() }) { Text("刷新") }
    }
    if (model.remoteMessage.isNotBlank()) Text(model.remoteMessage, style = MaterialTheme.typography.bodySmall)
    if (remote == null) {
        Text("首次登录请在下方终端输入电脑密码；电脑工作台须在“手机连接”中允许接续。", style = MaterialTheme.typography.bodySmall)
        return
    }
    Text("工具和权限沿用电脑会话；本轮请在电脑端修改工具与权限。系统授权仍在电脑上完成。", style = MaterialTheme.typography.bodySmall)
    ChoiceField("启动方式", if (model.remoteScope == "system") "系统 CLI（电脑）" else "工作台配置（电脑）", Modifier.fillMaxWidth(),
        listOf("workbench" to "工作台配置（电脑）", "system" to "系统 CLI（电脑）"), { model.remoteScope = it })
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        ChoiceField("客户端", model.remoteClient, Modifier.weight(1f), listOf("codex" to "Codex", "claude" to "Claude"), { model.remoteClient = it })
        if (model.remoteScope == "workbench") ChoiceField("电脑调用配置", remote.profiles.firstOrNull { it.id == model.remoteProfile }?.name ?: "选择电脑配置",
            Modifier.weight(1f), remote.profiles.map { it.id to it.name }, model::selectRemoteProfile)
    }
    if (model.remoteScope == "workbench") {
        OutlinedTextField(value = model.remoteModel, onValueChange = { model.remoteModel = it }, label = { Text("电脑模型 ID") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
        Row {
            TextButton(onClick = model::loadRemoteModels, enabled = !model.busy) { Text("读取电脑模型") }
            if (model.remoteModels.isNotEmpty()) ChoiceField("模型", model.remoteModel, Modifier.weight(1f), model.remoteModels.map { it to it }, { model.remoteModel = it })
        }
        Text("权限沿用电脑已确认设置：" + if (model.remoteClient == "codex") remote.codexPermission else remote.claudePermission, style = MaterialTheme.typography.labelSmall)
    }
    OutlinedTextField(value = model.remoteWorkspace, onValueChange = { model.remoteWorkspace = it }, label = { Text("电脑项目目录") }, modifier = Modifier.fillMaxWidth(), singleLine = true)
    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Button(onClick = { model.launchRemote() }, enabled = !model.busy) { Text("新建会话") }
        TextButton(onClick = { model.launchRemote("recent") }, enabled = !model.busy) { Text("恢复最近对话") }
        TextButton(onClick = { history(false) }, enabled = !model.busy && model.remoteScope == "workbench") { Text("从历史创建分支") }
    }
    model.selectedHistory?.let { row ->
        Text("分支来源：${row.title}", style = MaterialTheme.typography.bodySmall)
        TextButton(onClick = { model.launchRemote("fork", row) }, enabled = !model.busy) { Text("用当前配置创建分支") }
    }
    TextButton(onClick = { history(true) }, enabled = !model.busy) { Text("电脑全部项目历史") }
    Text("电脑运行中的会话", style = MaterialTheme.typography.titleSmall)
    remote.tasks.filter { it.running }.forEach { task ->
        TextButton(onClick = { model.attachRemote(task.id) }, enabled = !model.busy, modifier = Modifier.fillMaxWidth()) {
            Column(Modifier.weight(1f)) { Text(task.title); Text(task.workspace, style = MaterialTheme.typography.labelSmall) }
            Text("接续并控制")
        }
    }
}
