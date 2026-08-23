#!/usr/bin/env python3
"""Find external contributors between two git tags.

Usage:
    uv run python scripts/release_contributors.py v0.1.0 v0.2.0
    uv run python scripts/release_contributors.py v0.1.0 HEAD
    uv run python scripts/release_contributors.py              # auto: second-latest tag..latest tag

Scans git log for authors and `Co-authored-by:` trailers that are not the repo
owner or bots, and prints each contributor with their associated PRs.
"""

import argparse
import re
import subprocess
import sys

OWNER_EMAILS = frozenset(
    {
        "8505845+NodeJSmith@users.noreply.github.com",
        "12jessicasmith34@gmail.com",
    }
)

OWNER_NAMES = frozenset(
    {
        "Jessica Smith",
    }
)

BOT_PATTERNS = re.compile(r"\[bot\]|dependabot|renovate", re.IGNORECASE)

CO_AUTHOR_PATTERN = re.compile(
    r"^co-authored-by:\s*(?P<name>.+?)\s*<(?P<email>[^<>]+)>\s*$",
    re.IGNORECASE | re.MULTILINE,
)

RECORD_SEP = "\x1e"
FIELD_SEP = "\x1f"


def git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(result.stderr.strip(), file=sys.stderr)
        raise SystemExit(1)
    return result.stdout.strip()


def get_latest_tags(count: int = 2) -> list[str]:
    output = git("tag", "--sort=-v:refname", "--list", "v*")
    return output.splitlines()[:count]


def get_commits(from_ref: str, to_ref: str) -> list[dict[str, str]]:
    log_format = f"%an{FIELD_SEP}%ae{FIELD_SEP}%s{FIELD_SEP}%b{RECORD_SEP}"
    output = git("log", f"--format={log_format}", f"{from_ref}..{to_ref}")
    if not output:
        return []

    commits = []
    for record in output.split(RECORD_SEP):
        record = record.strip("\n")
        if not record:
            continue
        parts = record.split(FIELD_SEP, 3)
        if len(parts) != 4:
            continue
        name, email, subject, body = parts
        commits.append({"name": name, "email": email, "subject": subject, "body": body})
    return commits


def is_external(name: str, email: str) -> bool:
    if name in OWNER_NAMES or email in OWNER_EMAILS:
        return False
    return not (BOT_PATTERNS.search(name) or BOT_PATTERNS.search(email))


def parse_github_username(email: str) -> str | None:
    m = re.match(r"(?:\d+\+)?(.+)@users\.noreply\.github\.com", email)
    return m.group(1) if m else None


def add_contributor(
    contributors: dict[str, list[dict[str, str]]], name: str, email: str, subject: str
) -> None:
    entry = {"subject": subject, "email": email}
    contributors.setdefault(name, []).append(entry)


def find_external_contributors(from_ref: str, to_ref: str) -> dict[str, list[dict[str, str]]]:
    commits = get_commits(from_ref, to_ref)
    contributors: dict[str, list[dict[str, str]]] = {}

    for commit in commits:
        if is_external(commit["name"], commit["email"]):
            add_contributor(contributors, commit["name"], commit["email"], commit["subject"])

        for match in CO_AUTHOR_PATTERN.finditer(commit["body"]):
            co_name, co_email = match["name"], match["email"]
            if is_external(co_name, co_email):
                add_contributor(contributors, co_name, co_email, commit["subject"])

    return contributors


def print_contributors(
    contributors: dict[str, list[dict[str, str]]],
    from_ref: str,
    to_ref: str,
) -> None:
    if not contributors:
        print(f"No external contributors found in {from_ref}..{to_ref}")
        return

    print(f"External contributors in {from_ref}..{to_ref}:\n")
    for name, entries in sorted(contributors.items()):
        username = None
        for entry in entries:
            username = parse_github_username(entry["email"])
            if username:
                break

        display = f"@{username}" if username else name
        print(f"  {display}")
        for entry in entries:
            print(f"    - {entry['subject']}")
        print()


def main() -> int:
    parser = argparse.ArgumentParser(description="Find external contributors between git tags.")
    parser.add_argument("from_ref", nargs="?", help="Start ref (default: second-latest tag)")
    parser.add_argument("to_ref", nargs="?", default="HEAD", help="End ref (default: HEAD)")
    args = parser.parse_args()

    if args.from_ref is None:
        tags = get_latest_tags(2)
        if len(tags) < 1:
            print("No tags found. Provide explicit refs.", file=sys.stderr)
            return 1
        if len(tags) < 2:
            latest = tags[0]
            args.from_ref = latest
            print(f"Only one tag found, scanning {args.from_ref}..HEAD\n")
        else:
            latest, previous = tags[0], tags[1]
            args.from_ref = previous
            args.to_ref = latest
            print(f"Auto-detected range: {args.from_ref}..{args.to_ref}\n")

    contributors = find_external_contributors(args.from_ref, args.to_ref)
    print_contributors(contributors, args.from_ref, args.to_ref)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
