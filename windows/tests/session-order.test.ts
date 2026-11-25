import { describe, expect, it } from "vitest";
import { reorderSessions, tabDropTarget } from "../src/SessionOrder";

describe("terminal tab order", () => {
  it("resolves gaps and both edges without depending on the dragged element hit test", () => {
    const tabs = [{ id: "a", left: 0, right: 180 }, { id: "b", left: 184, right: 364 }, { id: "c", left: 368, right: 548 }];
    expect(tabDropTarget(182, tabs, "a")).toEqual({ id: "b", after: false });
    expect(tabDropTarget(600, tabs, "a")).toEqual({ id: "c", after: true });
    expect(tabDropTarget(-20, tabs, "c")).toEqual({ id: "a", after: false });
    expect(tabDropTarget(400, tabs, "a")).toEqual({ id: "c", after: false });
  });
  it("moves a dragged session before or after its target without changing session identity", () => {
    const sessions = [{ id: "a" }, { id: "b" }, { id: "c" }];
    expect(reorderSessions(sessions, "a", "c", true).map((session) => session.id)).toEqual(["b", "c", "a"]);
    expect(reorderSessions(sessions, "c", "a", false).map((session) => session.id)).toEqual(["c", "a", "b"]);
    expect(sessions.map((session) => session.id)).toEqual(["a", "b", "c"]);
  });

  it("leaves the order intact for unknown or same-tab drops", () => {
    const sessions = [{ id: "a" }, { id: "b" }];
    expect(reorderSessions(sessions, "missing", "b", false)).toBe(sessions);
    expect(reorderSessions(sessions, "a", "missing", false)).toBe(sessions);
    expect(reorderSessions(sessions, "a", "a", true)).toBe(sessions);
  });
});
