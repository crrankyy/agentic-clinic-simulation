# agent-clinic

A from-scratch reimplementation of an AgentClinic-style clinical encounter
simulation, built with **LangGraph** for orchestration and **OpenRouter** for
model access.

A panel of doctor agents must diagnose a patient whose case file is hidden from
them. They gather information by questioning a patient agent, requesting
physical exam findings and ordering tests through a gatekeeper agent, and
consulting the literature through an evidence agent. A judge agent scores the
final diagnosis against ground truth, and every run produces comparable
metrics.

Information isolation is enforced by **state design**, not by prompting: the
ground-truth diagnosis is never written into any graph channel a doctor-side
node can read.

## ⚠️ Disclaimer

**This is an educational simulation only.** It is not medical advice, must
never be presented or used as medical advice, and must never process real
patient data. It exists to study multi-agent orchestration and evaluation
methodology, not to diagnose anyone.

## Status

**Phase 0 — dataset.** Nothing but the dataset download and verification
tooling exists yet. See `docs/` for the decision log and phase notes.

## Dataset

Case files come from the MIT-licensed
[AgentClinic](https://github.com/SamuelSchmidgall/AgentClinic) repository,
pinned to commit `b6570edefb940857a7c334350656b29f9d984f24`. Only the dataset
files are used — no code is copied or imported from that project.

The `.jsonl` files are not committed. Fetch them with:

```bash
uv run python scripts/download_dataset.py
```

Provenance (source URL, commit SHA, SHA-256, case count, download time) is
recorded in `dataset/MANIFEST.json`, which *is* committed.

## Documentation

| File | Contents |
|---|---|
| `docs/BRIEF.md` | The original project brief, verbatim |
| `docs/DECISIONS.md` | Decision log — every choice, when it was made, and why |
| `docs/DECISIONS_OPEN.md` | Questions still awaiting a decision |
| `docs/PLAN.md` | Architecture and graph design (written after decisions are collected) |
| `docs/PLAN_REVIEW.md` | Adversarial review of the plan, plus resolutions |
| `docs/PHASE_N_NOTES.md` | Per-phase notes: what was built, decisions made, surprises |
