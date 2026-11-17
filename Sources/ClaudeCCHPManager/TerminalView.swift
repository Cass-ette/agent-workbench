import AppKit
import SwiftTerm
import SwiftUI

@MainActor
final class EmbeddedTerminalControl: ObservableObject {
    private weak var terminalView: LocalProcessTerminalView?
    private var appliedStyle: TerminalStyleSettings?

    func attach(_ view: LocalProcessTerminalView) {
        terminalView = view
    }

    func stop() {
        terminalView?.terminate()
    }

    func apply(_ style: SharedTerminalStyle, to view: LocalProcessTerminalView) {
        guard appliedStyle != style.settings else { return }
        style.apply(to: view)
        appliedStyle = style.settings
    }

    func focus() {
        if let terminalView { terminalView.window?.makeFirstResponder(terminalView) }
    }
}

struct EmbeddedAgentTerminal: NSViewRepresentable {
    @ObservedObject var control: EmbeddedTerminalControl
    @ObservedObject var style: SharedTerminalStyle
    let launch: TerminalLaunchSpec?
    let onExit: (Int32?) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(onExit: onExit)
    }

    func makeNSView(context: Context) -> LocalProcessTerminalView {
        let view = LocalProcessTerminalView(frame: .zero, font: style.font,
                                            options: TerminalOptions(scrollback: style.settings.scrollback))
        view.processDelegate = context.coordinator
        control.attach(view)
        control.apply(style, to: view)

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
        control.apply(style, to: view)
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
    let isEmpty: Bool
    let onChooseWorkspace: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Circle()
                    .fill(model.selectedTerminal?.isRunning == true ? Color.accentColor : Color.secondary)
                    .frame(width: 7, height: 7)
                Text(model.selectedTerminal?.title ?? "终端工作区")
                    .font(.system(size: 13, weight: .semibold))
                Spacer()
                Text(model.selectedTerminal?.workspace ?? "每个标签运行独立进程")
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                if let session = model.selectedTerminal, session.isRunning {
                    Button(role: .destructive) {
                        session.stop()
                    } label: {
                        Image(systemName: "stop.fill")
                    }
                    .buttonStyle(.borderless)
                    .help("停止当前标签中的 agent")
                }
                Button {
                    model.requestLaunch()
                } label: {
                    Image(systemName: "plus")
                }
                .buttonStyle(.borderless)
                .disabled(model.selectedProfile == nil || model.selectedModel.isEmpty || model.workspace.isEmpty || model.isLoadingModels)
                .help("按上方当前配置新建一个独立终端会话")
                Button {
                    model.showTerminalAppearance()
                } label: {
                    Image(systemName: "paintbrush")
                }
                .help("共享终端外观配置")
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
            .background(.bar)

            HStack(spacing: 0) {
                ScrollView(.horizontal) {
                    HStack(spacing: 6) {
                        ForEach(model.terminalSessions) { session in
                            TerminalSessionTab(session: session,
                                               selected: model.selectedTerminalID == session.id,
                                               onSelect: { model.selectedTerminalID = session.id },
                                               onClose: { model.closeSession(session) })
                        }
                    }
                    .padding(.horizontal, 8)
                    .padding(.vertical, 6)
                }
                .scrollIndicators(.hidden)
            }
            .frame(height: model.terminalSessions.isEmpty ? 0 : 42)
            .clipped()
            .background(.bar)
            .overlay(alignment: .bottom) { Divider() }

            ZStack {
                if isEmpty {
                    VStack(spacing: 10) {
                        Image(systemName: "terminal")
                            .font(.system(size: 28, weight: .light))
                            .foregroundStyle(Color(nsColor: NSColor(hex: model.terminalStyle.settings.foreground)).opacity(0.58))
                        Text("准备运行")
                            .font(.system(size: 15, weight: .medium))
                            .foregroundStyle(Color(nsColor: NSColor(hex: model.terminalStyle.settings.foreground)))
                        Text("选择模型和项目目录，然后启动一个 Agent 任务。")
                            .font(.system(size: 12))
                            .foregroundStyle(Color(nsColor: NSColor(hex: model.terminalStyle.settings.foreground)).opacity(0.7))
                        if model.workspace.isEmpty {
                            Button("选择项目目录", action: onChooseWorkspace)
                                .buttonStyle(.bordered)
                                .controlSize(.small)
                        }
                    }
                }
                ForEach(model.terminalSessions) { session in
                    TerminalSessionPane(session: session, style: model.terminalStyle,
                                        isSelected: model.selectedTerminalID == session.id,
                                        onExit: { code in model.processDidExit(sessionID: session.id, exitCode: code) })
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Color(nsColor: NSColor(hex: model.terminalStyle.settings.background)))
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Color(nsColor: NSColor(hex: model.terminalStyle.settings.background)))
    }
}

private struct TerminalSessionTab: View {
    @ObservedObject var session: TerminalSession
    let selected: Bool
    let onSelect: () -> Void
    let onClose: () -> Void

    var body: some View {
        HStack(spacing: 7) {
            Circle()
                .fill(session.isRunning ? Color.accentColor : Color.secondary)
                .frame(width: 6, height: 6)
            Button(action: onSelect) {
                VStack(alignment: .leading, spacing: 1) {
                    Text(session.title)
                        .font(.system(size: 10, weight: .medium))
                        .lineLimit(1)
                    Text("\(session.workspaceName) · \(session.profileName)")
                        .font(.system(size: 9))
                        .foregroundStyle(.secondary)
                        .lineLimit(1)
                }
                .frame(maxWidth: 180, alignment: .leading)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            Button(action: onClose) {
                Image(systemName: "xmark")
                    .font(.system(size: 8, weight: .bold))
                    .foregroundStyle(.tertiary)
                    .frame(width: 18, height: 18)
                    .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            .help(session.isRunning ? "停止并关闭此会话标签" : "关闭会话标签")
        }
        .padding(.horizontal, 10)
        .frame(height: 36)
        .foregroundStyle(selected ? .primary : .secondary)
        .background(alignment: .bottom) {
            Rectangle()
                .fill(selected ? Color.accentColor : .clear)
                .frame(height: 2)
        }
    }
}

private struct TerminalSessionPane: View {
    @ObservedObject var session: TerminalSession
    @ObservedObject var style: SharedTerminalStyle
    let isSelected: Bool
    let onExit: (Int32?) -> Void

    var body: some View {
        EmbeddedAgentTerminal(control: session.control, style: style, launch: session.launchSpec, onExit: onExit)
            .id(session.id)
            .opacity(isSelected ? 1 : 0)
            .allowsHitTesting(isSelected)
            .accessibilityHidden(!isSelected)
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .onChange(of: isSelected) { _, selected in
                if selected { DispatchQueue.main.async { session.control.focus() } }
            }
    }
}
