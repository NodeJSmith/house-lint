#!/usr/bin/env bash
# hassette: jscpd default thresholds with --mode weak (comments skipped).
F="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
H="${HASSETTE:?set HASSETTE to a hassette checkout}"
J="${JSCPD:-jscpd}"
O=${OUT:-$F/results}/hassette
G="${GATE_CLUSTERS:?set GATE_CLUSTERS to the PMD gate cluster listing (cpd-plain.txt)}"
cd /tmp || exit 1
mkdir -p "$O/abs-weak"
"$J" -c "$O/jscpd-paths.json" --no-gitignore --absolute --mode weak --reporters json,silent --output "$O/abs-weak" --no-tips >/dev/null 2>&1
python3 -I "$F/scripts/clusters.py" "$O/abs-weak/jscpd-report.json" --root "$H"/ | head -5
python3 -I "$F/scripts/compare_gate.py" "$G" "$O/abs-weak/jscpd-report.json" "$H"/
