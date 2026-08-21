#!/usr/bin/env python3
"""The kit's press is a SANITIZED DERIVATIVE of an upstream engine. Compare behaviour, not bytes.

The first version of this test compared sha256 over six vendored files, and it caught a real
drift the day it was written. Then the kit was sanitized — every reference to the upstream
project's domain was stripped — and byte-identity stopped being the invariant while the thing
it protected (the engine must not diverge FUNCTIONALLY) stayed exactly as important.

So it compares the public API surface: every public callable, and its signature. That catches
a new verb, a renamed function, a changed parameter — real divergence — and tolerates the
comment and docstring differences that sanitization deliberately introduced.

When the upstream engine is not present (the normal case once this kit lives in its own repo),
every test here skips. A test that cannot run must say so rather than pass silently.
"""
import importlib.util
import inspect
import pathlib
import sys

import pytest

KIT = pathlib.Path(__file__).resolve().parent
UPSTREAM = KIT.parents[2] / "docs" / "harness-book" / "build"
MODULES = ["press", "claims", "plates"]


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _surface(mod):
    out = {}
    for name, obj in vars(mod).items():
        if name.startswith("_") or not callable(obj):
            continue
        if getattr(obj, "__module__", None) != mod.__name__:
            continue
        try:
            out[name] = str(inspect.signature(obj))
        except (TypeError, ValueError):
            out[name] = "(?)"
    return out


@pytest.mark.parametrize("name", MODULES)
def test_public_api_matches_upstream(name):
    src = UPSTREAM / f"{name}.py"
    if not src.exists():
        pytest.skip(f"upstream {name}.py absent — kit is standalone")
    sys.path.insert(0, str(KIT))
    kit = _load(KIT / f"{name}.py", f"kit_{name}")
    up = _load(src, f"up_{name}")
    k, u = _surface(kit), _surface(up)
    assert set(k) == set(u), (
        f"{name}.py public API diverged.\n"
        f"  only in kit:      {sorted(set(k) - set(u))}\n"
        f"  only in upstream: {sorted(set(u) - set(k))}")
    mismatched = {n: (k[n], u[n]) for n in k if k[n] != u[n]}
    assert not mismatched, f"{name}.py signatures diverged: {mismatched}"


def test_the_kit_carries_every_module_the_engine_needs():
    for name in MODULES + ["press_css", "press_economist"]:
        assert (KIT / f"{name}.py").exists(), f"{name}.py missing from the kit"
    assert (KIT / "to_pdf.sh").exists()
    assert (KIT / "fonts").is_dir()
