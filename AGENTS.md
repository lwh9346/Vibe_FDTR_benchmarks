# FDTR Benchmark Runner

## Architecture

This repo is the **benchmark runner** for evaluating AI coding agents on FDTR thermoreflectance tasks. It does not contain the FDTR physics code — that lives in the `Vibe_FDTR/` git submodule.

```
fdtr-bench CLI  →  Docker containers (3 tiers)  →  opencode agent  →  output artifacts  →  metrics evaluation
```

- `src/fdtr_bench/` — Benchmark runner (`__main__.py`, `runner.py`, `config.py`, `metrics.py`, `trace.py`, `summary.py`, `prompts.py`, `models.py`)
- `tasks/` — 28 benchmark task YAML definitions (L1–L3 levels)
- `inputs/` — Measurement data files (copied into Docker containers at `/app/data/`)
- `configs/` — Model profiles (`models.yaml`), container image tags (`container.yaml`), prompt templates (`prompts.yaml`)
- `docker/` — Dockerfiles and build script for the 3 tier images
- `Vibe_FDTR/` — Git submodule (github.com:yfwsunny/Vibe_FDTR), the toolkit under test

## Configuration

`configs/models.yaml` contains API keys — gitignored. Copy `models.example.yaml` → `models.yaml` and fill in real keys.

`configs/container.yaml` maps tier names to Docker image tags. Must match images built by `docker/build.sh`.

## Commands

```bash
# Install the runner (not the FDTR toolkit)
uv sync

# Run benchmarks (-s and -b are repeatable; one flag per value)
uv run fdtr-bench run -b L1-F01 -s full -j 2
uv run fdtr-bench run -b L1-F01 -s full -s code -s minimal --repeat 3 --tag v1

# Generate results markdown table
uv run fdtr-bench summary           # all sessions
uv run fdtr-bench summary 20260623_120000  # specific session

# Clean stale Docker containers
uv run fdtr-bench clean --dry-run   # preview
uv run fdtr-bench clean             # actually remove
```

### CLI flag semantics

**`-b` and `-s` are independent accumulators — the runner does a Cartesian product of ALL `-b` × ALL `-s`.** There is no positional pairing.

```
uv run fdtr-bench run -b L1-F01 -b L2-D01 -s full -s code
→ (L1-F01, L2-D01) × (full, code) = 4 tasks

uv run fdtr-bench run -b L1-F01 -s full -b L3-01 -s code
→ (L1-F01, L3-01) × (full, code) = 4 tasks (NOT L1×full + L3×code)
```

**To run different tier sets for different benchmark groups, split into separate invocations:**

```bash
# L1+L2 × all 3 tiers
uv run fdtr-bench run -b L1-F01 ... L2-B02 -s full -s code -s minimal -j 50
# L3 × full only (advisory tasks, meaningless with code/minimal)
uv run fdtr-bench run -b L3-01 ... L3-12 -s full -j 50
```

Duplicate `-s` values are NOT deduplicated — `-s full -s full` produces 2× runs per task.

## Docker Image Build

```bash
# Build all 3 tiers
bash docker/build.sh
```

Build context is the repo root. The Vibe_FDTR submodule must be initialized first (`git submodule update --init`).

Three tiers:
| Tier | Image pattern | What's available |
|------|--------------|-----------------|
| `full` | `vibe-fdtr:full-{ver}` | Full FDTR package, docs, skills, examples |
| `code` | `vibe-fdtr:code-{ver}` | Only `src/` Python source, no docs/skills |
| `minimal` | `vibe-fdtr:minimal-{ver}` | Only numpy/scipy/matplotlib, no FDTR at all |

Image internals worth knowing (set in `docker/*.Dockerfile`):
- **ripgrep is required** — opencode's `skill`/`glob`/`grep` tools shell out to `rg`. Without it they fail with `ripgrep execution failed` and stall ~130s each.
- **`UV_NO_SYNC=1`** on full/code so the first `uv run fdtr ...` doesn't rebuild `fdtr-toolkit` (was the root cause of a 60s `scan-data` stall). minimal sets `UV_NO_SYNC=0` (it's no longer a uv project).
- Skills live at `/app/.opencode/skills/` (opencode's project-skill path), copied directly from the submodule's `.claude/skills/`.
- `docker/opencode.json` permissions: `webfetch`/`websearch`/`question` are `deny` (non-interactive, no network); everything else `allow`.

## Task Definition Notes

Each task YAML in `tasks/` requires:
- `timeout` (seconds) — required field, runner validates this
- `prompt` — the task instruction sent to the agent
- `outputs` (optional) — list of files to collect and how to grade them. Advisory tasks (most L3) omit it and are run without scoring.

Each `outputs` entry has a `compare` mode:
- `keyvalue` — agent writes a flat YAML of `key: value`; graded against `groundtruth` keys. `tolerance` (relative error) is **required**.
- `curve` — agent writes a CSV; graded against `groundtruth_csv` by frequency interpolation. `tolerance` is **required**.
- `artifact` — collected only, not scored. `description` is **required** (used to tell the agent what to produce).

Task IDs follow pattern `L{level}-{type}{num}` (e.g. L1-F01 = Level 1, Frequency-fit task 01).

### L3 advisory tasks

L3 tasks (L3-01 through L3-12) are **advisory / design / expert-mode** tasks. Most have no `outputs` section and are unscored. L3-09/10/11/12 do have scored outputs but require expert FDTR knowledge from skills/docs to complete correctly.

**L3 tasks MUST only be run with `-s full` tier.** Running them with `code` or `minimal` is meaningless:
- `code` lacks docs, skills, AGENTS.md, CLAUDE.md — agent has no way to learn FDTR expert workflows
- `minimal` has no FDTR package at all — agent can't even load data
- L3-01 through L3-08 are pure design/consulting tasks that need full context

The `prompts.py` module auto-generates output-contract instructions appended to each prompt (listing the exact keys/columns/files to write) — this is how the agent knows what to produce.

## Runner Internals

- `runner.py` uses `asyncio` with `Semaphore` for concurrency control
- Containers are launched with staggered starts (1s delay) to avoid Docker races
- On timeout: SIGINT → wait 60s → force kill
- Balance errors in agent output (e.g. "insufficient_quota", "402 Insufficient Balance") trigger global abort across all remaining tasks
- Results go to `results/{session_id}/` (each session gets a timestamped directory)

## Important Gotchas

- **Do not run `docker/build.sh`** without initializing the Vibe_FDTR submodule first
- `configs/models.yaml` is gitignored — new clones need to create it from the example
- The `results/` and `benchmark_results/` directories are gitignored
- When debugging, use `--preserve` to keep containers alive after the run
- `results/` sits on NFS and `opencode.db` is WAL-mode — always open it read-only with `immutable=1` (see `trace.py:_connect`); a plain sqlite connect triggers WAL recovery + NFS locking and fails with `disk I/O error` / `locking protocol`. If a run's post-processing already failed this way, re-grade offline with `scripts/recover_session.py <session_dir>`
- The Vibe_FDTR submodule has its own `CLAUDE.md`/`AGENTS.md` — those are for the agent *inside* the Docker container, not for working on this benchmark runner
