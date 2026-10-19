#!/usr/bin/env bash
# Assemble probe pair directories: pairs/<lang>/<case>/{a,b}.<ext>
set -euo pipefail
F="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
root=$F/corpus
src=$root/src
cp "$root/py/exact/a.py" "$src/base.py"
rm -rf "$root/pairs"
for lang in py ts; do
  for case in exact renamed literal insert delete; do
    d=$root/pairs/$lang/$case
    mkdir -p "$d"
    cp "$src/base.$lang" "$d/a.$lang"
    if [ "$case" = exact ]; then cp "$src/base.$lang" "$d/b.$lang"; else cp "$src/$case.$lang" "$d/b.$lang"; fi
  done
  d=$root/pairs/$lang/sigfp
  mkdir -p "$d"
  cp "$src/sig_a.$lang" "$d/a.$lang"
  cp "$src/sig_b.$lang" "$d/b.$lang"
  d=$root/pairs/$lang/sigml
  mkdir -p "$d"
  cp "$src/sigml_a.$lang" "$d/a.$lang"
  cp "$src/sigml_b.$lang" "$d/b.$lang"
done
find "$root/pairs" -type f | sort
