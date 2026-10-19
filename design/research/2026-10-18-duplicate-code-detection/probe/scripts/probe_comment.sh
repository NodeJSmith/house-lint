#!/usr/bin/env bash
# Does an inserted Python comment break a jscpd match? default (mild) vs --mode weak.
S="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
R=${OUT:-$S/results}
C=$S/corpus/comment
for m in mild weak strict; do
  echo "mode=$m"; bash "$S/scripts/jr.sh" "$R/comment-$m" "$C/a.py" "$C/b.py" -k 30 -l 3 --mode $m
done
