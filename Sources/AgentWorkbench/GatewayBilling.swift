import Foundation

struct GatewayAccount: Decodable {
    let profile_id: String
    let gateway: String
    let status: String
    let message: String
    let currency: String?
    let available: String?
    let wallet: String?
    let packages: String?
    let spendable: String?
    let unlimited: Bool?
    let ratio: String?
    let group: String?
    let limits: String?
    let observed_at: String?

    var balanceLabel: String {
        guard status == "ok" else { return "网关余额 · 未获取" }
        return "网关可用 · " + (unlimited == true ? "不限额" : gatewayMoney(spendable, currency: currency))
    }
}

struct GatewayBill: Decodable, Identifiable {
    let id: String
    let model: String
    let key_name: String?
    let started_at: String?
    let cost: String?
    let state: String
}

struct GatewayBilling: Decodable {
    let profile_id: String
    let gateway: String
    let status: String
    let message: String
    let console: String?
    let currency: String?
    let scope_label: String?
    let today_cost: String?
    let today_requests: Int?
    let observed_at: String?
    let has_more: Bool?
    let records: [GatewayBill]
}

struct BillingSettings: Decodable {
    let console: String
    let configured: Bool
}

func gatewayMoney(_ value: String?, currency: String? = "USD") -> String {
    guard let value, let amount = Decimal(string: value, locale: Locale(identifier: "en_US_POSIX")) else { return "—" }
    let formatter = NumberFormatter()
    formatter.numberStyle = .decimal
    formatter.minimumFractionDigits = 2
    formatter.maximumFractionDigits = 10
    let text = formatter.string(from: NSDecimalNumber(decimal: amount)) ?? value
    return (currency == "USD" ? "$" : (currency ?? "") + " ") + text
}

func billingTime(_ value: String?) -> String {
    guard let value else { return "时间未提供" }
    let parser = ISO8601DateFormatter()
    parser.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
    guard let date = parser.date(from: value) ?? ISO8601DateFormatter().date(from: value) else { return value }
    return date.formatted(date: .abbreviated, time: .standard)
}
