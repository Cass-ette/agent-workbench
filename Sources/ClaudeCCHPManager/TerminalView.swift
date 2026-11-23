import AppKit
import SwiftTerm
import SwiftUI

/// Accepts Finder file drops and inserts shell-safe paths into the active PTY.
/// The trailing space lets the user keep composing the command without running it.
@MainActor
final class FileDropTerminalView: LocalProcessTerminalView {
    var onLocalInput: (() -> Void)?
    var remotePTYSize: winsize?
    var remoteSizeIsActive: (() -> Bool)?
    override func getWindowSize() -> winsize {
        if remoteSizeIsActive?() == true, let size = remotePTYSize { return size }
        return super.getWindowSize()
    }
    override func send(source: TerminalView, data: ArraySlice<UInt8>) {
        onLocalInput?()
        super.send(source: source, data: data)
    }
    private(set) var isSelectedForInput = false
    var attachmentDirectory = FileManager.default.homeDirectoryForCurrentUser
        .appendingPathComponent(".config/cchp-client/claude-test/attachments", isDirectory: true)

    func setSelected(_ selected: Bool) {
        isSelectedForInput = selected
        if selected {
            isHidden = false
            registerForDraggedTypes([.fileURL, .png, .tiff])
        } else {
            unregisterDraggedTypes()
            if let window, window.firstResponder === self { window.makeFirstResponder(nil) }
            isHidden = true
        }
    }

    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation {
        guard isSelectedForInput, !isHiddenOrHasHiddenAncestor,
              sender.draggingSourceOperationMask.contains(.copy),
              (!fileURLs(from: sender.draggingPasteboard).isEmpty ||
               NSImage.canInit(with: sender.draggingPasteboard)) else { return [] }
        return .copy
    }

    override func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation {
        draggingEntered(sender)
    }

    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        performFileDrop(from: sender.draggingPasteboard)
    }

    func performFileDrop(from pasteboard: NSPasteboard) -> Bool {
        guard isSelectedForInput, !isHiddenOrHasHiddenAncestor else { return false }
        var urls = fileURLs(from: pasteboard)
        if urls.isEmpty {
            do {
                if let image = try ClipboardAttachment.saveImage(from: pasteboard, in: attachmentDirectory) {
                    urls = [image]
                }
            } catch {
                if let window {
                    let alert = NSAlert(error: error)
                    alert.beginSheetModal(for: window)
                } else { NSSound.beep() }
                return true // Consume a failed image paste; never fall through to hidden text.
            }
        }
        guard !urls.isEmpty else { return false }
        window?.makeFirstResponder(self)
        pasteText(urls.map { Self.shellQuote($0.path) }.joined(separator: " ") + " ")
        return true
    }

    override func paste(_ sender: Any) {
        guard isSelectedForInput, !isHiddenOrHasHiddenAncestor else { return }
        if !performFileDrop(from: .general) { super.paste(sender) }
    }

    override func keyDown(with event: NSEvent) {
        guard isSelectedForInput, !isHiddenOrHasHiddenAncestor else { return }
        // CLI users also expect Ctrl+V to attach a screenshot on macOS.
        if event.modifierFlags.intersection(.deviceIndependentFlagsMask) == .control,
           event.charactersIgnoringModifiers?.lowercased() == "v",
           performFileDrop(from: .general) { return }
        super.keyDown(with: event)
    }

    private func fileURLs(from pasteboard: NSPasteboard) -> [URL] {
        (pasteboard.readObjects(forClasses: [NSURL.self],
                                options: [.urlReadingFileURLsOnly: true]) as? [URL]) ?? []
    }

    private static func shellQuote(_ path: String) -> String {
        "'" + path.replacingOccurrences(of: "'", with: "'\\''") + "'"
    }
}

@MainActor
final class EmbeddedTerminalControl: ObservableObject {
    nonisolated let replay = RemoteTerminalBuffer()
    private var terminalView: FileDropTerminalView?
    private var processDelegateObject: AnyObject?
    var existingView: FileDropTerminalView? { terminalView }

    func keepProcessDelegate(_ delegate: AnyObject) { processDelegateObject = delegate }
    private var isSelected = false
    private var appliedStyle: TerminalStyleSettings?

    func attach(_ view: FileDropTerminalView) {
        terminalView?.setSelected(false)
        terminalView = view
        appliedStyle = nil
        view.setSelected(isSelected)
        let buffer = replay
        view.setProcessByteHandler { buffer.append($0) }
        view.remoteSizeIsActive = { buffer.hasRemoteControl }
        view.onLocalInput = { [weak view] in
            buffer.release()
            if let view {
                var size = view.getWindowSize()
                _ = view.process.updateWindowSize(&size)
            }
        }
    }

    func detach(_ view: FileDropTerminalView) {
        view.setSelected(false)
        // The session owns its PTY even while no desktop window presents it.
    }

    func setSelected(_ selected: Bool) {
        isSelected = selected
        // Revoke old destinations in the same action as the tab selection.
        terminalView?.setSelected(selected)
    }

    func stop() {
        terminalView?.terminate()
    }

    func remoteInput(_ bytes: Data, client: String, inputID: String) throws {
        guard let terminalView else { throw RemoteWorkbenchError.invalid("终端正在准备，请稍后重试") }
        if try replay.requireControl(client, inputID: inputID) {
            terminalView.process.send(data: Array(bytes)[...])
        }
    }

    func remoteResize(cols: Int, rows: Int, client: String) throws {
        guard let terminalView else { throw RemoteWorkbenchError.invalid("终端正在准备") }
        guard try replay.requireControl(client) else { return }
        var size = winsize(ws_row: UInt16(max(2, min(rows, 200))), ws_col: UInt16(max(10, min(cols, 400))), ws_xpixel: 0, ws_ypixel: 0)
        terminalView.remotePTYSize = size
        _ = terminalView.process.updateWindowSize(&size)
    }

    func apply(_ style: SharedTerminalStyle, to view: FileDropTerminalView) {
        guard appliedStyle != style.settings else { return }
        style.apply(to: view)
        appliedStyle = style.settings
    }

    func focus() {
        guard let terminalView, terminalView.isSelectedForInput,
              !terminalView.isHiddenOrHasHiddenAncestor else { return }
        terminalView.window?.makeFirstResponder(terminalView)
    }
}

struct EmbeddedAgentTerminal: NSViewRepresentable {
    @ObservedObject var control: EmbeddedTerminalControl
    @ObservedObject var style: SharedTerminalStyle
    let launch: TerminalLaunchSpec?
    let onExit: (Int32?) -> Void

    func makeCoordinator() -> Coordinator {
        Coordinator(control: control, onExit: onExit)
    }

    func makeNSView(context: Context) -> FileDropTerminalView {
        if let view = control.existingView {
            view.processDelegate = context.coordinator
            control.keepProcessDelegate(context.coordinator)
            control.attach(view)
            control.apply(style, to: view)
            return view
        }
        let view = FileDropTerminalView(frame: .zero, font: style.font,
                                        options: TerminalOptions(scrollback: style.settings.scrollback))
        view.processDelegate = context.coordinator
        control.keepProcessDelegate(context.coordinator)
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

    func updateNSView(_ view: FileDropTerminalView, context: Context) {
        control.apply(style, to: view)
    }

    static func dismantleNSView(_ view: FileDropTerminalView, coordinator: Coordinator) {
        coordinator.control?.detach(view)
    }

    @MainActor
    final class Coordinator: NSObject, LocalProcessTerminalViewDelegate {
        weak var control: EmbeddedTerminalControl?
        private let onExit: (Int32?) -> Void

        init(control: EmbeddedTerminalControl, onExit: @escaping (Int32?) -> Void) {
            self.control = control
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
                .disabled(!model.canLaunch)
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
                                               onSelect: { model.selectTerminal(session.id) },
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
                                        onExit: { [weak model, weak session] code in
                                            if let session { model?.processDidExit(sessionID: session.id, exitCode: code) }
                                        })
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
            .onChange(of: isSelected, initial: true) { _, selected in
                if selected { DispatchQueue.main.async { session.control.focus() } }
            }
    }
}
