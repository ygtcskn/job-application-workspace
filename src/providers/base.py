"""Discovery and normalized settings shared by the CLI adapters."""
from __future__ import annotations
import json
import os
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path


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


@dataclass
class Settings:
    provider: str
    executable: str
    model: str | None
    effort: str | None
    timeout_seconds: int
    resolved_model: str | None = None
    allow_web: bool = False
