#!/usr/bin/env bash
# Cluster every hassette report (jscpd JSON + PMD XML) and save full listings.
F="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
O=${OUT:-$F/results}/hassette
H="${HASSETTE:?set HASSETTE to a hassette checkout}/"
for r in "$O"/jscpd-*/jscpd-report.json "$O"/pmd-*.xml; do
  name=$(basename "$(dirname "$r")")
  [ "${r##*.}" = xml ] && name=$(basename "$r" .xml)
  echo "== $name"
  python3 -I "$F/scripts/clusters.py" "$r" --root "$H" | head -6
  python3 -I "$F/scripts/clusters.py" "$r" --root "$H" --list >"$O/clusters-$name.txt"
done
echo "== jscpd-sim, kind=similar only"
python3 -I "$F/scripts/clusters.py" "$O/jscpd-sim/jscpd-report.json" --root "$H" --kind similar | head -6
echo "== jscpd-norm, kind=renamed only"
python3 -I "$F/scripts/clusters.py" "$O/jscpd-norm/jscpd-report.json" --root "$H" --kind renamed | head -6
