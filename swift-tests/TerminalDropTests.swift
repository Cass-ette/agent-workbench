import AppKit
import SwiftTerm
import XCTest
@testable import ClaudeCCHPManager

final class TerminalDropTests: XCTestCase {
    @MainActor
    func testSwitchingToEarlierTabMakesItTheOnlyNativeDropTarget() {
        let frame = CGRect(x: 0, y: 0, width: 640, height: 400)
        let stack = NSView(frame: frame)
        let first = FileDropTerminalView(frame: frame, options: TerminalOptions())
        let second = FileDropTerminalView(frame: frame, options: TerminalOptions())
        stack.addSubview(first)
        stack.addSubview(second, positioned: .above, relativeTo: first)
        let point = NSPoint(x: 100, y: 100)
        let firstProcess = first.process

        first.setSelected(false)
        second.setSelected(true)
        XCTAssertTrue(stack.hitTest(point) === second)
        XCTAssertTrue(second.registeredDraggedTypes.contains(.fileURL))
        XCTAssertFalse(first.registeredDraggedTypes.contains(.fileURL))

        first.setSelected(true)
        second.setSelected(false)
        XCTAssertTrue(stack.hitTest(point) === first)
        XCTAssertTrue(first.registeredDraggedTypes.contains(.fileURL))
        XCTAssertFalse(second.registeredDraggedTypes.contains(.fileURL))
        XCTAssertTrue(first.process === firstProcess)
    }
}
