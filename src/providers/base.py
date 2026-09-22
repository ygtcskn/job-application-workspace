"""Discovery and normalized settings shared by the CLI adapters."""
from __future__ import annotations
import json
import os
import platform
import re
import shutil
from dataclasses import dataclass, asdict
from pathlib import Path
from src.process import run_process

UNKNOWN = "provider default; resolved value unknown"
SUPPORTED_PROVIDERS = ("codex", "claude", "gemini")
# Rechecked against installed help before every live run. A missing flag is an
# actionable error, never a silent fallback to a less restricted invocation.
REQUIRED_FLAGS = {
    "codex": ("--output-schema", "--ignore-user-config", "--ephemeral", "--sandbox"),
    "claude": ("--json-schema", "--tools", "--safe-mode", "--no-session-persistence", "--permission-mode"),
    "gemini": ("--output-format", "--approval-mode", "--model", "--prompt", "--skip-trust"),
}
# Whether the CLI itself constrains the reply to the supplied schema.
SCHEMA_ENFORCEMENT = {
    "codex": "provider enforced from --output-schema",
    "claude": "provider enforced from --json-schema",
    "gemini": "prompt only; validated locally with format repair",
}


class ProviderFailure(RuntimeError):
    """Provider rejection that should stop further jobs in a batch."""


def node_executable() -> str:
    found = shutil.which("node")
    if not found:
        raise ValueError("Node.js is required to launch the Gemini CLI; install Node and retry")
    return str(Path(found).resolve())


def package_entry(prefix: Path, package: str) -> Path | None:
    """Resolve an npm package's own bin script, so no Windows .cmd shim is executed."""
    for root in (prefix / "node_modules" / package, prefix / "lib/node_modules" / package):
        manifest = root / "package.json"
        if not manifest.is_file():
            continue
        try:
            entry = json.loads(manifest.read_text(encoding="utf-8")).get("bin")
        except (OSError, ValueError):
            entry = None
        relatives = [entry] if isinstance(entry, str) else [v for v in (entry or {}).values() if isinstance(v, str)]
        for relative in relatives + ["dist/index.js", "bundle/gemini.js"]:
            script = (root / relative).resolve()
            if script.is_file():
                return script
    return None


def npm_codex_executable(prefix: Path) -> Path | None:
    """Resolve native npm payloads without executing a shell wrapper."""
    arm = platform.machine().lower() in {"arm64", "aarch64"}
    target = "aarch64-pc-windows-msvc" if arm else "x86_64-pc-windows-msvc"
    package = "codex-win32-arm64" if arm else "codex-win32-x64"
    root = prefix / "node_modules/@openai/codex"
    vendors = (root / f"node_modules/@openai/{package}/vendor",
               prefix / f"node_modules/@openai/{package}/vendor", root / "vendor")
    for vendor in vendors:
        for folder in ("bin", "codex"):
            candidate = vendor / target / folder / "codex.exe"
            if candidate.is_file():
                return candidate.resolve()
    return None


def find_executable(provider: str, configured: str) -> str:
    # The desktop app prepends its bundled CLI to PATH. Prefer the independently
    # upgradable npm installation for the default name; explicit paths still win.
    if os.name == 'nt' and provider == 'codex' and configured == 'codex':
        prefixes = [Path(os.environ.get("APPDATA", "")) / "npm"]
        prefixes.extend(Path(p) for p in os.environ.get("PATH", "").split(os.pathsep) if p)
        for prefix in dict.fromkeys(prefixes):
            native = npm_codex_executable(prefix)
            if native:
                return str(native)
    found = shutil.which(configured + '.exe') if os.name == 'nt' and not Path(configured).suffix else None
    found = found or shutil.which(configured)
    p = Path(found or configured)
    if p.suffix.lower() in {".cmd", ".bat", ".ps1"} or (os.name == 'nt' and p.is_file() and not p.suffix):
        if provider == "claude":
            native = p.parent / "node_modules/@anthropic-ai/claude-code/bin/claude.exe"
            if native.is_file():
                return str(native.resolve())
        if provider == "gemini":
            script = package_entry(p.parent, "@google/gemini-cli")
            if script:
                return str(script)
        if provider != 'codex' or configured != 'codex':
            raise ValueError(f"Unverified Windows wrapper {p}; configure the native executable")
        p = Path('__native_codex_lookup__')
    if p.is_file():
        return str(p.resolve())
    if configured == "claude":
        for candidate in (Path.home()/".local/bin/claude.exe", Path(os.environ.get("APPDATA", ""))/"npm/node_modules/@anthropic-ai/claude-code/bin/claude.exe"):
            if candidate.is_file():
                return str(candidate)
    if configured == "codex":
        matches = list((Path(os.environ.get("LOCALAPPDATA", ""))/"OpenAI/Codex/bin").glob("*/codex.exe"))
        if matches:
            return str(max(matches, key=lambda p: p.stat().st_mtime))
    if provider == "gemini":
        for prefix in (Path(os.environ.get("APPDATA", ""))/"npm", Path.home()/".npm-global", Path("/usr/local"), Path("/usr")):
            script = package_entry(prefix, "@google/gemini-cli")
            if script:
                return str(script)
    raise ValueError(f"{provider} executable unavailable: {configured}")


def launcher(settings) -> list[str]:
    """Node-based CLIs run through the interpreter, never through a shell wrapper."""
    if settings.executable.lower().endswith(".js"):
        return [node_executable(), settings.executable]
    return [settings.executable]


def model_catalog() -> dict[str, list[str]]:
    cache = Path(os.environ.get("CODEX_HOME", str(Path.home()/".codex"))) / "models_cache.json"
    try:
        data = json.loads(cache.read_text(encoding="utf-8"))
        return {m["slug"]: [x["effort"] for x in m.get("supported_reasoning_levels", [])] for m in data["models"] if m.get("visibility", "list") == "list"}
    except (OSError, ValueError, KeyError, TypeError):
        return {}


# The CLI names exactly these in its own "set an Auth method" error.
GEMINI_AUTH_VARIABLES = ("GEMINI_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI", "GOOGLE_GENAI_USE_GCA")


def gemini_auth(home: Path | None = None) -> str:
    """Presence only: credential files are never opened and no key value is recorded."""
    home = home or Path(os.environ.get("GEMINI_HOME", str(Path.home()/".gemini")))
    if any(os.environ.get(name) for name in GEMINI_AUTH_VARIABLES):
        return "signed in"
    if (home/"oauth_creds.json").is_file():
        return "signed in"
    settings = home/"settings.json"
    if settings.is_file():
        try:
            # Only the declared auth-method name is inspected; no secret is read or stored.
            text = settings.read_text(encoding="utf-8")
        except OSError:
            return "not verified; run CLI authentication manually"
        if "selectedType" in text or "selectedAuthType" in text:
            return "signed in"
    return "not signed in"


@dataclass
class Settings:
    provider: str
    executable: str
    model: str | None
    effort: str | None
    timeout_seconds: int
    version: str = "not checked"
    availability: str = "Live request required to verify model/effort availability"
    resolved_model: str | None = None
    resolved_effort: str | None = None
    allow_web: bool = False

    def metadata(self):
        return {**asdict(self), "effective_model": self.resolved_model or self.model or UNKNOWN, "effective_effort": self.resolved_effort or self.effort or UNKNOWN}


def resolve_settings(config: dict, provider=None, model=None, effort=None) -> dict[str, Settings]:
    result = {}
    selected = provider if provider is not None else config.get('execution', {}).get('provider')
    if selected not in SUPPORTED_PROVIDERS:
        raise ValueError(f"Unsupported run provider: {selected}; choose one of {', '.join(SUPPORTED_PROVIDERS)}")
    for role in config["agents"]:
        defaults = config["providers"][selected]
        chosen_model = model if model is not None else defaults.get("model")
        chosen_effort = effort if effort is not None else defaults.get("effort")
        chosen_model = None if chosen_model == "default" else chosen_model
        chosen_effort = None if chosen_effort == "default" else chosen_effort
        for value in (chosen_model, chosen_effort):
            if value is not None and (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,150}", value)):
                raise ValueError("Model/effort must be a plain identifier")
        result[role] = Settings(selected, defaults["executable"], chosen_model, chosen_effort, defaults["timeout_seconds"])
    return result


def probe(settings: Settings, cwd: Path) -> dict:
    settings.executable = find_executable(settings.provider, settings.executable)
    launch = launcher(settings)
    version, _ = run_process(launch + ["--version"], cwd, 20)
    settings.version = version.strip()
    help_args = launch + (["exec", "--help"] if settings.provider == "codex" else ["--help"])
    help_text, _ = run_process(help_args, cwd, 20)
    if any(flag not in help_text for flag in REQUIRED_FLAGS[settings.provider]):
        raise ValueError(f"{settings.provider}: installed CLI lacks required safe/structured flags")
    report = {"version": settings.version, "models": [], "efforts": [], "availability": settings.availability,
              "schema_enforcement": SCHEMA_ENFORCEMENT[settings.provider], "help": help_text}
    if settings.provider == "gemini":
        # Gemini CLI advertises no reasoning-effort control; never substitute one silently.
        if settings.effort:
            raise ValueError(f"Gemini CLI accepts no reasoning effort; pass 'default' instead of {settings.effort!r}")
        default_model = re.search(r'--model[^\n]*?\[default:\s*"?([A-Za-z0-9._:/-]+)', help_text, re.S)
        # Presence-only check; no credential file is read and no account identifier is stored.
        return {**report, "auth": gemini_auth(), "models": [default_model[1]] if default_model else []}
    if settings.provider == "codex":
        catalog = model_catalog()
        levels = catalog.get(settings.model, sorted({x for values in catalog.values() for x in values}))
        if settings.effort and levels and settings.effort not in levels:
            raise ValueError(f"Codex effort {settings.effort!r} not supported by cached catalog for {settings.model or 'default'}: {levels}")
        auth_args = launch + ["login", "status"]
        models = list(catalog)
    else:
        effort_match = re.search(r"Effort level.*?\(([^)]+)\)", help_text, re.S)
        levels = [s.strip() for s in effort_match[1].split(",")] if effort_match else []
        if settings.effort and ("--effort" not in help_text or settings.effort not in levels):
            raise ValueError(f"Claude effort {settings.effort!r} not advertised in installed help: {levels}")
        models = re.findall(r"'(\w+)'", help_text.split("--model <model>", 1)[-1].split("-n,", 1)[0])
        auth_args = launch + ["auth", "status"]
    auth = "unknown"
    try:
        out, err = run_process(auth_args, cwd, 20)
        if settings.provider == "claude":
            auth = "signed in" if json.loads(out).get("loggedIn") else "not signed in"
        else:
            auth = "signed in" if "Logged in" in out + err else "unknown"
    except (RuntimeError, ValueError):
        auth = "not verified; run CLI authentication manually"
    # Do not persist auth output: it can contain account identifiers.
    return {**report, "auth": auth, "models": models, "efforts": levels}


def schema_for(contract, rules: dict, rubric: dict) -> dict:
    schema = contract.model_json_schema()
    if contract.__name__ == "Draft":
        keys = rules["cl_tokens"]
        # BLOCKED still returns all token keys as empty strings, avoiding unbounded maps.
        schema["properties"]["cl_tokens"] = {"type": "object", "properties": {k: {"type": "string"} for k in keys}, "required": keys, "additionalProperties": False}
    else:
        for field in ("cv_scores", "cl_scores"):
            schema["properties"][field] = {"type": "object", "properties": {k: {"$ref": "#/$defs/Criterion"} for k in rubric}, "required": list(rubric), "additionalProperties": False}
    return schema
