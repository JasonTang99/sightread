"""Background pipeline job management."""
import subprocess
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path

_lock = threading.Lock()
_current: "JobState | None" = None


@dataclass
class JobState:
    folder: str
    output_dir: str
    running: bool = True
    done: bool = False
    error: str | None = None
    _lines: list[str] = field(default_factory=list, repr=False)

    def append_line(self, line: str) -> None:
        with _lock:
            self._lines.append(line)
            if len(self._lines) > 100:
                self._lines = self._lines[-100:]

    @property
    def last_line(self) -> str | None:
        with _lock:
            return self._lines[-1] if self._lines else None


def current_job() -> "JobState | None":
    return _current


def start_pipeline(folder: Path, output_dir: Path, project_root: Path) -> JobState:
    global _current
    output_dir.mkdir(parents=True, exist_ok=True)
    job = JobState(folder=str(folder), output_dir=str(output_dir))

    def _run() -> None:
        try:
            proc = subprocess.Popen(
                [
                    sys.executable,
                    str(project_root / "scripts" / "pipeline.py"),
                    "--image-dir", str(folder.resolve()),
                    "--output-dir", str(output_dir),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                cwd=str(project_root),
            )
            for line in proc.stdout:
                line = line.rstrip()
                if line:
                    job.append_line(line)
            proc.wait()
            if proc.returncode != 0:
                job.error = f"Pipeline exited {proc.returncode}"
        except Exception as exc:
            job.error = str(exc)
        finally:
            with _lock:
                job.running = False
                job.done = True

    with _lock:
        _current = job

    threading.Thread(target=_run, daemon=True).start()
    return job
