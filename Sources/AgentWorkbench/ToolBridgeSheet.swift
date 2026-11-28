import AppKit
import SwiftUI

struct ToolBridgeItem: Decodable, Identifiable {
    let id: String
    let name: String
    let kind: String
    let enabled: Bool
    let command: String?
}

struct ToolBridgeState: Decodable {
    let config: String
    let screen: Bool
    let items: [ToolBridgeItem]
}

struct ToolBridgeSheet: View {
    @ObservedObject var model: ManagerModel
    @State private var client: ClientKind = .codex
    @State private var config = "{\"mcpServers\": {}}"
    @State private var screen = false
    @State private var items: [ToolBridgeItem] = []
    @State private var disabled = Set<String>()
    @State private var message = ""
    @State private var busy = false
    @State private var loaded = false
    @State private var confirmed = false

    private func accept(_ value: ToolBridgeState) {
        config = value.config
        screen = value.screen
        model.applyScreenCaptureSetting(value.screen, client: client)
        items = value.items
        disabled = Set(items.filter { !$0.enabled }.map(\.id))
        loaded = true
        confirmed = false
    }

    private func load() async {
        busy = true
        loaded = false
        defer { busy = false }
        do { accept(try await model.manageTools("tools-get", client: client)) }
        catch { message = error.localizedDescription }
    }

    private func importDirectory(_ kind: String) {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.message = kind == "skill" ? "选择包含 SKILL.md 的单个技能目录" : "选择包含 .claude-plugin/plugin.json 的 Claude 插件目录"
        guard panel.runModal() == .OK, let path = panel.url?.path else { return }
        Task {
            busy = true
            defer { busy = false }
            do {
                let value = try await model.manageTools("tools-import", client: client, payload: ["path": path, "kind": kind])
                let known = Set(items.map(\.id))
                disabled.formUnion(value.items.filter { !known.contains($0.id) }.map(\.id))
                items = value.items
                confirmed = false
                message = "已复制到工作台私有目录，默认停用。勾选后保存才能加载。"
            } catch { message = error.localizedDescription }
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("工具与插件").font(.title2.weight(.semibold))
            Text("仅配置工作台内的会话。外部客户端的认证、模型、历史和工具配置保持原样。")
            Picker("客户端", selection: $client) {
                Text("Codex").tag(ClientKind.codex)
                Text("Claude").tag(ClientKind.claude)
            }.pickerStyle(.segmented)
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    VStack(alignment: .leading, spacing: 7) {
                        Label("系统权限预检", systemImage: model.screenCaptureAuthorized ? "checkmark.shield.fill" : "exclamationmark.shield.fill")
                            .font(.headline)
                            .foregroundStyle(model.screenCaptureAuthorized || !screen ? Color.primary : Color.red)
                        HStack {
                            Text("屏幕录制")
                            Spacer()
                            Text(model.screenCaptureAuthorized ? "已授权" : "尚未授权")
                                .foregroundStyle(model.screenCaptureAuthorized ? .green : .red)
                        }
                        HStack {
                            Text("辅助功能")
                            Spacer()
                            Text(model.accessibilityAuthorized ? "已授权" : "未授权")
                                .foregroundStyle(model.accessibilityAuthorized ? .green : .secondary)
                        }
                        Text("内置屏幕读取只需要屏幕录制权限。辅助功能只在另接需要控制鼠标或键盘的工具时使用。系统权限已授权，也不代表当前模型或网关支持图片工具。")
                            .foregroundStyle(.secondary)
                        HStack {
                            Button("请求／检查屏幕录制授权") {
                                let granted = model.requestScreenCapturePermission()
                                message = granted ? "macOS 已授权屏幕录制。还需确认当前模型和网关支持图片工具。" : "尚未获得授权，请在系统设置中允许工作台；授权后可回到这里重新检查。"
                            }
                            Button("打开屏幕录制设置") { model.openScreenCaptureSettings() }
                            Button("打开辅助功能设置") { model.openAccessibilitySettings() }
                        }
                    }
                    .padding(12)
                    .background(.red.opacity(screen && !model.screenCaptureAuthorized ? 0.08 : 0.04), in: RoundedRectangle(cornerRadius: 8))

                    Text("MCP 服务（JSON）").font(.headline)
                    Text("填入 mcpServers 或 mcp_servers 对象。command/args 启动本机程序，url 连接 HTTP 服务。保存仅登记；之后启动会话才连接或执行。凭据请优先通过环境变量提供。")
                        .foregroundStyle(.secondary)
                    TextEditor(text: $config).font(.system(size: 12, design: .monospaced)).frame(height: 180)
                        .border(.secondary.opacity(0.3))
                    Button("从 JSON 文件载入…") {
                        let panel = NSOpenPanel()
                        panel.canChooseDirectories = false
                        guard panel.runModal() == .OK, let url = panel.url else { return }
                        do { config = try String(contentsOf: url, encoding: .utf8); confirmed = false }
                        catch { message = error.localizedDescription }
                    }
                    Toggle("启用本机屏幕读取（Codex / Claude 皆可）", isOn: $screen)
                    Text("只按工具调用截取主屏幕，不点击或输入。截图会发给所选模型和网关，需图片支持及 macOS 屏幕录制授权。启用不代表已通过授权或模型验证。")
                        .foregroundStyle(.secondary)
                    HStack {
                        Button("导入技能目录…") { importDirectory("skill") }
                        if client == .claude { Button("导入 Claude 插件目录…") { importDirectory("plugin") } }
                    }
                    ForEach(items) { item in
                        Toggle("\(item.name) · \(item.kind == "skill" ? "技能" : "Claude 插件")", isOn: Binding(
                            get: { !disabled.contains(item.id) },
                            set: { if $0 { disabled.remove(item.id) } else { disabled.insert(item.id) }; confirmed = false }))
                        if client == .claude, let command = item.command, !command.isEmpty {
                            Text("调用：/" + command).font(.system(size: 11, design: .monospaced)).textSelection(.enabled)
                        }
                    }
                    Text("导入复制单个组件，不改原目录。Claude 最小模式的技能主要通过 /命令调用，hooks 等能力仍受 CLI 模式限制；Codex 暂支持 MCP 与 Skills，不承诺加载桌面宿主专属插件。依赖与账号需在当前环境准备。停用仅影响之后启动的会话。")
                        .foregroundStyle(.secondary)
                    Toggle("我已核对以上服务程序、地址和组件，允许后续工作台会话加载它们。", isOn: $confirmed)
                    if !message.isEmpty { Text(message).foregroundStyle(.secondary).textSelection(.enabled) }
                }
            }
            HStack {
                if busy { ProgressView().controlSize(.small) }
                Spacer()
                Button("关闭") { model.isToolsPresented = false }
                Button("保存工具设置") {
                    Task {
                        busy = true
                        defer { busy = false }
                        do {
                            accept(try await model.manageTools("tools-save", client: client,
                                payload: ["config": config, "screen": screen, "disabled": Array(disabled)]))
                            message = "已保存。只应用到之后新建、恢复和分支的工作台会话；运行中的终端不变。"
                        } catch { message = error.localizedDescription }
                    }
                }.buttonStyle(.borderedProminent).disabled(!loaded || !confirmed || busy)
            }
        }
        .font(.system(size: 13)).padding(24).frame(width: 650, height: 700)
        .task(id: client) { await load() }
        .onChange(of: config) { _, _ in confirmed = false }
        .onChange(of: screen) { _, enabled in
            confirmed = false
            model.screenCaptureSettingChanged(enabled, client: client)
        }
        .disabled(busy)
    }
}
