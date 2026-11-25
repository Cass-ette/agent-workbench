import AppKit
import SwiftUI

struct ProjectTabItem: Identifiable {
    let id: UUID
    let title: String
    let detail: String
    let running: Bool
}

struct ProjectSessionTabs: NSViewRepresentable {
    let items: [ProjectTabItem]
    let selected: UUID?
    let palette: WorkbenchPalette
    let select: (UUID) -> Void
    let close: (UUID) -> Void
    let move: (UUID, UUID, Bool) -> Void

    func makeNSView(context: Context) -> ProjectTabScrollView {
        let view = ProjectTabScrollView()
        view.hasHorizontalScroller = true
        view.hasVerticalScroller = false
        view.autohidesScrollers = true
        view.drawsBackground = false
        view.documentView = ProjectTabDocument()
        return view
    }
    func updateNSView(_ view: ProjectTabScrollView, context: Context) {
        guard let document = view.documentView as? ProjectTabDocument else { return }
        document.configure(items: items, selected: selected, palette: palette, select: select, close: close, move: move)
    }
}

final class ProjectTabScrollView: NSScrollView {
    override func scrollWheel(with event: NSEvent) {
        let delta = abs(event.scrollingDeltaX) > abs(event.scrollingDeltaY) ? event.scrollingDeltaX : event.scrollingDeltaY
        let x = max(0, min(contentView.bounds.minX - delta, (documentView?.bounds.width ?? 0) - contentSize.width))
        contentView.scroll(to: NSPoint(x: x, y: 0))
        reflectScrolledClipView(contentView)
    }
}

final class ProjectTabDocument: NSView {
    static let pasteboardType = NSPasteboard.PasteboardType("com.cassette.workbench.project-tab")
    private var tabs: [UUID: ProjectTabView] = [:]
    private(set) var order: [UUID] = []
    private var move: ((UUID, UUID, Bool) -> Void)?
    private var marker: CGFloat?
    private var palette = WorkbenchPalette.light
    private var previousSelection: UUID?
    override var isFlipped: Bool { true }
    override var mouseDownCanMoveWindow: Bool { false }

    override init(frame: NSRect) {
        super.init(frame: frame)
        registerForDraggedTypes([Self.pasteboardType])
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }

    func configure(items: [ProjectTabItem], selected: UUID?, palette: WorkbenchPalette,
                   select: @escaping (UUID) -> Void, close: @escaping (UUID) -> Void,
                   move: @escaping (UUID, UUID, Bool) -> Void) {
        self.palette = palette; self.move = move
        order = items.map(\.id)
        for id in Array(tabs.keys) where !order.contains(id) { tabs.removeValue(forKey: id)?.removeFromSuperview() }
        var x: CGFloat = 8
        for item in items {
            let tab = tabs[item.id] ?? ProjectTabView(item: item)
            if tab.superview == nil { addSubview(tab); tabs[item.id] = tab }
            tab.item = item; tab.selected = selected == item.id; tab.palette = palette
            tab.onSelect = { select(item.id) }; tab.onClose = { close(item.id) }
            tab.onMove = { [weak self] direction in
                guard let self, let index = order.firstIndex(of: item.id), order.indices.contains(index + direction) else { return }
                move(item.id, order[index + direction], direction > 0)
            }
            tab.frame = NSRect(x: x, y: 4, width: 224, height: 40)
            tab.needsDisplay = true
            tab.setAccessibilityLabel("\(item.title)、\(item.detail)")
            tab.setAccessibilityValue(tab.selected ? "当前项目" : "")
            x += 228
        }
        setFrameSize(NSSize(width: max(x + 4, enclosingScrollView?.contentSize.width ?? 0), height: 48))
        if previousSelection != selected, let selected, let tab = tabs[selected] {
            tab.scrollToVisible(tab.bounds)
        }
        previousSelection = selected
        needsDisplay = true
    }

    static func destination(x: CGFloat, moving: UUID, frames: [(UUID, CGRect)]) -> (UUID, Bool)? {
        let other = frames.filter { $0.0 != moving }
        guard let last = other.last else { return nil }
        if let next = other.first(where: { x < $0.1.midX }) { return (next.0, false) }
        return (last.0, true)
    }

    private func movingID(_ sender: NSDraggingInfo) -> UUID? {
        guard let source = sender.draggingSource as? ProjectTabView, source.superview === self,
              let text = sender.draggingPasteboard.string(forType: Self.pasteboardType),
              let id = UUID(uuidString: text), id == source.item.id, order.contains(id) else { return nil }
        return id
    }
    private func target(_ sender: NSDraggingInfo, moving: UUID) -> (UUID, Bool)? {
        Self.destination(x: convert(sender.draggingLocation, from: nil).x, moving: moving,
                         frames: order.compactMap { id in tabs[id].map { (id, $0.frame) } })
    }
    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation { draggingUpdated(sender) }
    override func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation {
        guard let moving = movingID(sender), let (id, after) = target(sender, moving: moving), let tab = tabs[id] else { return [] }
        marker = after ? tab.frame.maxX + 2 : tab.frame.minX - 2
        needsDisplay = true
        if let scroll = enclosingScrollView {
            let point = scroll.contentView.convert(sender.draggingLocation, from: nil)
            let bounds = scroll.contentView.bounds
            let delta: CGFloat = point.x < bounds.minX + 32 ? -20 : point.x > bounds.maxX - 32 ? 20 : 0
            if delta != 0 {
                scroll.contentView.scroll(to: NSPoint(x: max(0, min(bounds.minX + delta, frame.width - bounds.width)), y: 0))
                scroll.reflectScrolledClipView(scroll.contentView)
            }
        }
        return .move
    }
    override func draggingExited(_ sender: NSDraggingInfo?) { clearMarker() }
    override func draggingEnded(_ sender: NSDraggingInfo) { clearMarker() }
    override func prepareForDragOperation(_ sender: NSDraggingInfo) -> Bool { movingID(sender) != nil }
    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        defer { clearMarker() }
        guard let moving = movingID(sender), let (id, after) = target(sender, moving: moving) else { return false }
        move?(moving, id, after)
        return true
    }
    func clearMarker() { marker = nil; needsDisplay = true }
    override func draw(_ dirtyRect: NSRect) {
        super.draw(dirtyRect)
        if let marker { NSColor(hex: palette["accent"]).setFill(); NSRect(x: marker, y: 4, width: 2, height: 40).fill() }
    }
}

final class ProjectTabView: NSView, NSDraggingSource {
    var item: ProjectTabItem
    var selected = false
    var palette = WorkbenchPalette.light
    var onSelect: (() -> Void)?
    var onClose: (() -> Void)?
    var onMove: ((Int) -> Void)?
    private let closeButton = NSButton()
    override var isFlipped: Bool { true }
    override var mouseDownCanMoveWindow: Bool { false }
    init(item: ProjectTabItem) {
        self.item = item
        super.init(frame: .zero)
        setAccessibilityElement(true); setAccessibilityRole(.button)
        setAccessibilityHelp("点击选择项目；拖动调整顺序；右侧关闭。也可用右键菜单移动。")
        closeButton.isBordered = false
        closeButton.image = NSImage(systemSymbolName: "xmark", accessibilityDescription: "关闭项目会话")
        closeButton.target = self; closeButton.action = #selector(closeTab)
        addSubview(closeButton)
    }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override func accessibilityPerformPress() -> Bool { onSelect?(); return true }
    private var closeRect: CGRect { CGRect(x: bounds.width - 28, y: 8, width: 24, height: 24) }
    override func layout() {
        super.layout(); closeButton.frame = closeRect
        closeButton.contentTintColor = NSColor(hex: palette["muted"])
    }
    @objc private func closeTab() { onClose?() }
    @objc private func moveTabLeft() { onMove?(-1) }
    @objc private func moveTabRight() { onMove?(1) }
    override func menu(for event: NSEvent) -> NSMenu? {
        let menu = NSMenu()
        for (title, action) in [("向左移动", #selector(moveTabLeft)), ("向右移动", #selector(moveTabRight))] {
            let item = NSMenuItem(title: title, action: action, keyEquivalent: ""); item.target = self; menu.addItem(item)
        }
        return menu
    }
    override func mouseDown(with event: NSEvent) {
        let start = convert(event.locationInWindow, from: nil)
        if closeRect.contains(start) { onClose?(); return }
        // Own the complete gesture, instead of competing with a nested SwiftUI Button.
        while let next = window?.nextEvent(matching: [.leftMouseUp, .leftMouseDragged]) {
            if next.type == .leftMouseUp { onSelect?(); return }
            let point = convert(next.locationInWindow, from: nil)
            if hypot(point.x - start.x, point.y - start.y) < 5 { continue }
            let pasteboard = NSPasteboardItem()
            pasteboard.setString(item.id.uuidString, forType: ProjectTabDocument.pasteboardType)
            let dragging = NSDraggingItem(pasteboardWriter: pasteboard)
            let image = NSImage(size: bounds.size)
            image.lockFocus(); draw(bounds); image.unlockFocus()
            dragging.setDraggingFrame(bounds, contents: image)
            beginDraggingSession(with: [dragging], event: next, source: self)
            return
        }
    }
    func draggingSession(_ session: NSDraggingSession, sourceOperationMaskFor context: NSDraggingContext) -> NSDragOperation {
        context == .withinApplication ? .move : []
    }
    func draggingSession(_ session: NSDraggingSession, endedAt screenPoint: NSPoint, operation: NSDragOperation) {
        (superview as? ProjectTabDocument)?.clearMarker()
    }
    override func draw(_ dirtyRect: NSRect) {
        NSColor(hex: palette[selected ? "surface0" : "mantle"]).setFill()
        NSBezierPath(roundedRect: bounds, xRadius: 6, yRadius: 6).fill()
        if selected { NSColor(hex: palette["accent"]).setFill(); NSRect(x: 0, y: bounds.height - 2, width: bounds.width, height: 2).fill() }
        NSColor(hex: palette[item.running ? "green" : "overlay1"]).setFill()
        NSBezierPath(ovalIn: NSRect(x: 10, y: 17, width: 6, height: 6)).fill()
        let paragraph = NSMutableParagraphStyle(); paragraph.lineBreakMode = .byTruncatingTail
        let width = bounds.width - 58
        (item.title as NSString).draw(in: NSRect(x: 24, y: 5, width: width, height: 17), withAttributes: [.font: NSFont.systemFont(ofSize: 11, weight: .medium), .foregroundColor: NSColor(hex: palette["text"]), .paragraphStyle: paragraph])
        (item.detail as NSString).draw(in: NSRect(x: 24, y: 23, width: width, height: 14), withAttributes: [.font: NSFont.systemFont(ofSize: 10), .foregroundColor: NSColor(hex: palette["muted"]), .paragraphStyle: paragraph])
    }
}
