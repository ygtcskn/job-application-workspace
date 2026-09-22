"""UTF-8 process execution with bounded waits and process-tree cleanup."""
import os
import signal
import subprocess
from pathlib import Path


class ProcessFailure(RuntimeError):
    def __init__(self, message, stdout="", stderr=""):
        super().__init__(message)
        self.stdout, self.stderr = stdout, stderr


def kill_tree(proc: subprocess.Popen):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW, timeout=15)
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    finally:
        if proc.poll() is None:
            proc.kill()


def run_process(args: list[str], cwd: Path, timeout: float, stdin: str | None = None):
    env = os.environ.copy()
    env.update({"PYTHONUTF8": "1", "NO_COLOR": "1"})
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}
    try:
        proc = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace", **kwargs)
    except OSError as exc:
        raise ProcessFailure(f"Cannot launch {args[0]}: {exc}") from exc
    try:
        stdout, stderr = proc.communicate(stdin, timeout=timeout)
    except subprocess.TimeoutExpired:
        kill_tree(proc)
        stdout, stderr = proc.communicate(timeout=15)
        raise ProcessFailure(f"Process timed out after {timeout}s", stdout, stderr)
    except KeyboardInterrupt:
        # The child runs in its own process group and never sees Ctrl+C; stop it with us.
        kill_tree(proc)
        raise
    if proc.returncode:
        raise ProcessFailure(f"Process exited {proc.returncode}: {stderr[-2000:] or stdout[-2000:]}", stdout, stderr)
    return stdout, stderr
