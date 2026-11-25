import { useEffect, useState } from "react";
import palettes from "../../shared/catppuccin.json";

export type AppearanceMode = "system" | "light" | "dark";
export type ResolvedTheme = "light" | "dark";
const names = ["black", "red", "green", "yellow", "blue", "magenta", "cyan", "white",
  "brightBlack", "brightRed", "brightGreen", "brightYellow", "brightBlue", "brightMagenta", "brightCyan", "brightWhite"];
export function terminalTheme(mode: ResolvedTheme) {
  const palette = palettes[mode];
  return { ...Object.fromEntries(names.map((name, index) => [name, palette.ansi[index]])),
    foreground: palette.colors.text, background: palette.colors.base, cursor: palette.colors.rosewater,
    cursorAccent: palette.colors.base, selectionBackground: palette.colors.surface1 };
}
export function applyAppearance(resolved: ResolvedTheme) {
  document.documentElement.dataset.theme = resolved;
  document.documentElement.style.colorScheme = resolved;
  for (const [name, color] of Object.entries(palettes[resolved].colors)) {
    document.documentElement.style.setProperty(`--ctp-${name}`, color);
  }
}
export function initialAppearance(): ResolvedTheme {
  const saved = window.localStorage.getItem("appearance.mode");
  return saved === "dark" || saved === "light" ? saved : window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}
export function useAppearance() {
  const [mode, setMode] = useState<AppearanceMode>(() => {
    const saved = window.localStorage.getItem("appearance.mode");
    return saved === "light" || saved === "dark" ? saved : "system";
  });
  const [systemDark, setSystemDark] = useState(() => window.matchMedia?.("(prefers-color-scheme: dark)").matches ?? false);
  useEffect(() => {
    const query = window.matchMedia?.("(prefers-color-scheme: dark)");
    if (!query) return;
    const changed = () => setSystemDark(query.matches);
    query.addEventListener("change", changed); changed();
    return () => query.removeEventListener("change", changed);
  }, []);
  const resolved: ResolvedTheme = mode === "system" ? (systemDark ? "dark" : "light") : mode;
  useEffect(() => {
    applyAppearance(resolved);
  }, [resolved]);
  return { mode, resolved, changeMode: (next: AppearanceMode) => {
    if (!["system", "light", "dark"].includes(next)) return;
    window.localStorage.setItem("appearance.mode", next); setMode(next);
  } };
}

export function AppearancePicker({ value, change }: { value: AppearanceMode; change: (value: AppearanceMode) => void }) {
  return <label className="appearance-picker">外观<select aria-label="外观" value={value}
    onChange={event => change(event.currentTarget.value as AppearanceMode)}>
    <option value="system">跟随系统</option><option value="light">浅色 · Latte</option><option value="dark">深色 · Mocha</option>
  </select></label>;
}
