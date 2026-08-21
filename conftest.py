"""Keep pytest away from the vendored store's self-running suite.

`store_test.py` matches pytest's default `*_test.py` pattern, but it is a SCRIPT: it runs
its 63 checks at import and calls sys.exit(0). Collected by pytest that becomes an
INTERNALERROR that aborts the whole run — not just this file — so a single vendored script
takes down every unrelated suite in the repo.

It is still run, as a subprocess, by verify.py's "boundary gate passes" check. That is the
correct place for it: it is an install-time proof, not a unit test.
"""
collect_ignore = ["core/01-memory/store_test.py"]
