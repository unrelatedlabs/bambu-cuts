#!/usr/bin/env bash
# Run every test. No framework and no hardware: the firmware runs under
# mocked CircuitPython modules and the marker link against a pty.
set -u
cd "$(dirname "$0")/.."
PY=${PY:-.venv/bin/python}
[ -x "$PY" ] || PY=python3
rc=0
for t in tests/test_*.py; do
    printf '%-44s' "$t"
    if out=$("$PY" "$t" 2>&1); then
        echo PASS
    else
        echo FAIL
        echo "$out" | sed 's/^/    /'
        rc=1
    fi
done
exit $rc
