"""Agent OS memory store — markdown + YAML-frontmatter notes under tenant-scoped stores.

This module is the single enforcement point for the partition model (design v0.3 §2/§3/§5):
  * tenants served here: ``work`` and ``meta`` (the cloud-ok side)
  * the RESTRICTED tenant is **hard-refused** — for anything you may not put in a local file
  * notes are validated against the §5 schema on write (lenient on read of legacy notes)
  * every write is git-committed in the store (audit trail)

Runs as you, against the two store roots below — no sudo.
Store roots are overridable via ``HARNESS_WORK_STORE`` / ``HARNESS_META_STORE`` (used by tests).
"""
from __future__ import annotations

import contextlib
import datetime
import fcntl
import json
import math
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Optional

import yaml

try:
    import sqlite3
except Exception:  # pragma: no cover — sqlite3 is stdlib but guard anyway per the fallback contract
    sqlite3 = None  # type: ignore[assignment]

FORBIDDEN_TENANT = "restricted"
ALLOWED_TENANTS = ("meta", "work")

# A git index.lock older than this (while we hold our own commit lock) is a crashed-commit leftover.
STALE_LOCK_SECS = 5

VALID_TYPES = {"episodic", "semantic", "procedural", "capability", "feature"}
VALID_SENSITIVITY = {"public", "internal", "private", "restricted"}
VALID_EGRESS = {"local-only", "cloud-ok"}
VALID_STATUS = {"committed", "quarantined", "archived"}
# Statuses WITHDRAWN FROM RETRIEVAL by default (PAB charter 2026-08-16, ruling R5). Until this
# existed, `quarantined` and `archived` were accepted values that nothing honored at read time:
# both search paths filtered sensitivity/tags/type/since and never looked at status, and the cached
# path could not have — the column was not even SELECTed. That made "stamp it and quarantine it"
# an illusory undo, which matters the moment anything writes notes automatically: a retirement
# path has to exist BEFORE the first machine write, not after.
# A MISSING status is NOT retired — notes predating the field must never silently vanish.
RETIRED_STATUSES = {"quarantined", "archived"}
REQUIRED = ("type", "title", "tenant", "sensitivity", "egress", "status")

# Link relations that drive passive follow-up completion (no manual `done` tag required):
#   * a note that SUPERSEDES/CLOSES a follow-up fully completes it (drops from the digest)
#   * a note that ADVANCES a follow-up partially completes it (stays open, flagged "partial")
# The closing note (usually the auto-updated capability) carries the link → target follow-up.
SUPERSEDE_RELATIONS = {"supersedes", "closes"}
ADVANCE_RELATIONS = {"advances", "partially_supersedes"}

GIT_NAME = os.environ.get("AGENTOS_GIT_NAME", "agent-os")
GIT_EMAIL = os.environ.get("AGENTOS_GIT_EMAIL", "agent-os@this machine")

# Preferred frontmatter ordering for readable, diff-stable files.
_ORDER = [
    "id", "type", "title", "created", "updated",
    "tenant", "principal", "sensitivity", "egress",
    "source", "captured_by", "confidence", "status",
    "capability_type", "interface", "cost", "scope", "composes", "health",
    "links", "tags", "version", "decay",
]


class StoreError(Exception):
    """Base class for store errors."""


class RefusalError(StoreError):
    """A privacy-wall / egress violation — the request is refused, not just invalid."""


class ValidationError(StoreError):
    """The note does not satisfy the §5 schema contract."""


class NotFound(StoreError):
    """No note with the given id in the requested tenant(s)."""


class AmbiguousIdError(StoreError):
    """A tenant-less MUTATION found the same id in more than one tenant.

    A first-match READ (get()) can reasonably stay permissive here, but a first-match WRITE
    (health/link/unlink/...) risks silently updating the wrong tenant's copy and leaving the other
    one stale forever — so mutators raise this and demand an explicit ``tenant=`` instead of
    guessing which store to touch."""


def _roots() -> dict[str, Path]:
    return {
        "work": Path(os.path.expanduser(os.environ.get("HARNESS_WORK_STORE", "~/harness/store/work"))),
        "meta": Path(os.path.expanduser(os.environ.get("HARNESS_META_STORE", "~/harness/store/meta"))),
    }


def list_tenants() -> list[str]:
    """Tenants this server can serve. RESTRICTED is excluded by design."""
    return list(ALLOWED_TENANTS)


# --- state dir (search-index cache + commit-failure log; shared contract with other workstreams) ---

def _state_dir() -> Path:
    """``$AGENTOS_STATE`` or ``~/.local/state/agent-os`` — created on demand, mode 0700."""
    d = Path(os.environ.get("AGENTOS_STATE") or (Path.home() / ".local" / "state" / "agent-os"))
    try:
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d


@contextlib.contextmanager
def _note_lock(tenant: str, note_id: str):
    """Exclusive advisory lock serializing the read-modify-write of ONE note across processes.

    put()/link()/unlink()/health() (and register(), which delegates to put()) all do
    read-existing -> modify -> _write. Without this, two agent-os processes racing the SAME note
    (the MCP server, the hourly health_sweep, the background worker, the dashboard) each read the
    old copy and the second _write silently clobbers the first's change — a lost update (a second lineage
    external review, finding F1, 2026-07-05; store_test.py test 11b reproduces it: 64 concurrent
    same-note links, only ~11 survive un-locked). A per-note flock makes each logical mutation
    atomic w.r.t. every other one. BLOCKING (LOCK_EX): a competing writer waits its turn rather
    than losing its write.

    Distinct from the _git_commit flock, which is NON-blocking and guards the shared git index (a
    skipped commit is swept by the winner's ``git add -A``); this guards a single note's file
    content, where "skip and let someone else sweep" is exactly the bug. No deadlock between the
    two: the commit flock never waits, and put() releases this lock BEFORE its trailing link()
    calls (which re-acquire it for the same note) so a mutation never nests its own note lock.

    Lock files live under ``<state>/locks/<tenant>/`` — rebuildable throwaway state, deliberately
    outside every store's git tree so the _git_commit ``git add -A`` never commits them.
    """
    d = _state_dir() / "locks" / _safe_name(tenant)
    try:
        d.mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    fd = os.open(str(d / f"{_safe_name(note_id)}.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


def _tenant_for_root(root: Path) -> str:
    """Best-effort reverse-lookup of which tenant a store root belongs to (for the failure log)."""
    for t, r in _roots().items():
        try:
            if r.resolve() == root.resolve():
                return t
        except OSError:
            if r == root:
                return t
    return str(root)


def _log_commit_failure(root: Path, err: BaseException) -> None:
    """Best-effort append to ``<state>/commit-failures.jsonl``. Must never raise itself."""
    try:
        line = json.dumps({"ts": _now(), "tenant": _tenant_for_root(root), "error": str(err)})
        with open(_state_dir() / "commit-failures.jsonl", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _root_for(tenant: str) -> Path:
    if tenant == FORBIDDEN_TENANT:
        raise RefusalError(
            "RESTRICTED tenant is local-only and unreachable from this server (design §3 egress wall)."
        )
    roots = _roots()
    if tenant not in roots:
        raise ValidationError(f"unknown tenant {tenant!r}; allowed: {list(roots)}")
    root = roots[tenant]
    (root / "notes").mkdir(parents=True, exist_ok=True)
    return root


def _now() -> str:
    return (
        datetime.datetime.now(datetime.timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:60] or "note"


def _safe_name(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "-", name)[:120] or "note"


# --- injection defense-in-depth (store-side charset gates on caller-supplied identifiers) ---------
# The dashboard escapes on render (authoritative), but a caller-supplied `id` (put/register) or
# `relation`/`target` (link) landing in frontmatter unescaped is exactly what enabled a stored-XSS
# there — so the store itself also gates the charset of these fields, conservatively (reject only
# what's actually dangerous — quotes, angle brackets, spaces, slashes — never legitimate existing
# data). Same charset _safe_name already uses for filenames (verified against all 707 live ids on
# a temp copy of the real work+meta stores before this was added — every one matches).
_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,200}$")
# Link relations are a small closed-ish vocabulary (relates_to, supersedes, child_of, ...; see the
# §5 schema doc) — lowercase word(s) joined by underscore. A charset gate (not a hard enum) so a
# not-yet-documented but legitimate relation name still works; it just can't contain the characters
# an injection needs.
_RELATION_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _valid_id(value) -> bool:
    return isinstance(value, str) and bool(_ID_RE.match(value))


def _valid_relation(value) -> bool:
    return isinstance(value, str) and bool(_RELATION_RE.match(value))


def _split_frontmatter(raw: str) -> tuple[dict, str]:
    if raw.startswith("---"):
        parts = raw.split("---", 2)
        if len(parts) == 3:
            fm = yaml.safe_load(parts[1]) or {}
            return (fm if isinstance(fm, dict) else {}), parts[2].lstrip("\n")
    return {}, raw


def _dump_note(fm: dict, body: str) -> str:
    ordered = {k: fm[k] for k in _ORDER if k in fm}
    for k, v in fm.items():
        if k not in ordered:
            ordered[k] = v
    y = yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True, default_flow_style=False).strip()
    return f"---\n{y}\n---\n\n{(body or '').strip()}\n"


def _read_note(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8")
    fm, body = _split_frontmatter(raw)
    nid = fm.get("id") or path.stem
    title = fm.get("title")
    if not title:
        m = re.search(r"^#\s+(.+)$", body, re.M)
        title = m.group(1).strip() if m else path.stem
    return {"_path": path, "id": nid, "title": title, "frontmatter": fm, "body": body}


def _iter_notes(tenant: str):
    root = _root_for(tenant)
    for p in sorted((root / "notes").glob("*.md")):
        try:
            yield _read_note(p)
        except Exception:
            continue  # never let one malformed file break a scan


def _find_path(tenant: str, note_id: str) -> Optional[Path]:
    root = _root_for(tenant)
    direct = root / "notes" / f"{_safe_name(note_id)}.md"
    if direct.exists():
        return direct
    for n in _iter_notes(tenant):
        if n["id"] == note_id:
            return n["_path"]
    return None


# --- search-index cache (a rebuildable per-tenant SQLite mirror of the notes dir) ------------------
# Full-text search over ~600+ markdown files means re-parsing every note on every call. This cache
# mirrors the parsed fields into ``<state>/fts/<tenant>.sqlite`` and keeps it current with a cheap
# incremental stat-scan (mtime_ns+size) on every search/registry/followups call — parsing (via
# _read_note) only the files that are new or changed. It is a *cache*: deleting the sqlite file is
# always safe (next call rebuilds it — or call ``rebuild()`` / run this module with ``--rebuild``),
# and any sqlite error at any point falls back transparently to the full-scan path below — the cache
# must never be able to break a read.
#
# Matching semantics (recall parity with the full-scan fallback — see _search_scan/_hay_text): a
# query term matches a note iff it is a case-insensitive SUBSTRING of that note's concatenated
# title+body+tags text, e.g. "foreclosure" MUST match a note that only contains "billing".
# An earlier version used FTS5 MATCH with a `"term"*` PREFIX query as an optimization, which is a
# strict subset of substring matching (a prefix match never finds a term buried mid-word) and
# silently dropped 22-57% of real matches in practice (audit finding, 2026-07-04) — the cache must
# never narrow recall relative to the fallback it stands in for. To make that a structural
# guarantee rather than a claim, both paths now call the SAME substring predicate (_hay_text +
# plain `in`) over their respective data sources (parsed-on-the-fly here, cached below), so a
# discrepancy would require the two data sources to disagree, not the matching logic — see the
# store_test.py "cache/fallback parity" gate, which asserts identical result sets for a query
# battery on a real-store-sized corpus.
#
# Bump when the cached column set/semantics change so existing caches self-rebuild (see _cache_conn).
_CACHE_SCHEMA_VERSION = 3
_CACHE_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS notes (
    rowid INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    id TEXT,
    mtime_ns INTEGER,
    size INTEGER,
    title TEXT,
    type TEXT,
    status TEXT,
    tenant TEXT,
    tags TEXT,
    links TEXT,
    updated TEXT,
    created TEXT,
    body TEXT,
    sensitivity TEXT
);
CREATE INDEX IF NOT EXISTS notes_id_idx ON notes(id);
"""

_CACHE_UPSERT_SQL = """
INSERT INTO notes (path, id, mtime_ns, size, title, type, status, tenant, tags, links, updated, created, body, sensitivity)
VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
ON CONFLICT(path) DO UPDATE SET
    id=excluded.id, mtime_ns=excluded.mtime_ns, size=excluded.size, title=excluded.title,
    type=excluded.type, status=excluded.status, tenant=excluded.tenant, tags=excluded.tags,
    links=excluded.links, updated=excluded.updated, created=excluded.created, body=excluded.body,
    sensitivity=excluded.sensitivity
"""


def _cache_path(tenant: str) -> Path:
    d = _state_dir() / "fts"
    try:
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
    except OSError:
        pass
    return d / f"{tenant}.sqlite"


def rebuild(tenant: Optional[str] = None) -> list:
    """Force a full reindex: delete the cached sqlite file(s) so the next search/registry/
    followups call rebuilds them from scratch (via the same incremental sync — a from-scratch
    sync is just "everything counts as changed").

    This is the sanctioned mitigation for the residual staleness risk documented on _sync_cache:
    an (mtime, size)-preserving write (``cp -p``, ``rsync -a``, some restores) can leave a
    same-size body edit undetected by the incremental stat-scan between rebuilds. Deleting the
    cache file was always a safe manual fix (it's a pure derivative of the notes dir); this just
    gives it a name callers/ops scripts can invoke directly, and a ``--rebuild`` CLI hook (run
    this module directly) for the same from the shell.
    """
    tenants = [tenant] if tenant else list(ALLOWED_TENANTS)
    removed = []
    for t in tenants:
        if t == FORBIDDEN_TENANT:
            continue
        p = _cache_path(t)
        if p.exists():
            p.unlink()
            removed.append(str(p))
    return removed


def _cache_conn(tenant: str):
    """A ready-to-query connection (WAL + busy_timeout, schema ensured), or None if unavailable.

    None means "no cache" — every caller must treat that as a signal to fall back to the full scan,
    never as an error to propagate.

    ``tenant == FORBIDDEN_TENANT`` is refused here, BEFORE any filesystem path is even built — a
    restricted cache file must never be created, not even transiently, regardless of what happens
    later in the caller (which will hit the real _root_for refusal, but by then a sqlite connect
    would have already materialized the file on disk).
    """
    if sqlite3 is None or tenant == FORBIDDEN_TENANT:
        return None
    conn = None
    try:
        conn = sqlite3.connect(str(_cache_path(tenant)), timeout=5.0, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        # Self-heal on a column-set change: an older cache lacks newly-added columns, which would
        # make every SELECT raise and silently disable the cache. Drop + rebuild on version drift.
        if conn.execute("PRAGMA user_version").fetchone()[0] != _CACHE_SCHEMA_VERSION:
            conn.executescript(
                "DROP TABLE IF EXISTS notes_fts; DROP TABLE IF EXISTS notes;")
        conn.executescript(_CACHE_SCHEMA_SQL)
        conn.execute(f"PRAGMA user_version={_CACHE_SCHEMA_VERSION}")
        return conn
    except Exception:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
        return None


def _sync_cache(tenant: str, conn) -> None:
    """Incremental sync: stat-scan the notes dir, upsert changed/new files, delete missing ones.

    A stat pass over ~600 files is cheap; parsing (_read_note, which does a YAML load) all of them
    on every call is not — so only files whose (mtime_ns, size) changed get re-parsed. Using
    mtime_ns (nanosecond int) rather than the float st_mtime narrows, but does not eliminate, a
    known residual gap: a write that preserves BOTH mtime and size (``cp -p``, ``rsync -a``, some
    restores) with a same-length body edit is invisible to this stat-scan and serves a stale
    cached body/snippet until the next real change — or an explicit ``rebuild()``. Low-probability
    (most edits go through this module's own _write, which always bumps ``updated``/mtime) but
    documented as a residual, not silently assumed away.

    All the changed-file writes for this sync land in ONE transaction (conn is opened with
    isolation_level=None, i.e. autocommit, so without an explicit BEGIN/COMMIT each insert would be
    its own WAL-synced transaction — fine for the common 0-1-file-changed case, but a cold cache
    facing ~600 new files as 600 separate fsync'd commits is what actually made an early version of
    this slow; batching a first-ever sync down to one commit is the difference between ~24s and
    ~50ms). The common warm-cache case (nothing changed) never opens a transaction at all.

    A restricted note (however it got here — this is the work/meta cache, so it can only
    arrive out-of-band: a manual file drop, a git merge, never this module's own write wall) is
    never upserted — its path is deleted from the cache instead, so its body never sits as
    plaintext in the sqlite file even transiently. See get()/search() for the matching read-side
    refusal/omission (defense in depth: the note must never be indexed OR returned).
    """
    notes_dir = _root_for(tenant) / "notes"
    disk: dict[str, tuple[Path, int, int]] = {}
    for p in notes_dir.glob("*.md"):
        try:
            st = p.stat()
        except OSError:
            continue
        disk[str(p)] = (p, st.st_mtime_ns, st.st_size)

    cached = {row[0]: (row[1], row[2])
              for row in conn.execute("SELECT path, mtime_ns, size FROM notes")}

    stale = [p for p in cached if p not in disk]
    changed = [(p, v) for p, v in disk.items() if cached.get(p) != (v[1], v[2])]
    if not stale and not changed:
        return  # nothing to sync — skip the write transaction entirely (the common warm-cache path)

    conn.execute("BEGIN IMMEDIATE")
    try:
        if stale:
            conn.executemany("DELETE FROM notes WHERE path = ?", [(p,) for p in stale])
        for p, (path_obj, mtime_ns, size) in changed:
            try:
                n = _read_note(path_obj)
            except Exception:
                continue  # never let one malformed file break the sync (matches _iter_notes)
            fm = n["frontmatter"]
            if fm.get("sensitivity") == "restricted":
                conn.execute("DELETE FROM notes WHERE path = ?", (p,))
                continue  # never index a restricted body, even transiently
            conn.execute(_CACHE_UPSERT_SQL, (
                p, n["id"], mtime_ns, size, n["title"], fm.get("type"), fm.get("status"),
                fm.get("tenant") or tenant,
                json.dumps(sorted(set(fm.get("tags") or []))),
                json.dumps(list(fm.get("links") or [])),
                fm.get("updated"), fm.get("created"), n["body"], fm.get("sensitivity"),
            ))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def _hay_text(title: str, body: str, tags) -> str:
    """The lowercased haystack a query term is tested against: substring-in-hay is the ONE
    matching semantic used everywhere (cache and full-scan fallback alike) — see the recall-parity
    note on the cache section above. Shared so the two paths cannot silently drift apart again."""
    return ((title or "") + "\n" + (body or "") + "\n" + " ".join(map(str, tags))).lower()


def _cache_all_rows(conn, type_filter: Optional[str]) -> list:
    """Every (non-restricted — see _sync_cache) cached row for a tenant, optionally
    type-filtered. Plain SQL, no query-text matching here — that happens in Python below so it can
    share the exact substring predicate the full-scan fallback uses (recall parity by construction,
    not by keeping two implementations in sync by hand)."""
    sql = ("SELECT path, id, title, type, tags, links, updated, created, body, sensitivity, "
           "status FROM notes")
    params: list = []
    if type_filter:
        sql += " WHERE type = ?"
        params.append(type_filter)
    return conn.execute(sql, params).fetchall()


def _search_cached(tenant: str, terms: list, want_tags: set, type_filter: Optional[str],
                    since: Optional[str], include_retired: bool = False) -> Optional[list]:
    """Cache-backed search for one tenant. Returns None (not []) on ANY failure, meaning "no cache
    available here" — the caller must fall back to the full scan. An empty match is a real `[]`.

    Mirrors _search_scan's structure deliberately (tags/since filter first, then AND-match, then
    OR-fallback if AND is empty, then BM25-score the survivors) so the two are provably equivalent
    given the same underlying data — see _hay_text/_bm25_scores, shared by both.
    """
    conn = _cache_conn(tenant)
    if conn is None:
        return None
    try:
        _sync_cache(tenant, conn)
        rows = _cache_all_rows(conn, type_filter)
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass

    base = []
    for (path, nid, title, ntype, tags_json, links_json, updated, created, body, sensitivity,
         status) in rows:
        if sensitivity == "restricted":
            continue  # belt-and-suspenders: _sync_cache already never indexes these
        if not include_retired and status in RETIRED_STATUSES:
            continue  # withdrawn from retrieval — see RETIRED_STATUSES
        note_tags = set(json.loads(tags_json) if tags_json else [])
        if want_tags and not want_tags.issubset(note_tags):
            continue
        if since and str(updated or created or "") < since:
            continue
        base.append((path, nid, title, ntype, note_tags, links_json, updated, created, body,
                     sensitivity, _hay_text(title, body, note_tags)))

    def _build(matched):
        out = []
        for path, nid, title, ntype, note_tags, links_json, updated, created, body, sensitivity, _hay in matched:
            out.append({
                "id": nid, "tenant": tenant, "title": title, "type": ntype,
                "tags": sorted(note_tags), "updated": updated,
                "links": json.loads(links_json) if links_json else [],
                "sensitivity": sensitivity, "path": path,
                "snippet": _snippet(body, terms[0] if terms else ""),
                "_rank": _rank_text(title, body, note_tags),
            })
        return out

    if not terms:
        cands = _build(base)
    else:
        cands = _build([b for b in base if all(term in b[10] for term in terms)])
        if not cands:
            cands = _build([b for b in base if any(term in b[10] for term in terms)])

    if terms:
        scores = _bm25_scores(cands, terms)
        for i, c in enumerate(cands):
            c["_score"] = scores.get(i, 0.0)
    else:
        for c in cands:
            c["_score"] = 0.0
    for c in cands:
        c.pop("_rank", None)
    return cands


def _cache_link_rows(tenant: str) -> Optional[list]:
    """(id, links) for every note in a tenant, from the cache. None on any failure (→ fall back)."""
    conn = _cache_conn(tenant)
    if conn is None:
        return None
    try:
        _sync_cache(tenant, conn)
        cur = conn.execute("SELECT id, links FROM notes")
        return [(rid, json.loads(lj) if lj else []) for rid, lj in cur.fetchall()]
    except Exception:
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _git_commit(root: Path, path: Path, msg: str) -> None:
    """Commit a write to the store — non-blocking and self-healing, so concurrent agents never stall.

    The note file is already written before we get here (search/get read from disk), so the commit
    is pure audit trail and must never halt work. We serialize our own commits per-store with a
    NON-BLOCKING flock: if another agent-os process is mid-commit we skip immediately — that process's
    ``git add -A`` will sweep our file into its commit. Because the flock means no other agent-os
    committer is active, any leftover ``index.lock`` is a crashed-commit artifact, which we clear.
    Any failure degrades to "file written, commit skipped" — the next writer's ``-A`` reconciles it."""
    try:
        if not (root / ".git").exists():
            subprocess.run(["git", "init", "-q"], cwd=root, check=True, capture_output=True)
        with open(root / ".git" / "agentos-commit.lock", "w") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except (BlockingIOError, OSError):
                return  # another commit is in flight; it sweeps our already-written file via `git add -A`
            index_lock = root / ".git" / "index.lock"
            try:
                if index_lock.exists() and (time.time() - index_lock.stat().st_mtime) > STALE_LOCK_SECS:
                    index_lock.unlink()  # stale: we hold the flock, so no live agent-os commit owns it
            except OSError:
                pass
            # `add -A` (not just this path) makes the trail self-healing: any file a contended commit
            # skipped earlier gets swept in here.
            subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)
            subprocess.run(
                ["git", "-c", f"user.name={GIT_NAME}", "-c", f"user.email={GIT_EMAIL}",
                 "commit", "-q", "-m", msg],
                cwd=root, check=True, capture_output=True,
            )
    except Exception as e:
        _log_commit_failure(root, e)


def _write(root: Path, path: Path, fm: dict, body: str, msg: str) -> None:
    path.write_text(_dump_note(fm, body), encoding="utf-8")
    _git_commit(root, path, msg)


def _validate(fm: dict) -> None:
    tenant = fm.get("tenant")
    if tenant == FORBIDDEN_TENANT:
        raise RefusalError("cannot write a restricted-tenant note here (local-only wall).")
    if tenant not in ALLOWED_TENANTS:
        raise ValidationError(f"tenant must be one of {list(ALLOWED_TENANTS)}, got {tenant!r}")
    # Caller-supplied id charset gate (injection defense-in-depth — see _ID_RE above). Only checked
    # when the caller actually supplied one: an omitted id is auto-generated later (always safe:
    # date + _slug(title) + a hex suffix, all already in-charset) and isn't present in `fm` yet at
    # this point in put()'s flow.
    if fm.get("id") is not None and not _valid_id(fm["id"]):
        raise ValidationError(
            f"id {fm['id']!r} contains characters outside the safe id charset "
            f"(letters, digits, dot, dash, underscore only)"
        )
    missing = [f for f in REQUIRED if not fm.get(f)]
    if missing:
        raise ValidationError(f"missing required field(s): {missing}")
    if fm["type"] not in VALID_TYPES:
        raise ValidationError(f"type must be one of {sorted(VALID_TYPES)}")
    if fm["sensitivity"] not in VALID_SENSITIVITY:
        raise ValidationError(f"sensitivity must be one of {sorted(VALID_SENSITIVITY)}")
    if fm["egress"] not in VALID_EGRESS:
        raise ValidationError(f"egress must be one of {sorted(VALID_EGRESS)}")
    if fm["status"] not in VALID_STATUS:
        raise ValidationError(f"status must be one of {sorted(VALID_STATUS)}")
    # Egress wall: restricted content must never live in the cloud-ok stores.
    if fm["sensitivity"] == "restricted":
        raise RefusalError(
            "restricted content cannot be stored in the work/meta (cloud-ok) stores."
        )


# --- supersedes enforcement (design intent: "terminal registration MUST carry supersedes") -------
# A note/capability whose title announces itself as done (SHIPPED, COMPLETE, ...) is exactly the
# moment a follow-up should close — but agents kept writing a rich terminal body and skipping the
# supersedes link, leaving the follow-up open forever (a whole audited failure mode). register()
# blocks on this (raises); put() only warns (a general note isn't the registry's single source of
# truth for a capability, so it shouldn't hard-fail a routine journal entry).
_TERMINAL_WORDS = {
    "complete", "completed", "shipped", "shipping", "done", "merged", "closed", "finished",
    "deployed", "released", "live", "ga", "resolved", "delivered", "landed",
    "deprecated", "retired", "wontfix", "final",
}
_TERMINAL_COMPOUNDS = {"prod-live"}  # position-agnostic: unambiguous wherever it appears
_WORD_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")  # hyphen-joined, for the compound check
# A title that OPENS with one of these reads as an open task/question, not a completion status,
# even if it ends on an otherwise-terminal-looking word — e.g. "Investigate why the connection
# closed" trails on "closed" but is plainly still-open work, not a "this note is done" announcement.
_LEADING_TASK_WORDS = {"investigate", "debug", "diagnose", "reproduce", "explore", "research",
                       "determine", "figure"}
# A terminal word used as a LEADING STATUS TAG — "SHIPPED: new checkout flow", "[DONE] widget
# exporter", "COMPLETE - migrate to new schema" — is common capability-naming convention and must
# still be caught (an any-position match caught it before the trailing-only precision fix below;
# trailing-only alone would silently lose it). What distinguishes a tag from a leading imperative
# verb + object ("Complete the RentCast migration", no separator) is the separator immediately
# after the word: a colon/dash/em-dash, or the word being bracket-enclosed.
_LEADING_TAG_RE = re.compile(
    r"^(?:\[([a-z0-9]+)\]|([a-z0-9]+)\s*[:\-—])\s"
)


def _leading_status_tag(t: str) -> Optional[str]:
    """The bracketed-or-separator-tagged leading word of ``t`` (already lowercased), if any."""
    m = _LEADING_TAG_RE.match(t)
    if not m:
        return None
    return m.group(1) or m.group(2)


def _looks_terminal(title: str) -> bool:
    """Keyword heuristic, not NLP — a title reads as terminal when its LAST word is a status word
    from ``_TERMINAL_WORDS`` ("Inventory sync SHIPPED", "Service now LIVE"), when it OPENS on one
    as a status tag ("SHIPPED: new checkout flow", "[DONE] widget exporter" — see
    ``_leading_status_tag``), or when it contains the unambiguous compound "prod-live" anywhere.

    Trailing-only for the plain (non-tagged) case — not "anywhere in the title" — is the precision
    fix: a terminal-looking word used as a LEADING imperative verb followed directly by an object,
    no separator ("Complete the RentCast migration", "Finish the report") or buried mid-title as a
    plain description of something else ("Bug: modal closed on outside click") is common and
    should NOT block registration — under the old any-position match it did, over-eagerly. The
    leading-TAG shape is deliberately kept as a second, position-agnostic-at-the-front signal
    precisely because it's what the trailing-only fix would otherwise have lost.

    The one residual gap trailing-only doesn't close on its own — a trailing status word
    describing something OTHER than the note itself ("Investigate why the connection closed") —
    is handled by the ``_LEADING_TASK_WORDS`` check: a title that opens on an investigative/
    imperative verb reads as an open task regardless of how it ends, and this check takes priority
    over every other signal in this function INCLUDING the "prod-live" compound (so "Investigate
    prod-live outage" reads as an open task, not a completion announcement — an investigation
    title's opening verb governs the whole title's intent more than an incidental compound
    elsewhere in it).

    Documented residual limits (still a keyword heuristic, not full NLP): a title that neither
    opens on a recognized task/tag shape NOR ends on a terminal word can still fool this in either
    direction — e.g. "Why the export finally shipped" doesn't open on a known task word and would
    still read terminal on "shipped"; unusual phrasing beyond the patterns above isn't covered.
    Improving further would need real parsing, not a bigger keyword list.
    """
    t = (title or "").lower()
    tokens = _TOKEN_RE.findall(t)
    if not tokens:
        return False
    if tokens[0] in _LEADING_TASK_WORDS:
        return False
    if any(tok in _TERMINAL_COMPOUNDS for tok in _WORD_RE.findall(t)):
        return True
    if tokens[-1] in _TERMINAL_WORDS:
        return True
    return _leading_status_tag(t) in _TERMINAL_WORDS


def _candidate_open_followups(cap: dict, limit: int = 10) -> list:
    """Up to ``limit`` open follow-ups ranked by tag/token overlap with ``cap`` (title+body+tags)."""
    tenant = cap.get("tenant")
    try:
        pool = followups(tenant=tenant)
    except Exception:
        pool = []
    cap_tags = set(cap.get("tags") or [])
    cap_tokens = set(_TOKEN_RE.findall((str(cap.get("title") or "") + " " + str(cap.get("body") or ""))
                                        .lower()))
    scored = []
    for f in pool:
        f_tags = set(f.get("tags") or [])
        f_tokens = set(_TOKEN_RE.findall(str(f.get("title") or "").lower()))
        overlap = 3 * len(cap_tags & f_tags) + len(cap_tokens & f_tokens)
        scored.append((overlap, str(f.get("updated") or ""), f))
    scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return [f for _, _, f in scored[:limit]]


def _terminal_no_supersedes_message(title: Optional[str], candidates: list) -> str:
    words = "/".join(sorted(_TERMINAL_WORDS | _TERMINAL_COMPOUNDS))
    lines = [f"title {title!r} ends on (or contains) a terminal-looking word ({words}) "
             f"but supersedes was omitted."]
    if candidates:
        lines.append(f"Candidate open follow-ups ({len(candidates)}):")
        for f in candidates:
            lines.append(f"  - {f.get('id')}  [{f.get('tenant')}]  {f.get('title')}")
    else:
        lines.append("No open follow-ups found to suggest.")
    lines.append("Pass supersedes=[ids...] to close them, or supersedes=[] explicitly if truly "
                 "nothing closes.")
    return "\n".join(lines)


def put(note: dict) -> dict:
    """Create or update a note.

    ``note`` carries frontmatter fields plus an optional ``body``. Required fields:
    type, title, tenant, sensitivity, egress, status. ``id`` / ``created`` / ``updated`` /
    ``version`` are auto-filled. Re-using an existing ``id`` updates that note (version bumps).

    If ``title`` looks terminal (SHIPPED/DONE/COMPLETE/...) and ``supersedes`` was omitted (not
    even an explicit ``[]``), the returned dict carries a non-blocking ``warning`` key naming
    candidate open follow-ups this note might close — see ``register()`` for the blocking version.
    """
    note = dict(note)
    body = note.pop("body", "") or ""
    # Passive-completion shortcuts: links this note draws to the follow-up(s) it (partially) closes.
    supersedes_val = note.pop("supersedes", None)
    supersedes = supersedes_val or []
    advances = note.pop("advances", None) or []
    # Validate these targets BEFORE writing anything (finding-6 follow-up, caught in review): link()
    # already rejects a syntactically-invalid target, but that call happens AFTER the primary note
    # is written and git-committed below — a bad target would otherwise leave a partial state (note
    # written, link never drawn, caller sees an exception). Failing fast here keeps put() atomic:
    # either the whole call (note + all its links) lands, or nothing does.
    for _target in supersedes + advances:
        if not _valid_id(_target):
            raise ValidationError(
                f"supersedes/advances target {_target!r} is not a syntactically valid id")
    fm = {k: v for k, v in note.items() if v is not None}
    fm.setdefault("status", "committed")
    _validate(fm)

    tenant = fm["tenant"]
    root = _root_for(tenant)
    now = _now()

    note_id = fm.get("id")
    # Serialize this note's read-modify-write (find/read existing -> merge/carry-over -> _write)
    # against any other process mutating the SAME note, so the second writer can't build on a stale
    # read and silently drop the first's change (F1 lost update). Only an id'd note can race a
    # concurrent writer; a brand-new note gets a unique generated id below, so it needs no lock. The
    # lock is released before the trailing link() calls, which re-acquire it for the same note.
    with (_note_lock(tenant, note_id) if note_id else contextlib.nullcontext()):
        existing = _find_path(tenant, note_id) if note_id else None
        if existing is not None:
            old_note = _read_note(existing)
            old = old_note["frontmatter"]
            # Read-time backstop, write side (finding 2 follow-up, caught in review): an existing note
            # that is restricted (arrived out-of-band — the write wall means this module never
            # created one) must never be touched via the ordinary cloud-ok put() path, even to
            # "demote" it away from restricted — that would silently launder a wall breach into a
            # normal update instead of surfacing it. Refuse outright; get()/search() already refuse to
            # show it, so this is the corresponding refusal on the write side of the same note.
            if old.get("sensitivity") == "restricted":
                raise RefusalError(
                    f"note {fm.get('id')!r} is sensitivity=restricted in {tenant!r} — refusing to "
                    f"update it via the cloud-ok write path; see design §3 egress wall."
                )
            fm.setdefault("created", old.get("created", now))
            if "version" not in note:
                fm["version"] = int(old.get("version", 1)) + 1
            # Non-destructive update (PATCH semantics): carry over any existing frontmatter the caller
            # did not re-supply, so a body/tags update never silently drops a note's capability_type,
            # health, or child_of links. This generalizes register()'s carry-over to every put — the
            # memory_put footgun fix (see meta note 2026-06-17-footgun-memory-put-...). ``updated``
            # is always refreshed below; ``created``/``version`` are handled above.
            for k, v in old.items():
                if k not in fm and k != "updated":
                    fm[k] = v
            # Non-destructive body (same PATCH intent as the frontmatter carry-over above): an update
            # that supplies no non-empty body keeps the existing one. Without this, a title-less
            # health/tags re-register (e.g. the hourly health_sweep) silently BLANKS a capability's
            # documentation every run. To truly clear a body, edit the note file directly.
            if not body and old_note.get("body"):
                body = old_note["body"]
            path = existing
        else:
            if not fm.get("id"):
                fm["id"] = f"{now[:10]}-{_slug(fm['title'])}-{uuid.uuid4().hex[:6]}"
            fm.setdefault("created", now)
            fm.setdefault("version", 1)
            path = root / "notes" / f"{_safe_name(fm['id'])}.md"

        warning = None
        if supersedes_val is None and _looks_terminal(fm.get("title") or ""):
            candidates = _candidate_open_followups({"tenant": tenant, "title": fm.get("title"),
                                                     "tags": fm.get("tags"), "body": body})
            warning = _terminal_no_supersedes_message(fm.get("title"), candidates)

        fm["updated"] = now
        _write(root, path, fm, body, f"memory.put: {fm['id']} ({fm['type']})")
    # Draw the passive-completion links (if any) now that this note exists.
    for target in supersedes:
        link(fm["id"], "supersedes", target, tenant)
    for target in advances:
        link(fm["id"], "advances", target, tenant)
    result = {"id": fm["id"], "tenant": tenant, "path": str(path), "version": fm["version"]}
    if warning:
        result["warning"] = warning
    return result


def _resolve_mutation_tenant(note_id: str, tenant: Optional[str]) -> str:
    """Resolve which tenant a tenant-less WRITE (link/unlink/health/...) should target.

    An explicit ``tenant`` is trusted as given (unchanged behavior). When omitted, every allowed
    tenant is checked for a note with this id — a hit in more than one tenant is a cross-tenant id
    collision (e.g. the same slug independently registered in both work and meta) that a
    WRITE must never silently resolve by picking the first match: that would update one tenant's
    copy while leaving the other one stale with no signal anything is wrong. Read-only get() keeps
    first-match browsing behavior; this is the write-path guard — see AmbiguousIdError.
    """
    if tenant:
        return tenant
    hits = [t for t in ALLOWED_TENANTS if _find_path(t, note_id) is not None]
    if len(hits) > 1:
        raise AmbiguousIdError(
            f"note id {note_id!r} exists in more than one tenant {hits} — a tenant-less write "
            f"can't safely pick one; pass tenant=... explicitly."
        )
    if not hits:
        raise NotFound(f"note {note_id!r} not found in {list(ALLOWED_TENANTS)}")
    return hits[0]


def get(note_id: str, tenant: Optional[str] = None) -> dict:
    tenants = [tenant] if tenant else list(ALLOWED_TENANTS)
    for t in tenants:
        p = _find_path(t, note_id)
        if p is not None:
            n = _read_note(p)
            # Read-time backstop for the crown-jewel wall: the write path (_validate) already
            # refuses to ever CREATE a restricted note in work/meta, but a note can still
            # arrive out-of-band (a manual file drop, a git merge) — get() must refuse it rather
            # than return it verbatim, even though it technically "found" the file. This tenant is
            # cloud-ok by construction (family is never in `tenants` here), so any restricted
            # note present is already a wall breach; refusing on read is the last line of defense.
            if n["frontmatter"].get("sensitivity") == "restricted":
                raise RefusalError(
                    f"note {note_id!r} is sensitivity=restricted in a cloud-ok store "
                    f"({t!r}) — refusing to read it out-of-band; see design §3 egress wall."
                )
            return {
                "id": n["id"], "tenant": t, "title": n["title"],
                "frontmatter": n["frontmatter"], "body": n["body"], "path": str(p),
            }
    raise NotFound(f"note {note_id!r} not found in {tenants}")


def _snippet(body: str, q: str, width: int = 140) -> str:
    flat = " ".join((body or "").split())
    if q:
        i = flat.lower().find(q.lower())
        if i >= 0:
            start = max(0, i - 30)
            return flat[start:start + width]
    return flat[:width]


# --- relevance ranking (BM25 over substring term-frequencies, title-boosted) ----------------
# Vectors are the eventual relevance layer (design §4), but until an embedding model is chosen
# the lexical layer should still rank, not just recency-sort an AND-filtered pile. We keep the
# existing strict-AND *inclusion* (a result must contain every query term, as before) and only
# reorder the matched set by BM25 — so nothing new matches, but the most on-point note rises.
# Stdlib-only (math), to stay inside store_test's pyyaml-only footprint.
_BM25_K1 = 1.5          # term-frequency saturation
_BM25_B = 0.75          # length normalization strength
_TITLE_BOOST = 3        # a title hit counts this many times a body hit (field weighting via repeat)
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _rank_text(title: str, body: str, note_tags) -> str:
    """The scored document: title weighted by repetition, plus body and tags. Lowercased."""
    tagtext = " ".join(map(str, note_tags))
    return ((title + " ") * _TITLE_BOOST + body + " " + tagtext).lower()


def _bm25_scores(cands: list, terms: list) -> dict:
    """BM25 score per candidate index. tf is substring count (consistent with substring inclusion),
    so query 'tax' scores against the token 'taxes' just as it matched it."""
    n = len(cands)
    dls = [len(_TOKEN_RE.findall(c["_rank"])) or 1 for c in cands]
    avgdl = (sum(dls) / n) if n else 1.0
    df = {term: sum(1 for c in cands if term in c["_rank"]) for term in terms}
    idf = {term: math.log(1 + (n - df[term] + 0.5) / (df[term] + 0.5)) for term in terms}
    scores = {}
    for i, c in enumerate(cands):
        text, dl, s = c["_rank"], dls[i], 0.0
        for term in terms:
            tf = text.count(term)
            if not tf:
                continue
            s += idf[term] * (tf * (_BM25_K1 + 1)) / (tf + _BM25_K1 * (1 - _BM25_B + _BM25_B * dl / avgdl))
        scores[i] = s
    return scores


def _search_scan(t: str, terms: list, want_tags: set, type_filter: Optional[str],
                  since: Optional[str], include_retired: bool = False) -> list:
    """Full-scan fallback (no cache): the pre-index behavior, tokenized AND-first/OR-fallback.

    Used when the SQLite cache is unavailable or errors — reads and parses every note in the
    tenant, so it is correct but O(n) in note count where the cache path is not. This is the
    reference implementation for matching semantics (substring, via _hay_text) — _search_cached
    mirrors it exactly so the two are provably equivalent given the same underlying data.
    """
    base = []
    for n in _iter_notes(t):
        fm = n["frontmatter"]
        if fm.get("sensitivity") == "restricted":
            continue  # read-side backstop: never surfaced by search, even out-of-band in work/meta
        if not include_retired and fm.get("status") in RETIRED_STATUSES:
            continue  # parity with _search_cached — a filter on only one path is not a guarantee
        if type_filter and fm.get("type") != type_filter:
            continue
        note_tags = set(fm.get("tags") or [])
        if want_tags and not want_tags.issubset(note_tags):
            continue
        if since and str(fm.get("updated") or fm.get("created") or "") < since:
            continue
        hay = _hay_text(n["title"], n["body"], note_tags)
        base.append((n, fm, note_tags, hay))

    def _build(matched):
        out = []
        for n, fm, note_tags, _hay in matched:
            out.append({
                "id": n["id"], "tenant": t, "title": n["title"], "type": fm.get("type"),
                "tags": sorted(note_tags), "updated": fm.get("updated"),
                "links": list(fm.get("links") or []), "sensitivity": fm.get("sensitivity"),
                "path": str(n["_path"]), "snippet": _snippet(n["body"], terms[0] if terms else ""),
                "_rank": _rank_text(n["title"], n["body"], note_tags),
            })
        return out

    if not terms:
        cands = _build(base)
    else:
        # All-terms first (tokenized substring AND); if that yields nothing, relax to OR so a
        # partial match (e.g. one term hits, one doesn't) still surfaces, ranked by BM25.
        cands = _build([b for b in base if all(term in b[3] for term in terms)])
        if not cands:
            cands = _build([b for b in base if any(term in b[3] for term in terms)])

    if terms:
        scores = _bm25_scores(cands, terms)
        for i, c in enumerate(cands):
            c["_score"] = scores.get(i, 0.0)
    else:
        for c in cands:
            c["_score"] = 0.0
    for c in cands:
        c.pop("_rank", None)
    return cands


def search(tenant: Optional[str] = None, query: Optional[str] = None,
           tags: Optional[list] = None, type: Optional[str] = None,
           since: Optional[str] = None, limit: int = 20,
           include_retired: bool = False) -> list:
    """Notes matching the filters. `quarantined` and `archived` notes are EXCLUDED by default
    (RETIRED_STATUSES); pass include_retired=True to see them — the flag is an opt-in view, never
    a bypass of the restricted-tenant wall, which is enforced independently at both read paths."""
    tenants = [tenant] if tenant else list(ALLOWED_TENANTS)
    # Tokenized (not raw-substring) terms: "session-start" -> ["session", "start"], so punctuation
    # in either the query or the note text never blocks a match the way strict substrings did.
    terms = _TOKEN_RE.findall((query or "").lower())
    want_tags = set(tags or [])
    cands = []
    for t in tenants:
        rows = _search_cached(t, terms, want_tags, type, since, include_retired)
        if rows is None:  # cache unavailable/errored — transparent fallback, never a hard failure
            rows = _search_scan(t, terms, want_tags, type, since, include_retired)
        cands.extend(rows)
    if terms:
        # Relevance first (BM25), recency as the tiebreak so equal-score notes stay newest-first.
        out = sorted(cands, key=lambda r: (r.get("_score", 0.0), str(r.get("updated") or "")),
                      reverse=True)
    else:
        # No query → browse mode: newest first (unchanged behavior).
        out = sorted(cands, key=lambda r: str(r.get("updated") or ""), reverse=True)
    out = out[:limit]
    for r in out:
        r.pop("_score", None)
        r.pop("_rank", None)
    return out


def link(from_id: str, relation: str, to_id: str, tenant: Optional[str] = None) -> dict:
    if not _valid_relation(relation):
        raise ValidationError(f"relation {relation!r} is not a valid relation token")
    if not _valid_id(to_id):
        raise ValidationError(f"target {to_id!r} is not a syntactically valid id")
    tenant = _resolve_mutation_tenant(from_id, tenant)
    with _note_lock(tenant, from_id):  # atomic read-modify-write of this note's link list (F1)
        note = get(from_id, tenant)
        t = note["tenant"]
        fm = dict(note["frontmatter"])
        fm.setdefault("id", note["id"])
        links = list(fm.get("links") or [])
        entry = {"relation": relation, "target": to_id}
        if entry not in links:
            links.append(entry)
        fm["links"] = links
        fm["updated"] = _now()
        _write(_root_for(t), Path(note["path"]), fm, note["body"],
               f"memory.link: {from_id} -{relation}-> {to_id}")
        return {"id": note["id"], "tenant": t, "links": links}


def unlink(from_id: str, relation: str, to_id: str, tenant: Optional[str] = None) -> dict:
    """Remove a typed graph link from a note — the inverse of ``link``. No-op if absent.

    Needed to cleanly re-parent or top-level a node (e.g. promoting a feature to a capability has
    to drop its ``child_of`` edge); ``link`` could only ever add, so a stale edge was unremovable.
    """
    if not _valid_relation(relation):
        raise ValidationError(f"relation {relation!r} is not a valid relation token")
    if not _valid_id(to_id):
        raise ValidationError(f"target {to_id!r} is not a syntactically valid id")
    tenant = _resolve_mutation_tenant(from_id, tenant)
    with _note_lock(tenant, from_id):  # atomic read-modify-write of this note's link list (F1)
        note = get(from_id, tenant)
        t = note["tenant"]
        fm = dict(note["frontmatter"])
        fm.setdefault("id", note["id"])
        links = [l for l in (fm.get("links") or [])
                 if not (l.get("relation") == relation and l.get("target") == to_id)]
        fm["links"] = links
        fm["updated"] = _now()
        _write(_root_for(t), Path(note["path"]), fm, note["body"],
               f"memory.unlink: {from_id} -{relation}-x-> {to_id}")
        return {"id": note["id"], "tenant": t, "links": links}


def _link_index(tenants: list) -> tuple[dict, dict]:
    """Reverse-link maps target_id -> [source_ids], for close and advance relations.

    Lets us ask "is this follow-up superseded/advanced by anything?" without per-note lookups.
    Reads from the search-index cache (id + links only, already synced) when available; falls
    back to a full parse of every note when the cache is unavailable/errored.
    """
    closed: dict[str, list] = {}
    advanced: dict[str, list] = {}
    for t in tenants:
        rows = _cache_link_rows(t)
        if rows is None:
            # Read-time backstop (finding 2 follow-up, caught in review): the cache path is safe
            # because _sync_cache never indexes a restricted note in the first place, but this
            # fallback parses the notes dir directly — without the same filter here, an out-of-band
            # restricted note's id (and its links) would leak into followups()'s closed_by/
            # advanced_by whenever the cache is unavailable.
            rows = [(n["id"], n["frontmatter"].get("links") or []) for n in _iter_notes(t)
                    if n["frontmatter"].get("sensitivity") != "restricted"]
        for src, links in rows:
            for l in links:
                rel, tgt = l.get("relation"), l.get("target")
                if not tgt:
                    continue
                if rel in SUPERSEDE_RELATIONS:
                    closed.setdefault(tgt, []).append(src)
                elif rel in ADVANCE_RELATIONS:
                    advanced.setdefault(tgt, []).append(src)
    return closed, advanced


def followups(tenant: Optional[str] = None, include_closed: bool = False) -> list:
    """Follow-up notes annotated with completion ``state``, derived passively from links/tags.

    state is one of:
      * ``closed``  — a `done`/`archived` marker OR another note supersedes/closes it (link).
      * ``partial`` — a note `advances` it (or it carries a `partial` tag) without a full close.
      * ``open``    — neither.

    No manual ``done``-tagging is required: shipping work that points ``supersedes`` -> the
    follow-up closes it; an ``advances`` link leaves it open but flags it partial. Closed ones
    are dropped unless ``include_closed`` is set. Each row also reports ``closed_by`` /
    ``advanced_by`` (the source note ids) so the trail is visible.
    """
    tenants = [tenant] if tenant else list(ALLOWED_TENANTS)
    closed_by, advanced_by = _link_index(tenants)
    rows = []
    for r in search(tenant=tenant or None, tags=["follow-up"], limit=1000):
        tags = set(r.get("tags") or [])
        fid = r["id"]
        closers = closed_by.get(fid, [])
        advancers = advanced_by.get(fid, [])
        if "done" in tags or closers:
            state = "closed"
        elif "partial" in tags or advancers:
            state = "partial"
        else:
            state = "open"
        if state == "closed" and not include_closed:
            continue
        rows.append({**r, "state": state, "closed_by": closers, "advanced_by": advancers})
    return rows


# ---- capability registry (a specialized partition of the same substrate, §1.3/§6) ----

def register(capability: dict) -> dict:
    """Register or update a capability. Raises ``ValidationError`` on a missing ``capability_type``.

    Raises ``ValueError`` when the incoming call EXPLICITLY passes a terminal-looking ``title``
    (SHIPPED/COMPLETE/DONE/...) with ``supersedes`` omitted — the design intent is that a
    "this is done" registration must also close the follow-up(s) it absorbs (or say ``[]``
    explicitly if none apply). Detection of "title passed" and "supersedes omitted" is against
    the raw incoming ``capability`` dict, BEFORE any defaulting/carry-over below, so a title-less
    health-only re-register (which relies on carrying the existing title/health forward) can never
    trip this — it never had a title to judge in the first place.
    """
    had_title = bool(capability.get("title"))
    supersedes_omitted = capability.get("supersedes", None) is None
    cap = dict(capability)
    cap["type"] = "capability"
    cap.setdefault("tenant", "work")
    cap.setdefault("sensitivity", "internal")
    cap.setdefault("egress", "cloud-ok")
    cap.setdefault("status", "committed")
    # Stable id by title → re-registering updates rather than duplicating. An explicit id
    # (e.g. a health-only PATCH from the health sweep) wins; else derive one from the title.
    cap.setdefault("id", "cap-" + _slug(cap.get("title", "capability")))
    # Non-destructive auto-update: a body-only or health-only re-register must not silently drop
    # fields the caller can't re-supply — carry them over from the existing capability BEFORE
    # validating. This is what makes "capabilities auto-update" safe (updating the body to absorb a
    # follow-up keeps health + links intact) and is what lets a title-less, type-less health-only
    # re-register work at all: it inherits capability_type/title/scope from the stored note rather
    # than being rejected for "missing" fields it was never meant to carry.
    existing = _find_path(cap["tenant"], cap["id"])
    if existing is not None:
        old = _read_note(existing)["frontmatter"]
        # Same write-side restricted refusal as put() (finding 2 follow-up, caught in review):
        # an out-of-band restricted note under this id must never be carried-over/demoted by
        # an ordinary re-register, even a health-only PATCH.
        if old.get("sensitivity") == "restricted":
            raise RefusalError(
                f"note {cap['id']!r} is sensitivity=restricted in {cap['tenant']!r} — refusing "
                f"to update it via the cloud-ok registry path; see design §3 egress wall."
            )
        for k in ("capability_type", "scope", "health", "links", "interface", "cost", "composes", "title"):
            if cap.get(k) is None and old.get(k) is not None:
                cap[k] = old[k]
    # Validate AFTER carry-over so an inherited capability_type satisfies this; a genuinely new
    # capability with no type (and nothing to inherit) still fails here as intended.
    if not cap.get("capability_type"):
        raise ValidationError("capability requires capability_type (tool|skill|agent|model)")
    if had_title and supersedes_omitted and _looks_terminal(cap.get("title") or ""):
        candidates = _candidate_open_followups(cap)
        raise ValueError(_terminal_no_supersedes_message(cap.get("title"), candidates))
    return put(cap)


def find(name: Optional[str] = None, type: Optional[str] = None,
         tag: Optional[str] = None, scope: Optional[str] = None, limit: int = 50) -> list:
    out = []
    for r in search(type="capability", limit=1000):
        full = get(r["id"], r["tenant"])
        fm = full["frontmatter"]
        if name and name.lower() not in (str(fm.get("title", "")).lower() + full["id"].lower()):
            continue
        if type and fm.get("capability_type") != type:
            continue
        if tag and tag not in (fm.get("tags") or []):
            continue
        if scope and scope not in (fm.get("scope") or []):
            continue
        out.append({
            "id": full["id"], "title": fm.get("title"), "capability_type": fm.get("capability_type"),
            "scope": fm.get("scope") or [], "health": fm.get("health") or {}, "tenant": r["tenant"],
        })
        if len(out) >= limit:
            break
    return out


def health(id: str, patch: Optional[dict] = None, tenant: Optional[str] = None) -> dict:
    tenant = _resolve_mutation_tenant(id, tenant)
    with _note_lock(tenant, id):  # atomic read-modify-write of this note's health block (F1)
        note = get(id, tenant)
        fm = dict(note["frontmatter"])
        if not patch:
            return {"id": id, "health": fm.get("health") or {}}
        h = dict(fm.get("health") or {})
        h.update(patch)
        fm["health"] = h
        fm["updated"] = _now()
        fm.setdefault("id", note["id"])
        _write(_root_for(note["tenant"]), Path(note["path"]), fm, note["body"], f"registry.health: {id}")
        return {"id": id, "health": h}


if __name__ == "__main__":
    import sys as _sys

    if "--rebuild" in _sys.argv:
        removed = rebuild()
        print(f"rebuilt search cache: deleted {removed or '(nothing was cached yet)'}")
    else:
        print(__doc__)
        print("Usage: python3 agentos_store.py --rebuild   (force a full search-cache reindex)")
