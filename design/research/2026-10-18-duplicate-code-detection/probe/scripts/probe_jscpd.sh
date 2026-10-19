#!/usr/bin/env bash
# Run jscpd 5.4.1 over each probe pair under several modes; summarize JSON duplicates.
set -uo pipefail
F="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
J="${JSCPD:-jscpd}"
P=$F/corpus/pairs
MINTOK=${MINTOK:-30}
OUT=${OUT:-$F/results}/jscpd-k$MINTOK
mkdir -p "$OUT"
declare -A MODES=(
  [default]=""
  [norm]="--ignore-identifiers --ignore-literals"
  [gap]="--max-gap-lines 2"
  [normgap]="--ignore-identifiers --ignore-literals --max-gap-lines 2"
  [sim]="--similarity"
)
for lang in py ts; do
  for case in ${CASES:-exact renamed literal insert delete sigfp sigml}; do
    for mode in default norm gap normgap sim; do
      o=$OUT/$lang-$case-$mode
      rm -rf "$o"; mkdir -p "$o"
      # shellcheck disable=SC2086
      "$J" "$P/$lang/$case/a.$lang" "$P/$lang/$case/b.$lang" --min-tokens "$MINTOK" --min-lines 3 --no-gitignore \
        --reporters json,silent --output "$o" --no-tips ${MODES[$mode]} >"$o/stdout.txt" 2>"$o/stderr.txt"
      if [ -f "$o/jscpd-report.json" ]; then
        summary=$(jq -r '[.duplicates[] | "\(.firstFile.name | split("/") | last):\(.firstFile.start)-\(.firstFile.end)~\(.secondFile.name | split("/") | last):\(.secondFile.start)-\(.secondFile.end) [\(.kind // .type // "?") tok=\(.tokens // "?")]"] | if length==0 then "NO MATCH" else join(" ; ") end' "$o/jscpd-report.json")
      else
        summary="(no report) $(head -c 200 "$o/stderr.txt")"
      fi
      printf '%-3s %-8s %-8s %s\n' "$lang" "$case" "$mode" "$summary"
    done
  done
done
