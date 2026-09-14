#!/usr/bin/env python3
"""The kit must run on Windows, macOS and Linux — these are the guards that keep it that way.

It was written on one Linux machine and installed on others the author cannot reach. The first
Windows install found that almost nothing ran: the memory store imported a Unix-only module, the
Stop hook was a bash script, and the SessionStart hook crashed printing a warning sign because a
Windows pipe does not default to UTF-8. None of that was visible from Linux, and none of it would
stay fixed on its own — the next `open(path)` without an encoding reintroduces the whole class.

So the rules are mechanical. The CI matrix runs the full suite on all three platforms; these run
everywhere, including on Linux, where they are the only warning a change gets before it ships.
"""
import ast
import json
import os
import pathlib
import subprocess
import sys
import tempfile

import pytest

KIT = pathlib.Path(__file__).resolve().parent
UNIX_ONLY = {"fcntl", "termios", "pwd", "grp", "resource", "posix"}
MCP_OWNS_STDIO = {"core/01-memory/memory_server.py"}


def _sources():
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=KIT, capture_output=True,
                         text=True, encoding="utf-8").stdout.split()
    files = [KIT / f for f in out if not f.startswith("book/")]
    if not files:  # not a git checkout (an installed copy): walk instead
        files = [p for p in KIT.rglob("*.py") if "__pycache__" not in p.parts]
    return files


def _rel(p):
    return p.relative_to(KIT).as_posix()


def _text_calls_missing_encoding(tree):
    """(line, call name) for every text-mode file or subprocess call with no encoding=."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
        kws = {k.arg for k in node.keywords}
        if "encoding" in kws:
            continue
        if name in ("open", "read_text", "write_text"):
            if isinstance(f, ast.Attribute) and getattr(f.value, "id", "") == "os":
                continue
            mode = None
            if name == "open":
                args = node.args if isinstance(f, ast.Attribute) else node.args[1:]
                if args and isinstance(args[0], ast.Constant):
                    mode = args[0].value
                mode = next((k.value.value for k in node.keywords
                             if k.arg == "mode" and isinstance(k.value, ast.Constant)), mode)
            if mode is not None and "b" in str(mode):
                continue
            yield node.lineno, name
        elif name in ("run", "check_output", "Popen") and ({"text", "universal_newlines"} & kws):
            yield node.lineno, f"subprocess.{name}"


def test_every_text_call_names_its_encoding():
    """Windows opens text in the ANSI code page unless told otherwise: a note with an em dash
    reads as mojibake, and some UTF-8 sequences do not decode at all."""
    missing, seen = [], 0
    for p in _sources():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        seen += sum(1 for n in ast.walk(tree) if isinstance(n, ast.Call))
        missing += [f"{_rel(p)}:{line} {name}" for line, name in _text_calls_missing_encoding(tree)]
    assert seen > 1000, f"only {seen} calls scanned — the walk is broken, so a pass means nothing"
    assert not missing, "text calls with no encoding=:\n  " + "\n  ".join(missing)


def test_the_encoding_probe_still_catches_what_it_is_for():
    """An absence test decays into a no-op when its probe breaks. Pin that it still fires."""
    tree = ast.parse("import subprocess\nopen('x')\npathlib.Path('y').read_text()\n"
                     "subprocess.run(['git'], text=True)\nopen('z', 'rb')\n")
    assert [n for _, n in _text_calls_missing_encoding(tree)] == ["open", "read_text", "subprocess.run"]


def test_no_unix_only_module_is_imported_unguarded():
    """`import fcntl` at the top of the store is what stopped the whole kit importing on Windows.
    A Unix-only import must sit inside a try that has a fallback."""
    bad, guarded = [], 0
    for p in _sources():
        tree = ast.parse(p.read_text(encoding="utf-8"))
        tried = {id(n) for t in ast.walk(tree) if isinstance(t, ast.Try)
                 for s in t.body for n in ast.walk(s)}
        for node in ast.walk(tree):
            names = ([a.name.split(".")[0] for a in node.names] if isinstance(node, ast.Import)
                     else [node.module.split(".")[0]] if isinstance(node, ast.ImportFrom) and node.module
                     else [])
            for mod in set(names) & UNIX_ONLY:
                if id(node) in tried:
                    guarded += 1
                else:
                    bad.append(f"{_rel(p)}:{node.lineno} import {mod}")
    assert guarded >= 1, "the store's guarded fcntl import was not found — the scan is not reading it"
    assert not bad, "unguarded Unix-only imports:\n  " + "\n  ".join(bad)


def test_every_entry_point_writes_utf8():
    """Claude Code reads hook output as UTF-8; a Windows pipe writes the ANSI code page."""
    entry, missing = 0, []
    for p in _sources():
        src = p.read_text(encoding="utf-8")
        if 'if __name__ == "__main__":' not in src or _rel(p) in MCP_OWNS_STDIO:
            continue
        entry += 1
        if 'reconfigure(encoding="utf-8"' not in src:
            missing.append(_rel(p))
    assert entry >= 10, f"only {entry} entry points found"
    assert not missing, "entry points that print in the platform code page:\n  " + "\n  ".join(missing)


def test_no_install_path_runs_a_shell_script():
    """A `.sh` hook needs bash, which a Windows machine may not have. The shims stay for old
    installs; nothing the installer writes may point at one."""
    src = (KIT / "install.py").read_text(encoding="utf-8")
    assert "stop_guard.py" in src
    assert ".sh" not in src


# ── the regression itself: run hooks the way Windows would ──────────────────────────────────

@pytest.fixture
def store(tmp_path):
    for t in ("work", "meta"):
        (tmp_path / t).mkdir()
        for cmd in (["init", "-q"], ["config", "user.email", "a@b.c"], ["config", "user.name", "t"]):
            subprocess.run(["git", *cmd], cwd=tmp_path / t, check=True, capture_output=True)
    (tmp_path / "proj" / ".claude" / "state" / "continuation").mkdir(parents=True)
    env = {**os.environ, "HARNESS_HOME": str(KIT),
           "HARNESS_WORK_STORE": str(tmp_path / "work"),
           "HARNESS_META_STORE": str(tmp_path / "meta"),
           "AGENTOS_STATE": str(tmp_path / "state"),
           "CLAUDE_PROJECT_DIR": str(tmp_path / "proj"),
           # What a hook's stdout is on Windows without UTF-8 mode, made strict so a
           # character outside it fails loudly instead of printing a question mark.
           "PYTHONIOENCODING": "cp1252:strict"}
    env.pop("PYTHONUTF8", None)
    return env


def _run(env, *argv):
    return subprocess.run([sys.executable, *map(str, argv)], capture_output=True, env=env, timeout=120)


def test_session_start_hook_survives_a_legacy_code_page(store):
    """Red before this change: exit 1, UnicodeEncodeError on the digest's warning sign."""
    code = (f"import sys; sys.path.insert(0, {str(KIT / 'core' / '01-memory')!r});"
            "import agentos_store as s;"
            "s.put({'type': 'semantic', 'title': 'FOLLOW-UP: pick the join key → customer_id — not email',"
            " 'tenant': 'meta', 'sensitivity': 'internal', 'egress': 'cloud-ok', 'status': 'committed',"
            " 'tags': ['follow-up'], 'body': 'x'})")
    put = _run({**store, "PYTHONIOENCODING": "utf-8"}, "-c", code)
    assert put.returncode == 0, put.stderr.decode("utf-8", "replace")
    r = _run(store, KIT / "core" / "01-memory" / "session_context.py")
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert "→ customer_id — not email" in r.stdout.decode("utf-8"), "the digest did not print the note"


def test_journal_cli_survives_a_legacy_code_page(store):
    """The arrow matters: é and — both exist in cp1252, so a probe using only those would pass
    against the very bug it is here for."""
    journal = KIT / "core" / "04-journal" / "journal.py"
    note = _run(store, journal, "note", "café export drops the accent → fix upstream", "--kind", "friction")
    assert note.returncode == 0, note.stderr.decode("utf-8", "replace")
    roll = _run(store, journal, "roll")
    assert roll.returncode == 0, roll.stderr.decode("utf-8", "replace")
    assert "accent → fix upstream" in roll.stdout.decode("utf-8"), "the roll-up did not print the note"


def test_stop_guard_block_message_survives_a_legacy_code_page(store):
    q = pathlib.Path(store["CLAUDE_PROJECT_DIR"]) / ".claude/state/continuation/s1.json"
    q.write_text(json.dumps({"items": [{"id": "w1", "text": "résumé the migration → staging"}]}),
                 encoding="utf-8")
    r = subprocess.run([sys.executable, str(KIT / "core" / "02-session" / "stop_guard.py")],
                       input=json.dumps({"session_id": "s1", "transcript_path": "",
                                         "stop_hook_active": False}).encode("utf-8"),
                       capture_output=True, env={**store, "HARNESS_STOP_GUARD_JOURNAL": "none"},
                       timeout=60)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    assert json.loads(r.stdout.decode("utf-8"))["decision"] == "block"
