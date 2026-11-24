package com.cassette.workbench

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlin.math.roundToInt

object ReasoningEffort {
    val levels = listOf("default", "low", "medium", "high", "xhigh", "max")
    fun label(value: String) = if(value == "default") "客户端默认" else value
    fun key(client: String, profile: String, model: String) = listOf(client, profile, model).joinToString("|") { "${it.length}:$it" }
    fun command(client: String, value: String): String? {
        if(value !in levels || client !in listOf("codex", "claude")) return null
        if(value == "default") return if(client == "codex") "/model" else "/effort"
        return if(client == "codex") "/model --reasoning=$value" else "/effort $value"
    }
}

@Composable internal fun ReasoningEffortSlider(value: String, onChange: (String) -> Unit, label: String) {
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row { Text(label, modifier = Modifier.weight(1f)); Text(ReasoningEffort.label(value)) }
        Slider(value = ReasoningEffort.levels.indexOf(value).coerceAtLeast(0).toFloat(),
            onValueChange = { onChange(ReasoningEffort.levels[it.roundToInt()]) }, valueRange = 0f..5f, steps = 4)
        Row(Modifier.fillMaxWidth()) {
            ReasoningEffort.levels.forEach { level ->
                TextButton(onClick = { onChange(level) }, contentPadding = PaddingValues(0.dp), modifier = Modifier.weight(1f)) {
                    Text(if(level == "default") "默认" else level, style = MaterialTheme.typography.labelSmall)
                }
            }
        }
        Text("通用档位；支持范围由客户端、模型和上游决定。较高强度通常需要更多时间和 Token。", style = MaterialTheme.typography.labelSmall)
    }
}
