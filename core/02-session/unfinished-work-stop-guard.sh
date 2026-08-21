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
#
# It also rolls the day's journal. That is deliberate placement: capture that depends on
# anyone REMEMBERING to capture is the rung of the ladder this kit argues decays, so the
# roll-up is a side effect of a gate that already runs on every single stop. It is
# idempotent, backgrounded, and its output and exit code are discarded — a broken journal
# must never be able to affect a stop decision.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KIT="$(cd "$HERE/../.." && pwd)"
JOURNAL="$KIT/core/04-journal/journal.py"

INPUT="$(cat)"
if [ -f "$JOURNAL" ]; then
  ( timeout 20s python3 "$JOURNAL" roll --quiet >/dev/null 2>&1 || true ) &
fi
printf '%s' "$INPUT" | exec python3 "$HERE/unfinished_work_lib.py"
