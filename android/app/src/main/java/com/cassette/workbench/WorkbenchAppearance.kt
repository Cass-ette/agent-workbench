package com.cassette.workbench

import androidx.compose.foundation.layout.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import org.json.JSONObject

internal enum class AppearanceMode(val id: String, val title: String) {
    SYSTEM("system", "跟随系统"), LIGHT("light", "浅色 · Latte"), DARK("dark", "深色 · Mocha");
    fun palette(systemDark: Boolean) = if (this == DARK || (this == SYSTEM && systemDark)) WorkbenchPalette.dark else WorkbenchPalette.light
    companion object { fun parse(value: String) = entries.firstOrNull { it.id == value } ?: SYSTEM }
}
internal val LocalWorkbenchPalette = staticCompositionLocalOf { WorkbenchPalette.light }
internal fun WorkbenchPalette.color(key: String) = Color(android.graphics.Color.parseColor(this[key]))
internal fun WorkbenchPalette.scheme(): ColorScheme {
    val dark = this == WorkbenchPalette.dark
    val base = if (dark) darkColorScheme() else lightColorScheme()
    return base.copy(primary = color("accent"), onPrimary = color("onAccent"), primaryContainer = color("surface0"),
        onPrimaryContainer = color("text"), secondary = color("subtext1"), onSecondary = color("base"),
        secondaryContainer = color("surface0"), onSecondaryContainer = color("text"),
        tertiary = color("teal"), onTertiary = color("base"), background = color("base"), onBackground = color("text"),
        surface = color("base"), onSurface = color("text"), surfaceVariant = color("mantle"), onSurfaceVariant = color("muted"),
        outline = color("overlay1"), outlineVariant = color("surface0"), error = color("red"), onError = color("base"),
        errorContainer = color("surface0"), onErrorContainer = color("red"), inverseSurface = color("text"),
        inverseOnSurface = color("base"), inversePrimary = color("accent"), surfaceTint = color("base"))
}
internal fun WorkbenchPalette.terminalTheme(): JSONObject {
    val names = listOf("black", "red", "green", "yellow", "blue", "magenta", "cyan", "white", "brightBlack", "brightRed", "brightGreen", "brightYellow", "brightBlue", "brightMagenta", "brightCyan", "brightWhite")
    return JSONObject().put("foreground", this["text"]).put("background", this["base"])
        .put("cursor", this["rosewater"]).put("cursorAccent", this["base"]).put("selectionBackground", this["surface1"])
        .also { result -> names.forEachIndexed { index, name -> result.put(name, ansi[index]) } }
}
@Composable internal fun AppearanceMenu(model: WorkbenchModel) {
    var open by remember { mutableStateOf(false) }
    Box {
        TextButton(onClick = { open = true }) { Text("外观") }
        DropdownMenu(expanded = open, onDismissRequest = { open = false }) {
            AppearanceMode.entries.forEach { mode -> DropdownMenuItem(text = { Text(mode.title + if (model.appearanceMode == mode) " · 当前" else "") },
                onClick = { model.setAppearance(mode); open = false }) }
        }
    }
}
