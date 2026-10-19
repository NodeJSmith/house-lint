#!/usr/bin/env bash
# Probe jscpd inline ignore markers in Python and TS.
S="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
R=${OUT:-$S/results}
C=$S/corpus/supp
O=$R/supp
echo "py with markers in b:"; "$S/scripts/jr.sh" "$O/py" "$C/a.py" "$C/b.py" -k 20 -l 3
echo "ts with markers in b:"; "$S/scripts/jr.sh" "$O/ts" "$C/a.ts" "$C/b.ts" -k 20 -l 3
