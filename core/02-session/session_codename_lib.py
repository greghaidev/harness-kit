#!/usr/bin/env python3
"""session_codename_lib.py — deterministic per-session codename + Stop-hook backstop logic.

The operator's ask: every session self-assigns a memorable codename at start, states it near the top
of its first reply, and restates it in its final message so it can be used later for cold
pickup (`sessions.py show <slug>` / `sessions.py find <codename words>`). This module is the
shared logic for both halves:

  * `codename_for(session_id)` — pure, deterministic (same session_id always yields the same
    codename, even across multiple SessionStart fires e.g. a resume/compaction). No marker
    file needed for the assignment itself since it's a pure function of session_id.
  * `decide_stop(...)` — the Stop-hook backstop, block-once-then-allow (same correctness shape
    as session_log_lib.decide): if the codename doesn't appear anywhere in the assistant's
    most recent text reply, block ONE time with a reminder; `stop_hook_active` on the input
    always short-circuits to allow, so this can never loop.

Two words (adjective + noun) from fixed, curated lists keeps collisions low (2,304 combos)
while staying pronounceable and memorable — the point is "say COBALT MERIDIAN to resume this",
not cryptographic uniqueness.
"""
import hashlib
import json
import os
import re
import sys

ADJECTIVES = [
    "Silent", "Iron", "Amber", "Cobalt", "Crimson", "Granite", "Velvet", "Arctic", "Golden",
    "Shadow", "Quiet", "Scarlet", "Obsidian", "Copper", "Ember", "Frost", "Midnight", "Azure",
    "Slate", "Ivory", "Marble", "Violet", "Bronze", "Storm", "Hollow", "Amberlit", "Pale",
    "Rustic", "Distant", "Faded", "Vivid", "Rugged", "Wandering", "Steady", "Restless", "Lone",
    "Tidal", "Northern", "Southern", "Gilded", "Weathered", "Sable", "Dappled", "Brisk",
    "Solemn", "Nomad", "Coastal", "Alpine", "Molten",
]

NOUNS = [
    "Meridian", "Harbor", "Tide", "Summit", "Raven", "Falcon", "Compass", "Lantern", "Anchor",
    "Horizon", "Sextant", "Beacon", "Canyon", "Ridge", "Current", "Echo", "Orbit", "Threshold",
    "Ledger", "Foundry", "Causeway", "Estuary", "Overlook", "Waypoint", "Cascade", "Bramble",
    "Ferry", "Outpost", "Signal", "Vantage", "Reef", "Grove", "Kestrel", "Marrow", "Loom",
    "Fathom", "Drift", "Pinnacle", "Hollowpoint", "Traverse", "Culvert", "Isthmus", "Redoubt",
    "Gable", "Crest", "Bastion", "Ravine", "Spire", "Delta",
]

_ID_STRIP_RE = re.compile(r"[^a-z0-9]+")


def codename_for(session_id):
    """Deterministic 'Adjective Noun' codename for a session_id. Same input -> same output,
    stable across repeated SessionStart fires within one session (resume/compaction)."""
    session_id = session_id or "unknown-session"
    h = int(hashlib.sha256(session_id.encode("utf-8")).hexdigest(), 16)
    a = ADJECTIVES[h % len(ADJECTIVES)]
    b = NOUNS[(h // len(ADJECTIVES)) % len(NOUNS)]
    return f"{a} {b}"


def slug_for(codename):
    """'Cobalt Meridian' -> 'cobalt-meridian' — the suggested --session-ref for sessions.py."""
    return _ID_STRIP_RE.sub("-", codename.strip().lower()).strip("-")


def mentions_codename(text, codename):
    """Case-insensitive check that `text` contains the codename, either as the two-word phrase
    or its hyphenated slug (covers an agent writing 'session codename: cobalt-meridian')."""
    if not text:
        return False
    t = text.lower()
    return codename.lower() in t or slug_for(codename) in t


def iter_assistant_text_blocks(transcript_path):
    """Yield assistant-message text block contents, in transcript order. Tolerant of a
    missing/malformed/partial file (never raises) — mirrors session_log_lib's transcript
    reader so a missing transcript degrades to 'nothing mentioned' rather than crashing."""
    if not transcript_path:
        return
    try:
        with open(transcript_path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                if not isinstance(entry, dict):
                    continue
                message = entry.get("message")
                if not isinstance(message, dict):
                    continue
                if message.get("role") != "assistant":
                    continue
                content = message.get("content")
                if not isinstance(content, list):
                    continue
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text")
                        if isinstance(text, str) and text:
                            yield text
    except (OSError, IOError):
        return


def last_assistant_text(transcript_path):
    """The most recent assistant text block in the transcript, or "" if none/unreadable."""
    last = ""
    for text in iter_assistant_text_blocks(transcript_path):
        last = text
    return last


# ── once-per-session dedupe ────────────────────────────────────────────────────────────────
#
# Why this exists (found live 2026-07-26: this hook nudged on EVERY stop of a long session
# despite the codename being stated in the final message every single time):
#
#     at Stop time the assistant's FINAL message is not yet flushed to the transcript.
#
# So `last_assistant_text()` returns whatever came before it — which, in any turn that used
# tools, is a short pre-tool preamble ("Let me check X first…"), never the closing summary.
# Proven by elimination against a real transcript: the block containing the codename WAS the
# final assistant block once written, and the hook still fired, so it cannot have seen it.
#
# The consequence is worse than a missed nudge — the backstop can never PASS, so it fires
# every turn forever and trains the operator to ignore it. Same alarm-fatigue failure as
# governance-file-guard's read-only false positive (fixed in PR #640): a guard that always
# fires carries exactly as much information as one that never fires.
#
# Scanning the whole turn instead of just the last block does NOT fix it — the final message
# is absent from the transcript either way. So make this what a backstop should be: a reminder
# that fires at most ONCE per session. Same marker-file pattern and directory convention as
# governance-file-guard's `already_flagged`.
_FLAG_DIRNAME = "session-codename-nudge-flags"


def _flag_dir():
    project = os.environ.get("CLAUDE_PROJECT_DIR") or os.getcwd()
    return os.path.join(project, ".claude", "scope", _FLAG_DIRNAME)


def _marker_path(session_id, flag_dir=None):
    d = flag_dir or _flag_dir()
    return os.path.join(d, re.sub(r"[^A-Za-z0-9_.-]", "_", str(session_id)))


def already_nudged(session_id, flag_dir=None):
    """READ-ONLY: has this session already been nudged?

    Deliberately does not write. `decide_stop` documents itself as a pure decision function
    and callers (including its own tests) rely on being able to call it repeatedly without
    side effects; folding the marker write in here made a second call silently answer
    differently from the first. The write lives in `mark_nudged`, called only by `main`.
    """
    if not session_id:
        return False
    return os.path.exists(_marker_path(session_id, flag_dir))


def mark_nudged(session_id, flag_dir=None):
    """Record that this session has been nudged. Fails OPEN on any filesystem error — a
    missing or read-only scope dir must not turn a reminder into a hard failure."""
    if not session_id:
        return
    try:
        d = flag_dir or _flag_dir()
        os.makedirs(d, exist_ok=True)
        with open(_marker_path(session_id, flag_dir), "w"):
            pass
    except OSError:
        pass


NUDGE_TEMPLATE = (
    "You haven't stated your session codename ({codename}) in your final reply yet. Before "
    "finishing, say something like \"Session codename: {codename}.\" so the operator can use "
    "it later to resume (`python3 scripts/sessions.py show {slug}`). This fires at most ONCE "
    "per session — you will not be nudged again, so state it whenever you next reply."
)


def decide_stop(hook_input, threshold=None):
    """Pure decision function for the Stop-hook backstop. Returns
    {"block": bool, "message": str|None, "reason": str, "codename": str|None}.

    Block-once-then-allow (same non-negotiable shape as session_log_lib.decide):
    `stop_hook_active=True` on the input ALWAYS short-circuits to block=False — a Stop hook
    re-fires on the same stop event, and this must never loop.
    """
    if hook_input.get("stop_hook_active"):
        return {"block": False, "message": None, "reason": "stop_hook_active", "codename": None}

    session_id = hook_input.get("session_id") or ""
    if not session_id:
        return {"block": False, "message": None, "reason": "no_session_id", "codename": None}

    codename = codename_for(session_id)
    last_text = last_assistant_text(hook_input.get("transcript_path", ""))
    if mentions_codename(last_text, codename):
        return {"block": False, "message": None, "reason": "already_mentioned",
                "codename": codename}

    # The transcript check above is best-effort only: the final message is usually NOT flushed
    # yet at Stop time (see `already_nudged`), so a miss here does not mean the codename was
    # never stated. Cap the reminder at once per session so a guard that cannot reliably
    # observe success also cannot nag forever.
    if already_nudged(session_id, flag_dir=hook_input.get("_flag_dir")):
        return {"block": False, "message": None, "reason": "already_nudged_this_session",
                "codename": codename}

    message = NUDGE_TEMPLATE.format(codename=codename, slug=slug_for(codename))
    return {"block": True, "message": message, "reason": "not_mentioned", "codename": codename}


def main(argv=None):
    """CLI entrypoint used by session-codename-stop-nudge.sh: reads the Stop-hook JSON payload
    on stdin, decides, signals via the classic exit-code protocol (2 = block, 0 = allow)."""
    raw = sys.stdin.read()
    try:
        hook_input = json.loads(raw) if raw.strip() else {}
    except Exception:
        hook_input = {}
    if not isinstance(hook_input, dict):
        hook_input = {}
    result = decide_stop(hook_input)
    if result["block"]:
        # Burn this session's single nudge only when we actually emit one. Keeping the write
        # here (not in decide_stop) is what preserves decide_stop as a side-effect-free
        # decision function.
        mark_nudged(hook_input.get("session_id"), flag_dir=hook_input.get("_flag_dir"))
        sys.stderr.write("\n" + result["message"] + "\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
