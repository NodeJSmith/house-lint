#!/usr/bin/env bash
# Run jscpd 5.4.1 and PMD CPD 7.26.0 over the same explicit hassette file list; time each run.
set -uo pipefail
H="${HASSETTE:?set HASSETTE to a hassette checkout}"
F="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
J="${JSCPD:-jscpd}"
PMD="${PMD:-pmd}"
O=${OUT:-$F/results}/hassette
mkdir -p "$O"
cd "$H" || exit 1

prune='( -name __pycache__ -o -name node_modules -o -name snapshots ) -prune'
# shellcheck disable=SC2086
find src tests client wire $prune -o -type f -name '*.py' -print | sort >"$O/py.txt"
# shellcheck disable=SC2086
find frontend/src $prune -o -type f \( -name '*.ts' -o -name '*.tsx' \) \
  ! -name '*.d.ts' ! -name 'generated-types.ts' ! -name 'ws-types.ts' ! -name '*.generated.ts' -print | sort >"$O/ts.txt"
cat "$O/py.txt" "$O/ts.txt" >"$O/all.txt"
echo "files: py=$(wc -l <"$O/py.txt") ts=$(wc -l <"$O/ts.txt")"
echo "lines: py=$(xargs cat <"$O/py.txt" | wc -l) ts=$(xargs cat <"$O/ts.txt" | wc -l)"

# jscpd config with explicit path list (avoids command-line length limits)
python3 -I -c 'import json,sys; print(json.dumps({"path": [sys.argv[2] + "/" + l.strip() for l in open(sys.argv[1]) if l.strip()]}))' "$O/all.txt" "$H" >"$O/jscpd-paths.json"

run_jscpd() {
  local name=$1; shift
  local o=$O/jscpd-$name
  rm -rf "$o"; mkdir -p "$o"
  local t0 t1
  t0=$(date +%s.%N)
  "$J" -c "$O/jscpd-paths.json" --no-gitignore --reporters json,silent --output "$o" --no-tips "$@" >"$o/stdout.txt" 2>"$o/stderr.txt"
  local rc=$?
  t1=$(date +%s.%N)
  printf 'jscpd %-14s rc=%s wall=%.2fs\n' "$name" "$rc" "$(echo "$t1 - $t0" | bc)"
}
run_jscpd default
run_jscpd default-again
run_jscpd k20 --min-tokens 20 --min-lines 5
run_jscpd norm --ignore-identifiers --ignore-literals
run_jscpd gap --max-gap-lines 2
run_jscpd sim --similarity

run_pmd() {
  local lang=$1 list=$2 k=$3
  local o=$O/pmd-$lang-k$k.xml
  local t0 t1
  t0=$(date +%s.%N)
  "$PMD" cpd --language "$lang" --minimum-tokens "$k" --format xml --no-fail-on-violation --file-list "$list" >"$o" 2>"$o.err"
  local rc=$?
  t1=$(date +%s.%N)
  printf 'pmd   %-10s k=%-3s rc=%s wall=%.2fs\n' "$lang" "$k" "$rc" "$(echo "$t1 - $t0" | bc)"
}
run_pmd python "$O/py.txt" 20
run_pmd typescript "$O/ts.txt" 20
run_pmd python "$O/py.txt" 50
run_pmd typescript "$O/ts.txt" 50
