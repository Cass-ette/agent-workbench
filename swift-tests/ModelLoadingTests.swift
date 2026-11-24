import Foundation
import XCTest
@testable import ClaudeCCHPManager

private enum ModelLoadFailure: Error { case offline }

private actor ModelReplies {
    var values: [Result<ModelResponse, ModelLoadFailure>]
    init(_ values: [Result<ModelResponse, ModelLoadFailure>]) { self.values = values }
    func next() throws -> ModelResponse { try values.removeFirst().get() }
}

private actor PendingModels {
    var replies: [Int: CheckedContinuation<ModelResponse, any Error>] = [:]
    var count = 0
    func load() async throws -> ModelResponse {
        let index = count
        count += 1
        return try await withCheckedThrowingContinuation { replies[index] = $0 }
    }
    func waiting(_ expected: Int) -> Bool { replies.count >= expected }
    func finish(_ index: Int, model: String) {
        replies.removeValue(forKey: index)?.resume(returning: ModelResponse(models: [model], gateway: nil))
    }
}

final class ModelLoadingTests: XCTestCase {
    @MainActor
    private func configured(_ defaults: UserDefaults,
                            loader: @escaping @Sendable (String) async throws -> ModelResponse) -> ManagerModel {
        let model = ManagerModel(modelDefaults: defaults, modelLoader: loader)
        model.profiles = [KeyProfile(id: "a", name: "A", gateway: "https://gateway.test")]
        model.selectedProfileID = "a"
        model.gateway = "https://gateway.test"
        model.workspace = NSTemporaryDirectory()
        model.launchScope = .workbench
        return model
    }

    @MainActor
    func testRefreshFailurePreservesModelAndRestartRecoversScopedCache() async {
        let name = "ModelLoadingTests." + UUID().uuidString
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        let replies = ModelReplies([.success(ModelResponse(models: ["b", "a"], gateway: nil)), .failure(.offline)])
        let model = configured(defaults) { _ in try await replies.next() }
        await model.loadModels(for: "a")
        model.selectedModel = "b"
        await model.loadModels(for: "a")
        XCTAssertEqual(model.models, ["a", "b"])
        XCTAssertEqual(model.selectedModel, "b")
        XCTAssertFalse(model.modelLoadError.isEmpty)
        XCTAssertFalse(model.isLoadingModels)
        XCTAssertTrue(model.canLaunch)
        let restarted = configured(defaults) { _ in throw ModelLoadFailure.offline }
        await restarted.loadModels(for: "a")
        XCTAssertEqual(restarted.selectedModel, "b")
        XCTAssertEqual(restarted.models, ["a", "b"])
        XCTAssertTrue(restarted.isUsingCachedModels)
    }

    @MainActor
    func testEmptyResponseKeepsSelectionAndGatewayNormalizationFinishesLoading() async {
        let name = "ModelLoadingTests." + UUID().uuidString
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        let replies = ModelReplies([.success(ModelResponse(models: ["a"], gateway: "https://gateway.test/")),
                                    .success(ModelResponse(models: [], gateway: nil))])
        let model = configured(defaults) { _ in try await replies.next() }
        await model.loadModels(for: "a")
        XCTAssertFalse(model.isLoadingModels)
        await model.loadModels(for: "a")
        XCTAssertEqual(model.selectedModel, "a")
        XCTAssertEqual(model.models, ["a"])
        XCTAssertTrue(model.canLaunch)
        XCTAssertFalse(model.modelLoadError.isEmpty)
    }

    @MainActor
    func testSwitchingProfileDoesNotBorrowModelsFromAnotherGateway() async {
        let name = "ModelLoadingTests." + UUID().uuidString
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        let replies = ModelReplies([.success(ModelResponse(models: ["private-a"], gateway: nil)), .failure(.offline)])
        let model = configured(defaults) { _ in try await replies.next() }
        await model.loadModels(for: "a")
        let second = KeyProfile(id: "b", name: "B", gateway: "https://other.test")
        model.profiles.append(second)
        await model.selectProfile(second)
        XCTAssertTrue(model.selectedModel.isEmpty)
        XCTAssertTrue(model.models.isEmpty)
        XCTAssertFalse(model.canLaunch)
        XCTAssertFalse(model.modelLoadError.isEmpty)
        let cache = ModelCatalogCache(defaults: defaults)
        XCTAssertTrue(cache.entry(profile: "a", gateway: "https://other.test").models.isEmpty)
    }

    @MainActor
    func testManualModelSurvivesCatalogRefreshAndMissingCatalogAllowsLaunch() async {
        let name = "ModelLoadingTests." + UUID().uuidString
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        let notice = "网关未提供模型列表接口；Key 尚未验证，请手动填写模型 ID。"
        let decoded = try! JSONDecoder().decode(ModelResponse.self, from: Data("{\"models\":[],\"catalog_notice\":\"\(notice)\"}".utf8))
        XCTAssertEqual(decoded.catalog_notice, notice)
        let replies = ModelReplies([.success(decoded), .success(ModelResponse(models: ["listed-model"], gateway: nil))])
        let model = configured(defaults) { _ in try await replies.next() }
        await model.loadModels(for: "a")
        XCTAssertEqual(model.modelLoadError, notice)
        model.selectedModel = "manual-model"
        XCTAssertTrue(model.canLaunch)
        await model.loadModels(for: "a")
        XCTAssertEqual(model.selectedModel, "manual-model")
        XCTAssertEqual(model.models, ["listed-model"])
        XCTAssertTrue(model.canLaunch)
    }

    @MainActor
    func testEarlierRequestCannotOverwriteNewerRefreshForSameProfile() async {
        let name = "ModelLoadingTests." + UUID().uuidString
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        let pending = PendingModels()
        let model = configured(defaults) { _ in try await pending.load() }
        let first = Task { await model.loadModels(for: "a") }
        while !(await pending.waiting(1)) { await Task.yield() }
        let second = Task { await model.loadModels(for: "a") }
        while !(await pending.waiting(2)) { await Task.yield() }
        await pending.finish(1, model: "latest")
        await second.value
        await pending.finish(0, model: "stale")
        await first.value
        XCTAssertEqual(model.models, ["latest"])
        XCTAssertEqual(model.selectedModel, "latest")
        XCTAssertFalse(model.isLoadingModels)
    }
}
