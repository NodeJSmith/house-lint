#!/usr/bin/env bash
# Run PMD CPD 7.26.0 over each probe pair (py + ts), default vs ignore flags.
set -uo pipefail
F="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PMD="${PMD:-pmd}"
P=$F/corpus/pairs
S=$F/scripts
MINTOK=${MINTOK:-30}
OUT=${OUT:-$F/results}/cpd-k$MINTOK
mkdir -p "$OUT"
for lang in py ts; do
  if [ "$lang" = py ]; then L=python; else L=typescript; fi
  for case in ${CASES:-exact renamed literal insert delete sigfp sigml}; do
    for mode in default norm; do
      flags=""
      [ "$mode" = norm ] && flags="--ignore-identifiers --ignore-literals"
      o=$OUT/$lang-$case-$mode.xml
      # shellcheck disable=SC2086
      "$PMD" cpd --language "$L" --minimum-tokens "$MINTOK" --format xml --no-fail-on-violation $flags \
        "$P/$lang/$case/a.$lang" "$P/$lang/$case/b.$lang" >"$o" 2>"$o.err"
      printf '%-3s %-8s %-8s %s\n' "$lang" "$case" "$mode" "$(python3 -I "$S/cpd_summary.py" "$o")"
    done
  done
done
