#!/usr/bin/env python3
"""stop_guard.py — Stop hook: refuse to end a turn with work outstanding.

the operator, 2026-07-26, after the fourth premature stop in one session: "This needs
permanently fixed. Don't fix it in this session. Fix it forever."

Block-UNTIL-CLEAR, unlike the codename/session-log nudges which block once. Those cannot
observe success so nagging would be alarm fatigue; this one's conditions are directly
observable and clearable (queue empties, worktree gets committed), and a single nudge is
exactly what already failed four times. Rationale, loop-safety and the reason it cannot
simply read the closing message: unfinished_work_lib.py.

It also rolls the day's journal. That is deliberate placement: capture that depends on anyone
REMEMBERING to capture is the rung of the ladder this kit argues decays, so the roll-up is a
side effect of a gate that already runs on every single stop. The roll runs in a detached
child under a time limit, with its output and exit code discarded — a broken journal must
never be able to affect a stop decision.

WHY PYTHON AND NOT BASH
This was a bash script. On Windows, Claude Code runs a shell-form hook through Git Bash, or
through PowerShell when Git Bash is absent, and macOS does not ship `timeout` at all — so the
journal roll silently never ran there. Python is already required by every other part of the
kit, and `sys.executable` is the kit's own interpreter, so the roll also stops depending on
whichever `python3` happens to be first on PATH. `unfinished-work-stop-guard.sh` is kept as a
shim for installs whose settings still point at it.
"""
import io
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
KIT = HERE.parent.parent
JOURNAL_TIMEOUT_SECS = 20


def journal_path() -> Path:
    """The journal to roll. Overridable so a test can substitute one that hangs or crashes."""
    return Path(os.environ.get("HARNESS_STOP_GUARD_JOURNAL")
                or KIT / "core" / "04-journal" / "journal.py")


def spawn_journal_roll():
    """Start the roll in a detached child and return at once. Never raises; None if not started."""
    journal = journal_path()
    if not journal.is_file():
        return None
    kw = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
          "stderr": subprocess.DEVNULL, "close_fds": True}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS
    else:
        kw["start_new_session"] = True
    try:
        return subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--roll-journal", str(journal)], **kw)
    except Exception:                                           # noqa: BLE001
        return None


def roll_journal(journal: str, timeout: float = JOURNAL_TIMEOUT_SECS) -> int:
    """The detached child: roll under a time limit and swallow every outcome."""
    try:
        subprocess.run([sys.executable, journal, "roll", "--quiet"],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=timeout)
    except Exception:                                           # noqa: BLE001
        pass
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] == ["--roll-journal"] and len(argv) > 1:
        return roll_journal(argv[1])

    # Read the hook payload as bytes and decode it ourselves: Claude Code writes UTF-8, and a
    # Windows console default would mangle a non-ASCII path in the transcript location.
    raw = sys.stdin.buffer.read().decode("utf-8", errors="replace")
    spawn_journal_roll()

    sys.path.insert(0, str(HERE))
    import unfinished_work_lib
    sys.stdin = io.StringIO(raw)
    try:
        unfinished_work_lib.main()
    except SystemExit as e:
        return e.code if isinstance(e.code, int) else 0
    return 0


if __name__ == "__main__":
    # A Windows pipe defaults to the ANSI code page, and Claude Code reads hook output as
    # UTF-8; one printed arrow or em dash would otherwise crash the hook.
    for _stream in (sys.stdout, sys.stderr):
        if hasattr(_stream, "reconfigure"):
            _stream.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
