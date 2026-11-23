package com.cassette.workbench

internal fun reorderSessionIds(ids: List<String>, movingId: String, targetId: String, afterTarget: Boolean): List<String> {
    if (movingId == targetId) return ids
    val sourceIndex = ids.indexOf(movingId)
    if (sourceIndex < 0) return ids
    val next = ids.toMutableList()
    val moving = next.removeAt(sourceIndex)
    val targetIndex = next.indexOf(targetId)
    if (targetIndex < 0) return ids
    next.add(targetIndex + if (afterTarget) 1 else 0, moving)
    return next
}
