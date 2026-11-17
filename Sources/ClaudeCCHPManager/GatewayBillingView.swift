import SwiftUI

struct GatewayBillingPanel: View {
    @ObservedObject var model: ManagerModel

    private var consoleURL: URL? {
        guard let source = model.modelPricing, source.source_kind == "gateway",
              var components = URLComponents(string: source.source_url) else { return nil }
        components.path = "/"
        components.query = nil
        components.fragment = nil
        return components.url
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("当前网关账户").font(.headline)
                Spacer()
                if model.isLoadingGatewayUsage { ProgressView().controlSize(.small) }
                Button("刷新") { Task { await model.refreshGatewayUsage(force: true) } }
                    .disabled(model.isLoadingGatewayUsage)
            }
            Text(model.selectedProfile?.name ?? "请先选择调用配置").font(.subheadline)
            Text(model.gateway).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
            if let account = model.gatewayAccount {
                if account.status == "ok" {
                    Grid(alignment: .leading, horizontalSpacing: 24, verticalSpacing: 6) {
                        GridRow { Text("可消费额度"); Text(account.unlimited == true ? "不限额" : gatewayMoney(account.spendable, currency: account.currency)) }
                        GridRow { Text("钱包余额"); Text(gatewayMoney(account.wallet, currency: account.currency)) }
                        GridRow { Text("套餐余额"); Text(gatewayMoney(account.packages, currency: account.currency)) }
                        GridRow { Text("网关分组倍率"); Text(account.ratio.map { "× " + $0 } ?? "未公开") }
                    }.font(.system(size: 12)).monospacedDigit()
                    if let group = account.group { Text("分组：" + group).font(.caption).foregroundStyle(.secondary) }
                    if let limits = account.limits {
                        DisclosureGroup("Key／用户配额规则") {
                            ScrollView { Text(limits).font(.system(size: 10, design: .monospaced)).textSelection(.enabled) }
                                .frame(maxHeight: 120)
                        }.font(.caption)
                    } else {
                        Text("Key／用户配额：接口未返回额外规则").font(.caption).foregroundStyle(.secondary)
                    }
                    Text("账户查询：" + billingTime(account.observed_at)).font(.caption2).foregroundStyle(.secondary)
                } else { Text(account.message).font(.caption).foregroundStyle(.secondary) }
            } else if !model.isLoadingGatewayUsage {
                Text("尚未获取账户信息").font(.caption).foregroundStyle(.secondary)
            }
            Text("余额可能由多把 Key 共用，不等于当前会话的花费。网关售价可在“网关售价”页查看，本地计算仍标为估算。")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Divider()
            HStack {
                Text("网关实际账单").font(.headline)
                Spacer()
            }
            if model.gatewayBilling?.scope_label == nil {
                Text("当前未接入实扣明细；请在 CCHP 控制台核对实际账单。本地 Token 费用标为估算，价格来源见各会话说明。余额可能由多把 Key 共用。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            if let url = consoleURL { Link("打开 CCHP 控制台查看实扣", destination: url).font(.caption) }
            DisclosureGroup("账户级账单连接（高级，可选）") {
                VStack(alignment: .leading, spacing: 6) {
                    Text("仅在需要读取整个管理账户的账单时配置；查看当前 Key 的公开售价无需此凭据。")
                        .font(.caption).foregroundStyle(.secondary)
                    Button(model.gatewayBilling?.console == nil ? "连接只读账户账单…" : "连接设置…") {
                        Task { await model.showBillingSettings() }
                    }.disabled(model.selectedProfile == nil)
                }.padding(.top, 6)
            }.font(.caption)
            if let billing = model.gatewayBilling {
                if let scope = billing.scope_label {
                    Text(scope).font(.caption).foregroundStyle(.secondary)
                    HStack {
                        Text("今日（UTC）网关记账：" + gatewayMoney(billing.today_cost, currency: billing.currency))
                        Spacer()
                        Text(billing.today_requests.map { "\($0) 次请求" } ?? "请求数未知")
                    }.font(.system(size: 12)).monospacedDigit()
                    Text("最近 24 小时 · 最多 20 笔；与本地会话未作一一关联。")
                        .font(.caption).foregroundStyle(.secondary)
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 10) {
                            if billing.records.isEmpty { Text("未返回逐次记录").foregroundStyle(.secondary) }
                            ForEach(billing.records) { bill in
                                VStack(alignment: .leading, spacing: 3) {
                                    HStack {
                                        Text(bill.model).lineLimit(1).truncationMode(.middle)
                                        Spacer()
                                        Text(bill.cost == nil ? bill.state : gatewayMoney(bill.cost, currency: billing.currency))
                                    }.font(.system(size: 12)).monospacedDigit()
                                    Text(billingTime(bill.started_at) + (bill.key_name.map { " · " + $0 } ?? ""))
                                        .font(.caption2).foregroundStyle(.secondary)
                                    Text(bill.id).font(.system(size: 9, design: .monospaced)).foregroundStyle(.tertiary)
                                }.textSelection(.enabled)
                            }
                        }.frame(maxWidth: .infinity, alignment: .leading)
                    }.frame(maxHeight: 180)
                    Text("账单查询：" + billingTime(billing.observed_at)).font(.caption2).foregroundStyle(.secondary)
                    if let console = billing.console, let url = URL(string: console) {
                        Link("在控制台查看完整账单", destination: url).font(.caption)
                    }
                }
                if billing.scope_label != nil && !billing.message.isEmpty { Text(billing.message).font(.caption).foregroundStyle(.secondary) }
            }
            if !model.gatewayUsageError.isEmpty {
                Text(model.gatewayUsageError).font(.caption).foregroundStyle(.orange)
            }
        }
    }
}

struct BillingSettingsSheet: View {
    @ObservedObject var model: ManagerModel
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            Text("连接只读账单").font(.title2).fontWeight(.semibold)
            Text("为“\(model.selectedProfile?.name ?? "")”指定 CCHP 控制台地址和只读管理 Token。可查询该管理用户全部调用 Key 的账单。")
                .font(.subheadline).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            TextField("控制台地址，例如 http://127.0.0.1:8080", text: $model.billingConsoleDraft)
                .textFieldStyle(.roundedBorder)
            SecureField(model.hasBillingToken ? "已保存；留空继续使用，或粘贴新 Token" : "只读管理 Token：cch_auth_live_…", text: $model.billingTokenDraft)
                .textFieldStyle(.roundedBorder)
            Text("请在 CCHP 管理 Token 页面创建只读凭据。模型调用 Key 不能读取此接口。凭据仅保存在本机权限 600 的私有文件中，查询只使用 GET。")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Link("查看 CCHP 管理 Token 文档", destination: URL(string: "https://cch-plus.com/en/docs/operations/management-tokens")!)
                .font(.caption)
            if !model.billingSettingsError.isEmpty {
                Text(model.billingSettingsError).font(.caption).foregroundStyle(.orange)
            }
            HStack {
                if model.hasBillingToken {
                    Button("断开并删除本机凭据", role: .destructive) { Task { await model.disconnectBilling() } }
                }
                Spacer()
                Button("取消") { dismiss() }.keyboardShortcut(.cancelAction)
                Button(model.isSavingBilling ? "正在验证…" : "验证并保存") { Task { await model.saveBillingSettings() } }
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(model.billingConsoleDraft.isEmpty || (!model.hasBillingToken && model.billingTokenDraft.isEmpty))
            }.disabled(model.isSavingBilling)
        }.padding(24).frame(width: 520)
            .interactiveDismissDisabled(model.isSavingBilling)
            .onDisappear { model.billingTokenDraft = "" }
    }
}
