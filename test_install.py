#!/usr/bin/env python3
"""Tests for install.py.

The property that matters most is not that it installs. It is that it never damages a
settings.json it did not write: the operator's file holds their permissions, their other hooks
and their environment, and an installer that clobbers it is worse than hand-wiring. So merge
is tested as a pure function from every direction, and the end-to-end test runs the hooks
exactly as the written settings describe them — the only proof that the entries are runnable.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

KIT = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(KIT))
import install  # noqa: E402

HOME = pathlib.Path("/opt/h") if os.name != "nt" else pathlib.Path("C:/Users/First Last/harness")
PY = HOME / ".venv" / "bin" / "python"


def _all_hooks(settings, event):
    return [h for g in settings["hooks"].get(event, []) for h in g.get("hooks", [])]


# ── merge ────────────────────────────────────────────────────────────────────────────────────

def test_an_empty_settings_file_gets_env_and_exec_form_hooks():
    out, changes = install.merge_settings({}, HOME, PY)
    assert out["env"]["HARNESS_HOME"] == str(HOME)
    for event in ("SessionStart", "Stop"):
        for h in _all_hooks(out, event):
            assert h["command"] == str(PY)
            assert h["args"], "exec form: no shell, so no quoting and no ~ to expand"
            assert pathlib.Path(h["args"][0]).is_absolute()
    stop = _all_hooks(out, "Stop")
    assert [pathlib.Path(h["args"][0]).name for h in stop] == ["stop_guard.py"]
    assert changes


def test_nothing_unrelated_is_touched():
    mine = {"permissions": {"allow": ["Bash(git status)"]},
            "env": {"EDITOR": "code"},
            "hooks": {"Stop": [{"hooks": [{"type": "command", "command": "notify-me"}]}],
                      "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "x"}]}]},
            "model": "opus"}
    out, _ = install.merge_settings(mine, HOME, PY)
    assert out["permissions"] == mine["permissions"]
    assert out["env"]["EDITOR"] == "code"
    assert out["hooks"]["PreToolUse"] == mine["hooks"]["PreToolUse"]
    assert out["model"] == "opus"
    assert {"type": "command", "command": "notify-me"} in _all_hooks(out, "Stop")
    assert mine["env"] == {"EDITOR": "code"}, "the input dict must not be mutated"


def test_running_it_twice_changes_nothing_the_second_time():
    once, _ = install.merge_settings({}, HOME, PY)
    twice, changes = install.merge_settings(once, HOME, PY)
    assert twice == once
    assert changes == []


def test_an_install_from_the_old_instructions_is_replaced_not_duplicated():
    """The earlier INSTALL.md had people paste shell-form hooks, including the bash Stop guard."""
    old = {"env": {"HARNESS_HOME": "~/harness"},
           "hooks": {
               "SessionStart": [{"hooks": [
                   {"type": "command", "command": "~/harness/.venv/bin/python ~/harness/core/01-memory/session_context.py"},
                   {"type": "command", "command": "~/harness/.venv/bin/python ~/harness/core/06-hygiene/hygiene.py status"}]}],
               "Stop": [{"hooks": [
                   {"type": "command", "command": "~/harness/core/02-session/unfinished-work-stop-guard.sh"}]}]}}
    out, changes = install.merge_settings(old, HOME, PY)
    assert len(_all_hooks(out, "SessionStart")) == 2
    assert len(_all_hooks(out, "Stop")) == 1
    assert not any(".sh" in json.dumps(h) for h in _all_hooks(out, "Stop"))
    assert any("replacing" in c for c in changes)


def test_a_group_shared_with_someone_elses_hook_keeps_their_hook():
    shared = {"hooks": {"Stop": [{"hooks": [
        {"type": "command", "command": "their-own-thing"},
        {"type": "command", "command": "python", "args": ["C:\\old\\core\\02-session\\stop_guard.py"]}]}]}}
    out, _ = install.merge_settings(shared, HOME, PY)
    commands = [h["command"] for h in _all_hooks(out, "Stop")]
    assert commands.count("their-own-thing") == 1
    assert len(commands) == 2, "the old Windows-path copy of ours was recognized and replaced"


@pytest.mark.parametrize("bad", [{"env": ["x"]}, {"hooks": "nope"}, {"hooks": {"Stop": {"a": 1}}}])
def test_a_shape_it_cannot_merge_into_is_refused(bad):
    with pytest.raises(ValueError):
        install.merge_settings(bad, HOME, PY)


def test_the_probe_recognizes_our_hooks_and_only_ours():
    assert install._is_ours({"command": "python", "args": ["/x/core/06-hygiene/hygiene.py", "status"]})
    assert not install._is_ours({"command": "python", "args": ["/x/hygiene_notes.py"]})
    assert not install._is_ours("not a dict")


# ── the pieces that differ by platform ───────────────────────────────────────────────────────

def test_the_virtual_environment_interpreter_is_where_each_os_puts_it():
    assert install.venv_python(pathlib.Path("v"), "nt") == pathlib.Path("v") / "Scripts" / "python.exe"
    assert install.venv_python(pathlib.Path("v"), "posix") == pathlib.Path("v") / "bin" / "python"


def test_windows_schedules_both_jobs_with_absolute_paths():
    tasks = install.schedule_tasks(HOME, PY, system="nt")
    assert [w for w, _ in tasks] == ["weekly hygiene sweep", "daily journal roll"]
    for _, argv in tasks:
        assert argv[:3] == ["schtasks", "/Create", "/F"], "/F makes a re-run replace, not fail"
        action = argv[argv.index("/TR") + 1]
        assert str(HOME / "core") in action
    assert install.schedule_tasks(HOME, PY, system="posix") == []


def test_the_mcp_registration_carries_the_store_locations():
    cfg = install.mcp_config(HOME, PY)
    assert cfg["command"] == str(PY)
    assert cfg["args"] == [str(HOME / "core" / "01-memory" / "memory_server.py")]
    assert cfg["env"]["HARNESS_WORK_STORE"] == str(HOME / "store" / "work")


def test_a_bom_at_the_top_of_settings_is_tolerated(tmp_path):
    p = tmp_path / "settings.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps({"model": "opus"}).encode("utf-8"))
    assert install.load_settings(p) == {"model": "opus"}


# ── end to end ───────────────────────────────────────────────────────────────────────────────

def _install(tmp_path, *extra):
    return subprocess.run(
        [sys.executable, str(KIT / "install.py"), "--no-venv", "--skip-mcp",
         "--home", str(tmp_path / "home dir"), "--settings", str(tmp_path / "claude" / "settings.json"),
         *extra],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300)


def test_a_broken_settings_file_stops_the_install_before_anything_changes(tmp_path):
    s = tmp_path / "claude" / "settings.json"
    s.parent.mkdir()
    s.write_text('{"hooks": {,}', encoding="utf-8")
    r = _install(tmp_path)
    assert r.returncode == 1
    assert "Nothing was changed" in r.stdout
    assert s.read_text(encoding="utf-8") == '{"hooks": {,}'
    assert not (tmp_path / "home dir").exists()


def test_dry_run_writes_nothing(tmp_path):
    r = _install(tmp_path, "--dry-run")
    assert r.returncode == 0, r.stdout + r.stderr
    assert "would change" in r.stdout
    assert not (tmp_path / "home dir").exists()
    assert not (tmp_path / "claude").exists()


def test_install_then_run_every_hook_exactly_as_settings_describes_it(tmp_path):
    """A path with a space in it, on purpose: that is what a Windows home directory looks like."""
    (tmp_path / "claude").mkdir()
    (tmp_path / "claude" / "settings.json").write_text(json.dumps({"model": "opus"}), encoding="utf-8")
    r = _install(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr

    home = tmp_path / "home dir"
    assert (home / "core" / "02-session" / "stop_guard.py").exists()
    assert (home / "verify.py").exists()
    assert (home / "store" / "work" / ".git").exists()
    settings = json.loads((tmp_path / "claude" / "settings.json").read_text(encoding="utf-8"))
    assert settings["model"] == "opus"
    assert list((tmp_path / "claude").glob("settings.json.bak-*")), "the old file was not backed up"

    project = tmp_path / "project"
    (project / ".claude" / "state" / "continuation").mkdir(parents=True)
    env = {**os.environ, **settings["env"], "CLAUDE_PROJECT_DIR": str(project),
           "AGENTOS_STATE": str(tmp_path / "state")}
    payload = json.dumps({"session_id": "e2e", "transcript_path": "", "stop_hook_active": False})
    ran = 0
    for event in ("SessionStart", "Stop"):
        for h in _all_hooks(settings, event):
            p = subprocess.run([h["command"], *h["args"]], input=payload, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", env=env, timeout=120)
            assert p.returncode == 0, f"{event} hook {h['args']} failed:\n{p.stderr}"
            ran += 1
    assert ran == 3


def test_a_rerun_updates_the_kit_and_leaves_the_notes_alone(tmp_path):
    assert _install(tmp_path).returncode == 0
    note = tmp_path / "home dir" / "store" / "work" / "notes" / "kept.md"
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text("---\nid: kept\n---\nmine\n", encoding="utf-8")
    stale = tmp_path / "home dir" / "core" / "02-session" / "stop_guard.py"
    stale.write_text("# an older copy\n", encoding="utf-8")
    r = _install(tmp_path)
    assert r.returncode == 0, r.stdout + r.stderr
    assert note.read_text(encoding="utf-8").endswith("mine\n")
    assert "an older copy" not in stale.read_text(encoding="utf-8")
    assert "already up to date" in r.stdout


def test_a_home_inside_the_kit_is_refused(tmp_path):
    r = subprocess.run([sys.executable, str(KIT / "install.py"), "--dry-run", "--home", str(KIT / "h")],
                       capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 1
    assert "inside the kit" in r.stdout
