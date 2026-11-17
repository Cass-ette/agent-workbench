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
    pending = b""

    def draw():
        rows = os.get_terminal_size(fd).lines
        text = [f"TUI_TOP={top} UNEXPECTED={unexpected}"]
        text += [f"Conversation line {top + n}" for n in range(rows - 1)]
        os.write(1, ("\x1b[2J\x1b[H" + "\r\n".join(text)).encode())

    try:
        tty.setraw(fd)
        os.write(1, b"\x1b[?1049h\x1b[?1003h\x1b[?1006h")
        draw()
        while True:
            pending += os.read(fd, 4096)
            while pending:
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
