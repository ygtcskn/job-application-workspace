"""Gemini CLI headless adapter, verified against installed @google/gemini-cli 0.59.0.

Unlike Codex and Claude Code, Gemini CLI exposes no per-request response-schema
flag. The schema still reaches the model inside the assembled prompt, and the
response is validated locally against the same contract, so a malformed reply
becomes an ordinary format-repair attempt rather than a silent acceptance.
"""
import json
import re
from src.process import run_process, ProcessFailure
from src.providers.base import ProviderFailure, launcher

FENCE = re.compile(r"^```[A-Za-z0-9_+-]*[ \t]*\r?\n(.*?)\r?\n?```[ \t]*$", re.S)
# Installed help: "Defaults to interactive mode. Use -p/--prompt for non-interactive (headless)
# mode", and -p is "Appended to input on stdin (if any)". The assembled prompt therefore travels
# on stdin and -p carries only this closing instruction, keeping personal content out of argv.
TRAILER = "Return only the JSON object described above, with no prose and no code fences."
AGENT_TRAILER = "Complete the task described above."


def command(settings, trailer=TRAILER, policy_path=None):
    # 'yolo' approval auto-runs every tool (read, edit, shell, web search) without prompts.
    # --skip-trust covers the per-job build folder the agent runs in: the CLI refuses to run
    # headless in an untrusted folder.
    args = launcher(settings) + ["--output-format", "json", "--approval-mode", "default",
                                 "--skip-trust", "--extensions", "none", "--prompt", trailer]
    if policy_path:
        args += ['--policy', str(policy_path)]
    if settings.model:
        args += ["--model", settings.model]
    return args


def extract_json(text: str) -> str:
    """Return the best JSON candidate; malformed text falls through to format repair."""
    stripped = text.strip()
    fenced = FENCE.match(stripped)
    if fenced:
        stripped = fenced[1].strip()
    start, end = stripped.find("{"), stripped.rfind("}")
    return stripped[start:end + 1] if 0 <= start < end else stripped


def error_envelope(*streams):
    """A failed run prints its JSON error envelope to stderr, after a stack trace."""
    decoder = json.JSONDecoder()
    for text in streams:
        for index, character in enumerate(text or ""):
            if character != "{":
                continue
            try:
                value, _ = decoder.raw_decode(text[index:])
            except ValueError:
                continue
            if isinstance(value, dict) and value.get("error"):
                error = value["error"]
                return error.get("message") or error.get("type") if isinstance(error, dict) else error
    return None


def resolved_model(stats):
    """Gemini routes roles to different models; the 'main' role produced the answer."""
    models = stats.get("models") if isinstance(stats, dict) else None
    if not isinstance(models, dict) or not models:
        return None
    main = [name for name, value in models.items() if isinstance(value, dict) and "main" in (value.get("roles") or {})]
    if len(main) == 1:
        return main[0]
    return next(iter(models)) if len(models) == 1 else None


def invoke(settings, prompt, schema_path, stage_dir, cwd):
    policy = stage_dir / 'tools.toml'
    rules = '[[rule]]\ntoolName = "*"\ndecision = "deny"\npriority = 900\n'
    if settings.allow_web:
        for tool in ('google_web_search', 'web_fetch'):
            rules += f'\n[[rule]]\ntoolName = "{tool}"\ndecision = "allow"\npriority = 950\n'
    policy.write_text(rules, encoding='utf-8')
    try:
        stdout, stderr = run_process(command(settings, TRAILER if schema_path else AGENT_TRAILER, policy), cwd, settings.timeout_seconds, prompt)
    except ProcessFailure as exc:
        (stage_dir / "stdout.txt").write_text(exc.stdout, encoding="utf-8")
        (stage_dir / "stderr.txt").write_text(exc.stderr, encoding="utf-8")
        # A rejected run exits non-zero and prints its JSON envelope to stderr after a trace.
        detail = error_envelope(exc.stderr, exc.stdout)
        if detail:
            raise ProviderFailure(f"Gemini rejected request: {detail}") from exc
        raise ProviderFailure(f'Gemini process failed: {exc}') from exc
    (stage_dir / "stdout.txt").write_text(stdout, encoding="utf-8")
    (stage_dir / "stderr.txt").write_text(stderr, encoding="utf-8")
    # Agent runs return free text; only schema runs are narrowed to a JSON candidate.
    extract = extract_json if schema_path else str.strip
    try:
        envelope = json.loads(stdout)
    except ValueError:
        # A non-JSON envelope is still handed on; local validation rejects anything unusable.
        return extract(stdout)
    if not isinstance(envelope, dict):
        return extract(stdout)
    if envelope.get("error"):
        raise ProviderFailure(f"Gemini rejected request: {error_envelope(stdout)}")
    settings.resolved_model = resolved_model(envelope.get("stats")) or settings.resolved_model
    response = envelope.get("response")
    if response is None:
        raise ValueError("Gemini returned no response field")
    return extract(response if isinstance(response, str) else json.dumps(response, ensure_ascii=False))
