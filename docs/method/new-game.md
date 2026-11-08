# Starting a recompilation of a new game

This page owns the order of work for a new project and the check that ends each step. It holds no knowledge of its own: when a step raises a question, the page named in the step answers it.

Read [principles.md](principles.md) and [operating.md](operating.md) first. The method was derived from one project, so expect the first steps to work as written and the later ones to need adjusting; record every adjustment as a decision.

## The steps

| # | Step | Owning page | Done when |
| --- | --- | --- | --- |
| 1 | Write the target in one sentence in the claim vocabulary, and list what is out of scope. | [principles.md](principles.md) | A reviewer can say which level of evidence would meet it. |
| 2 | Check that the source machine and the supporting libraries match the assumptions of the pipeline. If not, list the stages that will need changing. | [pipeline.md](pipeline.md) | Each assumption is confirmed, or a stage is marked for change. |
| 3 | Set up the workspace: a tracked tree for source, receipts, and projections, and an ignored directory for everything the program or the player supplies. Add the checks that keep them apart. | [principles.md](principles.md) | The pre-commit command rejects a game asset or a generated file. |
| 4 | Identify the input: hash the accepted builds and the installation, and unwrap the executable if it is wrapped. | [pipeline.md](pipeline.md), stage 1 | The supplied copy matches, and a copy that does not match is refused. |
| 5 | Map the program and seed the analyzer from an independent sweep. | [pipeline.md](pipeline.md), stage 2 | Counts reconcile, and the map's hash is pinned. |
| 6 | Plan the stages: budgets, exit criteria, kill criteria, and which decisions are the maintainer's. | [operating.md](operating.md) | Every stage has criteria a script can compute. |
| 7 | Lift a small slice end to end, and compare it to the original on fixtures, before lifting anything else. | [verification.md](verification.md), R1 | The slice matches, and the fixtures can fail. |
| 8 | Classify every imported service: implemented, trapping stub, or out of scope. | [gaps.md](gaps.md), class C | No import is unclassified. |
| 9 | Lift the whole image, register the extras that the flow missed, apply the passes, compile, and link. | [pipeline.md](pipeline.md), stages 3 to 6 | The lift receipt matches on a second, clean clone. |
| 10 | Run the program to its first scene, and record every trap. Fix each by its gap class. | [gaps.md](gaps.md) | The run reaches the scene with no traps, or the traps are listed. |
| 11 | Record the original and compare tick by tick. Read the first divergence, fix or label it, and repeat. | [verification.md](verification.md), R3 | The recording matches on the chosen fields, or every divergence is a labelled residue. |
| 12 | Compare the program's graphics and audio output through a second implementation. | [verification.md](verification.md), R4 | Differences are measured and explained. |
| 13 | Package it, and check the package for workstation detail. | [pipeline.md](pipeline.md), stage 8 | The package check passes with a local list of private terms. |
| 14 | Write the failure cases from what went wrong, each with a detector. | [records.md](records.md) | Every case names a check or states why none exists. |

## Habits that keep a project honest

- Work one stage at a time. Do not start the next stage before the gate closes.
- Count progress in numbers a tool generated, and keep the tool.
- When a step is repeated a third time, write the tool.
- Run the whole pipeline on a clean clone early, and fix what breaks on it as you go. A pipeline that works only where it was built has not been shown to work.
- Stop and ask when the rules in [operating.md](operating.md) say so.
