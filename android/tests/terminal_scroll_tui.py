"""Small full-screen PTY app for scroll regression tests; no model calls."""
import os
import re
import sys
import termios
import tty


def main():
    fd = sys.stdin.fileno()
    previous = termios.tcgetattr(fd)
    top, unexpected = 80, 0
    codex_mode = False
    pending = b""

    def draw():
        rows = os.get_terminal_size(fd).lines
        text = ["/ T R A N S C R I P T /"] if codex_mode else []
        text += [f"TUI_TOP={top} UNEXPECTED={unexpected}"]
        text += [f"Conversation line {top + n}" for n in range(rows - len(text) - (1 if codex_mode else 0))]
        if codex_mode:
            text += ["up/down to scroll - home/end to jump"]
        os.write(1, ("\x1b[2J\x1b[H" + "\r\n".join(text)).encode())

    try:
        tty.setraw(fd)
        os.write(1, b"\x1b[?1049h\x1b[?1003h\x1b[?1006h")
        draw()
        while True:
            pending += os.read(fd, 4096)
            while pending:
                shortcut = next((s for s in (b"\x1b[1;5F", b"\x1b[F") if pending.startswith(s)), None)
                if shortcut:
                    if (shortcut == b"\x1b[F") == codex_mode:
                        top = 100
                    else:
                        unexpected += 1
                    pending = pending[len(shortcut):]
                    draw()
                    continue
                arrow = next((s for s in (b"\x1b[A", b"\x1b[B") if pending.startswith(s)), None)
                if arrow:
                    top = max(0, min(100, top + (-1 if arrow == b"\x1b[A" else 1)))
                    pending = pending[len(arrow):]
                    draw()
                    continue
                page = next((s for s in (b"\x1b[5~", b"\x1b[6~") if pending.startswith(s)), None)
                if page:
                    top = max(0, min(100, top + (-10 if page == b"\x1b[5~" else 10)))
                    pending = pending[len(page):]
                    draw()
                    continue
                match = re.match(rb"\x1b\[<(\d+);\d+;\d+[Mm]", pending)
                if match:
                    button = int(match[1])
                    pending = pending[match.end():]
                elif pending.startswith(b"\x1b[M") and len(pending) >= 6:
                    button = pending[3] - 32
                    pending = pending[6:]
                elif pending.startswith(b"\x1b"):
                    break  # Incomplete report; preserve it across PTY reads.
                else:
                    key, pending = pending[0], pending[1:]
                    if key == ord("q"):
                        return
                    if key == ord("b"):
                        os.write(1, b"\x1b[?1006l")
                    elif key == ord("c"):
                        codex_mode = True
                        os.write(1, b"\x1b[?1006l\x1b[?1003l")
                    elif key == ord("g"):
                        codex_mode = False
                    elif key == ord("r"):
                        top = 80
                    else:
                        unexpected += 1
                    draw()
                    continue
                if button in (64, 65):
                    top = max(0, min(100, top + (-1 if button == 64 else 1)))
                elif button not in (0, 3, 32, 35):
                    unexpected += 1
                draw()
    finally:
        os.write(1, b"\x1b[?1006l\x1b[?1003l\x1b[?1049l")
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)


if __name__ == "__main__":
    main()
