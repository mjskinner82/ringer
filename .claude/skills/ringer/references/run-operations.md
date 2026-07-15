# Run operations

Use this reference after Ringer has been explicitly selected and before launching, integrating, or reviewing a run.

## Contents

- Visibility and launch
- Harness ownership
- Worktree behavior
- Integration and optional publication gate
- Failure handling
- Post-run review

## Visibility and launch

Open Ringside before writing a long spec or launching work.
Use the page at `http://127.0.0.1:8700`.
Do not launch the parked Ringside application.
Do not use `--no-dashboard` except in automated tests or when the user explicitly requests it.

Tell the human what preparation is underway when it will take more than about 30 seconds.
Lint the manifest before running it.
Use a dry run when the worker plan or ownership needs review before spawning anything.

## Harness ownership

Let Ringer own delegated work, worker worktrees, retries, and worker verification while the run is active.
Do not invoke firstmate, no-mistakes, or another worker scheduler inside the manifest.
Do not start a competing repair loop when another harness owns the active branch.

Use a Codex Goal only as an optional outer completion contract.
Do not use a Goal as a worker scheduler or as a substitute for executed checks.

## Worktree behavior

Run-level `worktrees: true` gives each task an isolated worktree detached at the repository head.
Account for these consequences:

1. Passing task worktrees are deleted.
2. Deliverables must leave the task worktree before deletion.
3. Worker commits disappear with deleted worktrees unless exported.
4. Raw logs survive under the declared log location.
5. Gitignored outputs do not enter a staged patch automatically.

Prefer workers leaving changes uncommitted.
Have the check stage only the worker-owned paths, export a patch outside the worktree, and validate the exported patch.
Copy required Gitignored outputs outside the worktree explicitly and validate both patch and copies.

When integrating into the real checkout, stage specific paths.
Never use `git add -A` in a checkout that may contain unrelated scratch files.

## Integration and optional publication gate

Review and integrate verified Ringer output into the authorized repository branch.
Run the canonical repository checks after integration.

Do not hand the branch to no-mistakes automatically.
Start no-mistakes only when one of these conditions is true:

- The user separately invoked no-mistakes.
- The user selected the full harness.
- The closest repository instructions require no-mistakes and the task already authorizes publication through that gate.

Otherwise stop after the normal verified handoff requested by the user.

If no-mistakes already owns an active review or fix round on the branch, do not route its fixes or verification back through Ringer.
If no-mistakes raises a non-mechanical product decision, return that decision to the human.

## Failure handling

Run `ringer status` before a new job.
Treat exit 2 as evidence of stranded escalations.
Resume, re-plan, or report each escalation before clearing it.

Treat a launch-class escalation as an engine, environment, or spec problem.
Do not rerun an unchanged manifest that will fail identically.

After two failed rounds in the same task family, stop the identical retry path.
Inspect status, run JSON, and raw logs.
Then split the task, change the model tier, repair the environment, or escalate to the human.

## Post-run review

1. Read the run JSON under `~/.ringer/runs/` for statuses, retries, and durations.
2. Read the raw worker log for every retried or failed task.
3. Spot-check at least one passing task artifact per run.
4. Treat a useless check failure message as a check-design problem.
5. Update `docs/MODEL-NOTES.md` when the run produced durable model evidence.

Report worker-check health separately from the substantive verdict in review artifacts.
A worker check passing does not mean the reviewed system has no findings.
