"""Randomized differential parity between house-lint's discovery and real `git check-ignore`.

`test_gitignore_parity.py` pins a curated table of pattern shapes someone thought to write down.
This generates combinations nobody thought of, and measures how often they disagree with git.

Two things come out of it. The divergence *direction* is the hard guarantee, asserted for every
distribution below: a disagreement may leave house-lint linting a file git would ignore, but must
never leave it skipping a file git would lint. Over-linting is visible and fixable in one
`exclude` line; under-linting is silent, and a linter that silently skips a file has failed at its
only job. The divergence *rate* is the softer one — a tripwire under a documented ceiling, so a
`pathspec` bump or an edit to the per-directory matcher shows up as a failure rather than as drift
nobody measured.

The rate is meaningless without the distribution that produced it, which is why three are
declared rather than one. `docs/configuration.md` quotes these; regenerate with
`CI=1 uv run pytest -s tests/integration/test_gitignore_fuzz.py` and update both together. The
no-negation case is the load-bearing one: the known divergence cannot occur without a negation,
and that is what pins it.

Thousands of real `git check-ignore` calls is worth the wait in CI but not on every local
`pytest`, so this runs when `CI` is set and skips otherwise — no marker to select and no flag to
remember, in either direction. Every CI provider sets `CI`, GitHub Actions included, so the
workflow needs no configuration for this and cannot silently stop running it by drifting out of
sync with a flag. Locally, prefix any invocation with `CI=1`.

One repository is initialised per distribution and its `.gitignore` files are rewritten per trial
— `git check-ignore` needs a repository, not a commit, so per-trial `git init` would be overhead.
"""

import os
import random
import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
from _git_harness import git_ignored, init_repository

from house_lint.discovery import discover_files

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("CI"), reason="randomized suite; set CI=1 to run it locally"
    ),
    pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed"),
]

TRIALS = 1500
# Fixed so a reported rate is reproducible and a regression is bisectable. Change it only to
# widen coverage deliberately, never to make a failing run pass.
SEED = 20260820

# A tree deep and wide enough that anchoring, directory-only patterns, and negation inside a
# nested directory all have somewhere to bite, but small enough that a trial stays cheap.
TREE = (
    "src/a.py",
    "src/b.py",
    "src/sub/a.py",
    "src/sub/b.py",
    "src/sub/deep/a.py",
    "src/other/a.py",
    "src/other/deep/b.py",
)
IGNORE_OWNERS = ("", "src", "src/sub")

# Plain names and globs, the shape an actual project's .gitignore is made of.
ORDINARY_BODIES = (
    "*.py",
    "*.pyc",
    "a.py",
    "b.py",
    "build",
    "dist",
    "sub",
    "other",
    "deep",
    "src/other",
    "/a.py",
    "sub/",
    "other/",
    "build/",
)
# The curated table's shapes recombined freely: anchored and unanchored, directory-only and not,
# with and without `**`. Deliberately unrepresentative — this is the corner-hunting pool.
#
# `**/**` and `**/**/` earn their place separately from the single-`**` entries above them. Every
# other body here contains at most one `**`, so no combination this file generated ever reached
# `_prefix_pattern`'s two-`**` path — a gap that hid a real under-linting bug through several
# rounds of review until someone read the rewrite by hand. Composing a token with itself is the
# cheap generalisation of "one of these", and the class it covers is exactly the one the
# generator was blind to.
CORNER_BODIES = (
    *ORDINARY_BODIES,
    "**",
    "**/",
    "**/a.py",
    "**/**",
    "**/**/",
    "sub/**",
    "deep/**/",
    "/sub",
    "a.py/",
    "sub/a.py",
    "src/sub",
    "deep/",
)


@dataclass(frozen=True)
class Distribution:
    """One way of generating `.gitignore` content, and the divergence rate it is allowed."""

    name: str
    negation_rate: float
    bodies: tuple[str, ...]
    max_divergence_rate: float


DISTRIBUTIONS = (
    # The known divergence requires a negation to re-include something under a broader ignore.
    # With no negation in play there is nothing to diverge about, so this ceiling is exactly zero
    # — the strongest of the three, and the one that would catch a genuinely new class of bug.
    Distribution("no-negation", 0.0, ORDINARY_BODIES, 0.0),
    # Observed 0/1500 for both distributions with the per-directory matcher (SEED is fixed, so
    # this is reproducible, not a lucky sample) — ceilings lowered to match rather than left at
    # their pre-fix slack.
    Distribution("typical", 0.05, ORDINARY_BODIES, 0.0),
    Distribution("adversarial", 0.30, CORNER_BODIES, 0.0),
)


@dataclass(frozen=True)
class Divergence:
    """One disagreement with git, kept with enough context to reproduce it by hand."""

    ignores: dict[str, tuple[str, ...]]
    skipped_by_git_only: frozenset[str]
    skipped_by_house_lint_only: frozenset[str]

    def render(self) -> str:
        rules = "; ".join(
            f"{owner or '<root>'}/.gitignore={list(lines)}" for owner, lines in self.ignores.items()
        )
        return (
            f"{rules} -> house-lint wrongly skips {sorted(self.skipped_by_house_lint_only)}, "
            f"wrongly lints {sorted(self.skipped_by_git_only)}"
        )


def _build_tree(root: Path) -> None:
    for relative in TREE:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x = 1\n")


def _random_rules(rng: random.Random, distribution: Distribution) -> dict[str, tuple[str, ...]]:
    """Pick one to three `.gitignore` files, each with one to four patterns."""
    owners = rng.sample(IGNORE_OWNERS, k=rng.randint(1, len(IGNORE_OWNERS)))
    rules: dict[str, tuple[str, ...]] = {}
    for owner in owners:
        bodies = rng.choices(distribution.bodies, k=rng.randint(1, 4))
        rules[owner] = tuple(
            f"!{body}" if rng.random() < distribution.negation_rate else body for body in bodies
        )
    return rules


def _write_rules(root: Path, rules: dict[str, tuple[str, ...]]) -> None:
    for owner in IGNORE_OWNERS:
        path = (root / owner / ".gitignore") if owner else (root / ".gitignore")
        if owner in rules:
            path.write_text("\n".join(rules[owner]) + "\n")
        else:
            path.unlink(missing_ok=True)


def _house_lint_skipped(root: Path) -> set[str]:
    result = discover_files(root, include=("src",))
    selected = {path.relative_to(root.resolve()).as_posix() for path in result.files}
    return {relative for relative in TREE if relative not in selected}


@pytest.fixture(scope="module", params=DISTRIBUTIONS, ids=lambda item: item.name)
def trial_run(
    request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory
) -> tuple[Distribution, tuple[Divergence, ...]]:
    """Run every trial for one distribution; both assertions below read the same result set."""
    distribution: Distribution = request.param
    root = tmp_path_factory.mktemp(f"fuzz-{distribution.name}")
    _build_tree(root)
    init_repository(root)
    rng = random.Random(SEED)

    found: list[Divergence] = []
    for _ in range(TRIALS):
        rules = _random_rules(rng, distribution)
        _write_rules(root, rules)
        git_skipped = git_ignored(root, TREE)
        house_lint_skipped = _house_lint_skipped(root)
        if house_lint_skipped != git_skipped:
            found.append(
                Divergence(
                    rules,
                    frozenset(git_skipped - house_lint_skipped),
                    frozenset(house_lint_skipped - git_skipped),
                )
            )
    # Producing this number is the harness's job, so it is reported rather than only asserted on:
    # `CI=1 uv run pytest -s <this file>` is how docs/configuration.md's figures get regenerated.
    print(
        f"\n[gitignore-fuzz] {distribution.name}: {len(found)}/{TRIALS} diverge "
        f"({len(found) / TRIALS:.2%}), ceiling {distribution.max_divergence_rate:.0%}"
    )
    return distribution, tuple(found)


def test_no_divergence_ever_skips_a_file_git_would_lint(
    trial_run: tuple[Distribution, tuple[Divergence, ...]],
) -> None:
    """The safety property, and the only one here that is not merely a quality target.

    A file house-lint skips is a file it reports nothing about, and `0 findings` is
    indistinguishable from a clean run. A file it lints that git ignores is at worst noise the
    user can see and silence. It is the property the surveyed alternatives (`igittigitt`,
    `dulwich.ignore`) fail.

    The known directory-negation defect that used to carve out an exemption here (a negated
    directory-only pattern winning against git's own verdict) no longer exists: the per-directory
    matcher decides directory-only negations correctly instead of delegating whole-path matching
    to pathspec's aggregate spec. There is no longer a tolerated class — any under-linting
    divergence is a hard failure.
    """
    _, divergences = trial_run
    unsafe = [item for item in divergences if item.skipped_by_house_lint_only]

    assert not unsafe, "discovery skipped files git would lint:\n" + "\n".join(
        item.render() for item in unsafe[:10]
    )


def test_divergence_rate_stays_within_its_documented_ceiling(
    trial_run: tuple[Distribution, tuple[Divergence, ...]],
) -> None:
    """Backs the rates `docs/configuration.md` quotes. Update both together, never one alone."""
    distribution, divergences = trial_run
    rate = len(divergences) / TRIALS

    assert rate <= distribution.max_divergence_rate, (
        f"{distribution.name}: {len(divergences)}/{TRIALS} ({rate:.2%}) of generated combinations "
        f"diverge from git, above the {distribution.max_divergence_rate:.0%} ceiling recorded here "
        f"and in docs/configuration.md. Examples:\n"
        + "\n".join(item.render() for item in divergences[:10])
    )
