"""Summarize a PMD CPD XML report as 'file:start-end ~ file:start-end [tokens]' per duplication."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

NS = "{https://pmd-code.org/schema/cpd-report}"


def main(path: str) -> None:
    text = Path(path).read_text()
    if not text.strip():
        print("NO REPORT")
        return
    root = ET.fromstring(text)
    dups = [el for el in root.iter() if el.tag.endswith("duplication")]
    if not dups:
        print("NO MATCH")
        return
    parts = []
    for dup in dups:
        locs = []
        for f in dup:
            if not f.tag.endswith("file"):
                continue
            name = Path(f.get("path", "?")).name
            locs.append(f"{name}:{f.get('line')}-{f.get('endline')}")
        parts.append(" ~ ".join(locs) + f" [tok={dup.get('tokens')} lines={dup.get('lines')}]")
    print(" ; ".join(parts))


if __name__ == "__main__":
    main(sys.argv[1])
