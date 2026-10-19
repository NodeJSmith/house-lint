"""How many of hassette's CPD-gate clusters (cpd-plain.txt) does a jscpd report also flag?

A gate cluster counts as 'seen' when at least two of its occurrences overlap jscpd clone fragments
(any kind). Also reports line-coverage overlap.

usage: python -I compare_gate.py <cpd-plain.txt> <jscpd-report.json> <repo_root_prefix>
"""

import json
import re
import sys
from collections import defaultdict
from pathlib import Path


def gate_clusters(path: Path) -> list[list[tuple[str, int, int]]]:
    clusters, cur = [], []
    for line in path.read_text().splitlines():
        m = re.match(r"\s+(\S+):(\d+)-(\d+)$", line)
        if m:
            cur.append((m.group(1), int(m.group(2)), int(m.group(3))))
        elif cur:
            clusters.append(cur)
            cur = []
    if cur:
        clusters.append(cur)
    return clusters


def main() -> None:
    gate = gate_clusters(Path(sys.argv[1]))
    data = json.loads(Path(sys.argv[2]).read_text())
    prefix = sys.argv[3]
    frags: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for dup in data["duplicates"]:
        for side in ("firstFile", "secondFile"):
            f = dup[side]
            frags[f["name"].removeprefix(prefix)].append((f["start"], f["end"]))

    def overlaps(p: str, s: int, e: int) -> bool:
        return any(fs <= e and s <= fe for fs, fe in frags.get(p, ()))

    seen = 0
    by_area = defaultdict(lambda: [0, 0])
    unseen = []
    for c in gate:
        hits = sum(1 for p, s, e in c if overlaps(p, s, e))
        area = c[0][0].split("/")[0]
        by_area[area][1] += 1
        if hits >= 2:
            seen += 1
            by_area[area][0] += 1
        else:
            unseen.append(c)
    print(f"gate clusters={len(gate)} seen-by-jscpd={seen} ({seen / len(gate):.0%})")
    print("  by top dir (seen/total):", {k: f"{v[0]}/{v[1]}" for k, v in by_area.items()})
    if "--unseen" in sys.argv:
        for c in unseen[:40]:
            print("   UNSEEN:", ", ".join(f"{p}:{s}-{e}" for p, s, e in c[:4]))


if __name__ == "__main__":
    main()
