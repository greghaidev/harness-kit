#!/usr/bin/env bash
# unfinished-work-stop-guard.sh — Stop hook: refuse to end a turn with work outstanding.
#
# the operator, 2026-07-26, after the fourth premature stop in one session: "This needs
# permanently fixed. Don't fix it in this session. Fix it forever."
#
# Block-UNTIL-CLEAR, unlike the codename/session-log nudges which block once. Those
# cannot observe success so nagging would be alarm fatigue; this one's conditions are
# directly observable and clearable (queue empties, worktree gets committed), and a
# single nudge is exactly what already failed four times.
#
# Rationale, loop-safety and the reason it cannot simply read the closing message:
# .claude/hooks/unfinished_work_lib.py
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec python3 "$HERE/unfinished_work_lib.py"
