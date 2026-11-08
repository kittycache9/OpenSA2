# The pipeline

This page owns the stages that turn a shipped program into a native one: what each stage takes in, what it produces, the receipt it leaves, and the check that says it is finished. It does not say how to call any tool, and it does not explain the ways the old machine and the new one differ; [gaps.md](gaps.md) does that.

The stages assume a program for a 32-bit x86 processor with a known set of supporting libraries, whose original executable the player supplies. Other source machines need other passes, and the first project to meet one should expect to add a stage.

Every stage works from pinned inputs, writes its output outside the tracked tree (see the repository boundary in [principles.md](principles.md)), and leaves a receipt a person can check on a fresh clone.

## 1. Identify the input

| | |
| --- | --- |
| In | The executable and the files installed with it, as the player has them. |
| Out | The hash of every accepted build of the executable, and the hash of every file in the supported installation. |
| Receipt | A record of the accepted hashes. |
| Exit | The supplied copy matches an accepted hash. A copy that does not match is refused, never patched to fit. |

If the shipped executable is packed or wrapped, unwrap it in this stage and record the unwrapped file's hash too. Then measure which bytes the finished program reads from the original at run time, so the installer knows which of the player's bytes it must check.

## 2. Map

| | |
| --- | --- |
| In | The unwrapped executable. |
| Out | Where every function starts and ends, the imported services, the jump-table targets, and the data sections. |
| Receipt | The counts per group, and the hash of the map. |
| Exit | The counts reconcile with the analyzer's own export, and the map's hash is pinned. |

An analyzer finds most of a program's code and misses some: code reached only through data tables, code created at run time, and code in unusual sections. Seed the analyzer with addresses found by a second, independent sweep, and record which seeds it accepted.

## 3. Lift

| | |
| --- | --- |
| In | The map and the executable's bytes. |
| Out | One intermediate-language module per function, one per jump-table arm, and one per extra block (see below). |
| Receipt | The hash of every module, with no paths in it. |
| Exit | Every mapped function is lifted or listed with a reason, and the receipt matches on a fresh clone. |

A lifter follows the flow it can see from each entry point. Where the analyzer's ranges and the lifter's flow disagree, there are blocks no entry reaches: arms of jump tables that the map did not cover, and code found only by running the program. Register each such block as an extra, with the reason it was added; [gaps.md](gaps.md) describes the class. The list of extras is evidence from running the program, which nothing else can regenerate, so track it as a projection.

## 4. Apply the semantic passes

| | |
| --- | --- |
| In | The lifted modules. |
| Out | The same modules with the old machine's behavior made explicit where the lifter leaves it implicit: flag and floating-point semantics, precision and rounding control, and dispatch. |
| Receipt | For each pass, what it changed and what it refused. |
| Exit | Every pass reports its counts, a refusal fails the stage, and a change to a pass rebuilds every module it touches. |

A pass is part of the translation. Record a fingerprint of the passes and of the helper modules they splice in, and refuse to link modules built by a different fingerprint. A rebuild that mixes two versions of a pass must fail.

## 5. Compile

| | |
| --- | --- |
| In | The folded modules. |
| Out | Native object files for each target. |
| Receipt | The build record: what compiled, with no absolute paths. |
| Exit | Every module compiled, or its failure is recorded and the stage fails. |

Cross-compile from one build host. A stale object must never link: before linking, tie each object to the module it was compiled from by hash.

## 6. Link with the runtime

| | |
| --- | --- |
| In | The objects, the runtime, and the table of function addresses. |
| Out | The program. |
| Receipt | A classification of every imported service. |
| Exit | Every import is implemented, stubbed with a named trap, or declared out of scope; no unresolved target returns success. |

The runtime keeps the original's address space and supplies the services the original expected from its operating system and libraries. Keep the program's logic and the platform's presentation in separate processes joined by a recorded stream: the logic then runs the same on every target, and only the player changes.

## 7. Verify

| | |
| --- | --- |
| In | The program and the recorded inputs. |
| Out | Results at each level of [verification.md](verification.md). |
| Receipt | The result at each level, with the first divergence where there is one. |
| Exit | The level named in the project's target has passed, and nothing that passed earlier now fails. |

## 8. Package

| | |
| --- | --- |
| In | A verified program. |
| Out | A package for each platform, and an installer that checks the player's files against stage 1's hashes. |
| Receipt | The package's file list with hashes. |
| Exit | The package check passes with a local list of private terms, so no workstation detail ships. |

## How the stages order each other

A stage's input is the previous stage's output. A change in stage 3 or 4 rebuilds stages 5 to 7; a change in stage 6 does not rebuild 3. Where a stage's output can be rebuilt from its input in minutes, rebuild it and check the receipt rather than store it: the receipt is the artifact worth keeping.
