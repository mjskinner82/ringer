# Model routing

Use this reference after Ringer has been explicitly selected and a worker model decision is required.

## Contents

- Evidence before selection
- Engine and model fields
- Sol, Terra, and Luna
- Exploration
- Task typing and scoreboard use

## Evidence before selection

Inspect the configured `[engines.<name>]` blocks before the first run of a job.
Run `ringer models --task-type <type>` for local performance evidence.
Check `ringer catalog --changes` for newly free or newly cheap models.

Recommend two or three grounded options with first-try pass rate, cost, and task-type evidence.
Ask the human to choose the worker model for the first run of the job.
Do not ask again on every round unless the selected mix is failing.
Never import another machine's routing conclusions.

## Engine and model fields

Put the engine in the manifest `engine` field.
Put the model in the manifest `model` field.
Put reasoning effort in `engine_args`.
Do not clone engine blocks or splice model selection through generic engine arguments.

Use Codex as the strongest general worker when its cost and task evidence justify it.
Use OpenCode as the universal harness for OpenRouter models.
Keep the OpenRouter slug in the task's `model` field.
Do not call an OpenRouter model through an ad hoc CLI because that loses sandboxing, raw logs, token counts, and executed verification.

Validate an unfamiliar model with a bounded task before scaling it.
Give conversational harnesses and build checks enough timeout.
Watch first-attempt failure and retry rates before assigning more work to a small or flash-class model.

## Sol, Terra, and Luna

Treat Sol, Terra, and Luna as optional operator-defined aliases.
Never guess what they resolve to.
Skip the aliases when local configuration does not define them.

- Use Sol for decomposition, architecture, check design, synthesis, and explicit rescue after a cheaper lane fails.
- Use Terra for normal implementation, debugging, integration, and invariant-heavy review.
- Use Luna for bounded mechanical edits, renderer work, tests, and verification with an exact check.
- Move a Luna task to Terra if it appears to require high reasoning effort.

Use Sol at xhigh, Terra at high, and Luna at medium as the normal starting points when those aliases exist.
Lower effort only when the task and check make the reduced risk explicit.
Do not assign every lane in a multi-task manifest to the frontier tier.

## Exploration

Prevent the scoreboard from fossilizing around the current winner.
In a selected run with at least three tasks and a low-risk lane, consider assigning about one task to an exploration candidate.
Use `ringer models --explore --task-type <type>` to find candidates.
Give temporary free promotions priority when the task is safe and the check is strong.

Do not explore on time-critical work.
Do not explore with more than a small slice of a batch.
Name the experiment when presenting the model choice so the human can veto it.

Treat an untested model as an audition.
Promote it after at least three relevant tasks with a first-try pass rate of at least 0.67.
End the audition after repeated first-attempt failures.
Record durable model lessons in `docs/MODEL-NOTES.md` using only executed checks and raw logs.

## Task typing and scoreboard use

Give every manifest task a canonical `task_type` such as `code-feature`, `code-fix`, `code-review`, `research`, `persona-review`, `site-build`, `image-gen`, `docs`, `probe`, or `bakeoff`.
Do not leave tasks untyped because untyped results teach the scoreboard little.

Use first-try pass rate as the main routing signal.
Treat overall pass rate as including retry rescues.
Read `docs/MODEL-NOTES.md` for judgment that the numeric scoreboard cannot carry.

When the human asks to see the scoreboard, run `ringer models --open`.
Use the generated zero-model HTML artifact instead of hand-summarizing the table.
