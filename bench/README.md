---
pretty_name: ERP-Bench
language:
- en
tags:
- responsible-ai
- odoo
- erp
- procurement
- manufacturing
- benchmark
size_categories:
- n<1K
---

# ERP-Bench

ERP-Bench is the Odoo 19 benchmark used in the Anchor paper, **"Preventing Artifact Drift in Agent Benchmark Generation."** It contains 300 long-horizon procurement and manufacturing tasks generated from a single solved specification.

Anchor's central claim is that benchmark tasks should not be hand-assembled from separate instructions, environments, oracle solutions, and verifiers. In this repo, each task is compiled from one CP-SAT-backed procurement specification into:

- `instruction.md`: the natural-language business task.
- `environment/`: a seeded Odoo database and runtime.
- `solution/`: the solver-certified reference plan.
- `tests/`: a terminal-state verifier over Odoo records.

The result is a benchmark where rewards are tied to end-state business correctness, not to a particular action trace.

## Responsible AI Metadata

Extended Croissant metadata is provided in `croissant.json`. It preserves the Hugging Face dataset identity and adds Croissant RAI fields for data collection, intended uses, limitations, known biases, sensitive-information handling, social impact, and release maintenance. The RAI-only overlay is also available in `croissant_rai.json`.

## What Is Included

- 300 generated Harbor tasks in `tasks/`.
- One Odoo 19 environment per task.
- Procurement and manufacturing workflows spanning 29 task patterns.
- Known optimal solutions generated with OR-Tools CP-SAT.
- Verifiers that score constraint satisfaction, optimality, and traceability.

Example tasks:

```text
tasks/2000_easy_01_buy_only_baseline/
tasks/2053_medium_07_screened_buy_only_mixed_seeded_invoicing/
tasks/2299_hard_repair_plan_hard/
```

## Install

```bash
uv sync
uv tool install harbor
```

For Daytona runs:

```bash
export DAYTONA_API_KEY=...
```

For model-backed agents, also set the relevant provider key, such as `ANTHROPIC_API_KEY`.

## Run

Use Harbor path mode against the checked-in tasks.

```bash
# Reference solution should solve the task.
harbor run -p tasks/2000_easy_01_buy_only_baseline -a oracle --env daytona

# No-op should receive zero reward.
harbor run -p tasks/2000_easy_01_buy_only_baseline -a nop --env daytona

# Run the full benchmark with parallelism.
harbor run -p tasks -a oracle --env daytona -n 10
```

Local Docker fallback:

```bash
harbor run -p tasks/2000_easy_01_buy_only_baseline -a oracle --env docker
```

Run a model-backed agent:

```bash
harbor run -p tasks \
  -a claude-code \
  -m claude-sonnet-4-5-20250929 \
  --env daytona \
  -n 10
```

## Regenerate

The current 300-task dataset is defined by `erp_bench/procurement/examples/diverse_300_dataset.toml`.

```bash
uv run generate-tasks \
  --category procurement \
  --dataset-config erp_bench/procurement/examples/diverse_300_dataset.toml \
  --output tasks \
  --force
```

Generate a small local batch:

```bash
uv run generate-tasks \
  --category procurement \
  --difficulty easy \
  --count 3 \
  --start-number 9000 \
  --output tasks
```

## Task Layout

```text
tasks/<task_name>/
├── task.toml
├── instruction.md
├── environment/
│   ├── Dockerfile
│   ├── entrypoint.sh
│   ├── odoo.conf
│   ├── scenario_data.json
│   └── setup_scenario.py
├── tests/
│   ├── test.sh
│   └── checks.py
└── solution/
    ├── solve.sh
    ├── solver.py
    └── optimal_plan.json
```

## Code Map

- `erp_bench/procurement/`: procurement configs, sampler, solver, prompts, and objectives.
- `erp_bench/generation/`: generic generation CLI and rendering pipeline.
- `erp_bench/templates/procurement/supply_planning/`: task artifact templates.
- `schemas/`: Pydantic schemas and shared validation.
- `agents/`: lightweight pi-based agent harness helpers used for ERP-Bench experiments.

## Development

```bash
uv run ruff check .
uv run ty check
```

After changing schemas, solver logic, or templates, regenerate affected tasks and validate with both `nop` and `oracle`.
