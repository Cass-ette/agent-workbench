import { describe, expect, it } from "vitest";
import { reorderSessions } from "../src/SessionOrder";

describe("terminal tab order", () => {
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
