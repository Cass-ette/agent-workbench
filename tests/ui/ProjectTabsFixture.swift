// Compile with ProjectSessionTabs.swift and WorkbenchPalette.swift for a key-free UI check.
import AppKit
import SwiftUI

extension NSColor {
    convenience init(hex: String) {
        let value = UInt32(hex.dropFirst(), radix: 16)!
        self.init(srgbRed: CGFloat((value >> 16) & 255) / 255, green: CGFloat((value >> 8) & 255) / 255, blue: CGFloat(value & 255) / 255, alpha: 1)
    }
}

@main struct ProjectTabsFixtureApp: App {
    var body: some Scene { WindowGroup { FixtureView() }.defaultSize(width: 1040, height: 640) }
}
private struct FixtureView: View {
    @State private var items = ["论文整理", "Web 项目", "脚本工具", "数据处理"].map {
        ProjectTabItem(id: UUID(), title: "Codex · 测试模型", detail: $0, running: true)
    }
    @State private var selected: UUID?
    @State private var dark = false
    private var palette: WorkbenchPalette { dark ? .dark : .light }
    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Text("项目标签与主题验证").font(.headline)
                Spacer()
                Button("浅色 · Latte") { dark = false }
                Button("深色 · Mocha") { dark = true }
            }.padding(20)
            ProjectSessionTabs(items: items, selected: selected, palette: palette,
                select: { selected = $0 }, close: { id in items.removeAll { $0.id == id } },
                move: { id, target, after in
                    guard let item = items.first(where: { $0.id == id }), target != id else { return }
                    var next = items.filter { $0.id != id }
                    guard let index = next.firstIndex(where: { $0.id == target }) else { return }
                    next.insert(item, at: index + (after ? 1 : 0)); items = next
                }).frame(height: 48)
            VStack(alignment: .leading, spacing: 16) {
                Text("顺序：" + items.map(\.detail).joined(separator: " → "))
                Text("当前项目：" + (items.first(where: { $0.id == selected })?.detail ?? "未选择"))
                Text("拖动标题、在标签间隙或最右侧释放，确认顺序和选中项目保持独立。")
                Spacer()
                Text("此窗口仅有测试标签，不连接模型，不读取 Key，不运行 Shell。")
            }.padding(24).frame(maxWidth: .infinity, alignment: .leading)
        }
        .frame(minWidth: 1000, minHeight: 600)
        .background(Color(nsColor: NSColor(hex: palette["base"])))
        .foregroundStyle(Color(nsColor: NSColor(hex: palette["text"])))
        .preferredColorScheme(dark ? .dark : .light)
        .onAppear { selected = items[1].id; NSApp.activate(ignoringOtherApps: true) }
    }
}
