import AppKit
import SwiftUI

struct ManagerView: View {
    @ObservedObject var model: ManagerModel
    @State private var isAddProfilePresented = false
    @State private var isTerminalFullScreen = false
    @State private var columnVisibility: NavigationSplitViewVisibility = .all

    var body: some View {
        NavigationSplitView(columnVisibility: $columnVisibility) {
            sidebar
                .navigationSplitViewColumnWidth(min: 210, ideal: 240, max: 290)
        } detail: {
            detail
        }
        .background(.ultraThinMaterial)
        .task {
            if model.profiles.isEmpty && !model.isLoading {
                await model.load()
            }
        }
        .onReceive(NotificationCenter.default.publisher(for: NSWindow.didExitFullScreenNotification)) { _ in
            guard isTerminalFullScreen else { return }
            isTerminalFullScreen = false
            columnVisibility = .all
        }
        .sheet(isPresented: $isAddProfilePresented) {
            AddProfileSheet(model: model)
        }
        .sheet(isPresented: $model.isModelPickerPresented) {
            ModelPickerSheet(model: model)
        }
        .confirmationDialog("恢复此会话？", isPresented: $model.isRestoreConfirmationPresented,
                            titleVisibility: .visible) {
            Button("确认并发送历史上下文", role: .destructive) {
                model.launchConfirmed()
            }
            Button("取消", role: .cancel) {}
        } message: {
            Text("所选会话的历史内容会发送给当前调用 Key 和上游模型；可能产生费用。")
        }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                Image(systemName: "point.3.connected.trianglepath.dotted")
                    .font(.system(size: 20, weight: .medium))
                    .foregroundStyle(Color.mint)
                    .frame(width: 34, height: 34)
                    .background(.white.opacity(0.08), in: RoundedRectangle(cornerRadius: 9))
                VStack(alignment: .leading, spacing: 2) {
                    Text("CCHP")
                        .font(.system(size: 14, weight: .bold))
                    Text("模型管理器")
                        .font(.system(size: 10))
                        .foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 16)
            .padding(.top, 38)
            .padding(.bottom, 18)

            HStack {
                Text("本机调用 Key")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.secondary)
                Spacer()
                Button {
                    isAddProfilePresented = true
                } label: {
                    Image(systemName: "plus")
                }
                .buttonStyle(.borderless)
                .help("添加本机 CCHP 调用 Key")
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 8)

            if model.isLoading {
                ProgressView()
                    .controlSize(.small)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if model.profiles.isEmpty {
                ContentUnavailableView {
                    Label("还没有 Key", systemImage: "key")
                } description: {
                    Text("添加一把本机调用 Key")
                } actions: {
                    Button("添加 Key") { isAddProfilePresented = true }
                        .buttonStyle(.borderedProminent)
                }
                .padding(12)
                Spacer()
            } else {
                List(selection: Binding(
                    get: { model.selectedProfileID.isEmpty ? nil : model.selectedProfileID },
                    set: { value in
                        guard let value, let profile = model.profiles.first(where: { $0.id == value }) else { return }
                        Task { await model.selectProfile(profile) }
                    }
                )) {
                    ForEach(model.profiles) { profile in
                        Label {
                            Text(profile.name)
                                .lineLimit(1)
                        } icon: {
                            Image(systemName: "key.horizontal.fill")
                                .foregroundStyle(profile.id == model.selectedProfileID ? Color.mint : Color.secondary)
                        }
                        .tag(profile.id)
                        .help(profile.name)
                    }
                }
                .listStyle(.sidebar)
                .disabled(model.agentRunning)
                .scrollContentBackground(.hidden)
            }

            Spacer(minLength: 12)
            Divider().opacity(0.35)
            HStack(spacing: 8) {
                Circle()
                    .fill(model.isStatusError ? Color.orange : Color.green)
                    .frame(width: 6, height: 6)
                VStack(alignment: .leading, spacing: 2) {
                    Text("本机网关")
                        .font(.system(size: 10, weight: .medium))
                    Text(URL(string: model.gateway)?.host ?? "127.0.0.1")
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
        }
        .background(.ultraThinMaterial)
    }

    private var detail: some View {
        VStack(spacing: 0) {
            header
                .frame(height: isTerminalFullScreen ? 0 : nil)
                .opacity(isTerminalFullScreen ? 0 : 1)
                .allowsHitTesting(!isTerminalFullScreen)
            Divider().opacity(0.25)
                .frame(height: isTerminalFullScreen ? 0 : 1)
            configuration
                .frame(height: isTerminalFullScreen ? 0 : nil)
                .opacity(isTerminalFullScreen ? 0 : 1)
                .allowsHitTesting(!isTerminalFullScreen)
            Divider().opacity(0.25)
                .frame(height: isTerminalFullScreen ? 0 : 1)
            TerminalPanel(model: model,
                          isFullScreen: isTerminalFullScreen,
                          onToggleFullScreen: toggleTerminalFullScreen)
                .padding(isTerminalFullScreen ? 0 : 16)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            statusBar
                .frame(height: isTerminalFullScreen ? 0 : nil)
                .opacity(isTerminalFullScreen ? 0 : 1)
                .allowsHitTesting(!isTerminalFullScreen)
        }
        .background(.regularMaterial)
    }

    private func toggleTerminalFullScreen() {
        let entering = !isTerminalFullScreen
        isTerminalFullScreen = entering
        columnVisibility = entering ? .detailOnly : .all
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.15) {
            guard let window = NSApp.keyWindow else { return }
            let isWindowFullScreen = window.styleMask.contains(.fullScreen)
            if entering != isWindowFullScreen {
                window.toggleFullScreen(nil)
            }
        }
    }

    private var header: some View {
        HStack(alignment: .center) {
            VStack(alignment: .leading, spacing: 4) {
                Text(model.activeClient.title)
                    .font(.system(size: 21, weight: .semibold))
                Text(model.activeClient == .codex
                     ? "\(model.selectedProfile?.name ?? "本机 Key") · 独立 CODEX_HOME"
                     : (model.selectedProfile?.name ?? "选择本机调用 Key"))
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Picker("客户端", selection: $model.activeClient) {
                ForEach(ClientKind.allCases) { client in
                    Text(client.title).tag(client)
                }
            }
            .pickerStyle(.segmented)
            .frame(width: 150)
            .help("选择要启动的客户端；两者使用隔离配置")
            .disabled(model.agentRunning)
            Spacer()
            Button {
                model.requestLaunch()
            } label: {
                Label("启动 \(model.activeClient.title)", systemImage: "play.fill")
                    .font(.system(size: 12, weight: .semibold))
                    .padding(.horizontal, 4)
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.large)
            .disabled(model.agentRunning || model.selectedProfile == nil || model.selectedModel.isEmpty || model.workspace.isEmpty || model.isLoadingModels)
            .keyboardShortcut(.return, modifiers: [.command])
            .help("按当前 Key、模型、目录和会话方式启动")
        }
        .padding(.horizontal, 22)
        .padding(.top, 42)
        .padding(.bottom, 15)
    }

    private var configuration: some View {
        HStack(alignment: .top, spacing: 22) {
            VStack(alignment: .leading, spacing: 7) {
                fieldLabel("模型")
                Button {
                    model.modelSearch = ""
                    model.isModelPickerPresented = true
                } label: {
                    HStack(spacing: 8) {
                        Image(systemName: "cpu")
                            .foregroundStyle(Color.mint)
                        Text(model.selectedModel.isEmpty ? (model.isLoadingModels ? "正在读取…" : "选择模型") : model.selectedModel)
                            .lineLimit(1)
                            .truncationMode(.middle)
                        Spacer(minLength: 5)
                        if model.isLoadingModels { ProgressView().controlSize(.small) }
                        else { Image(systemName: "chevron.up.chevron.down").font(.system(size: 9, weight: .bold)).foregroundStyle(.tertiary) }
                    }
                    .font(.system(size: 12, design: .monospaced))
                    .padding(.horizontal, 11)
                    .frame(height: 36)
                    .background(.white.opacity(0.055), in: RoundedRectangle(cornerRadius: 7))
                    .overlay(RoundedRectangle(cornerRadius: 7).stroke(.white.opacity(0.09), lineWidth: 1))
                }
                .buttonStyle(.plain)
                .disabled(model.models.isEmpty || model.agentRunning)
                Text("\(model.models.count) 个可用模型")
                    .font(.system(size: 10))
                    .foregroundStyle(.tertiary)
            }
            .frame(minWidth: 210, maxWidth: 320)

            VStack(alignment: .leading, spacing: 7) {
                fieldLabel("工作目录")
                HStack(spacing: 8) {
                    Button {
                        model.chooseWorkspace()
                    } label: {
                        HStack(spacing: 7) {
                            Image(systemName: "folder")
                                .foregroundStyle(Color.orange.opacity(0.9))
                            Text(model.workspace.isEmpty ? "选择文件夹" : URL(fileURLWithPath: model.workspace).lastPathComponent)
                                .lineLimit(1)
                                .truncationMode(.middle)
                            Spacer(minLength: 2)
                            Image(systemName: "arrow.up.forward.app").font(.system(size: 10)).foregroundStyle(.tertiary)
                        }
                        .font(.system(size: 12))
                        .padding(.horizontal, 11)
                        .frame(height: 36)
                        .background(.white.opacity(0.055), in: RoundedRectangle(cornerRadius: 7))
                        .overlay(RoundedRectangle(cornerRadius: 7).stroke(.white.opacity(0.09), lineWidth: 1))
                    }
                    .buttonStyle(.plain)
                    .help(model.workspace)
                    .disabled(model.agentRunning)

                    Menu {
                        ForEach(model.workspaces, id: \.self) { path in
                            Button(URL(fileURLWithPath: path).lastPathComponent) { model.setWorkspace(path) }
                                .help(path)
                        }
                        Divider()
                        Button("选择其他目录…", systemImage: "folder.badge.plus") { model.chooseWorkspace() }
                    } label: {
                        Image(systemName: "clock.arrow.circlepath")
                            .frame(width: 34, height: 34)
                            .background(.white.opacity(0.055), in: RoundedRectangle(cornerRadius: 7))
                    }
                    .menuStyle(.borderlessButton)
                    .help("最近使用的目录")
                .disabled(model.agentRunning)
                }
                Text(model.workspace.isEmpty ? "" : model.workspace)
                    .font(.system(size: 9, design: .monospaced))
                    .foregroundStyle(.tertiary)
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            .frame(minWidth: 210, maxWidth: .infinity)

            VStack(alignment: .leading, spacing: 7) {
                fieldLabel("会话")
                Picker("会话", selection: $model.sessionMode) {
                    ForEach(SessionMode.allCases) { mode in
                        Text(mode.title).tag(mode)
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .frame(height: 36)
                .disabled(model.agentRunning)
                if model.sessionMode != .new {
                    Label("恢复时会转发历史内容", systemImage: "arrowshape.turn.up.right")
                        .font(.system(size: 10))
                        .foregroundStyle(Color.orange)
                } else {
                    Text("历史按工作目录隔离")
                        .font(.system(size: 10))
                        .foregroundStyle(.tertiary)
                }
            }
            .frame(minWidth: 210, maxWidth: 320)

        }
        .padding(.horizontal, 22)
        .padding(.vertical, 14)
        .background(.ultraThinMaterial)
    }

    private var statusBar: some View {
        HStack(spacing: 8) {
            Image(systemName: model.isStatusError ? "exclamationmark.circle.fill" : "checkmark.circle.fill")
                .foregroundStyle(model.isStatusError ? Color.orange : Color.mint)
                .font(.system(size: 11))
            Text(model.statusMessage)
                .font(.system(size: 10))
                .foregroundStyle(model.isStatusError ? Color.orange : Color.secondary)
                .lineLimit(1)
            Spacer()
            Text("8081")
                .font(.system(size: 10, design: .monospaced))
                .foregroundStyle(.tertiary)
        }
        .padding(.horizontal, 18)
        .frame(height: 32)
        .background(.ultraThinMaterial)
    }

    private func fieldLabel(_ title: String) -> some View {
        Text(title)
            .font(.system(size: 10, weight: .semibold))
            .foregroundStyle(.secondary)
    }

}

private struct ModelPickerSheet: View {
    @ObservedObject var model: ManagerModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text("选择 \(model.activeClient.title) 模型")
                        .font(.system(size: 18, weight: .semibold))
                    Text("\(model.models.count) 个模型 · \(model.selectedProfile?.name ?? "")")
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button("完成") { dismiss() }
                    .keyboardShortcut(.defaultAction)
            }
            .padding(18)

            TextField("搜索模型名称", text: $model.modelSearch)
                .textFieldStyle(.roundedBorder)
                .padding(.horizontal, 18)
                .padding(.bottom, 10)

            List(model.filteredModels, id: \.self, selection: Binding(
                get: { model.selectedModel },
                set: { value in
                    if let value { model.selectedModel = value; dismiss() }
                }
            )) { item in
                HStack {
                    Text(item)
                        .font(.system(size: 12, design: .monospaced))
                    Spacer()
                    if item == model.selectedModel {
                        Image(systemName: "checkmark")
                            .foregroundStyle(Color.mint)
                    }
                }
                .tag(item)
            }
            .listStyle(.inset)
        }
        .frame(width: 520, height: 620)
        .background(.regularMaterial)
    }
}

private struct AddProfileSheet: View {
    @ObservedObject var model: ManagerModel
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var key = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Image(systemName: "key.horizontal")
                    .font(.system(size: 18))
                    .foregroundStyle(Color.mint)
                Text("添加本机调用 Key")
                    .font(.system(size: 17, weight: .semibold))
                Spacer()
                Button { dismiss() } label: { Image(systemName: "xmark") }
                    .buttonStyle(.borderless)
                    .help("关闭")
            }

            VStack(alignment: .leading, spacing: 6) {
                Text("名称")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(.secondary)
                TextField("例如：AutoBits 付费", text: $name)
                    .textFieldStyle(.roundedBorder)
            }

            VStack(alignment: .leading, spacing: 6) {
                Text("本机 CCHP 下游 Key")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(.secondary)
                SecureField("输入隐藏，不要填上游 Key", text: $key)
                    .textFieldStyle(.roundedBorder)
            }

            Label("只查询此 Key 的模型列表；不会发起推理。Key 以权限 600 保存于本机。",
                  systemImage: "lock.shield")
                .font(.system(size: 11))
                .foregroundStyle(.secondary)

            if !model.addProfileError.isEmpty {
                Text(model.addProfileError)
                    .font(.system(size: 11))
                    .foregroundStyle(Color.orange)
                    .textSelection(.enabled)
            }
            if !model.addProfileStatus.isEmpty {
                Text(model.addProfileStatus)
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
            }

            HStack {
                Button("取消") { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Spacer()
                Button {
                    Task {
                        if await model.addProfile(name: name, key: key) {
                            key = ""
                            dismiss()
                        }
                    }
                } label: {
                    if model.isAddingProfile {
                        ProgressView().controlSize(.small)
                    } else {
                        Text("验证并保存")
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || key.isEmpty || model.isAddingProfile)
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(22)
        .frame(width: 440)
        .background(.regularMaterial)
    }
}
