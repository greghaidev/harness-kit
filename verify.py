#!/usr/bin/env python3
"""verify.py — prove each component on arrival, on the machine it landed on.

The kit is built on one machine and installed on another that the author cannot reach.
So it cannot be tested where it is written; it has to test itself where it runs. Every
check below either executes something or reads state — none of them assert from a
config file that a thing "should" be installed.

Exit 0 only if every REQUIRED check passes. Optional checks report but never fail the
run, because the menu is deliberately not installed by default.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

HOME = pathlib.Path(os.environ.get("HARNESS_HOME", os.path.expanduser("~/harness")))
CORE = HOME / "core"
RESULTS: list[tuple[str, str, str, bool]] = []   # component, check, detail, ok


def check(component: str, name: str, required: bool = True):
    def deco(fn):
        try:
            ok, detail = fn()
        except Exception as e:                      # a check must never crash the run
            ok, detail = False, f"{type(e).__name__}: {e}"
        RESULTS.append((component, name, detail, ok if required else True))
        if not ok and not required:
            RESULTS[-1] = (component, name + " (optional)", detail, True)
        return fn
    return deco


def _load(path: pathlib.Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------- layout
@check("layout", "kit is installed at HARNESS_HOME")
def _layout():
    missing = [p for p in ("core/01-memory", "core/02-session", "core/03-press")
               if not (HOME / p).is_dir()]
    return (not missing), f"{HOME}" + (f" MISSING {missing}" if missing else "")


@check("layout", "nothing was installed into a shared repo")
def _no_repo_footprint():
    """The constraint that protects your teammates. Checked, not assumed."""
    cwd = pathlib.Path.cwd()
    root = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                          capture_output=True, text=True, cwd=cwd)
    if root.returncode != 0:
        return True, "not inside a git repo — nothing to contaminate"
    top = pathlib.Path(root.stdout.strip())
    if HOME.is_relative_to(top):
        return False, f"HARNESS_HOME {HOME} is INSIDE the repo {top} — move it out"
    return True, f"harness at {HOME}, repo at {top} — disjoint"


# ---------------------------------------------------------------- memory
@check("01-memory", "store imports and both tenants resolve")
def _store_imports():
    sys.path.insert(0, str(CORE / "01-memory"))
    store = _load(CORE / "01-memory" / "agentos_store.py", "agentos_store")
    tenants = store.list_tenants()
    return bool(tenants), f"tenants: {', '.join(tenants)}"


@check("01-memory", "put -> search -> get round-trips")
def _store_roundtrip():
    store = sys.modules["agentos_store"]
    # store.put takes ONE dict, not kwargs — the MCP tool wrapper is what accepts
    # keywords. Calling it the wrapper's way is the mistake this check caught on its
    # own first run, which is the entire argument for shipping a self-verifying install.
    rec = store.put({
        "id": "harness-verify-probe", "title": "harness verify probe",
        "type": "episodic", "tenant": "work", "sensitivity": "internal",
        "egress": "cloud-ok", "status": "committed", "tags": ["harness-verify"],
        "body": "written by verify.py; safe to delete",
    })
    got = store.get("harness-verify-probe", "work")
    hits = store.search(query="harness verify probe", tenant="work", limit=5)
    ok = bool(got) and any(h.get("id") == "harness-verify-probe" for h in hits)
    return ok, f"wrote {rec.get('id')}, search returned {len(hits)} hit(s)"


@check("01-memory", "every write is committed to git")
def _store_commits():
    store = sys.modules["agentos_store"]
    root = store._root_for("work")
    log = subprocess.run(["git", "log", "--oneline", "-1"],
                         capture_output=True, text=True, cwd=root)
    return log.returncode == 0 and bool(log.stdout.strip()), log.stdout.strip() or "no commits"


@check("01-memory", "boundary gate passes")
def _store_boundary():
    p = CORE / "01-memory" / "store_test.py"
    if not p.exists():
        return False, "store_test.py missing"
    r = subprocess.run([sys.executable, str(p)], capture_output=True, text=True, timeout=300)
    tail = (r.stdout or r.stderr).strip().splitlines()[-1:] or [""]
    return r.returncode == 0, tail[0][:120]


# ---------------------------------------------------------------- session
@check("02-session", "codename is deterministic")
def _codename():
    lib = _load(CORE / "02-session" / "session_codename_lib.py", "scl")
    a = lib.codename_for("abc-123")
    b = lib.codename_for("abc-123")
    return a == b and a != lib.codename_for("abc-124"), f"'abc-123' -> {a}"


@check("02-session", "THE RETARGET: analysis with no declaration is blocked")
def _retarget_blocks():
    u = _load(CORE / "02-session" / "unfinished_work_lib.py", "uwl")
    with tempfile.TemporaryDirectory() as d:
        (pathlib.Path(d) / ".claude/state/continuation").mkdir(parents=True)
        t = pathlib.Path(d) / "t.jsonl"
        t.write_text(json.dumps({"message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash",
             "input": {"command": "psql -c 'select count(*) from orders'"}}]}}))
        got = u.decide_stop({"session_id": "v", "transcript_path": str(t),
                             "stop_hook_active": False}, project_dir=d)
    return got["block"] is True, f"reason={got['reason']}"


@check("02-session", "THE RETARGET: a recorded claim ends the turn")
def _retarget_releases():
    u = sys.modules["uwl"]
    with tempfile.TemporaryDirectory() as d:
        q = pathlib.Path(d) / ".claude/state/continuation"
        q.mkdir(parents=True)
        (q / "v.json").write_text(json.dumps(
            {"items": [], "claims": [{"statement": "x", "source": "y"}]}))
        got = u.decide_stop({"session_id": "v", "transcript_path": "",
                             "stop_hook_active": False}, project_dir=d)
    return got["block"] is False and got["reason"] == "declared_claim", f"reason={got['reason']}"


@check("02-session", "read-only work is NOT blocked")
def _retarget_no_false_positive():
    """The waste guard. A widened trigger that fires on `git status` is worse than none."""
    u = sys.modules["uwl"]
    with tempfile.TemporaryDirectory() as d:
        (pathlib.Path(d) / ".claude/state/continuation").mkdir(parents=True)
        t = pathlib.Path(d) / "t.jsonl"
        t.write_text(json.dumps({"message": {"role": "assistant", "content": [
            {"type": "tool_use", "name": "Bash", "input": {"command": "git status"}}]}}))
        got = u.decide_stop({"session_id": "v", "transcript_path": str(t),
                             "stop_hook_active": False}, project_dir=d)
    return got["block"] is False, f"reason={got['reason']}"


@check("02-session", "Stop hook is wired in ~/.claude/settings.json")
def _hook_wired():
    p = pathlib.Path(os.path.expanduser("~/.claude/settings.json"))
    if not p.exists():
        return False, "no ~/.claude/settings.json"
    blob = p.read_text()
    return "unfinished-work-stop-guard" in blob, "Stop hook present" if \
        "unfinished-work-stop-guard" in blob else "Stop hook NOT wired"


# ---------------------------------------------------------------- press
@check("03-press", "renders markdown to a document")
def _press_renders():
    sys.path.insert(0, str(CORE / "03-press"))
    press = _load(CORE / "03-press" / "press.py", "press")
    doc, problems, stats = press.render(
        "## 00 · A\n\nprose\n\n!! a pull statement", HOME,
        title="verify", wordmark="W", eyebrow="")
    return "<h2" in doc and stats["pulls"] == 1, f"{stats['chars']:,} chars, {stats['sections']} section"


@check("03-press", "claim gate FAILS on a claim that stops tracing")
def _press_gate_fails():
    claims = _load(CORE / "03-press" / "claims.py", "claims")
    _, problems = claims.resolve("{{path:definitely/not/here.py}}", HOME)
    return len(problems) == 1, problems[0][:90] if problems else "gate did NOT fail — it is a comment"


@check("03-press", "PDF renderer is available", required=False)
def _press_pdf():
    chrome = (shutil.which("google-chrome") or shutil.which("chromium")
              or shutil.which("chromium-browser"))
    return bool(chrome), chrome or "no chrome/chromium — HTML still builds, PDF will not"


# ---------------------------------------------------------------- journal
@check("04-journal", "records a note and rolls it into the day")
def _journal():
    import subprocess, tempfile
    jp = CORE / "04-journal" / "journal.py"
    if not jp.exists():
        return False, "journal.py missing"
    env = dict(os.environ, HARNESS_HOME=str(HOME))
    with tempfile.TemporaryDirectory() as d:
        env["CLAUDE_PROJECT_DIR"] = d
        a = subprocess.run([sys.executable, str(jp), "note", "harness verify probe",
                            "--kind", "friction"], capture_output=True, text=True, env=env)
        b = subprocess.run([sys.executable, str(jp), "roll"],
                           capture_output=True, text=True, env=env)
    ok = a.returncode == 0 and b.returncode == 0 and "harness verify probe" in b.stdout
    return ok, (a.stderr or b.stderr or "note -> roll -> day round-tripped")[:110]


@check("04-journal", "the Stop guard rolls the day, and cannot be broken by it")
def _journal_wired():
    body = (CORE / "02-session" / "unfinished-work-stop-guard.sh").read_text()
    line = [l for l in body.split("\n") if "$JOURNAL" in l and " roll" in l]
    if not line:
        return False, "Stop guard does not invoke the journal"
    l = line[0]
    safe = "|| true" in l and ">/dev/null" in l and "timeout" in l and l.rstrip().endswith("&")
    return safe, "backgrounded, timed out, output discarded" if safe else f"UNSAFE: {l.strip()}"


# ---------------------------------------------------------------- agreement
@check("00-agreement", "operating agreement is installed and fully filled in")
def _agreement():
    p = pathlib.Path(os.path.expanduser("~/.claude/CLAUDE.md"))
    if not p.exists():
        return False, "no ~/.claude/CLAUDE.md"
    body = p.read_text()
    left = body.count("<<")
    return left == 0, "complete" if left == 0 else f"{left} placeholder(s) still unfilled"


# ---------------------------------------------------------------- report
def main() -> int:
    width = max(len(c) + len(n) for c, n, _, _ in RESULTS) + 4
    print(f"\n  harness verify — {HOME}\n")
    failed = 0
    for component, name, detail, ok in RESULTS:
        mark = "PASS" if ok else "FAIL"
        if not ok:
            failed += 1
        label = f"{component} · {name}"
        print(f"  [{mark}]  {label:<{width}}  {detail}")
    print()
    if failed:
        print(f"  {failed} check(s) FAILED — do not report those components as installed.\n")
        return 1
    print("  all required checks passed\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
