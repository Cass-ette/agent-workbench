export interface OrderedSession { id: string }

export function reorderSessions<T extends OrderedSession>(
  sessions: T[], movingId: string, targetId: string, afterTarget: boolean,
): T[] {
  const source = sessions.findIndex((session) => session.id === movingId);
  if (source < 0 || movingId === targetId) return sessions;
  const next = [...sessions];
  const [moving] = next.splice(source, 1);
  const target = next.findIndex((session) => session.id === targetId);
  if (target < 0) return sessions;
  next.splice(target + (afterTarget ? 1 : 0), 0, moving);
  return next;
}
