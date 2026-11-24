package com.cassette.workbench

import androidx.compose.foundation.Canvas
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import org.json.JSONObject
import java.time.LocalDate
import java.time.ZoneOffset
import java.util.Locale

data class UsageDay(
    val date: String, val input: Long?, val output: Long?, val cached: Long?, val created: Long?,
    val reasoning: Long?, val cost: Double?, val missing: Long = 0, val model: String? = null, val prices: List<UsagePrice> = emptyList(),
    val recordId: String = "", val sessionId: String = "", val client: String = "",
    val projectPath: String? = null, val profileId: String? = null, val profileName: String? = null,
)
data class UsagePrice(val model: String, val charges: Map<String, Double>)
data class UsageConversation(val id: String, val client: String, val model: String, val workspace: String, val days: List<UsageDay>, val profileId: String = "", val profileName: String = "")

private fun JSONObject.usageLong(key: String): Long? = if (isNull(key)) null else optLong(key).takeIf { it >= 0 }
fun parseUsageDays(json: JSONObject): List<UsageDay> {
    val rows = json.optJSONArray("usage_records") ?: json.optJSONArray("timeline") ?: return emptyList()
    return (0 until rows.length()).map { i ->
        val r = rows.getJSONObject(i)
        UsageDay(r.optString("date"), r.usageLong("input_tokens"), r.usageLong("output_tokens"), r.usageLong("cached_input_tokens"),
            r.usageLong("cache_creation_input_tokens"), r.usageLong("reasoning_output_tokens"),
            if (r.isNull("estimated_cost")) null else r.optDouble("estimated_cost").takeIf { it.isFinite() && it >= 0 }, r.optLong("unpriced_records"), if(r.has("model")) r.optString("model") else null,
            r.optJSONArray("unit_prices")?.let { prices -> (0 until prices.length()).map { j ->
                val price = prices.getJSONObject(j)
                val charges = price.optJSONObject("charges") ?: JSONObject()
                UsagePrice(price.optString("model"), charges.keys().asSequence().mapNotNull { key -> charges.optDouble(key).takeIf { it.isFinite() && it >= 0 }?.let { key to it } }.toMap())
            } } ?: emptyList(), r.optString("record_id"), r.optString("session_id"), r.optString("client"),
            if(r.has("project_path")) r.optString("project_path") else null,
            if(r.has("profile_id")) r.optString("profile_id") else null,
            if(r.has("profile_name")) r.optString("profile_name") else null)
    }
}
fun parseUsageConversations(json: JSONObject): List<UsageConversation> {
    val rows = json.optJSONArray("sessions") ?: return emptyList()
    return (0 until rows.length()).map { i -> val r = rows.getJSONObject(i)
        UsageConversation(r.optString("id"), r.optString("client"), r.optString("model"), r.optString("workspace"), parseUsageDays(r), r.optString("profile_id"), r.optString("profile_name"))
    }
}
fun usageProjectMatches(workspace: String, project: String, children: Boolean = false): Boolean {
    if(project.isBlank()) return true
    if(project == "__unknown") return workspace.isBlank()
    val root = project.trimEnd('/').ifEmpty { "/" }
    val path = workspace.trimEnd('/').ifEmpty { if(workspace.isEmpty()) "" else "/" }
    return path == root || (children && path.isNotEmpty() && path.startsWith(if(root == "/") "/" else "$root/"))
}
fun usageRows(session: UsageConversation): List<UsageDay> = session.days.map { r -> r.copy(
    sessionId = r.sessionId.ifBlank { session.id }, client = r.client.ifBlank { session.client },
    projectPath = r.projectPath ?: session.workspace, profileId = r.profileId ?: session.profileId,
    profileName = r.profileName ?: session.profileName, model = r.model ?: session.model,
) }
fun filterUsageDays(sessions: List<UsageConversation>, id: String, lower: String, upper: String, project: String = "", profile: String = "", client: String = "", model: String = "", children: Boolean = false, group: String = "date"): List<UsageDay> {
    fun sum(rows: List<UsageDay>, value: (UsageDay) -> Long?): Long? = rows.mapNotNull(value).let { if (it.isEmpty()) null else it.sum() }
    return sessions.flatMap(::usageRows).filter { r ->
        (id.isBlank() || r.sessionId == id) && usageProjectMatches(r.projectPath.orEmpty(), project, children) &&
        (profile.isBlank() || (if(profile == "__unknown") r.profileId.isNullOrBlank() else r.profileId == profile)) &&
        (client.isBlank() || r.client == client) && (model.isBlank() || r.model == model) &&
        ((lower.isBlank() && upper.isBlank()) || (r.date.isNotBlank() && r.date >= lower && (upper.isBlank() || r.date <= upper)))
    }.groupBy { r -> when(group) {
        "project" -> r.projectPath.orEmpty().ifBlank { "未关联项目" }
        "profile" -> r.profileId.orEmpty().ifBlank { "未识别配置" }
        "model" -> r.model.orEmpty().ifBlank { "模型未记录" }
        "client" -> r.client.ifBlank { "客户端未记录" }
        "session" -> r.sessionId.ifBlank { "会话未记录" }
        else -> r.date
    } }.toSortedMap().map { (key, rows) ->
        val costs = rows.mapNotNull { it.cost }
        UsageDay(key, sum(rows) { it.input }, sum(rows) { it.output }, sum(rows) { it.cached }, sum(rows) { it.created }, sum(rows) { it.reasoning },
            if (costs.isEmpty()) null else costs.sum(), rows.sumOf { it.missing }, prices = rows.flatMap { it.prices }.distinct())
    }
}
private fun usageMoney(cost: Double?): String = if (cost == null) "未提供" else if (cost > 0 && cost < 0.0001) "<$0.0001" else "$" + String.format(Locale.US, "%.4f", cost)

@Composable fun UsagePanel(model: WorkbenchModel) {
    var sessionId by remember { mutableStateOf("") }
    var picking by remember { mutableStateOf(false) }
    var project by remember { mutableStateOf("") }
    var children by remember { mutableStateOf(false) }
    var profile by remember { mutableStateOf("") }
    var client by remember { mutableStateOf("") }
    var selectedModel by remember { mutableStateOf("") }
    var expanded by remember { mutableStateOf(false) }
    var group by remember { mutableStateOf("date") }
    var range by remember { mutableStateOf("all") }
    var money by remember { mutableStateOf(false) }
    var tokenSeries by remember { mutableStateOf("both") }
    val today = LocalDate.now(ZoneOffset.UTC)
    var start by remember { mutableStateOf(today.minusDays(29).toString()) }
    var end by remember { mutableStateOf(today.toString()) }
    val lower = when(range) { "all" -> ""; "custom" -> start; else -> today.minusDays(range.toLong() - 1).toString() }
    val upper = when(range) { "all" -> ""; "custom" -> end; else -> today.toString() }
    val valid = range != "custom" || runCatching { !LocalDate.parse(start).isAfter(LocalDate.parse(end)) }.getOrDefault(false)
    val points = if (valid) filterUsageDays(model.usage.sessions, sessionId, lower, upper, project, profile, client, selectedModel, children, group) else emptyList()
    fun total(value: (UsageDay) -> Long?): String = points.mapNotNull(value).let { if(it.isEmpty()) "—" else String.format(Locale.US, "%,d", it.sum()) }
    val costs = points.mapNotNull { it.cost }
    val showMoney = money && costs.isNotEmpty()
    val allRows = model.usage.sessions.flatMap(::usageRows)
    val scopedSessions = model.usage.sessions.filter { filterUsageDays(listOf(it), "", lower, upper, project, profile, client, selectedModel, children).isNotEmpty() }
    val missing = points.sumOf { it.missing }
    Card(Modifier.fillMaxWidth()) { Column(Modifier.padding(16.dp)) {
        Text("本机用量", style = MaterialTheme.typography.titleMedium)
        Text("输入 " + (model.usage.inputTokens ?: "—") + " · 输出 " + (model.usage.outputTokens ?: "—"))
        TextButton(onClick = { expanded = true; model.refreshUsage() }) { Text("查看用量与费用") }
    } }
    if(expanded) androidx.compose.ui.window.Dialog(onDismissRequest = { expanded = false }) {
    Surface(shape = MaterialTheme.shapes.large, modifier = Modifier.fillMaxWidth().heightIn(max = 720.dp)) {
    Column(Modifier.verticalScroll(androidx.compose.foundation.rememberScrollState()).padding(16.dp)) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row { Text("本机用量与费用", modifier = Modifier.weight(1f), style = MaterialTheme.typography.titleMedium)
                TextButton(onClick = { model.refreshUsage() }, enabled = model.connected && !model.usageLoading) { Text(if(model.usageLoading) "读取中…" else "刷新") } }
            UsageFilter("项目目录", project, listOf("" to "全部项目", "__unknown" to "未关联项目") + allRows.map { it.projectPath.orEmpty() }.filter { it.isNotBlank() }.distinct().sorted().map { it to it }) { project = it; sessionId = "" }
            if(project.isNotBlank() && project != "__unknown") { Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) { Checkbox(children, { children = it; sessionId = "" }); Text("包含子目录") } }
            UsageFilter("配置档案", profile, listOf("" to "全部配置", "__unknown" to "未识别配置") + allRows.filter { !it.profileId.isNullOrBlank() }.distinctBy { it.profileId }.map { it.profileId!! to it.profileName.orEmpty().ifBlank { it.profileId!! } }) { profile = it; sessionId = "" }
            Row {
                Box(Modifier.weight(1f)) { UsageFilter("客户端", client, listOf("" to "全部", "codex" to "Codex", "claude" to "Claude")) { client = it; sessionId = "" } }
                Box(Modifier.weight(1f)) { UsageFilter("模型", selectedModel, listOf("" to "全部模型") + allRows.map { it.model.orEmpty() }.filter { it.isNotBlank() }.distinct().sorted().map { it to it }) { selectedModel = it; sessionId = "" } }
            }
            TextButton(onClick = { picking = true }) { Text(if (sessionId.isBlank()) "全部会话 ▾" else model.usage.sessions.find { it.id == sessionId }?.let { it.client + " · " + it.model + " · " + it.id.takeLast(8) } ?: "会话已不可用") }
            UsageFilter("统计视角", group, listOf("date" to "每日趋势", "project" to "按项目", "profile" to "按配置", "model" to "按模型", "client" to "按客户端", "session" to "按会话")) { group = it }
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                listOf("all" to "全部", "1" to "今天", "7" to "近7天", "30" to "近30天", "custom" to "自定义").forEach { (key,label) -> FilterChip(selected = range == key, onClick = { range = key }, label = { Text(label) }) }
            }
            if(range == "custom") { Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedTextField(start, { start = it }, label = { Text("开始 YYYY-MM-DD") }, singleLine = true, modifier = Modifier.weight(1f))
                OutlinedTextField(end, { end = it }, label = { Text("结束 YYYY-MM-DD") }, singleLine = true, modifier = Modifier.weight(1f))
            } }
            if(!valid) Text("请输入有效日期，开始不能晚于结束", color = MaterialTheme.colorScheme.error)
            Text("输入（含缓存）" + total { it.input } + " · 输出 " + total { it.output }, style = MaterialTheme.typography.bodyMedium)
            Text("缓存读 " + total { it.cached } + " / 写 " + total { it.created } + " · 推理 " + total { it.reasoning }, style = MaterialTheme.typography.bodySmall)
            Text(if(costs.isEmpty()) "费用未提供" else (if(missing > 0) "已提供部分费用 " else "上游费用／按上游单价计算 ") + usageMoney(costs.sum()))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) { FilterChip(!showMoney, { money = false }, label = { Text("Token") }); if(costs.isNotEmpty()) FilterChip(showMoney, { money = true }, label = { Text("费用 USD") }) }
            if(!showMoney) {
                UsageFilter("Token 系列", tokenSeries, listOf("both" to "输入与输出", "input" to "仅看输入", "output" to "仅看输出")) { tokenSeries = it }
                Text(if(tokenSeries == "both") "输入蓝／输出橙，分行同刻度；量级相差较大时可仅看输出。" else "单独按所选系列缩放；上方用量汇总不变。", style = MaterialTheme.typography.labelSmall)
            }
            val dated = points.filter { it.date.isNotBlank() }
            val max = dated.maxOfOrNull { if(showMoney) it.cost ?: 0.0 else maxOf(if(tokenSeries == "output") 0L else it.input ?: 0L, if(tokenSeries == "input") 0L else it.output ?: 0L).toDouble() }?.coerceAtLeast(1e-12) ?: 1.0
            if(dated.isEmpty()) Text("所选范围没有可按日期绘制的记录", style = MaterialTheme.typography.bodySmall)
            Text(if(showMoney) "费用 USD · 柱长从零起算" else "Token 柱长从零起算", style = MaterialTheme.typography.labelSmall)
            dated.forEach { point ->
                fun tokenCount(value: Long?) = value?.let { String.format(Locale.US, "%,d", it) } ?: "未提供"
                val label = if(showMoney) usageMoney(point.cost) else when(tokenSeries) {
                    "input" -> "输入 " + tokenCount(point.input)
                    "output" -> "输出 " + tokenCount(point.output)
                    else -> "输入 " + tokenCount(point.input) + " · 输出 " + tokenCount(point.output)
                }
                Text((if(group == "profile") allRows.find { it.profileId == point.date }?.profileName.orEmpty().ifBlank { point.date } else point.date) + (if(group == "date") " UTC · " else " · ") + label, style = MaterialTheme.typography.labelSmall)
                val inputColor = androidx.compose.ui.graphics.Color(0xFF668DF0)
                val outputColor = androidx.compose.ui.graphics.Color(0xFFD79045)
                Canvas(Modifier.fillMaxWidth().height(if(!showMoney && tokenSeries == "both") 28.dp else 12.dp)) {
                    val barHeight = if(!showMoney && tokenSeries == "both") (size.height - 4.dp.toPx()) / 2 else size.height
                    if(showMoney) {
                        drawRect(outputColor, size = Size(size.width * ((point.cost ?: 0.0)/max).toFloat().coerceIn(0f,1f),barHeight))
                    }
                    else {
                        if(tokenSeries != "output") {
                            drawLine(inputColor.copy(alpha = .3f), Offset(0f,barHeight), Offset(size.width,barHeight))
                            drawRect(inputColor, size = Size(size.width * ((point.input ?: 0)/max).toFloat().coerceIn(0f,1f),barHeight))
                        }
                        if(tokenSeries != "input") {
                            val top = if(tokenSeries == "both") barHeight + 4.dp.toPx() else 0f
                            drawLine(outputColor.copy(alpha = .3f), Offset(0f,top + barHeight), Offset(size.width,top + barHeight))
                            drawRect(outputColor, topLeft = Offset(0f,top), size = Size(size.width * ((point.output ?: 0)/max).toFloat().coerceIn(0f,1f),barHeight))
                        }
                    }
                }
            }
            points.find { group == "date" && it.date.isBlank() }?.let { Text("日期未知：输入 " + (it.input ?: "—") + " · 输出 " + (it.output ?: "—") + "；纳入全部，未绘入日期图。", style = MaterialTheme.typography.labelSmall) }
            if(missing > 0) Text("$missing 条记录缺少费用或单价；费用合计仅包含已知部分。", color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.labelSmall)
            Text(model.usage.note, style = MaterialTheme.typography.labelSmall)
            points.flatMap { it.prices }.distinct().forEach { price ->
                Text("上游单价 · " + price.model + " · USD／百万 Token\n输入 " + (price.charges["prompt"] ?: "未提供") + " · 输出 " + (price.charges["completion"] ?: "未提供") + " · 缓存读 " + (price.charges["cache_read"] ?: "未提供") + "／写 " + (price.charges["cache_write"] ?: "未提供"), style = MaterialTheme.typography.labelSmall)
            }
            if(model.usage.scopeNote.isNotBlank()) Text(model.usage.scopeNote, style = MaterialTheme.typography.labelSmall)
            TextButton(onClick = { expanded = false }) { Text("关闭") }
            if(model.usageError.isNotBlank()) Text(model.usageError, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.labelSmall)
        }
    }
    } }
    if(picking) AlertDialog(onDismissRequest = { picking = false }, title = { Text("选择统计会话") }, text = {
        androidx.compose.foundation.lazy.LazyColumn {
            item { TextButton(onClick = { sessionId = ""; picking = false }) { Text("全部会话") } }
            items(scopedSessions.size) { index -> val s = scopedSessions[index]
                TextButton(onClick = { sessionId = s.id; picking = false }) { Text(s.client + " · " + s.model + " · " + s.id.takeLast(8) + "\n" + s.workspace) }
            }
        }
    }, confirmButton = { TextButton(onClick = { picking = false }) { Text("关闭") } })
}

@Composable private fun UsageFilter(label: String, value: String, options: List<Pair<String,String>>, select: (String) -> Unit) {
    var show by remember { mutableStateOf(false) }
    TextButton(onClick = { show = true }) { Text(label + "：" + (options.find { it.first == value }?.second ?: "未识别") + " ▾") }
    if(show) AlertDialog(onDismissRequest = { show = false }, title = { Text(label) }, text = {
        androidx.compose.foundation.lazy.LazyColumn {
            items(options.size) { i -> TextButton(onClick = { select(options[i].first); show = false }) { Text(options[i].second) } }
        }
    }, confirmButton = { TextButton(onClick = { show = false }) { Text("关闭") } })
}
