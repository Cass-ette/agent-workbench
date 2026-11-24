import AppKit
import SwiftUI

struct ManagerView: View {
    @ObservedObject var model: ManagerModel
    @ObservedObject var updates: WorkbenchUpdates
    @State private var isAddProfilePresented = false
    @State private var profilePendingDeletion: KeyProfile?
    @State private var isProfileDeleteConfirmationPresented = false
    @State private var isTerminalFullScreen = false
    @State private var isTaskSetupVisible = true
    @State private var columnVisibility: NavigationSplitViewVisibility = .all
    @State private var isUsagePresented = false
    @State private var showSSHConnection = false

    var body: some View {
        NavigationSplitView(columnVisibility: $columnVisibility) {
            sidebar
                .navigationSplitViewColumnWidth(min: 180, ideal: 205, max: 245)
        } detail: {
            detail
        }
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
        .onChange(of: model.terminalSessions.count) { oldValue, newValue in
            if newValue > oldValue {
                isTaskSetupVisible = false
            } else if newValue == 0 {
                isTaskSetupVisible = true
            }
        }
        .task {
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(15)) } catch { return }
                await model.refreshUsage()
                await model.refreshGatewayUsage()
            }
        }
        .sheet(isPresented: $isAddProfilePresented) {
            AddProfileSheet(model: model)
        }
        .sheet(isPresented: $updates.show) {
            WorkbenchUpdateSheet(updates: updates, manager: model)
        }
        .sheet(isPresented: $model.isModelPickerPresented) {
            ModelPickerSheet(model: model)
        }
        .sheet(isPresented: $model.isGatewaySettingsPresented) {
            GatewaySettingsSheet(model: model)
        }
        .sheet(isPresented: $model.isTerminalAppearancePresented) {
            TerminalAppearanceSheet(model: model)
        }
        .sheet(isPresented: $model.isBillingSettingsPresented) {
            BillingSettingsSheet(model: model)
        }
        .sheet(isPresented: $showSSHConnection) { SSHConnectionSheet(model: model) }
        .sheet(isPresented: $model.isHistoryPresented) {
            HistoryPickerSheet(model: model)
        }
        .sheet(isPresented: $model.isAgentPermissionsPresented) {
            AgentPermissionsSheet(model: model)
        }
        .sheet(isPresented: $model.isToolsPresented) { ToolBridgeSheet(model: model) }
        .confirmationDialog("从历史创建分支？", isPresented: $model.isRestoreConfirmationPresented,
                            titleVisibility: .visible) {
            Button("创建分支并启动") { model.launchConfirmed() }
            Button("取消", role: .cancel) {}
        } message: {
            if let history = model.selectedHistory {
                Text("对话：\(history.title)\n调用配置：\(model.selectedProfile?.name ?? "")\n模型：\(model.selectedModel)\n网关：\(model.gateway)\n\n新分支使用这套配置，保留原对话。历史上下文会发送到该网关，可能产生费用。")
            }
        }
        .confirmationDialog("删除本机调用配置？", isPresented: $isProfileDeleteConfirmationPresented,
                            titleVisibility: .visible) {
            if let profile = profilePendingDeletion {
                Button("删除「\(profile.name)」", role: .destructive) {
                    Task { await model.deleteProfile(profile) }
                    profilePendingDeletion = nil
                }
            }
            Button("取消", role: .cancel) { profilePendingDeletion = nil }
        } message: {
            if let profile = profilePendingDeletion {
                let active = model.terminalSessions.filter { $0.isRunning && $0.profileID == profile.id }.count
                Text("将删除这把 Key 和该配置的只读账单 Token，不会删除历史对话。\(active > 0 ? "已有 \(active) 个运行中的终端仍可能持有 Key。" : "")")
            }
        }
    }

    private var sidebar: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 10) {
                Image(systemName: "terminal")
                    .font(.system(size: 17, weight: .medium))
                    .foregroundStyle(.secondary)
                    .frame(width: 26, height: 26)
                VStack(alignment: .leading, spacing: 2) {
                    #if WORKBENCH_LOCAL_UPDATE_TEST
                    Text("工作台更新测试")
                        .font(.system(size: 13, weight: .semibold))
                    Text("独立配置 · \(updates.current)")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(.secondary)
                    #else
                    Text("Agent 工作台")
                        .font(.system(size: 13, weight: .semibold))
                    Text("终端工作区")
                        .font(.system(size: 10))
                        .foregroundStyle(.secondary)
                    #endif
                }
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 16)
            .padding(.top, 14)
            .padding(.bottom, 20)
            Button { updates.open() } label: {
                Label(updates.nextVersion.map { "新版 \($0)" } ?? "检查更新",
                      systemImage: updates.nextVersion == nil ? "arrow.triangle.2.circlepath" : "arrow.down.circle")
            }
            .buttonStyle(.borderless)
            .help("当前版本 \(updates.current) · 查看更新说明或检查新版本")
            .padding(.horizontal, 16)
            .padding(.bottom, 14)

            Button {
                isTaskSetupVisible = true
                Task { await model.showGlobalHistory() }
            } label: {
                Label("恢复最近对话", systemImage: "clock.arrow.circlepath")
            }
            .buttonStyle(.borderless)
            .padding(.horizontal, 16)
            .padding(.bottom, 14)

            Button { showSSHConnection = true } label: {
                Label("手机连接", systemImage: "iphone")
            }
            .buttonStyle(.borderless)
            .padding(.horizontal, 16)
            .padding(.bottom, 14)

            HStack {
                Text("调用配置")
                    .font(.system(size: 11, weight: .semibold))
                    .foregroundStyle(.secondary)
                Spacer()
                #if !WORKBENCH_LOCAL_UPDATE_TEST
                Button {
                    isAddProfilePresented = true
                } label: {
                    Image(systemName: "plus")
                }
                .buttonStyle(.borderless)
                .disabled(model.launchScope == .system)
                .help("添加当前网关签发的调用 Key")
                #endif
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 8)

            if model.isLoading {
                ProgressView()
                    .controlSize(.small)
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if model.profiles.isEmpty {
                #if WORKBENCH_LOCAL_UPDATE_TEST
                ContentUnavailableView {
                    Label("测试配置", systemImage: "key")
                } description: {
                    Text("已保存的 Key 请在桌面上的正式工作台查看。")
                }
                .padding(12)
                #else
                ContentUnavailableView {
                    Label("还没有 Key", systemImage: "key")
                } description: {
                    Text("添加一把本机调用 Key")
                } actions: {
                    Button("添加 Key") { isAddProfilePresented = true }
                        .buttonStyle(.borderedProminent)
                }
                .padding(12)
                #endif
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
                        HStack(spacing: 4) {
                            Label {
                                Text(profile.name)
                                    .lineLimit(1)
                            } icon: {
                                Image(systemName: "key.horizontal.fill")
                                    .foregroundStyle(profile.id == model.selectedProfileID ? Color.accentColor : Color.secondary)
                            }
                            Spacer(minLength: 2)
                            if profile.id != "free" {
                                Button(role: .destructive) {
                                    profilePendingDeletion = profile
                                    isProfileDeleteConfirmationPresented = true
                                } label: {
                                    Image(systemName: "trash")
                                        .font(.system(size: 10))
                                        .frame(width: 22, height: 22)
                                        .contentShape(Rectangle())
                                }
                                .buttonStyle(.borderless)
                                .disabled(model.isDeletingProfile)
                                .help("删除本机 Key 配置")
                            }
                        }
                        .tag(profile.id)
                        .help(profile.name)
                    }
                }
                .listStyle(.sidebar)
                .scrollContentBackground(.hidden)
                .disabled(model.launchScope == .system)
            }

            Button {
                model.isAgentPermissionsPresented = true
            } label: {
                Label("权限设置", systemImage: "hand.raised")
            }
            .buttonStyle(.borderless)
            .padding(.horizontal, 16)
            .padding(.vertical, 10)

            Button { model.isToolsPresented = true } label: {
                Label("工具与插件", systemImage: "puzzlepiece.extension")
            }.buttonStyle(.borderless).padding(.horizontal, 16).padding(.vertical, 6)

            Spacer(minLength: 12)
            Divider().opacity(0.35)
            HStack(spacing: 8) {
                Circle()
                    .fill(model.isStatusError ? Color.orange : Color.accentColor)
                    .frame(width: 6, height: 6)
                VStack(alignment: .leading, spacing: 2) {
                    Text("本机网关")
                        .font(.system(size: 10, weight: .medium))
                    Text(URL(string: model.gateway)?.host ?? "127.0.0.1")
                        .font(.system(size: 10, design: .monospaced))
                        .foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
                Button {
                    model.showGatewaySettings()
                } label: {
                    Image(systemName: "slider.horizontal.3")
                }
                .buttonStyle(.borderless)
                .help("设置本机或远程网关地址")
            }
            .padding(.horizontal, 16)
            .padding(.vertical, 12)
        }
    }

    private var detail: some View {
        VStack(spacing: 0) {
            if !isTerminalFullScreen {
                if !model.terminalSessions.isEmpty && !isTaskSetupVisible {
                    taskContextBar
                } else {
                    configuration
                }
                Divider()
            }
            TerminalPanel(model: model,
                          isFullScreen: isTerminalFullScreen,
                          onToggleFullScreen: toggleTerminalFullScreen,
                          isEmpty: model.terminalSessions.isEmpty,
                          onChooseWorkspace: model.chooseWorkspace)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
            if !isTerminalFullScreen {
                statusBar
            }
        }
        .background(.background)
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

    private var taskContextBar: some View {
        HStack(spacing: 12) {
            Image(systemName: model.selectedTerminal?.client == .codex ? "terminal.fill" : "bubble.left.and.bubble.right.fill")
                .foregroundStyle(.secondary)
            VStack(alignment: .leading, spacing: 2) {
                Text("当前终端")
                    .font(.system(size: 10, weight: .semibold))
                    .foregroundStyle(.secondary)
                Text("\(model.selectedTerminal?.client.title ?? "Agent") · \(model.selectedTerminal?.model ?? "") · \(model.selectedTerminal?.workspaceName ?? "") · \(model.runningSessionCount) 个运行中")
                    .font(.system(size: 11, design: .monospaced))
                    .lineLimit(1)
                    .truncationMode(.middle)
                Text(model.selectedTerminal?.permissions ?? "")
                    .font(.system(size: 10))
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button {
                isTaskSetupVisible = true
            } label: {
                Label("配置任务", systemImage: "slider.horizontal.3")
            }
            .buttonStyle(.borderless)
            .help("展开模型、项目和会话配置")
            Button {
                model.requestLaunch()
            } label: {
                Label("新建任务", systemImage: "plus")
            }
            .buttonStyle(.borderedProminent)
            .controlSize(.small)
            .disabled(!model.canLaunch)
            .help(model.launchScope == .system ? "使用所选客户端的系统配置新增终端" : "使用当前工作台配置新增一个独立终端")
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 9)
        .background(.bar)
    }

    private var configuration: some View {
        HStack(spacing: 10) {
            Picker("客户端", selection: $model.activeClient) {
                ForEach(ClientKind.allCases) { client in
                    Text(client.title).tag(client)
                }
            }
            .pickerStyle(.segmented)
            .frame(width: 130)
            .help("选择要启动的 Agent")

            Picker("启动方式", selection: $model.launchScope) {
                ForEach(LaunchScope.allCases) { scope in
                    Text(scope.title).tag(scope)
                }
            }
            .labelsHidden()
            .pickerStyle(.menu)
            .fixedSize()
            .help("工作台配置使用这里选的 Key 和模型。系统 CLI 沿用你在终端直接运行该命令时的配置。")

            if model.launchScope == .workbench {
                VStack(alignment: .leading, spacing: 3) {
                    HStack(spacing: 4) {
                        Button {
                            model.modelSearch = ""
                            model.isModelPickerPresented = true
                        } label: {
                            Label {
                                Text(model.selectedModel.isEmpty ? (model.isLoadingModels ? "正在读取模型" : "选择模型") : model.selectedModel)
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                            } icon: {
                                Image(systemName: "cpu")
                            }
                        }
                        .buttonStyle(.bordered)
                        .disabled(model.selectedProfile == nil)
                        Button {
                            Task { await model.loadModels(for: model.selectedProfileID) }
                        } label: { Image(systemName: "arrow.clockwise") }
                        .buttonStyle(.borderless)
                        .disabled(model.selectedProfile == nil || model.isLoadingModels)
                        .help("刷新模型列表")
                        .accessibilityLabel("刷新模型列表")
                    }
                    if !model.modelLoadError.isEmpty {
                        Text(model.isUsingCachedModels ? "刷新失败，保留上次选择" : "模型读取失败，请刷新或切换配置")
                            .font(.system(size: 9)).foregroundStyle(.orange).lineLimit(1)
                            .help(model.modelLoadError)
                    }
                }
                .frame(minWidth: 150, idealWidth: 205, maxWidth: 235)
                .help("\(model.models.count) 个可用模型")
            } else {
                Text("沿用终端配置")
                    .lineLimit(1)
                    .foregroundStyle(.secondary)
                    .frame(minWidth: 150, idealWidth: 205, maxWidth: 235, alignment: .leading)
            }

            Button {
                model.chooseWorkspace()
            } label: {
                Label {
                    Text(model.workspace.isEmpty ? "选择项目目录" : URL(fileURLWithPath: model.workspace).lastPathComponent)
                        .lineLimit(1)
                        .truncationMode(.middle)
                } icon: {
                    Image(systemName: "folder")
                }
            }
            .buttonStyle(.bordered)
            .frame(minWidth: 130, maxWidth: .infinity)
            .help(model.workspace.isEmpty ? "选择 Agent 工作目录" : model.workspace)

            if model.launchScope == .workbench {
                Menu {
                    ForEach(SessionMode.allCases) { mode in
                        Button {
                            model.selectSessionMode(mode)
                        } label: {
                            if model.sessionMode == mode {
                                Label(mode.title, systemImage: "checkmark")
                            } else {
                                Text(mode.title)
                            }
                        }
                    }
                } label: {
                    Label(model.sessionMode.compactTitle, systemImage: "clock.arrow.circlepath")
                        .lineLimit(1)
                        .frame(maxWidth: 145)
                }
                .menuStyle(.borderlessButton)
                .help(model.selectedHistory.map { "分支来源：\($0.title)" } ??
                      "恢复最近对话沿用原配置。从历史创建分支使用当前选中的配置。")
            }

            Button {
                model.requestLaunch()
            } label: {
                Label(model.sessionMode == .resume && model.launchScope == .workbench ? "启动分支" : "启动", systemImage: "play.fill")
            }
            .buttonStyle(.borderedProminent)
            .disabled(!model.canLaunch)
            .keyboardShortcut(.return, modifiers: [.command])
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .background(.bar)
    }

    private var statusBar: some View {
        HStack(spacing: 8) {
            Image(systemName: model.isStatusError ? "exclamationmark.circle.fill" : "checkmark.circle.fill")
                .foregroundStyle(model.isStatusError ? Color.orange : Color.accentColor)
                .font(.system(size: 11))
            Text(model.statusMessage)
                .font(.system(size: 10))
                .foregroundStyle(model.isStatusError ? Color.orange : Color.secondary)
                .lineLimit(1)
            Spacer()
            Button { isUsagePresented.toggle() } label: {
                Text(model.gatewayAccount?.balanceLabel ?? "网关余额 · 未获取")
                    .font(.system(size: 10)).monospacedDigit().lineLimit(1)
            }
            .buttonStyle(.borderless)
            .help("查看当前配置的账户余额和网关账单")
            Button { isUsagePresented.toggle() } label: {
                Text(!model.usageError.isEmpty ? model.usageError : model.usage.sessions.isEmpty ? "Token · 尚无本地记录" :
                        "历史累计 · 输入 \(usageCount(model.usage.inputTokens)) · 输出 \(usageCount(model.usage.outputTokens)) · \(model.usage.costLabel)")
                    .font(.system(size: 10, design: .monospaced))
                    .lineLimit(1)
                    .truncationMode(.middle)
            }
            .buttonStyle(.borderless)
            .help("查看隔离会话历史累计；输入已含缓存，推理属于输出细分。")
            .popover(isPresented: $isUsagePresented) {
                UsageDetailsView(model: model)
            }
            Button {
                Task { await model.refreshUsage() }
            } label: {
                Image(systemName: model.isLoadingUsage ? "arrow.triangle.2.circlepath" : "arrow.clockwise")
            }
            .buttonStyle(.borderless)
            .disabled(model.isLoadingUsage)
            .help("刷新 Token 用量")
            Text(URL(string: model.gateway)?.port.map(String.init) ?? "网关")
                .font(.system(size: 10, design: .monospaced))
                .foregroundStyle(.tertiary)
        }
        .padding(.horizontal, 14)
        .frame(height: 27)
    }

}

private struct HistoryNode: Identifiable {
    let id: String
    let title: String
    var children: [HistoryNode]? = nil
    var entry: LocalHistoryEntry? = nil

    static func outline(_ entries: [LocalHistoryEntry]) -> [Self] {
        build(entries.map { entry in
            let parts = entry.workspace.isEmpty ? [] : URL(fileURLWithPath: entry.workspace).standardizedFileURL.pathComponents
            return (parts, entry)
        }, prefix: [])
    }

    private static func build(_ rows: [([String], LocalHistoryEntry)], prefix: [String]) -> [Self] {
        var folders: [String: [([String], LocalHistoryEntry)]] = [:]
        var conversations: [Self] = []
        for (parts, entry) in rows {
            if parts.count > prefix.count {
                folders[parts[prefix.count], default: []].append((parts, entry))
            } else {
                conversations.append(Self(id: entry.id, title: entry.title, entry: entry))
            }
        }
        let branches = folders.keys.sorted { $0.localizedStandardCompare($1) == .orderedAscending }.compactMap { folder -> Self? in
            guard let group = folders[folder] else { return nil }
            var path = prefix + [folder]
            // Skip parent directories with neither conversations nor multiple children.
            while let next = group.first?.0.dropFirst(path.count).first,
                  group.allSatisfy({ $0.0.count > path.count && $0.0[path.count] == next }) {
                path.append(next)
            }
            return Self(id: "folder:" + NSString.path(withComponents: path),
                        title: NSString.path(withComponents: Array(path.dropFirst(prefix.count))),
                        children: build(group, prefix: path))
        }
        return branches + conversations
    }
}

private struct HistoryPickerSheet: View {
    @ObservedObject var model: ManagerModel
    @Environment(\.dismiss) private var dismiss
    @State private var query = ""

    private var filtered: [LocalHistoryEntry] {
        guard !query.isEmpty else { return model.historySessions }
        return model.historySessions.filter {
            $0.title.localizedCaseInsensitiveContains(query)
                || ($0.preview?.localizedCaseInsensitiveContains(query) ?? false)
                || $0.workspace.localizedCaseInsensitiveContains(query)
                || $0.profile_name.localizedCaseInsensitiveContains(query)
                || $0.model.localizedCaseInsensitiveContains(query)
        }
    }

    @State private var byDirectory = false

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text(model.historyScope == "global" ? "恢复最近对话" : "从当前项目的历史创建分支").font(.headline)
                    Text(model.historyScope == "global"
                         ? "从所有项目中选一条对话，点击即用原配置恢复。"
                         : "只列出「\(URL(fileURLWithPath: model.workspace).lastPathComponent)」中的对话。分支使用当前选择的调用配置与模型。")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer()
                Button("关闭") { dismiss() }
                    .keyboardShortcut(.cancelAction)
            }
            .padding(16)
            if model.historyScope == "global" {
                Picker("浏览方式", selection: $byDirectory) {
                    Text("最近").tag(false)
                    Text("按目录").tag(true)
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .padding(.horizontal, 16)
                .padding(.bottom, 10)
            }
            TextField("搜索标题、项目或配置", text: $query)
                .textFieldStyle(.roundedBorder)
                .padding(.horizontal, 16)
                .padding(.bottom, 10)
            if model.isLoadingHistory {
                ProgressView().frame(maxWidth: .infinity, maxHeight: .infinity)
            } else if !model.historyError.isEmpty {
                ContentUnavailableView("读取历史失败", systemImage: "exclamationmark.circle",
                                      description: Text(model.historyError))
            } else if filtered.isEmpty {
                ContentUnavailableView("没有找到本地历史", systemImage: "clock.arrow.circlepath",
                                      description: Text("目前没有可供 Claude 或 Codex 恢复的本机会话。"))
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
            } else {
                List {
                    if byDirectory {
                        OutlineGroup(HistoryNode.outline(filtered), children: \.children) { node in
                            if let entry = node.entry {
                                conversation(entry)
                            } else {
                                Label(node.title, systemImage: "folder").lineLimit(1).truncationMode(.middle)
                            }
                        }
                    } else {
                        ForEach(filtered) { entry in conversation(entry) }
                    }
                }
                .listStyle(.inset)
            }
        }
        .frame(minWidth: 620, minHeight: 440)
    }

    private func conversation(_ entry: LocalHistoryEntry) -> some View {
        Button {
            model.chooseHistory(entry)
        } label: {
            VStack(alignment: .leading, spacing: 5) {
                HStack(spacing: 7) {
                    Image(systemName: entry.client == "codex" ? "terminal.fill" : "bubble.left.and.bubble.right.fill")
                        .foregroundStyle(.secondary)
                    Text(entry.title.isEmpty ? "未命名会话" : entry.title)
                        .font(.system(size: 12, weight: .semibold))
                        .lineLimit(1)
                    Spacer()
                    Text(entry.updatedDate, style: .relative)
                        .font(.caption2).foregroundStyle(.tertiary)
                }
                Text("\(entry.client == "codex" ? "Codex" : "Claude") · \(entry.model.isEmpty ? "模型未知" : entry.model) · \(entry.isGlobal ? "系统 CLI" : (entry.profile_name.isEmpty ? "来源配置未知" : entry.profile_name))")
                    .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                if let preview = entry.preview, !preview.isEmpty {
                    Text(preview).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
                Text(entry.workspace.isEmpty ? "项目目录未知" : entry.workspace)
                    .font(.system(size: 10, design: .monospaced))
                    .foregroundStyle(.tertiary).lineLimit(1).truncationMode(.middle)
            }
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .padding(.vertical, 4)
    }
}

private struct UsageDetailsView: View {
    @ObservedObject var model: ManagerModel
    @State private var page = 0
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Picker("统计内容", selection: $page) {
                Text("用量与费用").tag(0)
                Text("账户账单（可选）").tag(1)
            }.pickerStyle(.segmented)
            if page == 0 { ScrollView { UsageAnalyticsView(model: model) }.frame(maxHeight: 640) }
            else { GatewayBillingPanel(model: model) }
        }.padding(18).frame(width: 620)
    }
}

private struct ModelPickerSheet: View {
    @State private var manualModel = ""
    @ObservedObject var model: ManagerModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                VStack(alignment: .leading, spacing: 3) {
                    Text("选择 \(model.activeClient.title) 模型")
                        .font(.system(size: 18, weight: .semibold))
                    Text("\(model.models.count) 个模型 · \(model.selectedProfile?.name ?? "") · 能力标签按名称推断")
                        .font(.system(size: 11))
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Button {
                    Task { await model.loadModels(for: model.selectedProfileID) }
                } label: {
                    Label(model.isLoadingModels ? "正在刷新" : "刷新模型列表", systemImage: "arrow.clockwise")
                }
                .disabled(model.isLoadingModels || model.selectedProfile == nil)
                Button("完成") { dismiss() }
                    .keyboardShortcut(.defaultAction)
            }
            .padding(18)

            if !model.modelLoadError.isEmpty {
                Text(model.modelLoadError + (model.isUsingCachedModels ? "\n已保留上次模型选择，仍可尝试启动；实际连接情况由终端报告。" : "\n可手动填写模型 ID；实际可用性由客户端请求确认。"))
                    .font(.system(size: 11)).foregroundStyle(.orange)
                    .textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading)
                    .padding(.horizontal, 18).padding(.bottom, 8)
            }

            HStack {
                TextField("手动填写模型 ID", text: $manualModel)
                    .textFieldStyle(.roundedBorder)
                Button("使用此 ID") {
                    model.selectedModel = manualModel.trimmingCharacters(in: .whitespacesAndNewlines)
                    dismiss()
                }
                .disabled(manualModel.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || manualModel.count > 250 || manualModel.contains(where: { $0.isNewline || $0 == "\0" }))
            }.padding(.horizontal, 18).padding(.bottom, 10)
            Text("Claude 需要 Anthropic Messages，Codex 需要 OpenAI Responses；只支持 Chat Completions 的网关不能直接供 Codex 使用。")
                .font(.system(size: 10)).foregroundStyle(.secondary)
                .padding(.horizontal, 18).padding(.bottom, 10)
            TextField("搜索模型名称", text: $model.modelSearch)
                .textFieldStyle(.roundedBorder)
                .padding(.horizontal, 18)
                .padding(.bottom, 10)

            Picker("筛选", selection: $model.modelFilter) {
                ForEach(ModelFilter.allCases) { filter in
                    Text(filter.title).tag(filter)
                }
            }
            .pickerStyle(.menu)
            .padding(.horizontal, 18)
            .padding(.bottom, 10)
            Text("编程、视觉和图像候选仅按模型 ID 名称推断；上游未提供能力字段时，不能据此确认工具调用或图像能力。")
                .font(.system(size: 10))
                .foregroundStyle(.secondary)
                .padding(.horizontal, 18)
                .padding(.bottom, 8)

            List(model.filteredModels, id: \.self) { item in
                HStack {
                    Button {
                        model.selectedModel = item
                        dismiss()
                    } label: {
                        HStack(spacing: 8) {
                            Text(item)
                                .font(.system(size: 12, design: .monospaced))
                                .lineLimit(1)
                                .truncationMode(.middle)
                            Spacer(minLength: 0)
                            if item == model.selectedModel {
                                Image(systemName: "checkmark")
                                    .foregroundStyle(Color.accentColor)
                            }
                        }
                        .contentShape(Rectangle())
                    }
                    .buttonStyle(.plain)
                    .frame(maxWidth: .infinity, alignment: .leading)

                    Spacer()
                    Button {
                        model.toggleFavorite(item)
                    } label: {
                        Image(systemName: model.isFavorite(item) ? "bookmark.fill" : "bookmark")
                            .font(.system(size: 14, weight: .regular))
                            .foregroundStyle(model.isFavorite(item) ? Color.accentColor : Color.secondary)
                            .frame(width: 32, height: 32)
                            .contentShape(Rectangle())
                    }
                    .buttonStyle(.borderless)
                    .accessibilityLabel(model.isFavorite(item) ? "取消收藏 \(item)" : "收藏 \(item)")
                    .help(model.isFavorite(item) ? "取消收藏" : "收藏模型")
                }
                .listRowInsets(EdgeInsets(top: 2, leading: 10, bottom: 2, trailing: 6))
            }
            .listStyle(.inset)
        }
        .frame(width: 520, height: 620)
        .background(.regularMaterial)
    }
}

private struct GatewaySettingsSheet: View {
    @ObservedObject var model: ManagerModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Image(systemName: "network")
                    .foregroundStyle(Color.accentColor)
                Text("网关连接")
                    .font(.system(size: 17, weight: .semibold))
                Spacer()
                Button { dismiss() } label: { Image(systemName: "xmark") }
                    .buttonStyle(.borderless)
                    .help("关闭")
            }
            Text(model.selectedProfile == nil || model.selectedProfile?.id == "free"
                 ? "设置新 Key 使用的默认 API 基础地址（支持自定义路径）。无需使用 CCHP；Claude 需要 Anthropic Messages 兼容接口，Codex 需要 OpenAI Responses 兼容接口。远程地址必须使用 HTTPS。"
                 : "修改当前 Key 对应的网关 API 基础地址（支持自定义路径）。远程地址必须使用 HTTPS。其他 Key 配置和已运行的终端不会改变。")
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Text("调用 API 基础地址（可含路径）").font(.caption).foregroundStyle(.secondary)
            TextField("http://127.0.0.1:8081", text: $model.gatewayDraft)
                .textFieldStyle(.roundedBorder)
                .font(.system(size: 12, design: .monospaced))
            ModelCatalogAddressFields(shared: $model.sharedModelCatalogDraft, url: $model.modelCatalogURLDraft)
            Toggle("保存时读取模型列表", isOn: $model.readModelsOnSave)
            if !model.gatewaySettingsError.isEmpty { Text(model.gatewaySettingsError).font(.caption).foregroundStyle(.orange) }
            HStack {
                Spacer()
                Button("取消") { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button {
                    Task { await model.saveGateway() }
                } label: {
                    if model.isSavingGateway { ProgressView().controlSize(.small) }
                    else { Text(model.selectedProfile == nil || model.selectedProfile?.id == "free" ? "保存为默认网关" : "保存到当前 Key") }
                }
                .buttonStyle(.borderedProminent)
                .disabled(model.gatewayDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || model.isSavingGateway || (!model.sharedModelCatalogDraft && model.modelCatalogURLDraft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty))
            }
        }
        .padding(22)
        .frame(width: 520)
    }
}

private struct TerminalAppearanceSheet: View {
    @ObservedObject var model: ManagerModel
    @Environment(\.dismiss) private var dismiss
    @State private var draft = TerminalStyleSettings.defaults

    var body: some View {
        VStack(alignment: .leading, spacing: 15) {
            HStack {
                Image(systemName: "paintbrush.pointed")
                    .foregroundStyle(Color.accentColor)
                Text("共享终端外观")
                    .font(.system(size: 17, weight: .semibold))
                Spacer()
                Menu("配色预设") {
                    ForEach(TerminalThemePreset.all) { preset in
                        Button(preset.title) {
                            draft.foreground = preset.foreground
                            draft.background = preset.background
                            draft.cursor = preset.cursor
                        }
                    }
                }
            }
            HStack {
                Text("字体")
                    .frame(width: 76, alignment: .leading)
                TextField("SFMono-Regular", text: $draft.fontName)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 11, design: .monospaced))
                Stepper("字号 \(Int(draft.fontSize))", value: $draft.fontSize, in: 9...24, step: 1)
                    .frame(width: 150)
            }
            colorRow("文字", value: $draft.foreground)
            colorRow("背景", value: $draft.background)
            colorRow("光标", value: $draft.cursor)
            Stepper("滚动缓冲 \(draft.scrollback) 行", value: $draft.scrollback, in: 500...20_000, step: 500)
            Text("字体和颜色会同步应用到所有打开的 PTY；滚动缓冲对新建终端生效。此配置不含 Key 或会话信息。")
                .font(.system(size: 10))
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Text(model.terminalStyle.configURL.path)
                .font(.system(size: 9, design: .monospaced))
                .foregroundStyle(.tertiary)
                .textSelection(.enabled)
                .lineLimit(2)
            if !model.terminalStyle.errorMessage.isEmpty {
                Text(model.terminalStyle.errorMessage)
                    .font(.system(size: 10))
                    .foregroundStyle(Color.orange)
            }
            HStack {
                Button("打开 JSON") { model.terminalStyle.openConfigFile() }
                Button("重新读取") {
                    if model.terminalStyle.reload() { draft = model.terminalStyle.settings }
                }
                Spacer()
                Button("取消") { dismiss() }
                    .keyboardShortcut(.cancelAction)
                Button("保存并应用") {
                    model.terminalStyle.update(draft)
                    if model.terminalStyle.errorMessage.isEmpty { dismiss() }
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
            }
        }
        .padding(22)
        .frame(width: 520)
        .onAppear { draft = model.terminalStyle.settings }
    }

    private func colorRow(_ title: String, value: Binding<String>) -> some View {
        HStack {
            Text(title)
                .frame(width: 76, alignment: .leading)
            Circle()
                .fill(Color(nsColor: NSColor(hex: value.wrappedValue)))
                .frame(width: 16, height: 16)
            TextField("#RRGGBB", text: value)
                .textFieldStyle(.roundedBorder)
                .font(.system(size: 11, design: .monospaced))
                .frame(width: 115)
        }
    }
}

private struct ModelCatalogAddressFields: View {
    @Binding var shared: Bool
    @Binding var url: String

    var body: some View {
        Toggle("模型目录与调用共用地址", isOn: $shared)
        if !shared {
            VStack(alignment: .leading, spacing: 6) {
                Text("模型目录完整 URL").font(.caption).foregroundStyle(.secondary)
                TextField("https://服务商/api/paas/v4/models", text: $url).textFieldStyle(.roundedBorder)
                    .font(.system(size: 12, design: .monospaced))
                Text("只向此地址读取模型列表，使用同一把调用 Key；请填写服务商可信地址。推理仍使用调用地址。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
        }
    }
}

private struct AddProfileSheet: View {
    @ObservedObject var model: ManagerModel
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var key = ""
    @State private var gateway = ""
    @State private var readModels = true
    @State private var sharedCatalog = true
    @State private var catalogURL = ""

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Image(systemName: "key.horizontal")
                    .font(.system(size: 18))
                    .foregroundStyle(Color.accentColor)
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
                Text("当前网关的调用 Key")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(.secondary)
                SecureField("输入此网关签发的调用 Key", text: $key)
                    .textFieldStyle(.roundedBorder)
            }

            VStack(alignment: .leading, spacing: 6) {
                Text("调用 API 基础地址（可含路径）")
                    .font(.system(size: 11, weight: .medium))
                    .foregroundStyle(.secondary)
                TextField("http://127.0.0.1:8081", text: $gateway)
                    .textFieldStyle(.roundedBorder)
                    .font(.system(size: 12, design: .monospaced))
            }

            ModelCatalogAddressFields(shared: $sharedCatalog, url: $catalogURL)
            Toggle("保存时读取模型列表", isOn: $readModels)
            Text("支持 /proxy/v1 等 API 前缀；未提供模型列表的网关可手动填写模型 ID。Claude 需 Messages 接口，Codex 需 Responses 接口。")
                .font(.system(size: 11)).foregroundStyle(.secondary)
            Label("读取列表不会发起推理；取消读取时不校验 Key。Key 以权限 600 保存于本机。",
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
                        if await model.addProfile(name: name, key: key, gateway: gateway, readModels: readModels, modelCatalogURL: sharedCatalog ? "" : catalogURL) {
                            key = ""
                            dismiss()
                        }
                    }
                } label: {
                    if model.isAddingProfile {
                        ProgressView().controlSize(.small)
                    } else {
                        Text("保存配置")
                    }
                }
                .buttonStyle(.borderedProminent)
                .disabled(name.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || key.isEmpty || gateway.isEmpty || model.isAddingProfile || (!sharedCatalog && catalogURL.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty))
                .keyboardShortcut(.defaultAction)
            }
        }
        .onAppear {
            if gateway.isEmpty { gateway = model.gateway }
        }
        .padding(22)
        .frame(width: 440)
        .background(.regularMaterial)
    }
}
