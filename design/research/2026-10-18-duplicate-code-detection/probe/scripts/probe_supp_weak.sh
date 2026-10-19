#!/usr/bin/env bash
# Are jscpd:ignore markers honored under --mode weak (comments skipped)?
S="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
R=${OUT:-$S/results}
C=$S/corpus/supp
echo "py weak:"; bash "$S/scripts/jr.sh" "$R/suppweak-py" "$C/a.py" "$C/b.py" -k 20 -l 3 --mode weak
echo "ts weak:"; bash "$S/scripts/jr.sh" "$R/suppweak-ts" "$C/a.ts" "$C/b.ts" -k 20 -l 3 --mode weak
