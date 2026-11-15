import AppKit
import SwiftUI

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
            guard let window = NSApp.windows.first(where: { $0.isVisible }) else { return }
            window.title = "CCHP Model Manager"
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
                .preferredColorScheme(.dark)
                .tint(Color(red: 0.42, green: 0.82, blue: 0.68))
                .task { await model.load() }
        }
        .windowStyle(.hiddenTitleBar)
        .windowToolbarStyle(.unifiedCompact)
        .defaultSize(width: 1180, height: 780)
    }
}
