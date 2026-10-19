#!/usr/bin/env bash
# jr.sh <outdir> <jscpd args...>: run jscpd 5.4.1 with a JSON report and print one line per clone.
set -uo pipefail
J="${JSCPD:-jscpd}"
o=$1; shift
mkdir -p "$o"
"$J" "$@" --reporters json,silent --output "$o" --no-tips >"$o/stdout.txt" 2>"$o/stderr.txt"
echo "exit=$?"
jq -c '.duplicates[] | {k:.kind, a:"\(.firstFile.name):\(.firstFile.start)-\(.firstFile.end)", b:"\(.secondFile.name):\(.secondFile.start)-\(.secondFile.end)", t:.tokens}' "$o/jscpd-report.json" 2>/dev/null | head -${JR_HEAD:-20}
