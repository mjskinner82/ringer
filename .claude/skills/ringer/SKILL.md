---
name: ringer
description: >-
  Operate Ringer, the verified-swarm delegation tool, only when the user
  explicitly asks to use Ringer, invokes $ringer, selects the full harness,
  requests a Ringer manifest or verified Ringer swarm, or when the closest
  repository instructions explicitly require Ringer for the authorized task.
  Also use this skill to inspect, resume, or debug an existing Ringer run.
  Do not trigger Ringer from ordinary implementation, testing, validation,
  edit loops, file counts, model use, commits, pushes, or pull-request work.
  If Ringer might help but was not selected, recommend it once without
  launching it and continue directly.
---

# Ringer

Treat Ringer as an opt-in execution mode, not the default path for substantive work.
Standing authorization removes a second permission prompt after Ringer is selected.
It does not select Ringer or make it mandatory.

## Activation contract

- Launch Ringer only after explicit user selection or an explicit closest-repository requirement.
- Continue directly when the task merely involves several files, an edit-test loop, local validation, or a model-calling command.
- Recommend Ringer at most once when the work has at least two genuinely independent lanes, needs isolated perspectives, or benefits from a verified model comparison.
- Treat a user choice of `direct` as suppression of Ringer for the current job unless the scope materially changes.
- Do not hand work to no-mistakes automatically.
- Use no-mistakes after Ringer only when the user separately selected it, selected the full harness, or the closest repository requires it and publication is already authorized.

## Start an explicitly selected job

1. Announce that the Ringer skill caused the orchestration step.
2. Run `/Users/mattskinner/.local/bin/ringer status` and resolve or report stranded escalations before starting another run.
3. Open Ringside with `/Users/mattskinner/.local/bin/ringer hud` before preparing work that will take more than about 30 seconds.
4. Keep one human job under one stable `run_name` across all rounds.
5. Choose the required references below and read each selected reference completely before acting.
6. Lint every manifest before running it.
7. Review the run artifacts, executed checks, retries, and raw logs before accepting the result.

## Load only the required references

- Read [manifest-design.md](references/manifest-design.md) before authoring or reviewing a manifest, choosing a swarm pattern, designing task ownership, or writing checks.
- Read [model-routing.md](references/model-routing.md) before choosing engines, models, effort, exploration lanes, or interpreting the model scoreboard.
- Read [run-operations.md](references/run-operations.md) before launching a run, using worktrees, integrating results, handling failures, or completing post-run review.

## Core invariants after activation

- You review and orchestrate; Ringer workers type and execute task checks.
- A selected Ringer job may use a one-task manifest, but task size alone never selects Ringer.
- Make worker file ownership disjoint across concurrent lanes.
- Make checks executable, content-aware, and informative on failure.
- Never run no-mistakes or another worker scheduler inside a Ringer manifest.
- Treat a fast nonzero worker exit as a launch-class failure, stop the identical retry, and change the engine, environment, or spec.
- Stop after two failed rounds in the same task family, inspect status and evidence, then change the plan or return to the user.
- Preserve stdin closure, explicit sandbox mode, executed artifact verification, and raw-only worker logs in any Ringer implementation change.

## Commands

```bash
/Users/mattskinner/.local/bin/ringer lint manifest.json
/Users/mattskinner/.local/bin/ringer run manifest.json --identity <who-you-are>
/Users/mattskinner/.local/bin/ringer run manifest.json --dry-run
/Users/mattskinner/.local/bin/ringer status
```
