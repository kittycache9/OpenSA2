/* A recording DirectInput 8 device whose host answers come from the capture's
 * device profile.
 *
 * Why this exists
 * ---------------
 * `DirectInput8Create` answered E_FAIL, and the native game on the recording
 * host succeeds: dinput-trace-v1a:108 is the call, and the game calls through
 * the result at :114, which it does only past the pointer test at 0x00864D42.
 * The 2026-09-23 ruling (Documentation/decisions/
 * 2026-09-23-M4-refusal-native-outcome.md) counts a refusal as implemented
 * only if it reproduces the native outcome, so the E_FAIL stopped counting and
 * this device replaced it. Design 3.5 classes dinput8 as presentation, and S4's
 * work list asks for "presentation stubs as no-ops that record calls": nothing
 * here reads a host device, every method call is recorded, and every answer
 * is the one native gave.
 *
 * Where the host's answers come from
 * ----------------------------------
 * **Never from this file.** The devices a native run sees are a recorded
 * property of that capture (runtime/sa2_devprofile.h): which HRESULT
 * CreateDevice(GUID_SysKeyboard) and CreateDevice(GUID_SysMouse) got, and the
 * game controllers EnumDevices(DI8DEVCLASS_GAMECTRL, DIEDFL_ATTACHEDONLY)
 * listed, in native order. With no profile (SA2_DEVICE_PROFILE unset) the
 * profile is the plain keyboard-and-mouse default: both system devices
 * create, and no game controller is attached.
 *
 * On the recording host the profile lists one game controller, the keyboard's
 * receiver (native-outcomes-v1a:14762-14782). The game creates it at 0x00864CAE,
 * asks IsXInputDevice (0x00864680, which runs the WMI query of
 * runtime/sa2_wbem.h), and on "no" unacquires and releases it
 * (native-outcomes-v1a:15241-15244). This file enumerates it through the
 * game's own callback, 0x00864C90, with runtime/sa2_guest_call.h, so the whole
 * of that sequence runs in lifted code exactly as native ran it.
 *
 * The surface, decoded from the image (tools/s4/dinput_surface.py)
 * ------------------------------------------------------------------
 *   IDirectInput8A        CreateDevice   0x00863781 0x00863208 0x00864CAE
 *                         EnumDevices    0x00864E19
 *   IDirectInputDevice8A  SetDataFormat        0x0086379E 0x00863222
 *                         SetCooperativeLevel  0x008637B2 0x00863236
 *                         Acquire              0x008637C4 0x00863244 0x00863894
 *                                              0x008638A9 0x008633B0 0x008633C9
 *                         GetDeviceState       0x00863810 0x00863885 0x00863282
 *                                              0x008633A1
 *                         Unacquire            0x00863740 0x008631D0 0x00864CCE
 *                         Release              0x00864CDA
 * Those slots have handlers. Every other slot of both vtables is named with no
 * handler and refuses by name, and so does every argument those sites cannot
 * pass (another class, flags, version, GUID or format): reaching one means the
 * decode was wrong, and that should stop the run rather than be answered.
 *
 * What the answers are, and their native evidence
 * ------------------------------------------------
 * "dinput-trace-v1a:N" is line N of
 * private/generated/oracle/captures/dinput-trace-v1a/native-run.log (a
 * WINEDEBUG=+dinput boot) and "native-outcomes-v1a:N" line N of
 * private/generated/oracle/captures/native-outcomes-v1a/native-run.log
 * (+dinput,+xinput,+xaudio2,+wbemprox,+ole,+loaddll,+ddraw); the game's thread
 * is 0024 in both. Every success below is inferred from the trace's next call
 * through the result, because a +dinput trace prints no return values; each
 * such call sits past a guard the surface tool decodes.
 *
 *  - DirectInput8Create: an IDirectInput8A (dinput-trace-v1a:108, used at :114).
 *  - CreateDevice(system GUID): the profile's HRESULT; on success a device,
 *    on failure NULL written and that HRESULT returned. The recording host's
 *    keyboard is used at :310, its mouse at :857.
 *  - CreateDevice(an enumerated instance GUID): the profile's HRESULT for that
 *    controller, a device on success (:906, used at :953).
 *  - EnumDevices: one call of the guest callback per profile controller, in
 *    order, with a DIDEVICEINSTANCEA built from the profile; DIENUM_STOP
 *    stops it; DI_OK (:886, and 0x00864E1B ignores the result).
 *  - SetDataFormat: DI_OK after checking the format is the one the site
 *    passes (:310-312 keyboard 256 bytes, :857-859 mouse 16 bytes);
 *    DIERR_ACQUIRED while acquired, as Wine does.
 *  - SetCooperativeLevel(hwnd, 6): DI_OK for a window this runtime made,
 *    E_HANDLE for anything else (:825-827, :874).
 *  - Acquire: DI_OK, then DI_NOEFFECT; DIERR_INVALIDPARAM before a format.
 *    Native called it twice in the whole run (:828, :877), so no poll ever
 *    failed. **Never DIERR_INPUTLOST**: the game spins on it at
 *    0x008638A0..0x008638B0 and 0x008633C0..0x008633D0.
 *  - GetDeviceState: DIERR_NOTACQUIRED before Acquire, DIERR_INVALIDPARAM for
 *    a size other than the format's, otherwise DI_OK and `cbData` zero bytes:
 *    every key up and a still mouse, which is the device state of a run nobody
 *    touches (1204 GetDeviceState calls in dinput-trace-v1a, the first at
 *    :838, and no Acquire after the two at initialisation).
 *  - Unacquire: DI_OK when acquired, DI_NOEFFECT when not (:953 for the
 *    enumerated device, never acquired).
 *  - Release: the reference count; the enumerated device reaches 0 (:956).
 *
 * The keyboard and mouse state is all zero **because the replays feed input
 * elsewhere**: harness/replay/wrap_input_replay.c writes ControllersRaw
 * (0x01A52918) after UpdateControllers (0x0077E780), as the native probe does.
 * The `allkeys` break shows the comparison can see the keyboard. A host
 * keyboard (below) is the one other way a key byte reaches the guest, and it
 * is off in every registered run.
 *
 * What still differs from native, and what would show it
 * -------------------------------------------------------
 *  - **Held keys.** In the user's keyboard-driven recording, native
 *    GetDeviceState reports 0x80 for every key held, and this device reports
 *    256 zero bytes in every run. The replays carry the recording through
 *    ControllersRaw instead (above), so the difference is in what the
 *    keyboard's own bytes reach: the keyboard wrapper's two buffers (+0x21C
 *    and +0x31C, filled at 0x00863885), and through the keyboard branch
 *    (0x0042593D..0x004259D3) the raw records of players 0 and 1
 *    (0x01A52C48 + 0x20 per player). The image names the records' block,
 *    0x01A52C48..0x01A52D47, only at 0x00425725 and 0x00425A1F (the branch's
 *    loop) and inside UpdateControllers (0x0077E78A..0x0077E852), whose
 *    result the replay overwrites; which other code reads the wrapper's
 *    buffers is not decoded. **Unmeasured.** What
 *    measures it is the planned native probe harness/replay/native_input_view.c
 *    (not written yet), whose per-tick count of non-zero keyboard-buffer bytes
 *    is non-zero natively during the recording and zero in the lifted twin.
 *    What would show it reaching game state is a compared column that differs
 *    from native only in ticks where the recording holds a key.
 *  - **Focus.** Acquire always succeeds: the runtime has no focus model, and
 *    DISCL_FOREGROUND ties native acquisition to focus. On the recording
 *    desktop native's did too (two Acquire calls in the whole of
 *    dinput-trace-v1a, :828 and :877, so no poll fell back to 0x00863894 or
 *    0x008633B0). A native capture with Acquire calls from those two sites
 *    falsifies it; the `notacquired` break is the lifted run of that capture.
 *
 * The controller's control
 * ------------------------
 * What the enumerated controller does to a lifted run is measured against the
 * same replay under the capture's own profile with only its `dinput8_gamectrl`
 * records removed (and `dinput8_enum` counting 0):
 * dump(dataclasses.replace(parse(capture), gamectrl=())) with
 * tools/s4/device_profile.py, written to scratch when the run is made and
 * never committed, because the rest of it is the capture's host inventory.
 * **Not the default profile**: that differs from a capture's in more than the
 * controller. It answers one processor where the recording host's capture
 * answers 16, and with one the boot run diverges in d_01760000 from tick 379
 * (agents-journals/s4/2026-09-23-native-outcomes.md, "The boot replay's last
 * divergence"); its DirectDraw devices and mastering voice differ too.
 *
 * Under the control EnumDevices calls the callback no times. What that must
 * change: no CreateDevice at 0x00864CAE, no Unacquire at 0x00864CCE or Release
 * at 0x00864CDA, and no IsXInputDevice, so no OLE import, no WMI object, and
 * none of its heap traffic: the three names it allocates and frees, and the
 * DeviceID BSTRs it leaves allocated (runtime/sa2_wbem.h). What it must not
 * change: any compared column, except through where later heap blocks land,
 * which both of those move (the names' allocate-and-free cycle can move the
 * next-fit rover of runtime/sa2_imports.c's heap_alloc). The callback stores
 * nothing (it reads lpddi+4 at 0x00864CA6 and lpddi+0x14 at 0x00864CB4 and
 * returns 1 on every path; tools/s4/dinput_surface.py, claim
 * callback-keeps-nothing). So a column that differs here and also under
 * runtime/sa2_wbem.h's control, which removes only the BSTRs, is the BSTRs'
 * heap shift; one that differs here only is the names' cycle or a store that
 * claim missed.
 *
 * The host keyboard
 * -----------------
 * Design 3.5: "Input: xinput/dinput stubs fed from the host." The keyboard's
 * GetDeviceState, after its zero fill and before the breaks, writes 0x80 into
 * the byte of every key a host source holds, with integer writes and nothing
 * else: the image's controller code (0x00425700) turns the keys its bindings
 * name into the raw records UpdateControllers (0x0077E780) reads, as it does
 * natively. The bindings are Config/Keyboard.cfg's (the name at 0x0089E1A0,
 * read from 0x004054DF): each <key index> goes through the table at
 * 0x0174F348 to a key code, which the keyboard wrapper's map at 0x0089E9E8
 * turns into a DIK code; without the file, the image's own defaults at
 * 0x008AE040 (Enter, the arrows, X, Z, S, A, Q, W). private/install's file
 * binds player 1 to W, S, A and D (up, down, left, right), I, J, U, K, Q, E
 * and Enter; the arrows are player 2's. The answer class is SA2_DI_ANS_HOST
 * when a key was held, and SA2_DI_ANS_IDLE when none was, which is then true.
 * The mouse stays still (a follow-up). A poll index is the 0-based count of
 * the keyboard's DI_OK answers, so it is the same number in a live session
 * and in a headless replay of it. No key has DIK code 0x00, so neither source
 * holds byte 0. Two sources, one at a time:
 *
 *   SA2_INPUT_DI_SCRIPT=path  a text script, read when the run is configured:
 *                            lines "<first_poll> <last_poll> <dik>[,<dik>...]",
 *                            decimal inclusive polls, DIK codes 0x01 to 0xFF
 *                            in hex with 0x, '#' to the end of a line a
 *                            comment, blank lines ignored. A key is held at
 *                            every poll a line listing it covers. Anything
 *                            else, a NUL byte included, is refused at
 *                            configuration, naming the line.
 *   SA2_INPUT_DI_SOURCE=path a FIFO that exists when the run is configured
 *                            (anything else is refused then), opened
 *                            O_RDONLY|O_NONBLOCK at the first keyboard poll
 *                            and checked again. Records of SA2_DI_KEYREC_BYTES:
 *                            uint32 magic SA2_DI_KEYREC_MAGIC, uint32 seq,
 *                            uint8 keys[256] (nonzero: held; byte 0 ignored),
 *                            little-endian, under PIPE_BUF so one write is one
 *                            record. Each poll reads every whole record there
 *                            is without waiting and keeps a partial one for
 *                            the next poll. A poll that read records holds
 *                            every key any of them held, so a key pressed and
 *                            let go between two polls is held at one; a poll
 *                            that read none holds the latest record's keys.
 *                            End of file (no writer) lets every key up after
 *                            the poll that reads it and drops a partial
 *                            record. Gamepad records (SA2_DI_PADREC_MAGIC,
 *                            "The host gamepads" below) may come between
 *                            keyboard records and change no key; a record
 *                            with any other magic stops the run. XInput calls
 *                            read the source too, and the keyboard records
 *                            they read are held for the next keyboard poll,
 *                            as if it had read them. While it is set, SIGTERM asks for the end
 *                            of the run: the next keyboard poll posts WM_QUIT
 *                            as the host (sa2_window_post_quit), as the pump's
 *                            poll budget does, unless one is posted already,
 *                            so the guest leaves its loop through its own code
 *                            and the process exits through exit(); a second
 *                            SIGTERM kills it (SA_RESETHAND). On a Windows
 *                            host the FIFO is a named pipe (\\.\pipe\name)
 *                            that the session creates before the run, opened
 *                            as its client, and SIGTERM is a console control
 *                            event (runtime/sa2_win32.h).
 *   SA2_INPUT_DI_LOG=path   with either source: the keys it applied, one line
 *                            per run of consecutive polls holding the same
 *                            non-empty set, in the script's format, so a live
 *                            session replays headlessly as SA2_INPUT_DI_SCRIPT.
 *                            Created, or refused when it already holds
 *                            anything (two sessions' polls would merge) or is
 *                            not a regular file; each line is written and
 *                            flushed when its run ends, the last at exit
 *                            (atexit), with a '#' line of the counters after
 *                            it. A log holds one configuration's polls, so a
 *                            second configuration in one process refuses the
 *                            log the first one began.
 *
 * Refused at configuration: both sources; the log with no source; a source
 * with the device off or with a SA2_DINPUT_BREAK, under either of which no
 * keyboard poll would carry it. With none of the three set nothing here runs:
 * the same guest writes, answers, counters and traps as before.
 *
 * The host gamepads
 * -----------------
 * Design 3.5's "xinput ... stubs fed from the host": the FIFO source also
 * carries gamepad records (SA2_DI_PADREC_MAGIC, host/player/input.h
 * KeySender::send_pads), and XInput (runtime/sa2_iface.c, sa2_xinput) answers a
 * slot the latest of them holds connected as an Xbox 360 pad with that state;
 * every other slot, and every slot in a run whose source never sent one, as
 * before: the device profile's ERROR_DEVICE_NOT_CONNECTED. A script source
 * carries no gamepads, so every replay answers as before.
 *
 * **The game builds its pad objects once.** The input manager's start-up
 * (0x00864D00) asks XInputGetCapabilities about slots 0 to 3 (0x00864E29) after
 * the keyboard and the mouse, and makes a pad object (vtable 0x008FFFE0) only
 * for a slot answered 0, polling it once at once (slot 5, 0x00863DE0); the
 * rescan (0x00864A00) rebuilds the list of connected slots (vtable 0x008FFF54
 * entries) and never makes a pad object. A pad object polls with
 * XInputGetCapabilities then XInputGetState (slot 7, 0x00863D40) on every frame,
 * and is connected exactly when its last poll's XInputGetCapabilities answered
 * 0 (its byte +0x0C, read by slot 4). So the scan must know the sender's pads:
 * the first XInput call of a FIFO session opens the source and, until it has
 * read a gamepad record, waits for one, at most SA2_INPUT_PAD_WAIT_MS
 * milliseconds (default SA2_DI_PAD_WAIT_MS_DEFAULT; 0 does not wait; a SIGTERM
 * ends it). A sender that knows its pads sends them as soon as the source is
 * open (KeySender does, on every connection, with no pad connected too).
 *
 * **Pads that connect later.** A session whose source sent a gamepad record by
 * then (and with SA2_INPUT_PAD_HOTPLUG not 0) answers the start-up scan and
 * each new pad object's first poll connected for every slot, a slot no record
 * holds connected as a pad at rest (counted in pad_stand_ins), so the game
 * makes all four pad objects and its -r count (0x00864380, below) is four. Each
 * object's every-frame poll then answers the records, so a pad that connects
 * at any time drives the game from its next frame, and one that goes gives
 * its player the keyboard back. The rescan, every 61st frame of the game
 * loop's system-dialog step (0x0040A8F0), compares the number of connected
 * slots with its last one and on a change opens the game's "controllers
 * changed" dialog (state 4), whose "yes", the default, runs Launcher.exe -r
 * through ShellExecuteW and quits: the native game's answer to a pad plugged in
 * after start-up is to restart, and this runtime stands in for no
 * ShellExecuteW. So with SA2_INPUT_PAD_HOTPLUG not 0 the rescan answers a slot
 * connected exactly when it has a pad object, at rest when no record holds it,
 * and not connected when it has none (counted in pad_hidden), whether or not
 * a record came by the scan: the count never changes and the dialog never
 * comes. The calls are told apart by their return address (0x00864E2E, the
 * scan; 0x00863DF6 and 0x00863E1E, the first poll; 0x00864A2C, the rescan),
 * and the first poll's stand-in ends at the first XInput call from anywhere
 * else. A run whose records never hold a pad connected answers every call as
 * before, and no replay has a FIFO source. SA2_INPUT_PAD_HOTPLUG=0 answers
 * every call from the records, as before this, dialog included.
 * tools/s8/pad_e2e.py measures it (its runs hotplug and unplug).
 *
 * **A pad object alone does not drive the game: the player's InputWay does.**
 * The controller fill (0x00425700, under UpdateControllers 0x0077E780) gives a
 * player the next connected pad only when its InputWay (0x0174B5FC + 4 * player)
 * is not 0, and the keyboard otherwise (players 1 and 2 only). InputWay comes
 * from Config/Keyboard.cfg (<Player InputWay=...>, 0x004056DE), 0 in a default
 * one, unless the guest's command line is exactly "-r" (lstrcmpA at 0x00401BD1
 * sets 0x01933F6C): then the game sets it from the pads right after this scan
 * (0x00425AE0), counting the pad objects whose first poll found them connected
 * (0x00864380): player 1 gets 1 for one when both are 0, both players 1 for two
 * or more, and both 0 for none. So a live session that sends pads runs the
 * guest with SA2_GUEST_CMDLINE=-r (runtime/sa2_imports.c). With it, a pad that
 * is connected drives player 1 and the keyboard does not; with none connected
 * the keyboard does. Without it the pad is polled and ignored. InputWay 1 for
 * player 1 also picks the pad's button pictures in the game's prompts
 * (0x00666A27 and its neighbours) and its control map (0x0040C9F0), so with
 * pads that connect later a keyboard player sees the pad's pictures.
 * tools/s8/pad_e2e.py measures all of this on the boot build (the main menu
 * reached on pad records alone, at the same ticks as the keyboard's).
 *
 * Each kind of record has its own seq counter, from 0: a keyboard record's
 * seq counts keyboard records alone and a gamepad record's gamepad records
 * alone, so the keyboard's seqs, its poll observer's ranges and its seq_gaps
 * are what they were before gamepads, pads or none; pad_seq_gaps counts the
 * gamepad records' gaps.
 *
 * Each gamepad record replaces every slot's state. A slot's packet number
 * (XINPUT_STATE.dwPacketNumber) is one more each time its state changes,
 * connection included. Buttons are masked to 0xF3FF, the bits XInputGetState
 * reports (no guide button, nothing at 0x0400 or 0x0800). End of file lets
 * every slot go, as it lets every key up. A connected byte other than 0 or 1
 * stops the run, as another magic does: the bytes are out of step. Gamepads are
 * not in SA2_INPUT_DI_LOG (it replays the keyboard alone; SA2_INPUT_PAD_LOG,
 * "Gamepad replay" below, holds them); its closing line counts their records
 * when there were any.
 *
 *   SA2_INPUT_PAD_WAIT_MS=ms the first XInput call's wait, 0 to 60000, read at
 *                            configuration with a FIFO source (anything but a
 *                            decimal in range is refused then) and ignored
 *                            without one.
 *   SA2_INPUT_PAD_HOTPLUG=0|1 pads that connect later, 1 when unset; read at
 *                            configuration with a FIFO source (anything else is
 *                            refused then) and ignored without one.
 *
 * Gamepad replay
 * --------------
 * SA2_INPUT_DI_LOG replays the keyboard alone, so a session whose player drove a
 * pad (Android's touch overlay is slot 0's pad from the start) did not replay.
 * The pad answers are a function of the slots' states and the calls the game
 * makes, which the guest decides; what came from outside is only which state
 * each slot held at each XInput call. So the pad log keys every change of a
 * slot's state to the ordinal of the XInput call that first answered from it
 * (every call that reaches sa2_dinput_live_pad, from 0: the game's own count of
 * pad questions, as a poll index is its count of keyboard answers), and a
 * replay applies each change before answering that call. The stand-ins of
 * "Pads that connect later" are not logged: they follow from the calls and from
 * the mode line, which says whether that session answered them.
 *
 *   SA2_INPUT_PAD_LOG=path  with a FIFO source, or with SA2_INPUT_PAD_SCRIPT: one
 *                            line per change of a slot's state, written and
 *                            flushed at the XInput call that first answers from
 *                            it, "<call> <polls> <slot> <connected> <buttons>
 *                            <lt> <rt> <lx> <ly> <rx> <ry>" (call the XInput
 *                            call's ordinal, polls the keyboard polls answered
 *                            before it, buttons 0x and four hex digits, the rest
 *                            decimal, sticks signed), after one line "mode
 *                            <hotplug> <stand_ins>" at the first XInput call
 *                            (SA2_INPUT_PAD_HOTPLUG as read, and whether that
 *                            session's scan stands in for absent pads); '#'
 *                            lines are comments, the last the counters at exit.
 *                            Created, or refused when not empty, as
 *                            SA2_INPUT_DI_LOG is. A change that no XInput call
 *                            answered from before the run ended is not written.
 *   SA2_INPUT_PAD_SCRIPT=path a pad log, read whole at configuration, with
 *                            SA2_INPUT_DI_SCRIPT only (a FIFO carries its own
 *                            pads): XInput call n first applies every line of
 *                            call n, in order, then answers as a FIFO session
 *                            would, stand-ins included when the mode line says
 *                            so (no mode line: none). A line whose polls differ
 *                            from the replay's count at that call is applied
 *                            and counted (pad_script_desyncs; the first is said
 *                            on stderr): the replay has left the session's
 *                            path. Lines out of call order, or anything that
 *                            does not parse, are refused at configuration,
 *                            naming the line.
 *
 * With neither set nothing of this runs: the FIFO session's answers, counters
 * and SA2_INPUT_DI_LOG bytes are as before, and a script replay without a pad
 * script answers every XInput call as before (no pad). A replay of a pad
 * session needs the guest command line the session ran with
 * (SA2_GUEST_CMDLINE=-r, "InputWay" above), which the session's records name.
 *
 * Environment
 * -----------
 *   SA2_DINPUT_DEVICE=1      DirectInput8Create returns this device. Off,
 *                            the import answers E_FAIL through the refusal
 *                            marker, as it did before this file: the before
 *                            measurement, and legal only under the default
 *                            profile. Off under a capture's profile is refused
 *                            when the run is configured, because E_FAIL skips
 *                            the enumeration (0x00864D45), the WMI query and
 *                            the XInput scan (0x00864E29) the profile recorded,
 *                            and a run that carries a profile's sha256 must
 *                            have reproduced it.
 *   SA2_DEVICE_PROFILE=path  the capture's device profile
 *                            (runtime/sa2_devprofile.h); unset is the plain
 *                            keyboard-and-mouse default.
 *   SA2_DINPUT_BREAK=<name>  argc | bare-ok | noout | nodevice | notacquired |
 *                            inputlost | allkeys | mousemove; see the source.
 *                            bare-ok and nodevice are refused under a
 *                            capture's profile, for the reason the device off
 *                            is: bare-ok takes the E_FAIL path, and nodevice
 *                            replaces the system devices the profile recorded.
 *   SA2_INPUT_DI_SCRIPT, SA2_INPUT_DI_SOURCE, SA2_INPUT_DI_LOG
 *                            the host keyboard, above; all unset by default.
 *   SA2_INPUT_PAD_LOG, SA2_INPUT_PAD_SCRIPT
 *                            gamepad replay, above; both unset by default.
 */
#ifndef SA2_RUNTIME_DINPUT_H
#define SA2_RUNTIME_DINPUT_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* Method thunks: above sa2_dsound.h's (0x00220000..0x00221000) and below
 * guest_memory.h's 0x00300000, so every synthetic entry point this runtime
 * invents is identifiable by its address alone. */
#define SA2_DI_THUNK_BASE   0x00230000u
#define SA2_DI_THUNK_STRIDE 16u
#define SA2_DI_MAX_TYPES    4u
#define SA2_DI_MAX_SLOTS    32u     /* IDirectInputDevice8A has 32 */
#define SA2_DI_THUNK_END    0x00230800u

/* Objects, one enumeration scratch block and the vtables, above the dsound
 * vtables' end (0x03260400) and below the runtime arena (0x03300000). */
#define SA2_DI_OBJ_BASE      0x03270000u
#define SA2_DI_OBJ_STRIDE    64u
#define SA2_DI_MAX_OBJECTS   64u
#define SA2_DI_SCRATCH_BASE  0x03271000u
#define SA2_DI_SCRATCH_BYTES 0x1000u
/* The scratch block's reserved end, where runtime/sa2_wbem.h's objects start
 * (SA2_WBEM_OBJ_BASE): each file asserts its side against this name. */
#define SA2_DI_SCRATCH_END   0x03272000u
#define SA2_DI_VT_BASE       0x03280000u
#define SA2_DI_VT_END        0x03280400u
#define SA2_DI_VT_STRIDE     (SA2_DI_MAX_SLOTS * 4u)

/* The most game controllers this file enumerates. Both profile readers refuse
 * a profile listing more than runtime/sa2_devprofile.h's SA2_DP_MAX_GAMECTRL,
 * and a static assertion holds that limit to this one, so every profile a
 * reader accepts is one this file reproduces. The object pool holds the
 * IDirectInput8A, the keyboard and the mouse beside one device per enumerated
 * controller. */
#define SA2_DI_MAX_ENUM      16u

/* Consecutive DIERR_INPUTLOST answers after which the `inputlost` break stops
 * the run, naming the guest address that is spinning. */
#define SA2_DI_INPUTLOST_SPIN 256u

/* HRESULTs, by their published values. */
#define SA2_DI_OK                 0x00000000u
#define SA2_DI_NOEFFECT           0x00000001u   /* S_FALSE */
#define SA2_DIERR_ACQUIRED        0x800700AAu
#define SA2_DIERR_NOTACQUIRED     0x8007000Cu
#define SA2_DIERR_INPUTLOST       0x8007001Eu
#define SA2_DIERR_INVALIDPARAM    0x80070057u
#define SA2_DIERR_DEVICENOTREG    0x80040154u
#define SA2_DI_E_HANDLE           0x80070006u

/* How an answer was arrived at. */
enum {
    SA2_DI_ANS_REFUSED = 0,   /* recorded, then trapped by name */
    SA2_DI_ANS_NOOP    = 1,   /* DI_OK or DI_NOEFFECT with nothing to answer */
    SA2_DI_ANS_GUEST   = 2,   /* answered from what the guest passed */
    SA2_DI_ANS_OBJECT  = 3,   /* answered by fabricating an interface */
    SA2_DI_ANS_IDLE    = 4,   /* device state with nothing held: every byte zero */
    SA2_DI_ANS_INVALID = 5,   /* the native error for a call made in the wrong state */
    SA2_DI_ANS_PROFILE = 6,   /* the capture's device profile supplied the answer */
    SA2_DI_ANS_BREAK   = 7,   /* a named SA2_DINPUT_BREAK put the value there */
    SA2_DI_ANS_HOST    = 8,   /* a host keyboard source held at least one key */
};

/* Device kinds. */
enum { SA2_DI_KIND_KEYBOARD = 1, SA2_DI_KIND_MOUSE = 2, SA2_DI_KIND_ENUMERATED = 3 };

#define SA2_DI_LOG_MAX 4096u

typedef struct {
    uint32_t seq;
    uint32_t caller;
    uint32_t self;
    uint16_t type;
    uint16_t slot;
    uint8_t  answered;
    uint8_t  argc;
    uint8_t  depth;          /* 1 for a call from the guest, 2 inside an enumeration */
    uint32_t arg[8];
    uint32_t ret;
    uint32_t out0, out1;     /* what the call wrote through its out-parameter, 0 when nothing */
} sa2_di_call;

typedef struct {
    uint32_t calls;
    uint32_t first_seq;
    uint32_t first_caller;
    uint8_t  answered;
    uint8_t  used;
} sa2_di_slot_stat;

/* One row per device the guest created, kept after a Release. */
typedef struct {
    uint32_t addr;
    uint32_t kind;             /* SA2_DI_KIND_* */
    uint32_t profile_index;    /* the profile's controller, for an enumerated device */
    uint8_t  guid[16];         /* the GUID CreateDevice was given */
    uint32_t create_caller;
    uint32_t create_hr;
    uint32_t format_ptr, format_size, format_obj_size, format_flags;
    uint32_t format_data_size, format_objects, format_rgodf;
    uint32_t coop_hwnd, coop_flags, coop_hwnd_is_a_window;
    uint32_t acquired;
    uint32_t set_format_calls, coop_calls, acquire_calls, unacquire_calls;
    uint32_t state_calls, state_ok;
    uint64_t state_bytes_written;
    uint32_t inputlost_run;    /* consecutive DIERR_INPUTLOST answers, under that break */
    uint32_t refs;
    uint32_t released;
} sa2_di_device;

/* One row per EnumDevices call. */
typedef struct {
    uint32_t caller;
    uint32_t dev_type, flags, callback, ref;
    uint32_t offered;          /* callbacks made */
    uint32_t stopped;          /* a callback answered DIENUM_STOP */
    uint32_t last_callback_ret;
} sa2_di_enum;

typedef struct {
    uint32_t created;          /* DirectInput8Create calls answered with an object */
    uint32_t create_calls;     /* DirectInput8Create calls that reached this file */
    uint32_t create_last_hinst, create_last_version, create_last_caller, create_last_ret;
    uint32_t method_calls;
    uint32_t distinct_methods;
    uint32_t objects_built;
    uint32_t unknown_this;
    uint32_t refused, noop, from_guest, object, idle, invalid, profile, brk;
    uint32_t host;                    /* SA2_DI_ANS_HOST answers: 0 with no host keyboard */
    uint32_t enumerations, devices_enumerated;
    uint64_t state_bytes_written;
    uint32_t state_answers_nonidle;   /* non-zero only under allkeys or mousemove */
    uint32_t log_dropped;
    uint32_t profile_gamectrl;        /* the controllers the profile lists */
} sa2_di_stats;

/* The host keyboard's FIFO record (SA2_INPUT_DI_SOURCE), as host/player/input.cpp
 * writes it: magic, seq, keys[256], little-endian. The seq counts keyboard records
 * alone, 0 for the first and one more for each after it: gamepad records between
 * them have a counter of their own, so a session's keyboard seqs tile from 0 with
 * or without pads (the S5 play-latency checks require that). */
#define SA2_DI_KEYREC_MAGIC 0x4B324153u   /* "SA2K" */
#define SA2_DI_KEYREC_BYTES 264u

/* The host gamepads' record on the same source ("The host gamepads" below):
 * magic, seq (the gamepad records' own counter, 0 for the first and one more for
 * each after it; the keyboard records keep theirs), then SA2_DI_PAD_SLOTS slots of
 * SA2_DI_PADSLOT_BYTES: u8 connected (0 or 1), u8 reserved, u16 buttons
 * (XINPUT_GAMEPAD_* bits), u8 left trigger, u8 right trigger, i16 thumb LX, LY,
 * RX, RY, u8 reserved[2]; little-endian. */
#define SA2_DI_PADREC_MAGIC  0x50324153u  /* "SA2P" */
#define SA2_DI_PAD_SLOTS     4u
#define SA2_DI_PADSLOT_BYTES 16u
#define SA2_DI_PADREC_BYTES  (8u + SA2_DI_PAD_SLOTS * SA2_DI_PADSLOT_BYTES)   /* 72 */

/* How long the first XInput call of a FIFO session waits for the source's first
 * gamepad record, unless SA2_INPUT_PAD_WAIT_MS says otherwise. */
#define SA2_DI_PAD_WAIT_MS_DEFAULT 2000u
#define SA2_DI_PAD_WAIT_MS_MAX     60000u

/* One XInput slot as the latest gamepad record holds it. */
typedef struct {
    uint8_t  connected;
    uint16_t buttons;          /* XINPUT_GAMEPAD_* bits */
    uint8_t  lt, rt;
    int16_t  lx, ly, rx, ry;
    uint32_t packet;           /* XINPUT_STATE.dwPacketNumber: one more per change of state */
} sa2_di_pad;

/* The host keyboard's counters; every one 0 with no source set. */
typedef struct {
    uint32_t polls;                /* keyboard DI_OK answers, so the next poll's index */
    uint32_t polls_held;           /* ... that held at least one key */
    uint64_t keys_written;         /* key bytes written as 0x80 */
    uint32_t script_lines;         /* SA2_INPUT_DI_SCRIPT's key lines */
    uint32_t records;              /* SA2_INPUT_DI_SOURCE records read whole */
    uint32_t last_seq;             /* the last of them's seq */
    uint32_t seq_gaps;             /* records whose seq was not the one before's plus one
                                      (keyboard records only: each kind has its own seq) */
    uint32_t polls_without_writer; /* polls that read end of file: every key up */
    uint32_t partial_dropped;      /* bytes of a record its writer closed before finishing */
    uint32_t log_lines;            /* lines SA2_INPUT_DI_LOG holds */
    uint32_t stop_requested;       /* a SIGTERM asked for the end of the run */
    uint32_t quit_posted;          /* ... and a keyboard poll posted WM_QUIT for it: 0 when
                                      one was posted already (the window's record says by whom) */
    uint32_t quit_poll;            /* that poll's index */
    /* The host gamepads; every one 0 until the source sends a gamepad record. */
    uint32_t pad_records;          /* SA2P records read whole */
    uint32_t pad_last_seq;         /* the last of them's seq */
    uint32_t pad_seq_gaps;         /* SA2P records whose seq was not the SA2P record before's
                                      plus one */
    uint32_t pad_changes;          /* slot states a record changed, over the four slots */
    uint32_t pad_wait_ms;          /* the first XInput call's bound (0 before that call) */
    uint32_t pad_waited_ms;        /* how long it waited, about; 0 when a record was there */
    uint32_t pad_wait_found;       /* 1 when a gamepad record ended that wait or made it moot */
    uint32_t pad_answers;          /* XInput calls answered from a connected slot */
    /* Pads that connect later ("Pads that connect later" above); every one 0 in a
     * session whose source sent no gamepad record. */
    uint32_t pad_hotplug;          /* 1 when the start-up scan stands in for absent pads */
    uint32_t pad_objects;          /* the slots that scan answered connected, a bit each */
    uint32_t pad_stand_in_slots;   /* ... of them, the slots no record then held connected */
    uint32_t pad_stand_ins;        /* XInput calls answered connected at rest for such a slot */
    uint32_t pad_hidden;           /* rescan calls answered not connected for a connected slot
                                      with no pad object */
    /* Gamepad replay ("Gamepad replay" above); every one 0 with neither variable set. */
    uint32_t xi_calls;             /* XInput calls a pad source answered (the next call's ordinal) */
    uint32_t pad_log_lines;        /* change lines SA2_INPUT_PAD_LOG holds */
    uint32_t pad_script_lines;     /* SA2_INPUT_PAD_SCRIPT's change lines */
    uint32_t pad_script_applied;   /* ... applied by the end */
    uint32_t pad_script_desyncs;   /* ... applied at a call whose keyboard polls differed */
    uint32_t pad_script_first_desync;  /* the first such line's call */
} sa2_di_input_stats;

void sa2_dinput_config_from_env(void);
int  sa2_dinput_device_enabled(void);
const char *sa2_dinput_break_name(void);   /* "" when none */
const char *sa2_dinput_input_source(void); /* "", "script" or "fifo" */
void sa2_dinput_input_stats_get(sa2_di_input_stats *out);

/* Optional observer after a FIFO keyboard poll applies complete records and
 * updates its counters/log. No call for a script, no source, or no new records.
 * `first_seq`, `last_seq` and `records` count keyboard records only, on their own
 * seq counter: gamepad records between them, which have a counter of their own,
 * are not counted, so the ranges tile the keyboard seqs from 0 with no gaps.
 * A definition must not access the guest, floating point or the scheduler. */
void sa2_dinput_host_records_read(uint32_t poll, uint32_t first_seq,
                                  uint32_t last_seq, uint32_t records) __attribute__((weak));

/* XInput's question about slot `slot` (runtime/sa2_iface.c, sa2_xinput), from
 * the call at `caller`: 1, with *out the slot's state, when the live source's
 * latest gamepad record holds that slot connected; 0, *out untouched, otherwise,
 * and always 0 without a FIFO source. Reads the source first, without waiting,
 * except at the first call of a FIFO session ("The host gamepads"). The calls
 * "Pads that connect later" names by `caller` (the return address) answer as it
 * says: 2, with *out a pad at rest, for a slot no record holds connected, or 0
 * for a connected one with no pad object. */
int sa2_dinput_live_pad(uint32_t slot, uint32_t caller, sa2_di_pad *out);

/* DINPUT8.dll!DirectInput8Create(hinst, dwVersion, riidltf, ppvOut, punkOuter)
 * with the device enabled: `a` is the import's five stack arguments. Writes
 * the IDirectInput8A to a[3] and returns DI_OK; refuses by name any version,
 * IID or outer the one site (0x00864D14..0x00864D33) cannot pass. */
uint32_t sa2_dinput_create8(const uint32_t *a, uint32_t caller);

void sa2_dinput_stats_get(sa2_di_stats *out);
uint32_t           sa2_dinput_log_count(void);
const sa2_di_call *sa2_dinput_log_at(uint32_t i);

uint32_t    sa2_dinput_type_count(void);
const char *sa2_dinput_type_name(uint32_t type);
const char *sa2_dinput_method_name(uint32_t type, uint32_t slot);   /* NULL when unnamed */
int         sa2_dinput_method_argc(uint32_t type, uint32_t slot);   /* -1 when unnamed */
int         sa2_dinput_method_implemented(uint32_t type, uint32_t slot);
const char *sa2_dinput_method_flags(uint32_t type, uint32_t slot);
const sa2_di_slot_stat *sa2_dinput_slot_stat_at(uint32_t type, uint32_t slot);

uint32_t             sa2_dinput_device_count(void);
const sa2_di_device *sa2_dinput_device_at(uint32_t i);
uint32_t             sa2_dinput_enum_count(void);
const sa2_di_enum   *sa2_dinput_enum_at(uint32_t i);

#ifdef __cplusplus
}
#endif
#endif
