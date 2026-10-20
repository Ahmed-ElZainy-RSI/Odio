# Plan: tier benchmark

**Spec:** [spec.md](spec.md)

1. Approve a budget for the paid run (estimate from 025 on 1 page per tier first).
2. Run each tier on: Ibn Battuta (5 pp), `الشمس` (16 pp), 1 undiacritized book.
3. Score with `tools/score_corrections.py` (019), cost with `tools/cost_report.py` (025).
4. Write `results/<date>.md`; review with product owner; record Q-07 answer; update ADR-009.

```
tools/benchmark_tiers.py
specs/026-tier-benchmark/results/
docs/backend-v1/19-adrs.md, 22-open-questions.md
```
