#!/usr/bin/env python3
"""Agent OS MCP server — memory + capability registry over the the work + meta stores.

RESTRICTED is hard-refused (local-only, design §3). Default transport is stdio (Claude Code,
local, no exposure). Pass ``--http`` for the streamable-HTTP transport used by the Claude
chat remote connector and the agentos-svc unit: it REQUIRES env ``AGENTOS_TOKEN`` (refuses to
start without one) and demands ``Authorization: Bearer <token>`` on every request — see
``_run_http`` for the auth mechanism and why it's a hand-rolled ASGI wrap rather than the SDK's
built-in OAuth machinery. stdio mode is completely untouched by any of this.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp.server.fastmcp import FastMCP  # noqa: E402

import agentos_store as store  # noqa: E402

mcp = FastMCP("agent-os")


@mcp.tool()
def memory_search(query: str = "", tenant: str = "", type: str = "",
                  tags: list | None = None, limit: int = 20) -> list:
    """Search notes across the work + meta stores (the restricted tenant is never searched).

    Free-text ``query`` matches title/body/tags; optionally filter by ``tenant``,
    note ``type`` (episodic|semantic|procedural|capability), and ``tags``.
    """
    return store.search(tenant=tenant or None, query=query or None,
                        type=type or None, tags=tags or None, limit=limit)


@mcp.tool()
def memory_get(id: str, tenant: str = "") -> dict:
    """Fetch one note (frontmatter + body) by id."""
    return store.get(id, tenant=tenant or None)


@mcp.tool()
def memory_put(title: str, type: str, tenant: str, body: str = "",
               sensitivity: str = "internal", egress: str = "cloud-ok",
               status: str = "committed", tags: list | None = None,
               principal: str = "", source: str = "", id: str = "",
               supersedes: list | None = None, advances: list | None = None) -> dict:
    """Create or update a note. ``tenant`` must be work or meta (restricted is refused).

    Validates the §5 schema and git-commits the write. Pass an existing ``id`` to update.
    Pass ``supersedes=[follow-up-id,...]`` to fully close those follow-ups passively (no `done`
    tag needed) or ``advances=[...]`` to mark them partially done — this note links to them.
    """
    note = {"title": title, "type": type, "tenant": tenant, "body": body,
            "sensitivity": sensitivity, "egress": egress, "status": status,
            "tags": tags or []}
    if principal:
        note["principal"] = principal
    if source:
        note["source"] = source
    if id:
        note["id"] = id
    # is-not-None (not truthiness): an explicit supersedes=[] must reach the store as the
    # "terminal title but nothing to close" escape hatch, distinct from omitting it entirely.
    if supersedes is not None:
        note["supersedes"] = supersedes
    if advances is not None:
        note["advances"] = advances
    return store.put(note)


@mcp.tool()
def memory_link(from_id: str, relation: str, to_id: str, tenant: str = "") -> dict:
    """Add a typed graph link (e.g. relates_to, about, supersedes) from one note to another."""
    return store.link(from_id, relation, to_id, tenant=tenant or None)


@mcp.tool()
def memory_unlink(from_id: str, relation: str, to_id: str, tenant: str = "") -> dict:
    """Remove a typed graph link from a note (inverse of memory_link). No-op if it isn't present.

    Use to re-parent or top-level a node — e.g. when promoting a feature to a capability you must
    drop its ``child_of`` edge so it stops rendering as nested.
    """
    return store.unlink(from_id, relation, to_id, tenant=tenant or None)


@mcp.tool()
def memory_list_tenants() -> list:
    """List the tenants this server can serve (the restricted tenant is excluded by design)."""
    return store.list_tenants()


@mcp.tool()
def registry_register(title: str, capability_type: str, scope: list | None = None,
                      tags: list | None = None, body: str = "",
                      tenant: str = "work", id: str = "",
                      supersedes: list | None = None, advances: list | None = None) -> dict:
    """Register or update a capability. ``capability_type``: tool|skill|agent|model.

    When a capability absorbs a follow-up's work, pass ``supersedes=[follow-up-id,...]`` (or
    ``advances=[...]`` for partial) so updating the capability passively closes/flags those
    follow-ups in the same call — the capability becomes their durable home, no `done` tag.
    """
    cap = {"title": title, "capability_type": capability_type,
           "scope": scope or ["work"], "tags": tags or [], "body": body, "tenant": tenant}
    if id:
        cap["id"] = id
    # is-not-None (not truthiness): lets a caller pass supersedes=[] to acknowledge a terminal-titled
    # registration that genuinely closes nothing, which the store's enforcement requires (else raises).
    if supersedes is not None:
        cap["supersedes"] = supersedes
    if advances is not None:
        cap["advances"] = advances
    return store.register(cap)


@mcp.tool()
def registry_find(name: str = "", type: str = "", tag: str = "", scope: str = "") -> list:
    """Find capabilities by name, capability type (tool|skill|agent|model), tag, or scope."""
    return store.find(name=name or None, type=type or None, tag=tag or None, scope=scope or None)


@mcp.tool()
def registry_health(id: str, status: str = "", error_rate: float | None = None,
                    last_eval_score: float | None = None, tenant: str = "") -> dict:
    """Read or update a capability's health. With no fields supplied, returns current health."""
    patch = {}
    if status:
        patch["status"] = status
    if error_rate is not None:
        patch["error_rate"] = error_rate
    if last_eval_score is not None:
        patch["last_eval_score"] = last_eval_score
    return store.health(id, patch or None, tenant=tenant or None)


class _BearerAuthASGI:
    """Minimal ASGI gate: require ``Authorization: Bearer <token>`` on every HTTP request.

    Why this shape (checked against the installed SDK — mcp 1.27.2 — before choosing): FastMCP's
    own auth hooks (``auth=AuthSettings(...)`` + ``token_verifier``/``auth_server_provider``) model
    a full OAuth 2.1 *resource server* — issuer URL, protected-resource metadata,
    WWW-Authenticate challenge routing, an optional authorization-server mount. That machinery is
    for multi-client delegated auth; our threat model is one shared static secret behind a
    tailnet, held by a single trusted client. Standing up a TokenVerifier just to compare one
    string is more surface than it removes.

    So: get the real Starlette app FastMCP already builds (``streamable_http_app()``), which wires
    the streamable-HTTP session manager and its lifespan (startup/shutdown of the session manager
    happens there — see ``FastMCP.streamable_http_app``), and wrap the whole ASGI callable in a
    tiny gate that only inspects ``scope["type"] == "http"`` requests. Every other scope type
    (``lifespan``, and any future transport) passes straight through untouched, so the session
    manager's startup/shutdown still fires exactly as FastMCP wired it — we are not replacing the
    app, only intercepting its HTTP requests before they reach it.
    """

    def __init__(self, app, token: str):
        self._app = app
        self._expected = ("Bearer " + token).encode("utf-8")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self._app(scope, receive, send)
            return
        import hmac

        supplied = b""
        for name, value in scope.get("headers") or ():
            if name == b"authorization":
                supplied = value
                break
        if hmac.compare_digest(supplied, self._expected):
            await self._app(scope, receive, send)
            return
        await send({
            "type": "http.response.start",
            "status": 401,
            "headers": [(b"content-type", b"text/plain; charset=utf-8"),
                        (b"www-authenticate", b"Bearer")],
        })
        await send({"type": "http.response.body", "body": b"unauthorized\n"})


def _run_http() -> None:
    """Serve --http with mandatory bearer-token auth. Refuses to start without AGENTOS_TOKEN.

    Host/port default to FastMCP's own defaults (127.0.0.1:8000) and are overridable via
    AGENTOS_HTTP_HOST / AGENTOS_HTTP_PORT. NOTE: FastMCP's ``Settings`` normally reads
    ``FASTMCP_HOST`` / ``FASTMCP_PORT`` from the environment (env_prefix="FASTMCP_"), but
    ``FastMCP.__init__`` always passes ``host=`` / ``port=`` explicitly (even when the caller
    didn't), and pydantic-settings gives an explicit constructor arg priority over the
    environment — so those two env vars are silently inert here. Rather than depend on that
    quirk, host/port are read directly from our own env vars below.
    """
    token = os.environ.get("AGENTOS_TOKEN", "").strip()
    if not token:
        print(
            "FATAL: --http requires env AGENTOS_TOKEN (a shared secret clients send as "
            "'Authorization: Bearer <token>'). Refusing to start an unauthenticated HTTP "
            "endpoint over the stores. stdio mode does not need a token.",
            file=sys.stderr,
        )
        sys.exit(1)

    import uvicorn

    host = os.environ.get("AGENTOS_HTTP_HOST", mcp.settings.host)
    port = int(os.environ.get("AGENTOS_HTTP_PORT", mcp.settings.port))
    app = _BearerAuthASGI(mcp.streamable_http_app(), token)
    uvicorn.run(app, host=host, port=port, log_level=mcp.settings.log_level.lower())


if __name__ == "__main__":
    if "--http" in sys.argv:
        _run_http()
    else:
        mcp.run(transport="stdio")
