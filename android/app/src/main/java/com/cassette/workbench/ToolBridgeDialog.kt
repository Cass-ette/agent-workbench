package com.cassette.workbench

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import org.json.JSONObject

@Composable
fun ToolBridgeDialog(model: WorkbenchModel) {
    var client by remember { mutableStateOf(model.options.client) }
    var environment by remember { mutableStateOf(model.runtimeEnvironment) }
    var config by remember { mutableStateOf("{\"mcpServers\": {}}") }
    var items by remember { mutableStateOf(emptyList<JSONObject>()) }
    var disabled by remember { mutableStateOf(emptyList<String>()) }
    var source by remember { mutableStateOf("") }
    var confirmed by remember { mutableStateOf(false) }
    var loaded by remember { mutableStateOf(false) }
    fun accept(value: JSONObject) {
        config = value.getString("config")
        val array = value.getJSONArray("items")
        items = (0 until array.length()).map { array.getJSONObject(it) }
        disabled = items.filter { !it.getBoolean("enabled") }.map { it.getString("id") }
        confirmed = false
        loaded = true
    }
    fun imported(value: JSONObject) {
        val array = value.getJSONArray("items")
        val next = (0 until array.length()).map { array.getJSONObject(it) }
        val known = items.map { it.getString("id") }.toSet()
        disabled = disabled + next.filter { it.getString("id") !in known }.map { it.getString("id") }
        items = next
        confirmed = false
    }
    LaunchedEffect(client, environment) {
        loaded = false
        model.manageTools(client, environment, null) { accept(it) }
    }
    AlertDialog(onDismissRequest = { model.showTools = false }, title = { Text("工具与插件") }, text = {
        Column(Modifier.heightIn(max = 540.dp).verticalScroll(rememberScrollState())) {
            Text("只写工作台私有配置，不改外部客户端。工具运行在所选环境中，程序路径和依赖也必须在该环境可用。")
            Row { listOf("codex", "claude").forEach { value -> TextButton(onClick = { client = value }, enabled = !model.toolsBusy) { Text((if (client == value) "● " else "○ ") + value) } } }
            Row { listOf("debian" to "Debian", "vm" to "Linux VM").forEach { (value, label) -> TextButton(onClick = { environment = value }, enabled = !model.toolsBusy) { Text((if (environment == value) "● " else "○ ") + label) } } }
            OutlinedTextField(value = config, onValueChange = { config = it; confirmed = false }, label = { Text("MCP 服务 JSON") }, minLines = 5, maxLines = 10, enabled = !model.toolsBusy)
            Text("支持 mcpServers / mcp_servers；command/args 启动该环境程序，url 连接 HTTP 服务。保存不执行程序，之后启动会话才加载。")
            OutlinedTextField(value = source, onValueChange = { source = it }, label = { Text(if (environment == "vm") "虚拟机内的组件目录" else "Termux 内的组件目录") }, enabled = !model.toolsBusy)
            Row {
                TextButton(onClick = { model.manageTools(client, environment, JSONObject().put("action", "import").put("path", source).put("kind", "skill")) { imported(it) } }, enabled = loaded && source.isNotBlank() && !model.toolsBusy) { Text("导入技能") }
                if (client == "claude") TextButton(onClick = { model.manageTools(client, environment, JSONObject().put("action", "import").put("path", source).put("kind", "plugin")) { imported(it) } }, enabled = loaded && source.isNotBlank() && !model.toolsBusy) { Text("导入插件") }
            }
            items.forEach { item ->
                val id = item.getString("id")
                Row { Checkbox(checked = id !in disabled, onCheckedChange = { enabled -> disabled = if (enabled) disabled - id else disabled + id; confirmed = false }, enabled = !model.toolsBusy); Text(item.getString("name")) }
                if (client == "claude" && item.optString("command").isNotBlank()) Text("调用：/" + item.getString("command"))
            }
            Text("导入复制单个组件，默认停用。Claude 插件的 hooks 等能力由最小模式决定；Codex 支持 MCP 与 Skills。内置手机截屏尚未接入，Linux VM 不等于 Android 屏幕；可配置已授权的远端截图 MCP。SSH 在电脑端配置工具。")
            Row { Checkbox(checked = confirmed, onCheckedChange = { confirmed = it }, enabled = !model.toolsBusy); Text("我已核对服务程序、地址和组件，允许后续会话加载它们。") }
            if (model.toolsBusy) CircularProgressIndicator()
            if (model.toolsMessage.isNotBlank()) Text(model.toolsMessage)
        }
    }, confirmButton = {
        TextButton(enabled = loaded && confirmed && !model.toolsBusy, onClick = {
            val payload = JSONObject().put("action", "save").put("config", config).put("screen", false).put("disabled", org.json.JSONArray(disabled))
            model.manageTools(client, environment, payload) { accept(it) }
        }) { Text("保存工具设置") }
    }, dismissButton = { TextButton(onClick = { model.showTools = false }) { Text("关闭") } })
}
