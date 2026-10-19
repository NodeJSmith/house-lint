"""Cluster pairwise clone reports (jscpd JSON or PMD CPD XML) into groups.

Fragments in the same file whose line ranges overlap are merged into one node, then pairs are
union-found into clusters. Prints summary counts and, with --list, every cluster.

usage: python -I clusters.py <report> [--root PREFIX] [--list] [--min-occ N] [--kind K]
"""

import json
import sys
import xml.etree.ElementTree as ET
from collections import defaultdict
from pathlib import Path


def load_pairs(path: Path, kind_filter: str | None) -> list[tuple[tuple[str, int, int], tuple[str, int, int], int, str]]:
    pairs = []
    if path.suffix == ".json":
        data = json.loads(path.read_text())
        for dup in data["duplicates"]:
            kind = dup.get("kind", "?")
            if kind_filter and kind != kind_filter:
                continue
            a, b = dup["firstFile"], dup["secondFile"]
            pairs.append(((a["name"], a["start"], a["end"]), (b["name"], b["start"], b["end"]), dup.get("tokens", 0), kind))
        return pairs
    root = ET.fromstring(path.read_text())
    for dup in root.iter():
        if not dup.tag.endswith("duplication"):
            continue
        files = [f for f in dup if f.tag.endswith("file")]
        locs = [(f.get("path"), int(f.get("line")), int(f.get("endline"))) for f in files]
        for other in locs[1:]:
            pairs.append((locs[0], other, int(dup.get("tokens", 0)), "cpd"))
    return pairs


class Nodes:
    def __init__(self) -> None:
        self.by_file: dict[str, list[list[int]]] = defaultdict(list)

    def add(self, name: str, start: int, end: int) -> None:
        self.by_file[name].append([start, end])

    def merged(self) -> dict[str, list[tuple[int, int]]]:
        out = {}
        for name, spans in self.by_file.items():
            spans.sort()
            merged: list[list[int]] = []
            for s, e in spans:
                if merged and s <= merged[-1][1]:
                    merged[-1][1] = max(merged[-1][1], e)
                else:
                    merged.append([s, e])
            out[name] = [(s, e) for s, e in merged]
        return out


def find_node(merged: dict[str, list[tuple[int, int]]], name: str, start: int) -> tuple[str, int, int]:
    for s, e in merged[name]:
        if s <= start <= e:
            return (name, s, e)
    raise KeyError((name, start))


def main() -> None:
    args = sys.argv[1:]
    path = Path(args[0])
    root = args[args.index("--root") + 1] if "--root" in args else ""
    min_occ = int(args[args.index("--min-occ") + 1]) if "--min-occ" in args else 2
    kind = args[args.index("--kind") + 1] if "--kind" in args else None
    pairs = load_pairs(path, kind)
    nodes = Nodes()
    for a, b, _, _ in pairs:
        nodes.add(*a)
        nodes.add(*b)
    merged = nodes.merged()
    parent: dict[tuple[str, int, int], tuple[str, int, int]] = {}

    def find(x: tuple[str, int, int]) -> tuple[str, int, int]:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for a, b, _, _ in pairs:
        na, nb = find_node(merged, a[0], a[1]), find_node(merged, b[0], b[1])
        parent[find(na)] = find(nb)
    groups: dict[tuple[str, int, int], list[tuple[str, int, int]]] = defaultdict(list)
    for n in parent:
        groups[find(n)].append(n)
    clusters = [sorted(g) for g in groups.values()]
    kinds = defaultdict(int)
    for _, _, _, k in pairs:
        kinds[k] += 1
    print(f"pairs={len(pairs)} kinds={dict(kinds)} clusters={len(clusters)}")
    for n in (2, 3, 4):
        print(f"  clusters with >= {n} occurrences: {sum(1 for c in clusters if len(c) >= n)}")
    area = defaultdict(int)
    for c in clusters:
        for name, _, _ in c:
            rel = name.removeprefix(root)
            area[rel.split("/")[0] if "/" in rel else rel] += 1
    print(f"  fragments by top dir: {dict(sorted(area.items(), key=lambda kv: -kv[1]))}")
    if "--list" in args:
        for i, c in enumerate(sorted(clusters, key=lambda c: (-len(c), c)), 1):
            if len(c) < min_occ:
                continue
            print(f"\n#{i} ({len(c)})")
            for name, s, e in c:
                print(f"    {name.removeprefix(root)}:{s}-{e}")


if __name__ == "__main__":
    main()
