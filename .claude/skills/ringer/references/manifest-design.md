# Manifest design

Use this reference after Ringer has been explicitly selected.

## Contents

- One job, one artifact
- Spec-writing rules
- Check-writing rules
- Pattern selection
- Follow-up rounds

## One job, one artifact

Treat the human's requested job as one artifact even when it takes several rounds.
Use the same `run_name` for every round.
Name it after the job in the human's words, not after the internal batch structure.

Review results from the artifact page.
Do not reveal important results by dumping result files into a terminal.
Declare every important output in `expect_files` so the artifact store captures it.
Treat a missing artifact as a harvest gap to fix, not as a reason to bypass the artifact page.

## Spec-writing rules

Workers are stateless and cannot ask questions.
Make every spec self-contained.

- Open with the role and boundary.
- State what the worker must never touch before stating what it should do.
- Name every file the worker owns.
- Keep ownership disjoint across all concurrent lanes and branches.
- Include exact commands for every script or harness the worker must use.
- Define the output contract, including file paths and required contents.
- Include grading criteria for evaluated work.
- Put hard rules in the spec, including Git, privacy, runtime, and mutation boundaries.
- Write the instructions themselves in the spec.
- Point to files only as source material, not as a substitute for the brief.

## Check-writing rules

Treat the check as part of the product.
Its failure output becomes retry and evaluation evidence.

- Print why the check failed.
- Prefer a useful `diff` or validator message over a bare exit status.
- Verify content and behavior, not mere file existence.
- Execute generated code, builds, renderers, or validators when applicable.
- Use `expect_files` as an artifact floor, not as the substantive check.
- Never use `true`, unconditional `exit 0`, or `echo done` as verification.
- Be strict about substance and tolerant about formatting.
- Avoid exact heading counts, exact casing, and brittle phrase matching unless the format itself is the contract.

## Pattern selection

Browse `templates/README.md` before inventing a new pattern.
Choose the smallest pattern that fits the selected Ringer job.

| Pattern | Use when |
|---|---|
| `review-swarm` | Broad read-only coverage is needed before deciding what to fix. |
| `fix-swarm` | Confirmed independent fixes can use isolated worktrees. |
| `focus-group` | Isolated persona feedback is needed for a product, pitch, prompt, or workflow. |
| `bakeoff` | Models, prompts, or configurations need comparison across shared scenarios. |
| `research-with-proof` | Research claims need an executable proof task. |
| `launch-kit` | Research, persona review, and final assembly need separate rounds. |
| `asset-swarm` | Media assets need parallel production and executable render checks. |
| `adversarial-review` | Several models should review the same artifact before synthesis. |
| `repo-feature` | A known feature needs sandboxed workers, builds, and Git checks. |
| `migration-swarm` | A mechanical transform can be partitioned across worktrees. |
| `doc-swarm` | Module documentation needs executed examples and API checks. |
| `test-hardening` | Tests can be strengthened by module while production files stay off-limits. |
| `competitive-teardown` | Competitor research needs citation allowlists and synthesis. |
| `data-pipeline` | Fetch, transform, and validation stages need separate proof. |
| `probe` | An explicitly selected one-task smoke, probe, or post-mortem needs durable evidence. |

## Pattern rules

- Review before fixing when the findings are not already confirmed.
- Do not make one worker find and fix the same uncertain issue.
- Split an objective projected to touch more than about 25 files or two subsystems into landable slices.
- Give each slice its own branch and pull request when independent landing is possible.
- Keep each persona in a separate worker to prevent context bleed.
- Reuse the same persona panel across product or prompt iterations.
- Put a model-calling smoke or probe under Ringer only when the user selected Ringer for that job.

## Follow-up rounds

Resume from the existing exported patch, branch, or commits.
Do not silently reimplement prior rounds.
Keep the same `run_name` so the artifact page accumulates versions under one job.

When a check failure reveals an ambiguous spec, fix the spec or split the task before retrying.
When the same task family has failed two rounds, do not launch an identical third round.
Inspect status and raw evidence, then change the task, tier, environment, or plan.
