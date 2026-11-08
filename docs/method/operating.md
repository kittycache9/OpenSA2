# Operating the work

This page owns how a recompilation project is run: who decides what, how work is divided into stages, how budgets and gates stop drift, how changes are reviewed, and when an agent must stop and ask. It is written for a team of LLM agents with one maintainer, because that is how the method was used, and it applies as well to people.

## Roles

- The **maintainer** sets the project's target, approves direction changes, and holds the decisions reserved to a person (see [Who may close a gate](#who-may-close-a-gate)).
- The **main agent** runs the current stage, integrates work, keeps the records, and is the only one that commits and pushes.
- **Parallel units** are agents given one self-contained task each, on files no other unit touches. They never commit, push, or run a command that changes the repository's state; the main agent integrates their results one at a time.
- **Reviewers** are read-only agents that check a change or a report and write findings. A finding is folded, with a fix or a recorded reason, before the next push.

## Stages and gates

Divide the project into stages, each ending in a gate. A stage has:

- **entry criteria**: the previous gate passed and its verdict is recorded;
- **exit criteria**: specific metric values that a script computes;
- **a budget** in agent active hours (see [Budgets](#budgets));
- **kill or pivot criteria**: the conditions under which the stage stops and a named fallback is proposed.

Work on a stage does not start before the previous gate passes. "It's just scaffolding" is how work drifts deep into one subsystem before anyone has measured the rest; the stage rule exists to make that impossible.

## Budgets

Count budgets in agent active hours, not calendar time. Measure them from the agents' own session activity, and split a session at any idle gap longer than a stated threshold, so a pause is not charged as work. Report the threshold and the alternative readings beside the binding figure at every review.

A budget is a ceiling that triggers review, not an estimate:

- at 50 %, commit a metrics snapshot;
- at 100 %, hold a gate review whether or not the exit criteria are met;
- at 150 % without the exit criteria, stop. Write a pivot proposal (see [Direction changes](#direction-changes)) and wait for the maintainer.

## Slices

A slice is a run of work toward one metric. If a slice goes eight active hours without committing a change to a metric, it stops, and the journal records why. A slice that cannot move a number is usually solving the wrong problem.

## Numbers are generated, never typed

A number in a durable record, such as a gate report, a status page, or a decision, is copied from a tool's output with the commit it came from. A gate report is the output of a script, not prose with numbers in it.

This rule comes from a failure: a summary that claimed to be generated had been typed by hand, and every error in it flattered the work.

## Gate reviews

The gate review is a generated report with the stage, budget, hours spent, each exit criterion with its metric value, the metrics with their change since the last review, the rate of progress, new residues, the risk register, the adversarial review's findings, and the decision requested: proceed, pivot to a named fallback, or halt.

At every gate, answer these questions with numbers. If any answer is "not measured", the gate does not pass.

1. What fraction of the target's functions does the lifter translate, and what fraction of those compile?
2. What fraction of fixtures pass? Has anything that passed later failed?
3. How many imported services are implemented, and are the missing ones reachable on the recorded runs?
4. For each recorded run, where is the first divergence, and is it a labelled residue or a bug?
5. How many scenes start, and how many complete a recorded run?
6. Is this stage's rate consistent with the next stage's budget?
7. What did the adversarial review disagree with, and was it folded?

Before any gate verdict, several independent reviewers, preferably on different models, try to refute the report. This step is never waived for schedule. In practice every such review found something real, including a report that read PASS and had five actionable defects.

## Who may close a gate

The main agent may close a gate when every exit criterion is met, the adversarial review's findings are folded, and nothing below applies. It records the closure as its own decision, with what a reviewer would find.

These are reserved to the maintainer and never the agent's to decide:

- loosening an exit criterion, a metric's denominator, or a kill threshold;
- any direction change or pivot;
- continuing past 150 % of a budget.

Strengthening a criterion is the agent's own call. Tell the maintainer when a strengthening changes a gate's pass condition or is expected to cost a lot of time, because a criterion that only ever tightens can stall a stage for no gain.

## Reviews beside implementation

Give every change that affects translated-code semantics, a runtime service with behavior, or a comparison tool a read-only reviewer with a self-contained brief.

Match the review depth to what else checks the change:

- game-side code (the runtime, device stand-ins, fixes to translated code) gets one reviewer, because recorded-run comparisons check it independently;
- measuring instruments (comparers, gates, masks, the recorder, the harness) get extra review, because nothing else checks them;
- tooling, installers, and user interface changes may need none.

Review time counts against the stage budget like any other work.

## Repeated steps become tools

The third time a manual step is about to be repeated, make it a tool with a plain name and a clear refusal when its inputs are wrong. A tool is a record a reviewer can rerun; a remembered sequence of commands is not.

## Direction changes

A direction change or pivot is written in five parts: the claim it replaces, the evidence, the new approach, what from the old approach remains valid, and the smallest experiment that would prove the new approach wrong. Name the fallback and what it inherits from the work so far. The maintainer decides.

## Running the game

Agents run everything the project builds without a person at the keyboard. When a run needs the original game as a reference:

- start the executable directly, not through a store client;
- never write to the store's installation;
- send scripted input only to the game process, never as operating-system-level keyboard or mouse events, so a run cannot type into another window;
- record in the run's receipt what environment it ran in, and list any way that window focus or other desktop activity could change its timing as a determinism risk.

Stop and ask when a run needs a person: a login, a dialog that cannot be dismissed, or a rebuilt prefix.

## When to stop

An agent stops, writes down the question, and waits for the maintainer when:

1. a decision reserved to the maintainer comes up;
2. a budget reaches 150 % without the exit criteria;
3. a gate is refused twice on the same criterion;
4. a result fails for a reason the stage did not expect, and fixing it would mean changing the expectation;
5. work needs a person's hands or accounts.

After a loss of context, an agent rebuilds its picture from the durable records (the stage plan, the gate journals, the decision records, and the metrics), not from a summary of the conversation.
