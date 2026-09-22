"""Console progress: numbered steps with a live timer and total elapsed time."""
import sys
import threading
import time
from contextlib import contextmanager


def clock(seconds: float) -> str:
    hours, rest = divmod(int(seconds), 3600)
    minutes, seconds = divmod(rest, 60)
    return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


class Progress:
    def __init__(self, total_steps: int):
        self.started = time.monotonic()
        self.total_steps = total_steps
        self.current = 0
        # A terminal redraws one line every second; a log or IDE pane gets a line every 30 seconds.
        self.live = sys.stdout.isatty()

    def elapsed(self) -> str:
        return clock(time.monotonic() - self.started)

    @contextmanager
    def step(self, label: str):
        self.current += 1
        prefix = f"[{self.current}/{self.total_steps}] {label}"
        step_started = time.monotonic()
        stop = threading.Event()

        def status(state: str) -> str:
            return f"{prefix} {state} {clock(time.monotonic() - step_started)}  (total {self.elapsed()})"

        def tick():
            while not stop.wait(1 if self.live else 30):
                if self.live:
                    print("\r" + status("..."), end="", flush=True)
                else:
                    print(status("... running"), flush=True)

        print(prefix + " ...", end="" if self.live else "\n", flush=True)
        ticker = threading.Thread(target=tick, daemon=True)
        ticker.start()
        try:
            yield
        except BaseException:
            stop.set()
            ticker.join()
            print(("\r" if self.live else "") + status("FAILED after").ljust(90), flush=True)
            raise
        stop.set()
        ticker.join()
        print(("\r" if self.live else "") + status("done in").ljust(90), flush=True)
