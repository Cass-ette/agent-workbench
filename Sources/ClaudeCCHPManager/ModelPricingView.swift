import Foundation
import SwiftUI

struct ModelPriceQuote: Decodable, Identifiable {
    let model: String
    let charges: [String: String]
    let base_charges: [String: String]
    let upper_charges: [String: String]?
    let ratio: String?
    let basis: String
    let source_kind: String
    let is_gateway_price: Bool?
    var id: String { model }

    func rate(_ key: String) -> String {
        guard let value = charges[key] else { return "—" }
        if let upper = upper_charges?[key], upper != value { return "$\(value)–$\(upper)" }
        return "$" + value
    }
}

struct ModelPricingSnapshot: Decodable {
    let profile_id: String
    let gateway: String
    let source_url: String
    let source_override: String
    let source_kind: String
    let fetched_at: TimeInterval?
    let stale: Bool
    let ratio: String?
    let notice: String
    let quotes: [ModelPriceQuote]
    let missing_models: Int
    let disclaimer: String
}

struct ModelPricingPanel: View {
    @ObservedObject var model: ManagerModel
    @State private var search = ""
    @State private var source = ""
    @State private var showSource = false

    private var quotes: [ModelPriceQuote] {
        (model.modelPricing?.quotes ?? []).filter { search.isEmpty || $0.model.localizedCaseInsensitiveContains(search) }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Text("当前 Key 的网关售价").font(.headline)
                Spacer()
                if model.isLoadingPricing { ProgressView().controlSize(.small) }
                Button("刷新价格") { Task { await model.refreshPricing(force: true) } }
                    .disabled(model.isLoadingPricing)
            }
            Text("\(model.selectedProfile?.name ?? "当前配置") · USD／百万 Token")
                .font(.subheadline).foregroundStyle(.secondary)
            if let pricing = model.modelPricing {
                Text("\(pricing.quotes.count) 个模型已匹配 · 倍率 \(pricing.ratio.map { "×" + $0 } ?? "未公开，仅供参考")")
                    .font(.caption)
                if let time = pricing.fetched_at {
                    Text("抓取：\(Date(timeIntervalSince1970: time).formatted())\(pricing.stale ? " · 缓存／参考快照" : "")")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Text(pricing.source_url).font(.caption).textSelection(.enabled).lineLimit(2)
                if !pricing.notice.isEmpty { Text(pricing.notice).font(.caption).foregroundStyle(.orange) }
                if pricing.missing_models > 0 {
                    Text("\(pricing.missing_models) 个模型暂无可计算的文本单价；图像等计费不按 Token 冒算。")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            DisclosureGroup("网关售价目录地址（高级，无需管理 Token）", isExpanded: $showSource) {
                VStack(alignment: .leading, spacing: 8) {
                    TextField("留空自动发现；或填此网关公开售价目录的 URL", text: $source).textFieldStyle(.roundedBorder)
                    Text("远程需 HTTPS；本机可用 HTTP。此地址只接收公开 GET，不会附带 Key 或对话。")
                        .font(.caption).foregroundStyle(.secondary)
                    Button("保存来源") { Task { await model.savePricingSource(source) } }.disabled(model.isLoadingPricing)
                }.padding(.top, 6)
            }.font(.caption)
            if !model.pricingError.isEmpty { Text(model.pricingError).font(.caption).foregroundStyle(.orange) }
            TextField("搜索模型", text: $search).textFieldStyle(.roundedBorder)
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 12) {
                    ForEach(quotes) { quote in
                        VStack(alignment: .leading, spacing: 4) {
                            Text(quote.model).font(.system(size: 12, weight: .semibold, design: .monospaced))
                            Text(quote.basis).font(.caption).foregroundStyle(.secondary)
                            Grid(alignment: .leading, horizontalSpacing: 14, verticalSpacing: 3) {
                                GridRow { Text(""); Text("目录基础价"); Text(quote.is_gateway_price == true ? "× 倍率 → 网关单价" : "参考值 · 非确认售价") }
                                ForEach(["prompt", "completion", "cache_read", "cache_write"], id: \.self) { key in
                                    GridRow {
                                        Text(["prompt": "输入", "completion": "输出", "cache_read": "缓存读", "cache_write": "缓存写"][key] ?? key)
                                        Text(quote.base_charges[key].map { "$" + $0 } ?? "—")
                                        Text("\(quote.ratio ?? "未知") → \(quote.rate(key))")
                                    }
                                }
                            }.font(.system(size: 10, design: .monospaced))
                        }
                        Divider()
                    }
                }.frame(maxWidth: .infinity, alignment: .leading)
            }.frame(height: 235)
            Text(model.modelPricing?.disclaimer ?? "正在读取公开单价；不会调用模型。")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
        .onAppear { source = model.modelPricing?.source_override ?? "" }
        .task { await model.refreshPricing() }
    }
}
