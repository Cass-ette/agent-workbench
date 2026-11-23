package com.cassette.workbench

import androidx.compose.foundation.gestures.detectDragGesturesAfterLongPress
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Close
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ScrollableTabRow
import androidx.compose.material3.Tab
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.hapticfeedback.HapticFeedbackType
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.layout.positionInRoot
import androidx.compose.ui.platform.LocalHapticFeedback
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import kotlin.math.abs

@Composable
internal fun TerminalSessionTabs(
    sessions: List<TerminalSession>,
    selectedId: String,
    onSelect: (String) -> Unit,
    onClose: (TerminalSession) -> Unit,
    onMove: (String, String, Boolean) -> Unit,
) {
    if (sessions.isEmpty()) return
    val currentSessions by rememberUpdatedState(sessions)
    val move by rememberUpdatedState(onMove)
    val centers = remember { mutableMapOf<String, Float>() }
    var draggingId by remember { mutableStateOf<String?>(null) }
    var dragOffset by remember { mutableFloatStateOf(0f) }
    var dragOrigin by remember { mutableFloatStateOf(0f) }
    val haptics = LocalHapticFeedback.current

    LaunchedEffect(sessions.map { it.id }) {
        centers.keys.retainAll(sessions.mapTo(mutableSetOf()) { it.id })
    }
    // TabRow subcomposes the tabs: use the same list for its selected index and
    // contents when a selected tab is removed or the list is reordered.
    key(sessions.map { it.id }) {
        ScrollableTabRow(selectedTabIndex = sessions.indexOfFirst { it.id == selectedId }.coerceAtLeast(0), edgePadding = 4.dp) {
            sessions.forEach { session ->
                Box(Modifier
                    .onGloballyPositioned { coordinates ->
                        centers[session.id] = coordinates.positionInRoot().x + coordinates.size.width / 2f
                    }
                    .pointerInput(session.id) {
                        detectDragGesturesAfterLongPress(
                            onDragStart = {
                                draggingId = session.id
                                dragOrigin = centers[session.id] ?: 0f
                                dragOffset = 0f
                                haptics.performHapticFeedback(HapticFeedbackType.LongPress)
                            },
                            onDragCancel = { draggingId = null; dragOffset = 0f },
                            onDragEnd = {
                                val dropX = dragOrigin + dragOffset
                                val target = currentSessions.asSequence()
                                    .filter { it.id != session.id }
                                    .mapNotNull { candidate -> centers[candidate.id]?.let { candidate.id to it } }
                                    .minByOrNull { abs(it.second - dropX) }
                                if (target != null && abs(dragOffset) >= abs(target.second - dragOrigin) / 2f) {
                                    move(session.id, target.first, dropX > target.second)
                                }
                                draggingId = null
                                dragOffset = 0f
                            },
                            onDrag = { change, amount -> change.consume(); dragOffset += amount.x },
                        )
                    }) {
                    Tab(selected = selectedId == session.id, onClick = { onSelect(session.id) },
                        modifier = Modifier.width(240.dp).graphicsLayer {
                            if (draggingId == session.id) { translationX = dragOffset; alpha = 0.72f }
                        }) {
                        // A fixed row width keeps weighted text visible during
                        // TabRow's intrinsic measurement as well as its layout.
                        Row(Modifier.width(216.dp).height(48.dp), verticalAlignment = Alignment.CenterVertically) {
                            Text(session.title, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                            IconButton(onClick = { onClose(session) }, modifier = Modifier.size(40.dp)) {
                                Icon(Icons.Outlined.Close, contentDescription = "关闭 ${session.title}")
                            }
                        }
                    }
                }
            }
        }
    }
}
