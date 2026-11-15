import AppKit
import SwiftTerm
import SwiftUI

@MainActor
final class EmbeddedTerminalControl: ObservableObject {
    private weak var terminalView: LocalProcessTerminalView?

    func attach(_ view: LocalProcessTerminalView) {
        terminalView = view
    }

    func stop() {
        terminalView?.terminate()
    }
}

struct EmbeddedAgentTerminal: NSViewRepresentable {
    @ObservedObject var control: EmbeddedTerminalControl
    let launch: TerminalLaunchSpec?
    let onExit: (Int32?) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(onExit: onExit)
    }

    func makeNSView(context: Context) -> LocalProcessTerminalView {
        let view = LocalProcessTerminalView(frame: .zero)
        view.processDelegate = context.coordinator
        control.attach(view)

        if let launch {
            view.startProcess(executable: launch.executable,
                              args: launch.arguments,
                              environment: launch.environment,
                              currentDirectory: launch.workspace)
        } else {
            view.feed(text: "\u{1b}[2m选择客户端、模型和工作目录后，点击「启动」开始会话。\u{1b}[0m\r\n")
        }
        return view
    }

    func updateNSView(_ view: LocalProcessTerminalView, context: Context) {
        control.attach(view)
    }

    @MainActor
    final class Coordinator: NSObject, LocalProcessTerminalViewDelegate {
        private let onExit: (Int32?) -> Void

        init(onExit: @escaping (Int32?) -> Void) {
            self.onExit = onExit
        }

        func sizeChanged(source: LocalProcessTerminalView, newCols: Int, newRows: Int) {}
        func setTerminalTitle(source: LocalProcessTerminalView, title: String) {}
        func hostCurrentDirectoryUpdate(source: TerminalView, directory: String?) {}

        func processTerminated(source: TerminalView, exitCode: Int32?) {
            onExit(exitCode)
        }

        func processFailedToStart(source: TerminalView, error: LocalProcessError) {
            onExit(127)
        }
    }
}

struct TerminalPanel: View {
    @ObservedObject var model: ManagerModel
    let isFullScreen: Bool
    let onToggleFullScreen: () -> Void
    @StateObject private var control = EmbeddedTerminalControl()
    @State private var idleViewID = UUID()

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Circle()
                    .fill(model.agentRunning ? Color(red: 0.34, green: 0.82, blue: 0.62) : Color.secondary)
                    .frame(width: 7, height: 7)
                Text(model.agentRunning ? "\(model.activeClient.title) 正在运行" : "内嵌终端")
                    .font(.system(size: 13, weight: .semibold))
                Spacer()
                Text(model.workspace.isEmpty ? NSHomeDirectory() : model.workspace)
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                if model.agentRunning {
                    Button(role: .destructive) {
                        control.stop()
                    } label: {
                        Image(systemName: "stop.fill")
                    }
                    .buttonStyle(.borderless)
                    .help("停止当前 agent")
                }
                Button {
                    onToggleFullScreen()
                } label: {
                    Image(systemName: isFullScreen ? "arrow.down.right.and.arrow.up.left" : "arrow.up.left.and.arrow.down.right")
                }
                .buttonStyle(.borderless)
                .help(isFullScreen ? "退出终端全屏" : "终端全屏")
            }
            .padding(.horizontal, 14)
            .frame(height: 40)
            .background(.ultraThinMaterial)

            EmbeddedAgentTerminal(control: control,
                                  launch: model.embeddedLaunchSpec,
                                  onExit: model.processDidExit)
                .id(model.embeddedLaunchSpec?.id ?? idleViewID)
                .background(Color(red: 0.055, green: 0.065, blue: 0.07))
        }
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 10))
        .overlay {
            RoundedRectangle(cornerRadius: 10)
                .strokeBorder(.white.opacity(0.12), lineWidth: 1)
        }
        .clipShape(RoundedRectangle(cornerRadius: 10))
    }
}
