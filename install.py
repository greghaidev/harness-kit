#!/usr/bin/env python3
"""install.py — put the kit on this machine and wire it into Claude Code. Windows, macOS, Linux.

    python install.py                 # on Windows: py install.py
    python install.py --dry-run       # print every change, make none
    python install.py --schedule      # Windows: also register the weekly sweep and daily roll

Every step is safe to re-run, and a re-run is how you update:

  1. Reads ~/.claude/settings.json FIRST. If it cannot be parsed, nothing on the machine changes.
  2. Copies core/, menu/ and verify.py into the harness home (default ~/harness). The note
     stores live beside them and are never touched.
  3. Creates a virtual environment there and installs pyyaml and the MCP SDK.
  4. Creates the two note stores as git repositories.
  5. MERGES the environment and hooks into settings.json, after backing the old file up. Hooks
     from an earlier install of this kit are replaced, never duplicated; anything else in the
     file is left exactly as it was.
  6. Registers the memory server with Claude Code, if the `claude` command is on PATH, and
     reads the registration back rather than assuming it took.

WHY THE HOOKS ARE WRITTEN IN EXEC FORM
A hook entry with "args" is started directly, with no shell involved (Claude Code docs: hooks,
"Exec form and shell form"). The previous instructions wrote shell-form strings such as
`~/harness/.venv/bin/python ~/harness/core/...`. Those depend on a shell expanding `~`, on the
Unix layout of a virtual environment (Windows puts the interpreter in Scripts\\python.exe, not
bin/python), and for the Stop hook on bash itself. On Windows, Claude Code's shell is Git Bash
when installed and PowerShell when not, and a home directory like C:\\Users\\First Last breaks
the quoting of either. Exec form avoids all of it: an absolute interpreter path, an absolute
script path, and nothing for any shell to interpret.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

KIT = Path(__file__).resolve().parent
COPY_DIRS = ("core", "menu")
COPY_FILES = ("verify.py",)
IGNORE = shutil.ignore_patterns("__pycache__", ".pytest_cache", "*.pyc")
PACKAGES = ["pyyaml", "mcp[cli]"]
MCP_NAME = "harness-memory"

# A hook belongs to this kit if its command line names one of these. The list includes the old
# bash entry point, so a re-run replaces a hand-wired install from the earlier instructions
# instead of adding a second copy of every hook beside it.
OUR_HOOK_MARKERS = ("01-memory/session_context.py", "06-hygiene/hygiene.py",
                    "02-session/stop_guard.py", "02-session/unfinished-work-stop-guard")


# ─────────────────────────────────────────────────────────────── pure parts (tested directly)

def venv_python(venv: Path, system: str = os.name) -> Path:
    return venv / "Scripts" / "python.exe" if system == "nt" else venv / "bin" / "python"


def harness_env(home: Path) -> dict:
    return {"HARNESS_HOME": str(home),
            "HARNESS_WORK_STORE": str(home / "store" / "work"),
            "HARNESS_META_STORE": str(home / "store" / "meta")}


def _hook(python: Path, script: Path, *args: str) -> dict:
    return {"type": "command", "command": str(python), "args": [str(script), *args]}


def harness_hooks(home: Path, python: Path) -> dict:
    core = home / "core"
    return {
        "SessionStart": [_hook(python, core / "01-memory" / "session_context.py"),
                         _hook(python, core / "06-hygiene" / "hygiene.py", "status")],
        "Stop": [_hook(python, core / "02-session" / "stop_guard.py")],
    }


def _is_ours(hook) -> bool:
    if not isinstance(hook, dict):
        return False
    line = " ".join([str(hook.get("command", ""))] + [str(a) for a in hook.get("args") or []])
    line = line.replace("\\", "/")
    return any(marker in line for marker in OUR_HOOK_MARKERS)


def merge_settings(settings: dict, home: Path, python: Path) -> tuple[dict, list[str]]:
    """The merged settings and a readable list of what changed. Never touches disk.

    Raises ValueError when the file has a shape this cannot merge into without guessing."""
    out = json.loads(json.dumps(settings))
    changes = []

    env = out.setdefault("env", {})
    if not isinstance(env, dict):
        raise ValueError('"env" is not a JSON object')
    for key, value in harness_env(home).items():
        if env.get(key) != value:
            changes.append(f"env {key} = {value}" + (f"   (was {env[key]})" if key in env else ""))
            env[key] = value

    hooks = out.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise ValueError('"hooks" is not a JSON object')
    for event, ours in harness_hooks(home, python).items():
        before = hooks.get(event) or []
        if not isinstance(before, list):
            raise ValueError(f'"hooks.{event}" is not a list')
        kept, replaced = [], 0
        for group in before:
            inner = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(inner, list):
                kept.append(group)
                continue
            theirs = [h for h in inner if not _is_ours(h)]
            replaced += len(inner) - len(theirs)
            if theirs:
                kept.append({**group, "hooks": theirs})
        after = kept + [{"hooks": ours}]
        if after != before:
            hooks[event] = after
            changes.append(f"hooks.{event}: {len(ours)} harness hook(s)"
                           + (f", replacing {replaced} from an earlier install" if replaced else ""))
    return out, changes


def load_settings(path: Path) -> dict:
    """{} when absent or empty. Raises ValueError when present and unreadable as a JSON object."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8-sig")  # tolerate the BOM some Windows editors add
    if not text.strip():
        return {}
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("the top level is not a JSON object")
    return data


def mcp_config(home: Path, python: Path) -> dict:
    return {"type": "stdio", "command": str(python),
            "args": [str(home / "core" / "01-memory" / "memory_server.py")],
            "env": {k: v for k, v in harness_env(home).items() if k != "HARNESS_HOME"}}


def schedule_tasks(home: Path, python: Path, system: str = os.name) -> list[tuple[str, list[str]]]:
    """(what, argv) for each scheduled job on Windows. pythonw.exe is preferred so a console
    window does not flash open on the operator's screen every morning."""
    if system != "nt":
        return []
    quiet = python.with_name("pythonw.exe")
    runner = quiet if quiet.exists() else python

    def task(name, when, script, *verb):
        action = subprocess.list2cmdline([str(runner), str(script), *verb])
        return ["schtasks", "/Create", "/F", "/TN", name, *when, "/TR", action]

    core = home / "core"
    return [
        ("weekly hygiene sweep", task("harness hygiene sweep", ["/SC", "WEEKLY", "/D", "MON", "/ST", "08:00"],
                                      core / "06-hygiene" / "hygiene.py", "sweep")),
        ("daily journal roll", task("harness journal roll", ["/SC", "DAILY", "/ST", "18:30"],
                                    core / "04-journal" / "journal.py", "roll", "--quiet")),
    ]


def posix_schedule_hint(home: Path, python: Path, system: str = sys.platform) -> list[str]:
    if system == "darwin":
        return ["Add these lines with `crontab -e` (macOS has no systemd):",
                f"  0 8 * * 1  {python} {home / 'core/06-hygiene/hygiene.py'} sweep",
                f"  30 18 * * *  {python} {home / 'core/04-journal/journal.py'} roll --quiet"]
    return ["Install the systemd user timers described in",
            f"  {home / 'core/06-hygiene/weekly.timer.example'}",
            f"  {home / 'core/04-journal/daily.timer.example'}",
            f"and change their ExecStart interpreter to {python}"]


# ─────────────────────────────────────────────────────────────────────────── side effects

def say(msg=""):
    print(msg, flush=True)


def run(argv, **kw):
    return subprocess.run([str(a) for a in argv], capture_output=True, text=True,
                          encoding="utf-8", errors="replace", **kw)


def copy_kit(home: Path) -> None:
    home.mkdir(parents=True, exist_ok=True)
    for d in COPY_DIRS:
        shutil.copytree(KIT / d, home / d, ignore=IGNORE, dirs_exist_ok=True)
    for f in COPY_FILES:
        shutil.copy2(KIT / f, home / f)


def make_venv(home: Path) -> Path:
    python = venv_python(home / ".venv")
    if not python.exists():
        r = run([sys.executable, "-m", "venv", home / ".venv"])
        if r.returncode != 0:
            raise SystemExit(f"install: could not create the virtual environment:\n{r.stderr}")
    r = run([python, "-m", "pip", "install", "-q", "--disable-pip-version-check", *PACKAGES])
    if r.returncode != 0:
        raise SystemExit(f"install: pip could not install {PACKAGES}:\n{r.stderr[-2000:]}")
    return python


def init_stores(home: Path) -> None:
    if not shutil.which("git"):
        raise SystemExit("install: git is required — every note write is a commit. Install it "
                         "(Windows: https://git-scm.com/download/win) and re-run.")
    for tenant in ("work", "meta"):
        d = home / "store" / tenant
        d.mkdir(parents=True, exist_ok=True)
        if not (d / ".git").exists():
            r = run(["git", "init", "-q"], cwd=d)
            if r.returncode != 0:
                raise SystemExit(f"install: git init failed in {d}:\n{r.stderr}")


def write_settings(path: Path, data: dict) -> Path | None:
    backup = None
    if path.exists():
        stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = path.with_name(f"{path.name}.bak-{stamp}")
        shutil.copy2(path, backup)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return backup


def register_mcp(home: Path, python: Path) -> tuple[bool, str]:
    manual = ("claude mcp add-json --scope user " + MCP_NAME + " "
              + json.dumps(json.dumps(mcp_config(home, python))))
    claude = shutil.which("claude")
    if not claude:
        return False, f"`claude` is not on PATH. Register the memory server with:\n    {manual}"
    if run([claude, "mcp", "get", MCP_NAME]).returncode == 0:
        run([claude, "mcp", "remove", "--scope", "user", MCP_NAME])
    r = run([claude, "mcp", "add-json", "--scope", "user", MCP_NAME,
             json.dumps(mcp_config(home, python))])
    if r.returncode != 0 or run([claude, "mcp", "get", MCP_NAME]).returncode != 0:
        return False, (f"registration did not take ({(r.stderr or r.stdout).strip()[:200]}). "
                       f"Register it by hand:\n    {manual}")
    return True, f"registered `{MCP_NAME}` (user scope), and read it back"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Install the harness kit and wire it into Claude Code.")
    ap.add_argument("--home", default=str(Path.home() / "harness"),
                    help="where the kit and its note stores live (default: ~/harness)")
    ap.add_argument("--settings", default=str(Path.home() / ".claude" / "settings.json"),
                    help="the Claude Code settings file to merge into (default: user scope)")
    ap.add_argument("--no-venv", action="store_true",
                    help="use this interpreter instead of a new virtual environment "
                         "(it must already have pyyaml and mcp)")
    ap.add_argument("--skip-mcp", action="store_true", help="do not register the memory server")
    ap.add_argument("--schedule", action="store_true",
                    help="Windows: register the weekly sweep and daily roll with Task Scheduler")
    ap.add_argument("--dry-run", action="store_true", help="print every change and make none")
    a = ap.parse_args(argv)

    if sys.version_info < (3, 10):
        say("install: Python 3.10 or newer is required (the MCP SDK needs it).")
        return 1
    home = Path(a.home).expanduser().resolve()
    settings_path = Path(a.settings).expanduser()
    if home == KIT or KIT in home.parents:
        say(f"install: {home} is inside the kit checkout — pick a home outside it.")
        return 1

    try:
        settings = load_settings(settings_path)
        python = Path(sys.executable) if a.no_venv else venv_python(home / ".venv")
        merged, changes = merge_settings(settings, home, python)
    except ValueError as e:
        say(f"install: cannot merge into {settings_path}: {e}\n"
            "  Nothing was changed. Fix that file (or move it aside) and re-run.")
        return 1

    say(f"harness home : {home}")
    say(f"interpreter  : {python}")
    say(f"settings     : {settings_path}")
    if a.dry_run:
        say("\nwould change settings.json:" if changes else "\nsettings.json already up to date")
        for c in changes:
            say(f"  {c}")
        say("\n(dry run — nothing written)")
        return 0

    say("\n[1/5] copying the kit");            copy_kit(home)
    say("[2/5] note stores");                 init_stores(home)
    if not a.no_venv:
        say("[3/5] virtual environment + packages (this takes a minute)")
        python = make_venv(home)
    else:
        say("[3/5] virtual environment skipped (--no-venv)")
    probe = run([python, "-c", "import yaml"])
    if probe.returncode != 0:
        say(f"install: {python} cannot import yaml — install pyyaml into it, or drop --no-venv.")
        return 1

    say("[4/5] settings.json")
    if changes:
        backup = write_settings(settings_path, merged)
        for c in changes:
            say(f"  {c}")
        if backup:
            say(f"  previous file kept at {backup}")
    else:
        say("  already up to date")

    say("[5/5] memory server")
    if a.skip_mcp:
        say("  skipped (--skip-mcp)")
    else:
        ok, msg = register_mcp(home, python)
        say(f"  {msg}")

    tasks = schedule_tasks(home, python)
    say("\nscheduling")
    if tasks and a.schedule:
        for what, argv_ in tasks:
            r = run(argv_)
            say(f"  {what}: " + ("registered with Task Scheduler" if r.returncode == 0
                                 else f"FAILED — {(r.stderr or r.stdout).strip()[:160]}"))
    elif tasks:
        say("  re-run with --schedule to register the weekly sweep and daily roll with Task Scheduler")
    else:
        for line in posix_schedule_hint(home, python):
            say(f"  {line}")

    say(f"\nnext: {python} {home / 'verify.py'}")
    return 0


if __name__ == "__main__":
    # A Windows console or pipe defaults to the ANSI code page; the dashes above would crash it.
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
