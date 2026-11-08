# Records

This page owns the kinds of record a project keeps and the rules for them: receipts, projections, gates, decisions, and failure cases, and how a lesson becomes a check. The schema for the records kept in the tracked tree is `RECORD_FIELDS` in `tools/method_check.py`; this page describes the fields in words and does not copy the list.

## Receipts

A receipt is a record a tool writes about what it did and found. It holds a schema name, an id, the name of the tool that wrote it, the hashes of its inputs, and counts and verdicts. It holds no absolute paths and no timestamps, so that two runs of the same tool on the same inputs write the same file.

A receipt is checkable: a person on a fresh clone runs the tool again and compares. If the result differs, the first difference is named. A receipt nobody can regenerate is a claim, not a record.

## Projections

Some information is evidence that nothing can regenerate, such as the list of extra blocks found by running the program, each with the reason it was added. Keep such a record in the tracked tree as a projection of the generated state: the decision-bearing fields, without timings and without working directories. A projection changes only when a decision changes, so a regeneration that changes nothing leaves the file byte-identical.

A projection must be reversible enough to restore the generated state from it. Otherwise a lost working directory takes the evidence with it.

## Gates

A gate is a script that exits with an error when a rule is broken. Run every gate from the project's one pre-commit command, so a contributor cannot forget one. A gate that fails for no semantic reason is a defect and gets fixed as one; a team that learns to re-run past a flaky gate has no gates.

## Decision records

A decision record holds an id, a title, the situation, the choice, the evidence, and the condition that would reverse it. The evidence is a path in the tracked tree or a command a reader can run. Records carry no dates; their order in time comes from the history of the repository.

Write a decision record when a choice would surprise a later reader or when someone might reasonably undo it. Do not write one for a routine fix.

## Failure cases

A failure case holds an id, a class, the stage where it arose, the symptom, the root cause, the pattern of the fix, the path of the fix, and the detector: the existing check that would catch the failure again. A case with no detector states why none exists. The class is one of the five in [gaps.md](gaps.md), or reproducibility, verification, or process.

Keep each of the symptom, cause, and fix pattern to a sentence or two. A failure case is a lesson, not a transcript.

## From lesson to check

A lesson that lives only in prose is forgotten. When a failure case names a detector, the check enforces that the detector exists. When it names none, the missing detector is a standing item: the next change in that area adds one, or records why it cannot.

The records themselves are kept true by the checker, which refuses a record with a wrong shape, a skipped id, a date, or a reference to a file that is not in the tree.

## Reproducibility records

Whatever a team can regenerate, a stranger must be able to regenerate, and the way to know is to try it. Run the whole pipeline on a clean clone, record where it breaks, fix those breaks, and record each one as a failure case of class reproducibility. A receipt that matches on a clean clone, and on a second one, is worth more than any number of receipts that match only where they were made.
