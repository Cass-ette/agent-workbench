import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
            guard let window = NSApp.windows.first(where: { $0.isVisible }) else { return }
            window.title = "Agent 工作台"
            window.titleVisibility = .hidden
            window.titlebarAppearsTransparent = true
            window.isOpaque = false
            window.backgroundColor = .clear
            window.isMovableByWindowBackground = true
            window.setFrameAutosaveName("ClaudeCCHPManager")
        }
    }
}

@main
struct ClaudeCCHPManagerApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var appDelegate
    @StateObject private var model = ManagerModel()

    var body: some Scene {
        WindowGroup {
            ManagerView(model: model)
                .frame(minWidth: 960, minHeight: 680)
                .tint(.accentColor)
                .task { await model.load() }
        }
        .windowStyle(.hiddenTitleBar)
        .windowToolbarStyle(.unifiedCompact)
        .defaultSize(width: 1180, height: 780)
    }
}
