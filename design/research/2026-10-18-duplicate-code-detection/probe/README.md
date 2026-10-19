# Duplicate-code probe

Inputs and scripts behind the measurements in `../followup-tool-selection.md` and `../research.md`.

## Contents

- `corpus/`: the hand-built probe corpus. `pairs/{py,ts}/<case>/{a,b}.*` holds one file pair per case (exact, renamed, literal, insert, delete, signature cases). `src/` holds the sources `scripts/build_pairs.sh` assembles them from. `comment/`, `supp/` and `blind/` hold the comment-handling, suppression-marker and blind-identifier-normalization inputs. The corpus is deliberately duplicated code, and house-lint's own config (`pyproject.toml`) excludes `design/`.
- `cpdprobe/`, `cpdprobe2/`, `cpdprobe3/`: the small inputs for the PMD CPD 7.26.0 probes cited in `../research.md`.
- `scripts/`: the probe, run and clustering scripts. `jr.sh` and `cpd_summary.py` are helpers the others call.

## Running

The scripts find the probe directory from their own location and write to `results/` (override with `OUT`). `results/` is gitignored and was not retained. Rerun the scripts to regenerate it.

Several scripts `cd` before using their inputs, so give every path variable below as an absolute path. The scripts also need `jq`, `bc` and `python3`.

Environment variables:

- `JSCPD`: the jscpd binary. Defaults to `jscpd` on `PATH`. The briefs used jscpd 5.4.1.
- `PMD`: the PMD launcher. Defaults to `pmd` on `PATH`. The briefs used PMD 7.26.0.
- `HASSETTE`: a hassette checkout. Required by the `hassette_*.sh` scripts.
- `GATE_CLUSTERS`: hassette's PMD gate cluster listing (`cpd-plain.txt`, one cluster per block of `path:start-end` lines). Required by `hassette_nogen.sh` and `hassette_weak.sh`, and not committed.
- `MINTOK`: minimum token count for `probe_jscpd.sh` and `probe_cpd.sh`. Defaults to 30.

`hassette_run.sh` must run before the other `hassette_*.sh` scripts, since it writes the file lists they reuse.
