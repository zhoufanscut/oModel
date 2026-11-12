"""Start an omodel binary in a pseudo-terminal, then quit it with q."""
from __future__ import annotations

import errno
import fcntl
import os
import pty
import select
import struct
import subprocess
import sys
import tempfile
import termios
import time

_READY = b"oModel [opencode]:"
_TIMEOUT = 30.0


def _read(fd: int, output: bytearray) -> bool:
    try:
        chunk = os.read(fd, 65536)
    except OSError as exc:
        if exc.errno == errno.EIO:  # The child closed its side of the pty.
            return False
        raise
    if not chunk:
        return False
    output.extend(chunk)
    return True


def _drain(fd: int, output: bytearray) -> None:
    while select.select([fd], [], [], 0)[0]:
        if not _read(fd, output):
            return


def smoke(binary: str) -> None:
    with tempfile.TemporaryDirectory(prefix="omodel-tui-smoke-") as tmp:
        env = os.environ.copy()
        env.update(
            {
                "HOME": tmp,
                "USERPROFILE": tmp,
                "XDG_CONFIG_HOME": f"{tmp}/config",
                "XDG_CACHE_HOME": f"{tmp}/cache-home",
                "XDG_DATA_HOME": f"{tmp}/data",
                "OMODEL_CACHE_DIR": f"{tmp}/model-cache",
                "PATH": "/usr/bin:/bin",
                "TERM": "xterm-256color",
            }
        )
        master, slave = pty.openpty()
        process = None
        output = bytearray()
        error = None
        try:
            fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
            process = subprocess.Popen(
                [binary, "--config", f"{tmp}/omo.jsonc"],
                stdin=slave,
                stdout=slave,
                stderr=slave,
                cwd=tmp,
                env=env,
                close_fds=True,
            )
            os.close(slave)
            slave = -1
            deadline = time.monotonic() + _TIMEOUT

            while _READY not in output:
                if process.poll() is not None:
                    _drain(master, output)
                    raise RuntimeError(f"exited {process.returncode} before drawing the TUI")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("timed out waiting for the TUI header")
                readable = select.select([master], [], [], min(0.1, remaining))[0]
                if readable and not _read(master, output):
                    process.wait()
                    raise RuntimeError(f"closed the terminal after exit {process.returncode}")

            os.write(master, b"q")
            while process.poll() is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError("timed out waiting for q to quit the TUI")
                readable = select.select([master], [], [], min(0.1, remaining))[0]
                if readable and not _read(master, output):
                    process.wait()
                    break
            _drain(master, output)
            if process.returncode:
                raise RuntimeError(f"q exited with status {process.returncode}")
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            error = exc
        finally:
            if process is not None and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            if slave >= 0:
                os.close(slave)
            os.close(master)

        if error is not None:
            text = output.decode("utf-8", errors="replace")
            raise RuntimeError(f"{error}\n--- captured TUI output ---\n{text}") from error


def main() -> int:
    if len(sys.argv) != 2:
        print(f"usage: {sys.argv[0]} /path/to/omodel", file=sys.stderr)
        return 2
    binary = os.path.abspath(sys.argv[1])
    try:
        smoke(binary)
    except (OSError, RuntimeError) as exc:
        print(f"TUI smoke failed: {exc}", file=sys.stderr)
        return 1
    print(f"TUI smoke passed: {binary}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
