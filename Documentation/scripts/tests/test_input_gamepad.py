"""Controllers end to end, without the game: the gamepad records of a live input source.

Contract (A) of Documentation/decisions/2026-10-12-S7-all-platforms-by-tuesday.md's push: the
live input source (SA2_INPUT_DI_SOURCE, the FIFO the keyboard records already use) carries
gamepad records beside keyboard records, and the runtime's XInput ordinals answer a connected
slot from the latest of them (runtime/sa2_dinput.h, "The host gamepads"; runtime/sa2_iface.c,
sa2_xinput). Two halves are driven here:

  * the runtime: runtime/sa2_dinput.c and runtime/sa2_iface.c, compiled with
    runtime/sa2_guest_call.c and runtime/sa2_devprofile.c and a driver that stands in for guest
    memory and the trap (as harness/dinput_poll_observer_probe.c does), fed SA2K and SA2P records
    through a real FIFO. The driver calls the keyboard through its fabricated DirectInput vtable
    and XInput through sa2_xinput, and prints every answer; each test compares the transcript
    with literal lines: the capabilities and state bytes, packet numbers, disconnect and
    reconnect, the masked buttons, keyboard records read by an XInput call held for the next
    keyboard poll, each kind's own seq counter (the keyboard's poll ranges tile its seqs from 0
    with gamepad records between them, as the S5 play-latency checks require), the boot wait
    (found, timed out, off), the refusals, and a run with no gamepad record, or with a script or
    no source, answering exactly as before. Pads that connect later are driven by the image's
    call sites (the return addresses the import sees): the start-up scan and a new object's first
    poll standing in for the absent pads, the every-frame poll answering the records, the rescan
    answering a slot connected exactly when it has a pad object, and SA2_INPUT_PAD_HOTPLUG=0
    answering everything from the records.
  * the sender: host/player/input.cpp's KeySender::send_pads, compiled with a driver into a real
    FIFO: the 72-byte record byte for byte, one record per change and none for a repeat, a seq
    counter of the pad records' own, so the key records' seqs, the key log's and the report's
    tile from 0 with pad records between them, the pads re-sent on every new connection (the boot
    wait needs them), owed through ENXIO and a full pipe, close() letting every slot go, and the
    report's "pads" member, absent until send_pads is first called.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
ZIG = ROOT / "private" / "predecessor-tools" / "zig" / "zig-x86_64-linux-0.15.2" / "zig"
SDL_INCLUDE = ROOT / "private" / "s5-tools" / "install" / "include"
GXX = shutil.which(os.environ.get("CXX", "g++"))
RUNTIME_SOURCES = ["runtime/sa2_dinput.c", "runtime/sa2_iface.c", "runtime/sa2_guest_call.c",
                   "runtime/sa2_devprofile.c"]

RUNTIME_DRIVER = r"""
#include <errno.h>
#include <fcntl.h>
#include <signal.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#include "cpu_state.h"
#include "function_table.h"
#include "guest_memory.h"
#include "remill_runtime.h"
#include "sa2_devprofile.h"
#include "sa2_dinput.h"
#include "sa2_iface.h"
#include "sa2_trap.h"
#include "sa2_window.h"

#define LO 0x03000000u
#define HI 0x03300000u
#define DATA (LO + 0x4000u)
#define STACK (LO + 0xeff00u)
static uint8_t mem[HI - LO];
static sa2_lifted_fn ft[SA2_DI_MAX_TYPES * SA2_DI_MAX_SLOTS];
static char note[1024];
int sa2_peek(uint32_t a, uint32_t n, void *out) {
    if (a < LO || a >= HI || n > HI - a) return SA2_E_RANGE;
    memcpy(out, mem + a - LO, n); return SA2_OK;
}
int sa2_poke(uint32_t a, uint32_t n, const void *in) {
    if (a < LO || a >= HI || n > HI - a) return SA2_E_RANGE;
    memcpy(mem + a - LO, in, n); return SA2_OK;
}
int sa2_guest_fill(uint32_t a, int v, uint32_t n) {
    if (a < LO || a >= HI || n > HI - a) return SA2_E_RANGE;
    memset(mem + a - LO, v, n); return SA2_OK;
}
int sa2_ft_add(uint32_t a, sa2_lifted_fn f, const char *name, uint8_t kind) {
    (void)name; (void)kind;
    if (a < SA2_DI_THUNK_BASE || a >= SA2_DI_THUNK_END) return 0;
    ft[(a - SA2_DI_THUNK_BASE) / SA2_DI_THUNK_STRIDE] = f; return 0;
}
sa2_lifted_fn sa2_ft_lookup(uint32_t a) {
    if (a < SA2_DI_THUNK_BASE || a >= SA2_DI_THUNK_END) return NULL;
    return ft[(a - SA2_DI_THUNK_BASE) / SA2_DI_THUNK_STRIDE];
}
uint32_t sa2_rt_get_gpr(const void *s, int i) { return ((const sa2_cpu_state_t *)s)->gpr[i]; }
void sa2_rt_set_gpr(void *s, int i, uint32_t v) { ((sa2_cpu_state_t *)s)->gpr[i] = v; }
void sa2_trap_note(const char *fmt, ...) {
    va_list ap; va_start(ap, fmt); vsnprintf(note, sizeof note, fmt, ap); va_end(ap);
}
void sa2_trap_raise(uint32_t status, uint32_t pc) {
    printf("TRAP %u %s\n", status, note); fflush(stdout); exit(3);
}
const sa2_wnd_window *sa2_window_of(uint32_t hwnd) {
    static const sa2_wnd_window w = {0}; return hwnd == 1 ? &w : NULL;
}
void sa2_window_stats(sa2_wnd_stats *out) { memset(out, 0, sizeof *out); }
void sa2_window_post_quit(uint32_t code, int host) { (void)code; (void)host; }
/* The poll observer the S5 play-latency receipts are built from (tools/s5/play_receipt.py). */
void sa2_dinput_host_records_read(uint32_t poll, uint32_t first, uint32_t last, uint32_t n) {
    printf("seen poll=%u seq=%u..%u records=%u\n", poll, first, last, n);
}

static uint32_t get32(uint32_t a) { uint32_t v = 0; sa2_peek(a, 4, &v); return v; }
static void put32(uint32_t a, uint32_t v) { sa2_poke(a, 4, &v); }
static uint32_t slot(uint32_t self, uint32_t k, unsigned argc, const uint32_t *a) {
    sa2_cpu_state_t st; memset(&st, 0, sizeof st);
    uint32_t esp = STACK - 4u * (argc + 2u); st.gpr[SA2_ESP] = esp;
    put32(esp, 0x00401000u); put32(esp + 4, self);
    for (unsigned i = 0; i < argc; i++) put32(esp + 8 + 4 * i, a[i]);
    uint32_t thunk = get32(get32(self) + 4 * k);
    sa2_lifted_fn f = sa2_ft_lookup(thunk);
    if (!f) { printf("no thunk\n"); exit(2); }
    f(&st, thunk, NULL);
    return st.gpr[SA2_EAX];
}
static uint32_t keyboard(void) {
    static const uint8_t iid[16] = {0x30,0x80,0x79,0xbf,0x3a,0x48,0xa2,0x4d,0xaa,0x99,0x5d,0x64,0xed,0x36,0x97,0};
    static const uint8_t guid[16] = {0x61,0x2b,0x1d,0x6f,0xa0,0xd5,0xcf,0x11,0xbf,0xc7,0x44,0x45,0x53,0x54,0,0};
    sa2_poke(DATA, 16, iid); sa2_poke(DATA + 16, 16, guid);
    uint32_t a[5] = {0, 0x800, DATA, DATA + 32, 0}; sa2_dinput_create8(a, 0x00401000);
    uint32_t b[3] = {DATA + 16, DATA + 36, 0}; slot(get32(DATA + 32), 3, 3, b);
    uint32_t k = get32(DATA + 36), fmt[6] = {24, 16, 2, 256, 256, 0}; sa2_poke(DATA + 64, sizeof fmt, fmt);
    uint32_t f = DATA + 64; slot(k, 11, 1, &f);
    uint32_t coop[2] = {1, 6}; slot(k, 13, 2, coop);
    slot(k, 7, 0, NULL);
    return k;
}
/* A keyboard poll: "keys <hr> <dik>..." */
static void poll_keys(uint32_t k) {
    uint32_t a[2] = {256, DATA + 512};
    uint32_t hr = slot(k, 9, 2, a);
    uint8_t got[256]; sa2_peek(a[1], 256, got);
    printf("keys %u", hr);
    for (int i = 0; i < 256; i++) if (got[i]) printf(" %02X=%02X", i, got[i]);
    printf("\n");
}
/* XInput as imp_xinput calls it: "<name> <slot> <status> <bytes>". */
static void xi(uint32_t ordinal, uint32_t s) {
    uint32_t argv[3] = {s, ordinal == 4 ? 1u : 0x5000u, 0x5000u}, buf[32], len = 0;
    const uint32_t st = sa2_xinput(ordinal, argv, 0x00863DF1u, buf, &len);
    printf("%s %u %u", ordinal == 2 ? "state" : ordinal == 3 ? "vibrate" : "caps", s, st);
    for (uint32_t i = 0; i < len; i++) printf(" %02X", buf[i]);
    printf("\n");
}
static void scan(void) { for (uint32_t s = 0; s < 4; s++) xi(4, s); }
/* XInput from one of the image's call sites, by the return address the import sees: the
 * start-up scan, a new pad object's first poll, the every-frame poll, the rescan. */
#define BOOT 0x00864E2Eu
#define INIT_CAPS 0x00863DF6u
#define INIT_STATE 0x00863E1Eu
#define FRAME_CAPS 0x00863D56u
#define FRAME_STATE 0x00863D91u
#define RESCAN 0x00864A2Cu
static void xr(uint32_t ordinal, uint32_t s, uint32_t ret, const char *site) {
    uint32_t argv[3] = {s, ordinal == 4 ? 1u : 0x5000u, 0x5000u}, buf[32], len = 0;
    const uint32_t st = sa2_xinput(ordinal, argv, ret, buf, &len);
    printf("%s %s %u %u", site, ordinal == 2 ? "state" : "caps", s, st);
    if (ordinal == 2 && st == 0) for (uint32_t i = 0; i < len; i++) printf(" %02X", buf[i]);
    printf("\n");
}
/* The game's start-up scan: for each slot answered 0, the new object's first poll. */
static void boot_scan(void) {
    for (uint32_t s = 0; s < 4; s++) {
        uint32_t argv[3] = {s, 1u, 0x5000u}, buf[32], len = 0;
        const uint32_t st = sa2_xinput(4, argv, BOOT, buf, &len);
        printf("boot caps %u %u\n", s, st);
        if (st) continue;
        uint32_t a2[3] = {s, 1u, 0x5000u};
        const uint32_t c = sa2_xinput(4, a2, INIT_CAPS, buf, &len);
        printf("init caps %u %u\n", s, c);
        if (!c) xr(2, s, INIT_STATE, "init");
    }
}
static void frame(uint32_t s) {
    uint32_t argv[3] = {s, 1u, 0x5000u}, buf[32], len = 0;
    const uint32_t st = sa2_xinput(4, argv, FRAME_CAPS, buf, &len);
    printf("frame caps %u %u\n", s, st);
    if (!st) xr(2, s, FRAME_STATE, "frame");
}
static void rescan(void) {
    printf("rescan");
    for (uint32_t s = 0; s < 4; s++) {
        uint32_t argv[3] = {s, 1u, 0x5000u}, buf[32], len = 0;
        printf(" %u", sa2_xinput(4, argv, RESCAN, buf, &len));
    }
    printf("\n");
}
static void later(void) {
    sa2_di_input_stats st; sa2_dinput_input_stats_get(&st);
    printf("later hotplug=%u objects=0x%x stand_in_slots=%u stand_ins=%u hidden=%u answers=%u\n",
           st.pad_hotplug, st.pad_objects, st.pad_stand_in_slots, st.pad_stand_ins, st.pad_hidden,
           st.pad_answers);
}
static void keyrec(int fd, uint32_t seq, const uint8_t *keys, unsigned n) {
    uint8_t r[264] = {0}; uint32_t magic = 0x4b324153;
    for (unsigned i = 0; i < 4; i++) { r[i] = (uint8_t)(magic >> (8 * i)); r[4 + i] = (uint8_t)(seq >> (8 * i)); }
    for (unsigned i = 0; i < n; i++) r[8 + keys[i]] = 0x80;
    if (write(fd, r, sizeof r) != (ssize_t)sizeof r) { printf("short write\n"); exit(2); }
}
typedef struct { uint8_t c; uint16_t b; uint8_t lt, rt; int16_t lx, ly, rx, ry; } pad;
static void padrec(int fd, uint32_t seq, const pad *p, uint32_t magic) {
    uint8_t r[72] = {0};
    for (unsigned i = 0; i < 4; i++) { r[i] = (uint8_t)(magic >> (8 * i)); r[4 + i] = (uint8_t)(seq >> (8 * i)); }
    for (unsigned s = 0; s < 4; s++) {
        uint8_t *q = r + 8 + 16 * s;
        q[0] = p[s].c; q[2] = (uint8_t)p[s].b; q[3] = (uint8_t)(p[s].b >> 8); q[4] = p[s].lt; q[5] = p[s].rt;
        int16_t ax[4] = {p[s].lx, p[s].ly, p[s].rx, p[s].ry};
        for (unsigned j = 0; j < 4; j++) { q[6 + 2 * j] = (uint8_t)ax[j]; q[7 + 2 * j] = (uint8_t)((uint16_t)ax[j] >> 8); }
    }
    if (write(fd, r, sizeof r) != (ssize_t)sizeof r) { printf("short write\n"); exit(2); }
}
static void stats(void) {
    sa2_di_input_stats st; sa2_dinput_input_stats_get(&st);
    sa2_iface_stats is; sa2_iface_stats_get(&is);
    printf("input polls=%u held=%u records=%u last_seq=%u gaps=%u nowriter=%u dropped=%u "
           "pad_records=%u pad_last_seq=%u pad_gaps=%u pad_changes=%u pad_wait_ms=%u found=%u "
           "answers=%u\n",
           st.polls, st.polls_held, st.records, st.last_seq, st.seq_gaps, st.polls_without_writer,
           st.partial_dropped, st.pad_records, st.pad_last_seq, st.pad_seq_gaps, st.pad_changes,
           st.pad_wait_ms, st.pad_wait_found, st.pad_answers);
    printf("xinput getstate=%u setstate=%u getcaps=%u absent=%u present=%u live=%u\n",
           is.xinput_getstate, is.xinput_setstate, is.xinput_getcaps, is.pad_absent,
           is.pad_present, is.pad_live);
}
static void waited(void) {
    sa2_di_input_stats st; sa2_dinput_input_stats_get(&st);
    printf("waited %u\n", st.pad_waited_ms);
}
/* Gamepad replay: one session's calls, with its records (w >= 0, the FIFO session that
 * writes the pad log) or without (w < 0, the replay of that log as a pad script). */
static void padseq(int w, uint32_t k) {
    const uint8_t enter[] = {0x1C};
    pad p[4] = {{0}};
    if (w >= 0) padrec(w, 0, p, 0x50324153);                /* no pad at boot: stand-ins */
    boot_scan();
    poll_keys(k);
    frame(0);
    if (w >= 0) {
        p[0] = (pad){1, 0x0010, 0, 0, 0, 0, 0, 0};          /* a pad comes, START held */
        padrec(w, 1, p, 0x50324153);
        keyrec(w, 0, enter, 1);
    }
    poll_keys(k);                                            /* reads both; the pad waits */
    frame(0); frame(1);
    if (w >= 0) {
        p[0] = (pad){1, 0x1000, 0, 255, -32768, 32767, 100, -100};
        padrec(w, 2, p, 0x50324153);
        p[0].b = 0x1010;                                     /* two changes before one call */
        padrec(w, 3, p, 0x50324153);
    }
    frame(0);
    rescan();
    poll_keys(k);
    if (w >= 0) { p[0] = (pad){0}; padrec(w, 4, p, 0x50324153); }
    frame(0); rescan();
    poll_keys(k);
    later();
}
static void padstats(void) {
    sa2_di_input_stats st; sa2_dinput_input_stats_get(&st);
    printf("padreplay calls=%u log_lines=%u script_lines=%u applied=%u desyncs=%u first=%u\n",
           st.xi_calls, st.pad_log_lines, st.pad_script_lines, st.pad_script_applied,
           st.pad_script_desyncs, st.pad_script_first_desync);
}

int main(int argc, char **argv) {
    if (argc != 3) return 2;
    const char *mode = argv[1], *dir = argv[2];
    char path[600], log[600];
    snprintf(path, sizeof path, "%s/input.fifo", dir);
    snprintf(log, sizeof log, "%s/input.log", dir);
    unsetenv("SA2_DEVICE_PROFILE"); unsetenv("SA2_DINPUT_BREAK"); unsetenv("SA2_INPUT_DI_SCRIPT");
    unsetenv("SA2_INPUT_DI_SOURCE"); unsetenv("SA2_INPUT_DI_LOG"); unsetenv("SA2_XINPUT_PAD");
    unsetenv("SA2_INPUT_PAD_HOTPLUG"); unsetenv("SA2_INPUT_PAD_LOG"); unsetenv("SA2_INPUT_PAD_SCRIPT");
    setenv("SA2_DINPUT_DEVICE", "1", 1);
    char padlog[600], padlog2[600];
    snprintf(padlog, sizeof padlog, "%s/pad.log", dir);
    snprintf(padlog2, sizeof padlog2, "%s/pad-replay.log", dir);
    if (!strcmp(mode, "padrec") || !strcmp(mode, "padrec-script")) {
        setenv("SA2_INPUT_PAD_LOG", padlog, 1);
        setenv("SA2_INPUT_PAD_WAIT_MS", "0", 1);
    }
    if (!strncmp(mode, "script-pad", 10)) {
        setenv("SA2_INPUT_DI_SCRIPT", log, 1);              /* the recording's keyboard log */
        setenv("SA2_INPUT_PAD_SCRIPT", padlog, 1);
        setenv("SA2_INPUT_PAD_LOG", padlog2, 1);
    }
    if (!strcmp(mode, "unset-padlog")) setenv("SA2_INPUT_PAD_LOG", padlog, 1);
    if (!strcmp(mode, "padrec-script")) setenv("SA2_INPUT_PAD_SCRIPT", padlog2, 1);
    const int fifo = strncmp(mode, "script", 6) && strncmp(mode, "unset", 5);
    if (fifo) {
        if (mkfifo(path, 0600)) return 2;
        setenv("SA2_INPUT_DI_SOURCE", path, 1);
        setenv("SA2_INPUT_DI_LOG", log, 1);
    }
    if (!strcmp(mode, "script")) {
        FILE *f = fopen(path, "w"); fputs("0 0 0x1C\n", f); fclose(f);
        setenv("SA2_INPUT_DI_SCRIPT", path, 1);
        setenv("SA2_INPUT_PAD_WAIT_MS", "5000", 1);      /* ignored without a FIFO */
    }
    if (!strcmp(mode, "unset")) setenv("SA2_INPUT_PAD_WAIT_MS", "5000", 1);
    if (!strcmp(mode, "badwait")) setenv("SA2_INPUT_PAD_WAIT_MS", "60001", 1);
    if (!strcmp(mode, "pads") || !strcmp(mode, "keyboard") || !strcmp(mode, "interleave")
        || !strcmp(mode, "badconn") || !strcmp(mode, "badmagic"))
        setenv("SA2_INPUT_PAD_WAIT_MS", "0", 1);
    if (!strncmp(mode, "later", 5)) setenv("SA2_INPUT_PAD_WAIT_MS", "0", 1);
    if (!strcmp(mode, "later-off")) setenv("SA2_INPUT_PAD_HOTPLUG", "0", 1);
    if (!strcmp(mode, "later-on")) setenv("SA2_INPUT_PAD_HOTPLUG", "1", 1);
    if (!strcmp(mode, "badhotplug")) setenv("SA2_INPUT_PAD_HOTPLUG", "yes", 1);
    if (!strcmp(mode, "timeout")) setenv("SA2_INPUT_PAD_WAIT_MS", "300", 1);
    if (!strcmp(mode, "found")) setenv("SA2_INPUT_PAD_WAIT_MS", "10000", 1);
    sa2_devprofile_config_from_env();
    sa2_dinput_config_from_env();
    sa2_iface_config_from_env();
    printf("source %s\n", sa2_dinput_input_source());
    const uint32_t k = keyboard();
    /* A holder, as the launcher's: the reader never reads end of file. */
    const int w = fifo ? open(path, O_RDWR | O_NONBLOCK) : -1;
    const pad none[4] = {{0}};
    const uint8_t enter[] = {0x1C}, up[] = {0xC8};
    if (!strcmp(mode, "pads")) {
        pad p[4] = {{0}};
        p[0] = (pad){1, 0x0010, 0, 0, 0, 0, 0, 0};          /* START held at launch */
        padrec(w, 0, p, 0x50324153);
        scan();                                              /* the game's boot scan */
        poll_keys(k);
        xi(4, 0); xi(2, 0);                                  /* the pad object's poll */
        keyrec(w, 0, enter, 1);                              /* each kind counts from 0 */
        p[0] = (pad){1, 0x1000, 0, 255, -32768, 32767, 100, -100};   /* A, RT, the sticks */
        padrec(w, 1, p, 0x50324153);
        xi(4, 0); xi(2, 0);                                  /* reads both records */
        keyrec(w, 1, up, 1);
        xi(2, 0);                                            /* reads the keyboard record */
        poll_keys(k);                                        /* Enter and Up: both records */
        poll_keys(k);                                        /* the latest's: Up */
        padrec(w, 2, p, 0x50324153);                         /* the same state: no new packet */
        xi(2, 0);
        p[1] = (pad){1, 0xFFFF, 1, 2, 3, 4, 5, 6};           /* a second pad; 0x0C00 masked */
        padrec(w, 3, p, 0x50324153);
        xi(4, 1); xi(2, 1); xi(2, 0); xi(3, 1);
        p[0].c = 0; p[0].b = 0;                              /* the first unplugged */
        padrec(w, 4, p, 0x50324153);
        xi(4, 0); xi(2, 0); xi(3, 0);
        p[0] = (pad){1, 0, 0, 0, 0, 0, 0, 0};                /* and plugged in again */
        padrec(w, 6, p, 0x50324153);                         /* a pad seq gap: 5 never came */
        xi(2, 0); xi(4, 2); xi(2, 3);
        close(w);                                            /* no holder now */
        const int w2 = open(path, O_WRONLY | O_NONBLOCK);    /* a last writer, then none */
        close(w2);
        xi(2, 1);                                            /* end of file: every slot let go */
        poll_keys(k);
        stats();
        return 0;
    }
    if (!strcmp(mode, "interleave")) {
        /* Pad records between key records, each kind on its own counter from 0: the
         * keyboard's poll ranges tile 0..3 with no gap, as the S5 latency checks require. */
        pad p[4] = {{0}};
        p[0].c = 1;
        padrec(w, 0, p, 0x50324153);
        keyrec(w, 0, enter, 1);
        p[0].b = 0x1000; padrec(w, 1, p, 0x50324153);
        scan();                                              /* the boot scan reads all three */
        poll_keys(k);
        keyrec(w, 1, up, 1);
        p[0].b = 0; padrec(w, 2, p, 0x50324153);
        keyrec(w, 2, up, 0);                                 /* every key up */
        poll_keys(k);
        p[0].b = 0x0010; padrec(w, 3, p, 0x50324153);
        poll_keys(k);                                        /* a gamepad record alone */
        keyrec(w, 3, enter, 1);
        poll_keys(k);
        stats();
        return 0;
    }
    if (!strcmp(mode, "keyboard")) {                         /* no gamepad record at all */
        keyrec(w, 0, enter, 1);
        scan();                                              /* reads the record, holds it */
        keyrec(w, 1, up, 1);
        poll_keys(k);                                        /* Enter and Up */
        xi(2, 0); xi(3, 0); xi(4, 0);
        poll_keys(k);
        stats();
        return 0;
    }
    if (!strcmp(mode, "timeout")) {                          /* no sender: the wait's bound */
        keyrec(w, 0, enter, 1);
        scan();
        stats(); waited();
        return 0;
    }
    if (!strcmp(mode, "found")) {                            /* the sender comes 150 ms late */
        fflush(stdout);
        const pid_t pid = fork();
        if (pid == 0) {
            struct timespec t = {0, 150000000L}; nanosleep(&t, NULL);
            pad p[4] = {{0}}; p[2] = (pad){1, 0x2000, 0, 0, 0, 0, 0, 0};
            padrec(w, 0, p, 0x50324153);
            _exit(0);
        }
        scan();
        waitpid(pid, NULL, 0);
        stats(); waited();
        return 0;
    }
    if (!strcmp(mode, "later") || !strcmp(mode, "later-off") || !strcmp(mode, "later-on")) {
        /* Pads that connect later: the sender's first record holds no pad. */
        pad p[4] = {{0}};
        padrec(w, 0, p, 0x50324153);
        boot_scan();                                         /* four objects, at rest */
        frame(0); frame(1);                                  /* the records: none connected */
        rescan();                                            /* every slot has an object */
        xr(4, 0, INIT_CAPS, "init");                         /* the first poll's stand-in is over */
        p[0] = (pad){1, 0x0010, 0, 0, 0, 0, 0, 0};           /* a pad comes: START held */
        padrec(w, 1, p, 0x50324153);
        frame(0);
        rescan();
        p[0].c = 0; p[0].b = 0;                              /* and goes */
        padrec(w, 2, p, 0x50324153);
        frame(0);
        rescan();
        later();
        stats();
        return 0;
    }
    if (!strcmp(mode, "later-boot")) {
        /* A pad at boot in slot 1, unplugged, then a second in slot 0. */
        pad p[4] = {{0}};
        p[1] = (pad){1, 0x1000, 0, 0, 0, 0, 0, 0};
        padrec(w, 0, p, 0x50324153);
        boot_scan();
        rescan();
        p[1].c = 0; p[1].b = 0;
        padrec(w, 1, p, 0x50324153);
        frame(1); rescan();
        p[0] = (pad){1, 0x0020, 0, 0, 0, 0, 0, 0};
        padrec(w, 2, p, 0x50324153);
        frame(0); rescan();
        later();
        return 0;
    }
    if (!strcmp(mode, "later-nopads")) {
        /* No gamepad record by the scan: no stand-ins, and the rescan hides a later pad. */
        keyrec(w, 0, enter, 1);
        boot_scan();
        rescan();
        pad p[4] = {{0}};
        p[2] = (pad){1, 0, 0, 0, 0, 0, 0, 0};
        padrec(w, 0, p, 0x50324153);
        rescan();
        frame(2);                                            /* the records, as ever */
        later();
        return 0;
    }
    if (!strcmp(mode, "badhotplug")) return 0;
    if (!strcmp(mode, "padrec") || !strcmp(mode, "script-padplay")) {
        padseq(w, k);
        padstats();
        return 0;
    }
    if (!strcmp(mode, "script-paddesync")) {
        poll_keys(k);                                        /* one poll the session never had */
        padseq(-1, k);
        padstats();
        return 0;
    }
    if (!strcmp(mode, "badconn")) {
        pad p[4] = {{0}}; p[1].c = 2;
        padrec(w, 0, p, 0x50324153);
        scan();
        return 0;
    }
    if (!strcmp(mode, "badmagic")) {
        padrec(w, 0, none, 0x50324154);
        scan();
        return 0;
    }
    /* script, unset: XInput as before, no wait though SA2_INPUT_PAD_WAIT_MS is set */
    scan(); xi(2, 0); xi(3, 0);
    poll_keys(k);
    stats();
    return 0;
}
"""


@pytest.fixture(scope="module")
def runtime_driver(tmp_path_factory) -> Path:
    if not ZIG.is_file():
        pytest.skip(f"the pinned zig is not present: {ZIG}")
    work = tmp_path_factory.mktemp("gamepad-runtime")
    (work / "driver.c").write_text(RUNTIME_DRIVER)
    exe = work / "driver"
    env = dict(os.environ, TMPDIR=str(work), ZIG_GLOBAL_CACHE_DIR=str(work / "cache"))
    r = subprocess.run([str(ZIG), "cc", "-target", "x86_64-linux-musl", "-O1", "-Wall",
                        "-I" + str(ROOT / "runtime"), str(work / "driver.c"),
                        *[str(ROOT / s) for s in RUNTIME_SOURCES], "-o", str(exe)],
                       env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-3000:]
    return exe


def drive(exe: Path, mode: str, tmp_path: Path, rc: int = 0) -> tuple[list[str], str]:
    r = subprocess.run([str(exe), mode, str(tmp_path)], capture_output=True, text=True, timeout=30)
    assert r.returncode == rc, r.stdout + r.stderr
    return r.stdout.splitlines(), r.stderr


CAPS_360 = "01 01 04 00 FF F3 FF FF C0 FF C0 FF C0 FF C0 FF FF 00 FF 00"
ABSENT_CAPS = "1167" + " 00" * 20
ABSENT_STATE = "1167" + " 00" * 16


def test_a_connected_pad_answers_as_an_xbox_360_pad_from_the_latest_record(runtime_driver, tmp_path):
    out, err = drive(runtime_driver, "pads", tmp_path)
    assert out == [
        "source fifo",
        # the boot scan: slot 0 connected by the record sent before it, 1 to 3 as the profile says
        f"caps 0 0 {CAPS_360}",
        f"caps 1 {ABSENT_CAPS}", f"caps 2 {ABSENT_CAPS}", f"caps 3 {ABSENT_CAPS}",
        "keys 0",                                            # a gamepad record holds no key
        f"caps 0 0 {CAPS_360}",
        "state 0 0 01 00 00 00 10 00 00 00 00 00 00 00 00 00 00 00",       # packet 1, START
        f"caps 0 0 {CAPS_360}",
        # packet 2: A, RT 255, LX -32768, LY 32767, RX 100, RY -100
        "state 0 0 02 00 00 00 00 10 00 FF 00 80 FF 7F 64 00 9C FF",
        "state 0 0 02 00 00 00 00 10 00 FF 00 80 FF 7F 64 00 9C FF",
        "seen poll=1 seq=0..1 records=2",                    # the keyboard's own seqs, from 0
        "keys 0 1C=80 C8=80",       # Enter (read by an XInput call) and Up, held at one poll
        "keys 0 C8=80",
        "state 0 0 02 00 00 00 00 10 00 FF 00 80 FF 7F 64 00 9C FF",       # a repeat: same packet
        f"caps 1 0 {CAPS_360}",
        "state 1 0 01 00 00 00 FF F3 01 02 03 00 04 00 05 00 06 00",       # 0x0C00 masked off
        "state 0 0 02 00 00 00 00 10 00 FF 00 80 FF 7F 64 00 9C FF",
        "vibrate 1 0",
        f"caps 0 {ABSENT_CAPS}",                             # unplugged: as before
        f"state 0 {ABSENT_STATE}",
        "vibrate 0 1167",
        "state 0 0 04 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00",       # back: packet 4
        f"caps 2 {ABSENT_CAPS}",
        f"state 3 {ABSENT_STATE}",
        f"state 1 {ABSENT_STATE}",                           # end of file lets every slot go
        "keys 0",
        # the gap is the gamepad records' (5 never came); the keyboard's seqs have none
        "input polls=4 held=2 records=2 last_seq=1 gaps=0 nowriter=1 dropped=0 pad_records=6 "
        "pad_last_seq=6 pad_gaps=1 pad_changes=7 pad_wait_ms=0 found=1 answers=12",
        "xinput getstate=10 setstate=2 getcaps=9 absent=9 present=0 live=12",
    ]
    assert err == ""
    assert (tmp_path / "input.log").read_text().splitlines()[1:] == [
        "1 1 0x1C,0xC8", "2 2 0xC8",
        "# closed at exit: 4 keyboard polls, 2 holding keys, 2 lines; 2 records, last seq 1, 0 seq "
        "gaps, 1 polls with no writer, 0 bytes of an unfinished record dropped; 6 gamepad records, "
        "last seq 6, 1 seq gaps, 7 slot changes, 12 XInput answers from a connected slot "
        "(gamepads are not replayed from this log)",
    ]


def test_a_source_that_sends_no_gamepad_record_answers_as_before(runtime_driver, tmp_path):
    """Keyboard records only: XInput answers the profile's 1167 with zeroed buffers, the
    counters and the log's closing line are as they were, and the keyboard record the boot
    scan's XInput call read is held at the next keyboard poll with the one after it."""
    out, err = drive(runtime_driver, "keyboard", tmp_path)
    assert out == [
        "source fifo",
        f"caps 0 {ABSENT_CAPS}", f"caps 1 {ABSENT_CAPS}", f"caps 2 {ABSENT_CAPS}",
        f"caps 3 {ABSENT_CAPS}",
        "seen poll=0 seq=0..1 records=2",
        "keys 0 1C=80 C8=80",
        f"state 0 {ABSENT_STATE}", "vibrate 0 1167", f"caps 0 {ABSENT_CAPS}",
        "keys 0 C8=80",
        "input polls=2 held=2 records=2 last_seq=1 gaps=0 nowriter=0 dropped=0 pad_records=0 "
        "pad_last_seq=0 pad_gaps=0 pad_changes=0 pad_wait_ms=0 found=0 answers=0",
        "xinput getstate=1 setstate=1 getcaps=5 absent=7 present=0 live=0",
    ]
    assert err == ""
    assert (tmp_path / "input.log").read_text().splitlines()[1:] == [
        "0 0 0x1C,0xC8", "1 1 0xC8",
        "# closed at exit: 2 keyboard polls, 2 holding keys, 2 lines; 2 records, last seq 1, 0 seq "
        "gaps, 0 polls with no writer, 0 bytes of an unfinished record dropped",
    ]


def test_key_seqs_tile_from_0_at_the_polls_with_pad_records_between_them(runtime_driver, tmp_path):
    """Each record kind has its own seq counter: SA2P records interleaved with SA2K records
    leave the keyboard's seqs 0, 1, 2, 3, and the poll observer's ranges tile them from 0 with
    no gap and no gamepad record counted, which is what the S5 play-latency checks require
    (check_s5_exit.input_latency: "poll sequence ranges do not tile whole records from 0")."""
    out, err = drive(runtime_driver, "interleave", tmp_path)
    assert out == [
        "source fifo",
        f"caps 0 0 {CAPS_360}", f"caps 1 {ABSENT_CAPS}", f"caps 2 {ABSENT_CAPS}",
        f"caps 3 {ABSENT_CAPS}",
        "seen poll=0 seq=0..0 records=1", "keys 0 1C=80",
        "seen poll=1 seq=1..2 records=2", "keys 0 C8=80",
        "keys 0",                                            # pad 3 alone: no keyboard record
        "seen poll=3 seq=3..3 records=1", "keys 0 1C=80",
        "input polls=4 held=3 records=4 last_seq=3 gaps=0 nowriter=0 dropped=0 pad_records=4 "
        "pad_last_seq=3 pad_gaps=0 pad_changes=4 pad_wait_ms=0 found=1 answers=1",
        "xinput getstate=0 setstate=0 getcaps=4 absent=3 present=0 live=1",
    ]
    assert err == ""
    assert (tmp_path / "input.log").read_text().splitlines()[1:] == [
        "0 0 0x1C", "1 1 0xC8", "3 3 0x1C",
        "# closed at exit: 4 keyboard polls, 3 holding keys, 3 lines; 4 records, last seq 3, 0 seq "
        "gaps, 0 polls with no writer, 0 bytes of an unfinished record dropped; 4 gamepad records, "
        "last seq 3, 0 seq gaps, 4 slot changes, 1 XInput answers from a connected slot "
        "(gamepads are not replayed from this log)",
    ]


@pytest.mark.parametrize("mode", ["script", "unset"])
def test_no_fifo_source_answers_as_before_and_never_waits(runtime_driver, tmp_path, mode):
    out, err = drive(runtime_driver, mode, tmp_path)
    assert out == [
        "source " + ("script" if mode == "script" else ""),
        f"caps 0 {ABSENT_CAPS}", f"caps 1 {ABSENT_CAPS}", f"caps 2 {ABSENT_CAPS}",
        f"caps 3 {ABSENT_CAPS}", f"state 0 {ABSENT_STATE}", "vibrate 0 1167",
        "keys 0 1C=80" if mode == "script" else "keys 0",
        f"input polls={1 if mode == 'script' else 0} held={1 if mode == 'script' else 0} records=0 "
        "last_seq=0 gaps=0 nowriter=0 dropped=0 pad_records=0 pad_last_seq=0 pad_gaps=0 "
        "pad_changes=0 pad_wait_ms=0 found=0 answers=0",
        "xinput getstate=1 setstate=1 getcaps=4 absent=6 present=0 live=0",
    ]
    assert err == ""


def test_the_boot_scan_waits_its_bound_for_a_sender_that_never_sends_pads(runtime_driver, tmp_path):
    out, err = drive(runtime_driver, "timeout", tmp_path)
    assert out[:5] == ["source fifo", f"caps 0 {ABSENT_CAPS}", f"caps 1 {ABSENT_CAPS}",
                       f"caps 2 {ABSENT_CAPS}", f"caps 3 {ABSENT_CAPS}"]
    assert out[5] == ("input polls=0 held=0 records=1 last_seq=0 gaps=0 nowriter=0 dropped=0 "
                      "pad_records=0 pad_last_seq=0 pad_gaps=0 pad_changes=0 pad_wait_ms=300 "
                      "found=0 answers=0")
    waited = int(out[7].split()[1])
    assert 300 <= waited < 2000, out
    assert re.fullmatch(r"sa2 dinput: the game's controller scan waited \d+ ms for "
                        r"SA2_INPUT_DI_SOURCE's first gamepad record \(SA2_INPUT_PAD_WAIT_MS=300\): "
                        r"none came; no pad this run\n", err), err


def test_the_boot_scan_takes_a_pad_that_arrives_during_its_wait(runtime_driver, tmp_path):
    out, err = drive(runtime_driver, "found", tmp_path)
    assert out[:5] == ["source fifo", f"caps 0 {ABSENT_CAPS}", f"caps 1 {ABSENT_CAPS}",
                       f"caps 2 0 {CAPS_360}", f"caps 3 {ABSENT_CAPS}"]
    assert out[5] == ("input polls=0 held=0 records=0 last_seq=0 gaps=0 nowriter=0 dropped=0 "
                      "pad_records=1 pad_last_seq=0 pad_gaps=0 pad_changes=1 pad_wait_ms=10000 "
                      "found=1 answers=1")
    waited = int(out[7].split()[1])
    assert 100 <= waited < 5000, out
    assert "SA2_INPUT_PAD_WAIT_MS=10000): read\n" in err


@pytest.mark.parametrize("mode,words", [
    ("badconn", "TRAP 1 XINPUT1_3::XInput called from 0x00863df1: SA2_INPUT_DI_SOURCE record 0 "
                "(seq 0, \"SA2P\") gives slot 1 the connected byte 2, not 0 or 1"),
    ("badmagic", "TRAP 1 XINPUT1_3::XInput called from 0x00863df1: SA2_INPUT_DI_SOURCE record 0 "
                 "has magic 0x50324154, not 0x4b324153 (\"SA2K\") or 0x50324153 (\"SA2P\")"),
])
def test_records_out_of_step_stop_the_run_by_name(runtime_driver, tmp_path, mode, words):
    out, _ = drive(runtime_driver, mode, tmp_path, rc=3)
    assert out[0] == "source fifo" and out[1].startswith(words), out


def test_a_wait_bound_out_of_range_is_refused_at_configuration(runtime_driver, tmp_path):
    out, _ = drive(runtime_driver, "badwait", tmp_path, rc=3)
    assert out == ["TRAP 1 SA2_INPUT_PAD_WAIT_MS=60001 is not a decimal count of milliseconds "
                   "from 0 to 60000"]


REST = " 00" * 16
LATER_LOG = ("pad objects for slots 0xf, {n} XInput answers standing in for a pad not connected, 0 "
             "rescan answers hiding one with no object")
LATER_SAID = ("sa2 dinput: the game's controller scan made pad objects for slots 0xf, {n} of them for "
              "pads not connected yet (SA2_INPUT_PAD_HOTPLUG): a pad that connects later drives the "
              "game\n")


@pytest.mark.parametrize("mode", ["later", "later-on"])
def test_pads_that_connect_later_get_the_objects_the_boot_scan_makes(runtime_driver, tmp_path, mode):
    """Pads that connect later (runtime/sa2_dinput.h): the sender's first record holds no pad,
    and the start-up scan (return address 0x00864E2E) and each new object's first poll
    (0x00863DF6, 0x00863E1E) answer all four slots connected at rest, so the game makes four pad
    objects. Every other call answers the records: the every-frame poll (0x00863D56,
    0x00863D91) finds no pad, then the one that comes (START held), then none; the first poll's
    caller after the scan is over answers the records too. The rescan (0x00864A2C) answers every
    slot connected throughout, so its count never changes and the game's "controllers changed"
    dialog never comes."""
    out, err = drive(runtime_driver, mode, tmp_path)
    boot = []
    for s in range(4):
        boot += [f"boot caps {s} 0", f"init caps {s} 0", f"init state {s} 0{REST}"]
    assert out == ["source fifo", *boot,
                   "frame caps 0 1167", "frame caps 1 1167",
                   "rescan 0 0 0 0",
                   "init caps 0 1167",
                   "frame caps 0 0", "frame state 0 0 01 00 00 00 10 00 00 00 00 00 00 00 00 00 00 00",
                   "rescan 0 0 0 0",
                   "frame caps 0 1167",
                   "rescan 0 0 0 0",
                   "later hotplug=1 objects=0xf stand_in_slots=4 stand_ins=23 hidden=0 answers=3",
                   "input polls=0 held=0 records=0 last_seq=0 gaps=0 nowriter=0 dropped=0 "
                   "pad_records=3 pad_last_seq=2 pad_gaps=0 pad_changes=2 pad_wait_ms=0 found=1 "
                   "answers=3",
                   "xinput getstate=5 setstate=0 getcaps=25 absent=4 present=0 live=3"]
    assert err == LATER_SAID.format(n=4)
    assert (tmp_path / "input.log").read_text().splitlines()[-1].endswith(
        "3 XInput answers from a connected slot (gamepads are not replayed from this log); "
        + LATER_LOG.format(n=23))


def test_hotplug_off_answers_every_call_from_the_records_as_before(runtime_driver, tmp_path):
    """SA2_INPUT_PAD_HOTPLUG=0: the same calls as above answered from the records alone: no pad
    object at the scan, the rescan sees the pad come and go (the native game's dialog), and the
    log's closing line is as it was."""
    out, err = drive(runtime_driver, "later-off", tmp_path)
    assert out == ["source fifo",
                   "boot caps 0 1167", "boot caps 1 1167", "boot caps 2 1167", "boot caps 3 1167",
                   "frame caps 0 1167", "frame caps 1 1167",
                   "rescan 1167 1167 1167 1167",
                   "init caps 0 1167",
                   "frame caps 0 0", "frame state 0 0 01 00 00 00 10 00 00 00 00 00 00 00 00 00 00 00",
                   "rescan 0 1167 1167 1167",
                   "frame caps 0 1167",
                   "rescan 1167 1167 1167 1167",
                   "later hotplug=0 objects=0x0 stand_in_slots=0 stand_ins=0 hidden=0 answers=3",
                   "input polls=0 held=0 records=0 last_seq=0 gaps=0 nowriter=0 dropped=0 "
                   "pad_records=3 pad_last_seq=2 pad_gaps=0 pad_changes=2 pad_wait_ms=0 found=1 "
                   "answers=3",
                   "xinput getstate=1 setstate=0 getcaps=21 absent=19 present=0 live=3"]
    assert err == ""
    assert (tmp_path / "input.log").read_text().splitlines()[-1].endswith(
        "3 XInput answers from a connected slot (gamepads are not replayed from this log)")


def test_a_pad_at_boot_keeps_its_state_and_the_rescan_count_holds_when_pads_come_and_go(
        runtime_driver, tmp_path):
    """A pad in slot 1 at boot answers its own state at the scan and its first poll (A held),
    the other three slots stand in at rest; it is unplugged and a second pad comes in slot 0,
    and the rescan's four answers never change."""
    out, err = drive(runtime_driver, "later-boot", tmp_path)
    assert out == ["source fifo",
                   "boot caps 0 0", "init caps 0 0", f"init state 0 0{REST}",
                   "boot caps 1 0", "init caps 1 0",
                   "init state 1 0 01 00 00 00 00 10 00 00 00 00 00 00 00 00 00 00",
                   "boot caps 2 0", "init caps 2 0", f"init state 2 0{REST}",
                   "boot caps 3 0", "init caps 3 0", f"init state 3 0{REST}",
                   "rescan 0 0 0 0",
                   "frame caps 1 1167", "rescan 0 0 0 0",
                   "frame caps 0 0", "frame state 0 0 01 00 00 00 20 00 00 00 00 00 00 00 00 00 00 00",
                   "rescan 0 0 0 0",
                   "later hotplug=1 objects=0xf stand_in_slots=3 stand_ins=19 hidden=0 answers=7"]
    assert err == LATER_SAID.format(n=3)


def test_no_gamepad_record_by_the_scan_stands_in_for_nothing_and_the_rescan_hides_a_later_pad(
        runtime_driver, tmp_path):
    """A sender whose first gamepad record comes after the scan: the scan answers the records
    (no pad, no object), and the rescan answers a pad that comes later not connected, since it
    has no object, so the dialog does not come for it either; the every-frame poll's caller
    answers the records as ever."""
    out, err = drive(runtime_driver, "later-nopads", tmp_path)
    assert out == ["source fifo",
                   "boot caps 0 1167", "boot caps 1 1167", "boot caps 2 1167", "boot caps 3 1167",
                   "rescan 1167 1167 1167 1167",
                   "rescan 1167 1167 1167 1167",
                   "frame caps 2 0", "frame state 2 0 01 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00",
                   "later hotplug=0 objects=0x0 stand_in_slots=0 stand_ins=0 hidden=1 answers=2"]
    assert err == ""


def test_a_hotplug_value_other_than_0_or_1_is_refused_at_configuration(runtime_driver, tmp_path):
    out, _ = drive(runtime_driver, "badhotplug", tmp_path, rc=3)
    assert out == ["TRAP 1 SA2_INPUT_PAD_HOTPLUG=yes is neither 0 nor 1"]


# ---------------------------------------------------------------------------- the sender

SENDER_DRIVER = r"""
#include "input.h"
#include <cstdio>
#include <cstring>
#include <fcntl.h>
#include <string>
#include <sys/stat.h>
#include <unistd.h>

// Every whole record waiting: "<tag> <magic> <seq> <bytes after the header, hex>" for a pad
// record, "<tag> <magic> <seq> <DIK>=<byte> ..." for a key record (nothing for no key).
static void drain(int fd, const char* tag) {
    unsigned char r[4096];
    const ssize_t n = read(fd, r, sizeof r);
    for (ssize_t off = 0; n > 0 && off < n;) {
        const unsigned magic = r[off] | r[off + 1] << 8 | r[off + 2] << 16 | (unsigned)r[off + 3] << 24;
        const unsigned seq = r[off + 4] | r[off + 5] << 8 | r[off + 6] << 16 | (unsigned)r[off + 7] << 24;
        std::printf("%s %08X %u", tag, magic, seq);
        const ssize_t size = magic == 0x50324153u ? 72 : 264;
        if (size == 72) for (int i = 8; i < 72; i++) std::printf("%s%02X", i % 16 == 8 ? " " : "", r[off + i]);
        else for (int i = 8; i < 264; i++) if (r[off + i]) std::printf(" %02X=%02X", i - 8, r[off + i]);
        std::printf("\n");
        off += size;
    }
}

static SDL_Event key(SDL_Scancode sc, bool down = true) {
    SDL_Event e;
    std::memset(&e, 0, sizeof e);
    e.type = down ? SDL_EVENT_KEY_DOWN : SDL_EVENT_KEY_UP;
    e.key.windowID = 7;
    e.key.scancode = sc;
    e.key.down = down;
    return e;
}

// Key and pad records interleaved on an open FIFO, with the key log a live session keeps.
static int interleave(const std::string& dir, const std::string& path) {
    const std::string log_path = dir + "/keys.tsv";
    FILE* log = std::fopen(log_path.c_str(), "w");
    const int rd = open(path.c_str(), O_RDONLY | O_NONBLOCK);
    {
        sa2_input::KeySender k(path, log, log_path);
        std::printf("start %d\n", k.start());
        sa2_input::PadSet p;
        p.slot[0].connected = true;
        k.send_pads(p);
        k.event(key(SDL_SCANCODE_RETURN), 7, 1.0, 0);
        p.slot[0].buttons = 0x1000; k.send_pads(p);
        p.slot[0].buttons = 0; k.send_pads(p);
        k.event(key(SDL_SCANCODE_RETURN, false), 7, 2.0, 0);
        k.event(key(SDL_SCANCODE_UP), 7, 3.0, 0);
        p.slot[0].lx = 1000; k.send_pads(p);
        k.event(key(SDL_SCANCODE_UP, false), 7, 4.0, 0);
        k.close();                                              // no key held; the pad let go
        drain(rd, "i");
        std::printf("%s", k.report_json().c_str());
    }
    std::fclose(log);
    return 0;
}

int main(int argc, char** argv) {
    const std::string path = std::string(argv[1]) + "/pads.fifo";
    mkfifo(path.c_str(), 0600);
    if (argc > 2 && std::string(argv[2]) == "interleave") return interleave(argv[1], path);
    sa2_input::KeySender k(path);
    sa2_input::PadSet none, p;
    std::printf("before-start %d\n", k.send_pads(p));
    std::printf("idle-report %s", k.report_json().c_str());
    std::printf("start %d\n", k.start());                       // no reader: ENXIO
    p.slot[0].connected = true;
    p.slot[0].buttons = 0x1010;                                 // A and START
    p.slot[0].lx = -32768; p.slot[0].ly = 32767; p.slot[0].rt = 255;
    p.slot[2].buttons = 0x2000;                                 // not connected: written as zeroes
    std::printf("send-owed %d\n", k.send_pads(p));
    const int rd = open(path.c_str(), O_RDONLY | O_NONBLOCK);
    k.pump();                                                   // opens: the owed pads, pad seq 0
    drain(rd, "a");
    std::printf("send-same %d\n", k.send_pads(p));              // a repeat: nothing written
    k.event(key(SDL_SCANCODE_RETURN), 7, 1.0, 0);               // a key record: key seq 0
    p.slot[1].connected = true; p.slot[1].ry = -2;
    std::printf("send-new %d\n", k.send_pads(p));               // pad seq 1
    drain(rd, "b");
    close(rd);                                                  // the reader goes away
    p.slot[1].buttons = 0x0001;
    std::printf("send-epipe %d\n", k.send_pads(p));             // EPIPE: owed
    k.pump();                                                   // ENXIO
    const int rd2 = open(path.c_str(), O_RDONLY | O_NONBLOCK);
    k.pump();                                                   // a new reader: the pads again
    drain(rd2, "c");
    k.pump();                                                   // nothing owed
    drain(rd2, "d");
    k.close();                                                  // every slot let go; keys up
    drain(rd2, "e");
    std::printf("%s", k.report_json().c_str());
    std::printf("after-close %d\n", k.send_pads(none));
    return 0;
}
"""


@pytest.fixture(scope="module")
def sender_driver(tmp_path_factory) -> Path:
    if GXX is None or not (SDL_INCLUDE / "SDL3" / "SDL_events.h").is_file():
        pytest.skip(f"no C++ compiler, or no SDL3 headers under {SDL_INCLUDE}")
    d = tmp_path_factory.mktemp("padsender")
    (d / "driver.cpp").write_text(SENDER_DRIVER)
    exe = d / "driver"
    r = subprocess.run([GXX, "-std=c++17", "-O1", "-Wall", "-Wextra", "-Werror",
                        "-Wno-unused-parameter", f"-I{SDL_INCLUDE}", f"-I{ROOT / 'host' / 'player'}",
                        str(d / "driver.cpp"), str(ROOT / "host" / "player" / "input.cpp"),
                        "-o", str(exe)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-3000:]
    return exe


Z = "00" * 16
PAD0 = "0100101000FF0080FF7F000000000000"     # A|START, RT 255, LX -32768, LY 32767


def test_send_pads_writes_one_whole_record_per_change_and_on_every_connection(sender_driver, tmp_path):
    r = subprocess.run([str(sender_driver), str(tmp_path)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = r.stdout.splitlines()
    records = [x for x in out if re.match(r"^[a-e] ", x)]
    pad1 = "010000000000000000000000FEFF0000"          # RY -2
    pad1b = "010001000000000000000000FEFF0000"         # and DPAD_UP
    assert records == [
        f"a 50324153 0 {PAD0} {Z} {Z} {Z}",           # owed while no reader; slot 2 zeroes
        "b 4B324153 0 1C=80",                         # each kind's seq counts from 0
        f"b 50324153 1 {PAD0} {pad1} {Z} {Z}",
        # the write that met EPIPE took no seq; the new connection gets the pads as they are
        # then, once
        f"c 50324153 2 {PAD0} {pad1b} {Z} {Z}",
        "e 4B324153 1",                               # close(): every key up
        f"e 50324153 3 {Z} {Z} {Z} {Z}",              # and every slot let go
    ]
    assert [x for x in out if not re.match(r"^([a-e] |  \"host_input\"|idle-report)", x)] == [
        "before-start 0", "start 1", "send-owed 0", "send-same 1", "send-new 1", "send-epipe 0",
        "after-close 0"]
    idle = next(x for x in out if x.startswith("idle-report "))
    assert '"pads"' not in idle                        # no send_pads yet: the report as before
    line = next(x for x in out if x.startswith('  "host_input": '))
    got = json.loads("{" + line.strip().rstrip(",") + "}")["host_input"]
    assert got["pads"] == {"calls": 5, "changes": 4, "records_written": 4, "connected_now": 0,
                           "owed": False}
    assert (got["records_written"], got["last_seq"], got["write_errors"]) == (2, 1, 1)


def test_key_seqs_tile_from_0_with_pad_records_between_them(sender_driver, tmp_path):
    """The sender's half of the per-kind seq: key records 0..3 and pad records 0..4 written
    interleaved, the key log's sent rows carry the key seqs, and the report's last_seq is
    records_written - 1, which check_s5_exit compares with the runtime's."""
    r = subprocess.run([str(sender_driver), str(tmp_path), "interleave"], capture_output=True,
                       text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    out = r.stdout.splitlines()
    assert out[0] == "start 1"
    records = [tuple(x.split()[1:3]) for x in out if x.startswith("i ")]
    assert records == [("50324153", "0"), ("4B324153", "0"), ("50324153", "1"),
                       ("50324153", "2"), ("4B324153", "1"), ("4B324153", "2"),
                       ("50324153", "3"), ("4B324153", "3"), ("50324153", "4")]
    rows = (tmp_path / "keys.tsv").read_text().splitlines()
    head = rows[0].split("\t")
    sent = [dict(zip(head, row.split("\t"))) for row in rows[1:]]
    assert [(row["dik"], row["down"], row["seq"], row["status"]) for row in sent] == [
        ("28", "1", "0", "sent"), ("28", "0", "1", "sent"), ("200", "1", "2", "sent"),
        ("200", "0", "3", "sent")]
    line = next(x for x in out if x.startswith('  "host_input": '))
    got = json.loads("{" + line.strip().rstrip(",") + "}")["host_input"]
    assert (got["records_written"], got["last_seq"]) == (4, 3)
    assert got["pads"]["records_written"] == 5


# ---------------------------------------------------------------- gamepad replay

def answers(out: list[str]) -> list[str]:
    """The game's view of a run: every XInput and keyboard answer and the stand-in counters,
    without the lines only a FIFO has (its source's name, the poll observer)."""
    return [l for l in out if not l.startswith(("source ", "seen ", "padreplay "))]


def test_a_pad_log_replays_every_xinput_answer_of_its_session(runtime_driver, tmp_path):
    """SA2_INPUT_PAD_LOG (runtime/sa2_dinput.h, "Gamepad replay") keys each change of a slot to
    the XInput call that first answered from it; replayed as SA2_INPUT_PAD_SCRIPT beside the
    keyboard's log as SA2_INPUT_DI_SCRIPT, with no FIFO, the same calls get the same answers
    byte for byte (packets, stand-ins, the rescan's hiding), and the replay's own pad log is the
    session's line for line."""
    rec, rec_err = drive(runtime_driver, "padrec", tmp_path)
    assert "made pad objects for slots 0xf, 4 of them for pads not connected yet" in rec_err
    assert rec[0] == "source fifo"
    log = (tmp_path / "pad.log").read_text().splitlines()
    assert log[0].startswith("# SA2_INPUT_PAD_LOG of a FIFO source: ")
    assert log[1] == "mode 1 1"                              # hotplug on, stand-ins answered
    changes = [l for l in log[2:] if not l.startswith("#")]
    assert changes == [
        "13 2 0 1 0x0010 0 0 0 0 0 0",       # read at poll 1, first answered at call 13 (after 2 polls)
        "16 2 0 1 0x1000 0 255 -32768 32767 100 -100",      # two changes before one call: both
        "16 2 0 1 0x1010 0 255 -32768 32767 100 -100",
        "22 3 0 0 0x0000 0 0 0 0 0 0",                      # unplugged
    ], log
    assert log[-1].startswith("# closed at exit: 27 XInput calls, 4 change lines, 0 changes no call")
    keys = (tmp_path / "input.log").read_text()
    assert "(gamepads replay from SA2_INPUT_PAD_LOG)" in keys
    play, err = drive(runtime_driver, "script-padplay", tmp_path)
    assert err == rec_err
    assert play[0] == "source script"
    assert answers(play) == answers(rec)
    assert "padreplay calls=27 log_lines=4 script_lines=4 applied=4 desyncs=0 first=0" in play
    again = (tmp_path / "pad-replay.log").read_text().splitlines()
    assert again[0].startswith("# SA2_INPUT_PAD_LOG of a script source: ")
    assert again[1:-1] == log[1:-1]


def test_a_pad_replay_off_its_sessions_path_is_counted_and_said(runtime_driver, tmp_path):
    drive(runtime_driver, "padrec", tmp_path)
    out, err = drive(runtime_driver, "script-paddesync", tmp_path)
    err = err.split("\n", 1)[1]                              # after the scan's own line
    assert "padreplay calls=27 log_lines=4 script_lines=4 applied=4 desyncs=4 first=13" in out
    assert err == ("sa2 dinput: SA2_INPUT_PAD_SCRIPT: XInput call 13 comes after 3 keyboard polls, "
                   "the session's after 2: the replay has left its path\n")


def test_without_the_pad_variables_the_fifo_sessions_bytes_are_as_before(runtime_driver, tmp_path):
    """Off means off: the "later" session's answers and its keyboard log's closing line do not
    change when no pad log is asked for, and no pad log is written."""
    out, _ = drive(runtime_driver, "later", tmp_path)
    assert not (tmp_path / "pad.log").exists()
    assert (tmp_path / "input.log").read_text().rstrip().endswith("(gamepads are not replayed from this log)"
                                                                  "; pad objects for slots 0xf, 23 XInput "
                                                                  "answers standing in for a pad not "
                                                                  "connected, 0 rescan answers hiding one "
                                                                  "with no object")


@pytest.mark.parametrize("mode,body,words", [
    ("unset-padlog", None, "TRAP 1 SA2_INPUT_PAD_SCRIPT or SA2_INPUT_PAD_LOG is set and neither "
                           "SA2_INPUT_DI_SCRIPT nor SA2_INPUT_DI_SOURCE is"),
    ("padrec-script", "mode 1 1\n", "TRAP 1 SA2_INPUT_PAD_SCRIPT is set without SA2_INPUT_DI_SCRIPT"),
    ("script-padplay", "mode 1 1\n5 1 0 1 0x0010 0 0 0 0 0 0\n4 1 0 0 0x0000 0 0 0 0 0 0\n",
     "TRAP 1 SA2_INPUT_PAD_SCRIPT line 3: call 4 comes after call 5"),
    ("script-padplay", "5 1 0 1 0x0C00 0 0 0 0 0 0\n",
     "TRAP 1 SA2_INPUT_PAD_SCRIPT line 1: the buttons \"0x0C00\" are not 0x and hex within 0xF3FF"),
    ("script-padplay", "5 1 4 1 0x0000 0 0 0 0 0 0\n",
     "TRAP 1 SA2_INPUT_PAD_SCRIPT line 1: the call, polls, slot (0 to 3) or connected (0 or 1)"),
    ("script-padplay", "mode 2 1\n", "TRAP 1 SA2_INPUT_PAD_SCRIPT line 1: a mode line is"),
])
def test_pad_replay_refuses_what_it_cannot_carry_at_configuration(runtime_driver, tmp_path, mode, body, words):
    (tmp_path / "input.log").write_text("")
    if body is not None:
        (tmp_path / ("pad-replay.log" if mode == "padrec-script" else "pad.log")).write_text(body)
    out, _ = drive(runtime_driver, mode, tmp_path, rc=3)
    assert out[0].startswith(words), out
