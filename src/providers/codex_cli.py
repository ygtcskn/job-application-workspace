"""Codex structured-response adapter; Python owns document I/O."""
import json
import os
from pathlib import Path
from src.process import ProcessFailure, run_process
from src.providers.base import ProviderFailure


def command(settings, schema_path: Path | None, output_path: Path, cwd: Path):
    args = [settings.executable, "exec", "--ignore-user-config", "--ephemeral", "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never", "--json", "--output-last-message", str(output_path), "-C", str(cwd)]
    if schema_path:
        args += ["--output-schema", str(schema_path)]
    if os.name == "nt":
        # --ignore-user-config also discards [windows].sandbox. Select the
        # configured Windows isolation mode explicitly so tools can run.
        args += ["-c", 'windows.sandbox="elevated"']
    for value in ("approval_policy=\"never\"", "features.multi_agent=false", "features.apps=false", "features.memories=false", "features.shell_tool=false",
                  'web_search="live"' if settings.allow_web else 'web_search="disabled"'):
        args += ["-c", value]
    if settings.model:
        args += ["--model", settings.model]
    if settings.effort:
        args += ["-c", "model_reasoning_effort=" + json.dumps(settings.effort)]
    return args + ["-"]


def request_error(stdout):
    for line in reversed(stdout.splitlines()):
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get('type') in {'error', 'turn.failed'}:
            detail = event.get('error') or event.get('message') or event
            if isinstance(detail, dict):
                detail = detail.get('message') or detail
            return str(detail)
    return None


def invoke(settings, prompt, schema_path, stage_dir, cwd):
    output = stage_dir / ("last_message.json" if schema_path else "last_message.txt")
    args = command(settings, schema_path, output, cwd)
    try:
        stdout, stderr = run_process(args, cwd, settings.timeout_seconds, prompt)
    except ProcessFailure as exc:
        (stage_dir / "stdout.txt").write_text(exc.stdout, encoding="utf-8")
        (stage_dir / "stderr.txt").write_text(exc.stderr, encoding="utf-8")
        detail = request_error(exc.stdout) or str(exc)
        raise ProviderFailure(f"Codex request failed: {detail}; logs: {stage_dir}") from exc
    (stage_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
    (stage_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
    if not output.is_file():
        raise ProviderFailure("Codex returned no final message; see provider logs")
    detail = request_error(stdout)
    if detail:
        raise ProviderFailure(f'Codex request failed: {detail}')
    return output.read_text(encoding="utf-8")
