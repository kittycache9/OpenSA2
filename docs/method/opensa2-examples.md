# OpenSA2 examples

This page holds every fact about OpenSA2 that the method pages use as an example. Each section names the real tools and records, and says what the example does not show. The general pages never name a game; they link here.

OpenSA2 recompiles Sonic Adventure 2 for PC, a 2012 32-bit x86 Windows program, into native programs for Linux, Windows, and Android. The player supplies their own copy of the game from a store. The project's architecture is in [../architecture.md](../architecture.md), and its current status is in [../status.md](../status.md).

## Identify the input

`Documentation/receipts/install-hashes-v1.json` lists the hashes of the accepted installation, and `tools/pins.json` pins the executables and the tools. `tools/hash_install.py` hashes a local installation against them.

The shipped executable is wrapped. [../getting-started.md](../getting-started.md) describes unwrapping it with a third-party tool, and `tools/normalize_unpacked.py` makes the result match the pinned file, because the tool's output differed in two section names on one runtime. `tools/s7/drm_unwrap.py` measures which bytes of the original the finished program reads, and `Documentation/receipts/s7-drm-unwrap-v1.json` records the result.

*Does not show:* how to unwrap a different packer.

## Map and lift

The map comes from a Ghidra export (`Documentation/scripts/s1_function_map.py`). `tools/s3/whole_image/lift_all.py` lifts the mapped functions and `tools/s3/arms/lift_arms_image.py` lifts jump-table arms. `tools/s4/rebuild_image.py` rebuilds the whole translated image in one command on a clean clone, and `tools/s4/lift_ir_receipt.py` checks it against `Documentation/receipts/lift-ir-v1.json`, which holds the hash of every module.

*Does not show:* a lift of a program that is not x86.

## Extras: blocks the flow missed

`tools/s4/lift_extra.py` registers a block as its own module, with a recorded reason. `Documentation/lifted-extras.json` is the tracked projection of that state, and `tools/s4/track_extra_functions.py --restore` writes the generated state back from it. `Documentation/scripts/check_lifted_extras.py` is the gate.

*Does not show:* how the blocks were first found by running the program.

## Traps and dispatch exceptions

`Documentation/dispatch-exceptions.json` lists dispatch targets that were reviewed and allowed, each with a reason, and `Documentation/scripts/check_dispatch_exceptions.py` is its gate. `tools/s4/mb_edges.py` builds the table of control-flow edges that leave a module's range. The runtime reads it to decide whether a missing block may be dispatched, as a tail call, or must trap. An unresolved target traps; it never returns success.

## Semantic passes

`tools/spike/pc24pass/pc24pass.py` makes the original's x87 precision and rounding controls explicit in the lifted modules. `tools/s4/rebuild_image.py` fingerprints the passes and their helper modules, and refuses to link modules built under a different fingerprint.

The desktop development builds use a faster floating-point mode that holds x87 register values in 64-bit floats, and keep a native mode for fidelity work. [../architecture.md](../architecture.md) declares this.

## Import surface

`Documentation/receipts/import-classification.json` and `Documentation/receipts/s4-import-surface-v1.json` classify the imported services.

## The game and player split

The game process writes graphics and decoded audio to a stream, defined in `host/sa2_stream.h`, and a separate SDL3 player draws and plays it. [../architecture.md](../architecture.md) describes the flow.

## Replay comparison

`harness/replay/tick_digest.h` defines the per-tick digest of selected state, and `Documentation/scripts/check_replay_regression.py` is the regression gate for recorded runs. The recordings themselves are not in the repository.

## A checked extension point: tweaks

`runtime/sa2_tweaks.h` is the registry for optional behavior changes. [../getting-started.md](../getting-started.md) describes adding one.

## A recorded decision

`Documentation/decisions/depth-clamp.md` records a rendering-policy decision, with a machine-readable policy beside it. It shows a decision record and says nothing about the floating-point passes.

## Packaging and publication

`tools/release/check_package.py` checks a package for files that must not ship and for terms from a local deny list, and `tools/public_check.py` checks the source tree, with the same list through `--deny-file`.

## Failure cases

The failure cases in `Documentation/method/records.json` come from this project. Each names the tool that fixed it and the check that would catch it again.
