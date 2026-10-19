#!/usr/bin/env bash
# Re-run jscpd variants with --absolute paths, timing each 3x, then cluster.
F="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
J="${JSCPD:-jscpd}"
O=${OUT:-$F/results}/hassette
H="${HASSETTE:?set HASSETTE to a hassette checkout}/"
cd /tmp || exit 1
uptime
run() {
  local name=$1; shift
  local o=$O/abs-$name
  rm -rf "$o"; mkdir -p "$o"
  for i in 1 2 3; do
    local t0 t1
    t0=$(date +%s.%N)
    "$J" -c "$O/jscpd-paths.json" --no-gitignore --absolute --reporters json,silent --output "$o" --no-tips "$@" >"$o/stdout.txt" 2>"$o/stderr.txt"
    t1=$(date +%s.%N)
    printf '%s run%s wall=%.2fs\n' "$name" "$i" "$(echo "$t1 - $t0" | bc)"
  done
  python3 -I "$F/scripts/clusters.py" "$o/jscpd-report.json" --root "$H" | head -4
  python3 -I "$F/scripts/clusters.py" "$o/jscpd-report.json" --root "$H" --list >"$O/clusters-abs-$name.txt"
}
run default
run norm --ignore-identifiers --ignore-literals
run sim --similarity
run gap --max-gap-lines 2
