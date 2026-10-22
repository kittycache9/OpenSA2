#!/usr/bin/env python3
"""android_app: build the Android app (com.sa2recomp.app) as one signed arm64-v8a APK, no Gradle.

Subtitle: host 2's GPU player, its SDL3 and the installer built by the NDK for Android (API 29),
the app's Java (SDL3's android-project classes and host/android's activity) compiled by javac and
d8, the resources and manifest linked by aapt2, and the APK aligned and signed, into
private/generated/s8/android/:

  deps        zlib, libpng (NEON off) and zstd's decompressor (tools/pins.json zlib, libpng, zstd),
              static, position-independent
  sdl3        the pinned SDL3 tarball with patches/sdl3 applied (tools/s5/sdl3_patches.py), the
              NDK's cmake toolchain file, SDL_VENDOR_INFO as on Linux: libSDL3.so (stripped; the
              player's host contract hashes this file), and SDL's android-project Java sources
  dxbc        dxbc-spirv's 50 units, with DXVK's own Linux flags less the x86 ones
  translator  host/d3d9gpu/shader's units (DXVK's lowering pass, analysis, constant copy, shader
              I/O, host 2's d9g_shader*.cpp), DXVK's flags less the x86 ones, D9G_TRANSLATOR_ID
              from extract_dxvk_shader.py identity
  d9g         host/d3d9gpu's device units against DXVK Native's headers, as on Linux
  player      lib/libmain.so: host/player's units (SA2_PLAYER_GPU=1; player.cpp's main renamed
              sa2_player_main), the clear matrix's static leg, the three archives, and the app's
              session (host/android/native) with the launcher's relay and POSIX process layer and
              the installer's config reader (host/launch, host/install), linked against libSDL3.so
  installer   lib/libsa2install.so: sa2-install (host/install) as a PIE executable, so it can be
              exec'd from the app's native library directory
  java        classes.dex: SDL's Java and host/android/java (javac --release 11 against
              platforms/android-36's android.jar, then d8 --min-api 29)
  apk         aapt2 (host/android's manifest and res/), the dex, assets/game.env
              (tools/s7/release_build.py env_lines: the game's settings) and lib/arm64-v8a
              (libmain.so, libSDL3.so, libsa2install.so, and libsa2game.so when --game exists),
              zipalign -p, apksigner with a debug keystore made by keytool:
              private/generated/s8/android/sa2recomp.apk

Sources that read x86 state (the x87 control word, MXCSR, the SHA extensions' CPUID) are compiled
from build-time copies with each such site under an architecture test (arm_portable below), so
the tree's own files are never edited here; a file that already tests __aarch64__ is used as is.

Memory (AGENTS.md): at most --jobs compiles at once (default 4), and no phase starts while less
than 8 GB is available.

  .venv/bin/python tools/s8/android_app.py [--only PHASE ...] [--jobs 4] [--game FILE]
  .venv/bin/python tools/s8/android_app.py cut-stream SRC DEST --frames N
      DEST is SRC's header and records up to and including its Nth FRAME record (a prefix the
      player plays with --allow-truncated), for the app's renderer check on a device.
  .venv/bin/python tools/s8/android_app.py portable-diff
      prints the unified diff arm_portable applies to each source (what the owners can adopt).

Prints ANDROID_APP_OK <apk> <sha256> on success.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import difflib
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tarfile
import time
import zipfile
from pathlib import Path

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "AGENTS.md").is_file())


def _load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


W = _load("host2_win_build", "tools/s7/host2_win_build.py")   # the shared unit lists and helpers
sha256, pinned_archive, depfile_inputs, wait_for_memory = W.sha256, W.pinned_archive, W.depfile_inputs, W.wait_for_memory

PACKAGE = "com.sa2recomp.app"
MIN_SDK, TARGET_SDK = 29, 35
VERSION_CODE, VERSION_NAME = 1, "0.1-s8"
ABIS = {"arm64-v8a": "aarch64-linux-android", "x86_64": "x86_64-linux-android"}
SDK_REL = "private/android-sdk"
NDK_VERSION = "28.2.13676358"
BUILD_TOOLS = "36.0.0"
PLATFORM = "android-36"
DEFAULT_OUT = "private/generated/s8/android"
DEFAULT_GAME = "private/generated/s8/android/game/libsa2game.so"
PHASES = ("deps", "sdl3", "dxbc", "translator", "d9g", "player", "installer", "java", "apk")
H = ROOT / "host/d3d9gpu"
P = ROOT / "host/player"
A = ROOT / "host/android"
LAUNCH = ROOT / "host/launch"
INSTALL = ROOT / "host/install"
S5_INSTALL = ROOT / "private/s5-tools/install"
CC_JSON_REL = W.CC_JSON_REL
EXTRACT = W.EXTRACT
# host/player's units that are no part of the player (each has a main of its own; so has every test_*).
PLAYER_EXCLUDE = {"synth_stream"}
WARN = ("-std=c++17", "-O2", "-fPIC", "-Wall", "-Wextra", "-Wno-unused-parameter")
# DXVK's Linux flags that name x86 (or the host's terminal); everything else is kept.
X86_FLAGS = re.compile(r"^-m(sse|avx|fpmath|tune|arch)|^-fdiagnostics-color|^-Winvalid-pch$|^-pthread$")
KEY_ALIAS, KEY_PASS = "sa2recomp-debug", "android"


class Refused(SystemExit):
    def __init__(self, why: str):
        super().__init__(f"android_app: {why}")


def android_flags(linux_flags: list[str]) -> list[str]:
    """DXVK's Linux compile flags made Android ones: the x86 options dropped, -fPIC kept."""
    out = [f for f in linux_flags if not X86_FLAGS.match(f)]
    return out if "-fPIC" in out else out + ["-fPIC"]


# ---------------------------------------------------------------- arm portability (build-time copies)

X86 = "#if defined(__x86_64__) || defined(__i386__)"
FPCR_HELPERS = """#if defined(__x86_64__) || defined(__i386__)
#include <xmmintrin.h>
#elif defined(__aarch64__)
// android_app.py arm_portable: AArch64's FPCR in MXCSR's place (0: round to nearest, no flush to
// zero, no trapped exception: the environment 0x1F80 and 0x037F name on x86).
static inline uint32_t sa2_fpcr_get() { uint64_t v; __asm__ volatile("mrs %0, fpcr" : "=r"(v)); return uint32_t(v); }
static inline void sa2_fpcr_set(uint32_t v) { __asm__ volatile("msr fpcr, %0" : : "r"(uint64_t(v))); }
#endif"""

PORTABLE: dict[str, list[tuple[str, str]]] = {
    "host/player/sha256.cpp": [
        ("#include <cpuid.h>\n#include <immintrin.h>\n",
         X86 + "\n#include <cpuid.h>\n#include <immintrin.h>\n#endif\n"),
        ("// The SHA extensions: four rounds per sha256rnds2 pair",
         X86 + "\n// The SHA extensions: four rounds per sha256rnds2 pair"),
        ("    return ssse3 && sse41 && (b & (1u << 29));\n}\n",
         "    return ssse3 && sse41 && (b & (1u << 29));\n}\n#else\n"
         "void blocks_shani(uint32_t h[8], const uint8_t* p, size_t nblocks) { blocks_portable(h, p, nblocks); }\n"
         "bool cpu_has_sha() { return false; }\n#endif\n"),
    ],
    "host/player/player.cpp": [
        ("uint16_t x87_cw() {\n    uint16_t cw;\n    __asm__ __volatile__(\"fnstcw %0\" : \"=m\"(cw));\n    return cw;\n}",
         "uint16_t x87_cw() {\n    uint16_t cw = 0;\n" + X86 + "\n    __asm__ __volatile__(\"fnstcw %0\" : \"=m\"(cw));\n"
         "#endif\n    return cw;\n}"),
    ],
    "host/d3d9gpu/d9g_report.cpp": [
        ("uint16_t x87_cw() {\n    uint16_t cw;\n    __asm__ __volatile__(\"fnstcw %0\" : \"=m\"(cw));\n    return cw;\n}",
         "uint16_t x87_cw() {\n    uint16_t cw = 0;\n" + X86 + "\n    __asm__ __volatile__(\"fnstcw %0\" : \"=m\"(cw));\n"
         "#endif\n    return cw;\n}"),
        ("uint32_t mxcsr() {\n    uint32_t v;\n    __asm__ __volatile__(\"stmxcsr %0\" : \"=m\"(v));\n    return v;\n}",
         "uint32_t mxcsr() {\n    uint32_t v = 0;\n" + X86 + "\n    __asm__ __volatile__(\"stmxcsr %0\" : \"=m\"(v));\n"
         "#elif defined(__aarch64__)\n    uint64_t f;\n    __asm__ __volatile__(\"mrs %0, fpcr\" : \"=r\"(f));\n"
         "    v = uint32_t(f);\n#endif\n    return v;\n}"),
    ],
    "host/d3d9gpu/d9g_shader.cpp": [
        ("#include <xmmintrin.h>\n", FPCR_HELPERS + "\n"),
        ("  m_mxcsr = _mm_getcsr();\n  __asm__ volatile(\"fnstcw %0\" : \"=m\"(m_x87));\n",
         X86 + "\n  m_mxcsr = _mm_getcsr();\n  __asm__ volatile(\"fnstcw %0\" : \"=m\"(m_x87));\n"
         "#elif defined(__aarch64__)\n  m_mxcsr = sa2_fpcr_get();\n#endif\n"),
        ("  _mm_setcsr(0x1F80u);  // all exceptions masked, round to nearest, no FTZ, no DAZ\n"
         "  uint16_t cw = 0x037Fu;  // the x87 default: extended precision, round to nearest, masked\n"
         "  __asm__ volatile(\"fldcw %0\" : : \"m\"(cw));\n",
         X86 + "\n  _mm_setcsr(0x1F80u);  // all exceptions masked, round to nearest, no FTZ, no DAZ\n"
         "  uint16_t cw = 0x037Fu;  // the x87 default: extended precision, round to nearest, masked\n"
         "  __asm__ volatile(\"fldcw %0\" : : \"m\"(cw));\n#elif defined(__aarch64__)\n  sa2_fpcr_set(0);\n#endif\n"),
        ("  _mm_setcsr(m_mxcsr);\n  __asm__ volatile(\"fldcw %0\" : : \"m\"(m_x87));\n",
         X86 + "\n  _mm_setcsr(m_mxcsr);\n  __asm__ volatile(\"fldcw %0\" : : \"m\"(m_x87));\n"
         "#elif defined(__aarch64__)\n  sa2_fpcr_set(m_mxcsr);\n#endif\n"),
    ],
}


# glibc's FILE internals (the read-ahead count before a live pipe's idle wait), which bionic's
# opaque FILE does not have. The app's live stream is --video-fd's, read without a FILE, so on
# bionic a FILE stream (the debug mode's file) never waits in slices.
PORTABLE["host/player/stream.cpp"] = [
    ("    if (!live_ || !idle_hook_ || f_->_IO_read_ptr < f_->_IO_read_end) return;\n",
     "#if defined(__BIONIC__)\n"
     "    // android_app.py arm_portable: bionic's FILE is opaque (no read-ahead count), so no idle wait.\n"
     "    if (true) return;\n"
     "#else\n"
     "    if (!live_ || !idle_hook_ || f_->_IO_read_ptr < f_->_IO_read_end) return;\n"
     "#endif\n", ("__BIONIC__",)),
]
# The player's check that DXVK's WSI would load this process's SDL names Linux's soname; the
# app's SDL is lib/<abi>/libSDL3.so, which has no versioned soname.
PORTABLE["host/player/player.cpp"].append(
    ('    if (void* h = dlopen("libSDL3.so.0", RTLD_NOW | RTLD_NOLOAD)) dlclose(h);\n',
     "#if defined(__ANDROID__)\n"
     "    // android_app.py arm_portable: the app's SDL is lib/<abi>/libSDL3.so (no versioned soname).\n"
     '    if (void* h = dlopen("libSDL3.so", RTLD_NOW | RTLD_NOLOAD)) dlclose(h);\n'
     "#else\n"
     '    if (void* h = dlopen("libSDL3.so.0", RTLD_NOW | RTLD_NOLOAD)) dlclose(h);\n'
     "#endif\n", ('dlopen("libSDL3.so",',)))
# Android's window is the whole screen: SDL cannot resize it ("That operation is not supported"),
# and the device's Present scales the back buffer onto the swapchain, so a resize to the game's
# back buffer is not needed there (interactive mode makes the window before CreateDevice).
PORTABLE["host/player/player.cpp"].append(
    ("        if (!SDL_SetWindowSize(win_, int(width), int(height)))\n"
     "            return device_failure(strf(\"SDL_SetWindowSize: %s\", SDL_GetError()));\n",
     "#if defined(__ANDROID__)\n"
     "        // android_app.py arm_portable: Android's window is the screen; Present scales onto it.\n"
     "        (void)SDL_SetWindowSize(win_, int(width), int(height));\n"
     "#else\n"
     "        if (!SDL_SetWindowSize(win_, int(width), int(height)))\n"
     "            return device_failure(strf(\"SDL_SetWindowSize: %s\", SDL_GetError()));\n"
     "#endif\n", ("(void)SDL_SetWindowSize(",)))
# SDL2's hint, which SDL3 removed (it never exposes the accelerometer as a joystick): optional,
# applied only while the line is there.
PORTABLE["host/player/player.cpp"].append(
    ('    SDL_SetHint(SDL_HINT_ACCELEROMETER_AS_JOYSTICK, "0");\n',
     "    // android_app.py arm_portable: SDL3 has no SDL_HINT_ACCELEROMETER_AS_JOYSTICK (no accelerometer joystick).\n",
     (), True))
X86_PORTED = ("__aarch64__", "__ARM_ARCH")


def arm_portable(rel: str, text: str) -> str:
    """`text` (the tree's file `rel`) with every x86-only (or glibc-only) site of PORTABLE[rel]
    under a test. A site is left alone when the file already holds one of its markers (an x86
    site's: __aarch64__ or __ARM_ARCH): its owner ported it. Each other site's anchor must occur
    exactly once, or the build refuses (the file moved on)."""
    original = text
    for site in PORTABLE.get(rel, ()):
        old, new = site[0], site[1]
        markers = site[2] if len(site) > 2 else X86_PORTED
        optional = len(site) > 3 and site[3]
        if any(m in original for m in markers) or new in original:
            continue                    # ported in the tree (the site's own replacement is there)
        n = text.count(old)
        if optional and n == 0:
            continue
        if n != 1:
            raise Refused(f"{rel}: the site {old.splitlines()[0]!r} occurs {n} times, not once "
                          "(update PORTABLE in tools/s8/android_app.py, or port the file)")
        text = text.replace(old, new)
    return text


def portable_source(rel: str, gen: Path) -> Path:
    """The file to compile for `rel`: the tree's own, or its arm_portable copy under gen/portable."""
    src = ROOT / rel
    text = src.read_text()
    out = arm_portable(rel, text)
    if out == text:
        return src
    dest = gen / "portable" / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.is_file() or dest.read_text() != out:
        dest.write_text(out)
    return dest


# ---------------------------------------------------------------- the stream prefix

FRAME_KIND = None


def stream_kinds() -> dict[str, int]:
    """host/sa2_stream.h's record kinds, read from its enum (SA2S_CALL = 1, ...)."""
    text = (ROOT / "host/sa2_stream.h").read_text()
    kinds = {m.group(1): int(m.group(2), 0) for m in re.finditer(r"\b(SA2S_[A-Z0-9_]+)\s*=\s*(0x[0-9a-fA-F]+|\d+)", text)}
    if "SA2S_FRAME" not in kinds:
        raise Refused("host/sa2_stream.h names no SA2S_FRAME kind")
    return kinds


def cut_stream(src: Path, dest: Path, frames: int, frame_kind: int | None = None) -> dict:
    """DEST = SRC's header and every record through its `frames`th FRAME record."""
    frame_kind = stream_kinds()["SA2S_FRAME"] if frame_kind is None else frame_kind
    with src.open("rb") as f:
        head = f.read(16)
        if len(head) != 16 or head[:8] != b"SA2D3D9S":
            raise Refused(f"{src} is no SA2D3D9S stream")
        header_bytes = struct.unpack_from("<I", head, 12)[0]
        f.seek(0)
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        seen = records = 0
        with tmp.open("wb") as out:
            out.write(f.read(header_bytes))
            while seen < frames:
                rh = f.read(12)
                if len(rh) < 12:
                    break
                kind, nbytes, _seq = struct.unpack("<III", rh)
                if nbytes < 12 or nbytes % 4:
                    raise Refused(f"{src}: a record of {nbytes} bytes at offset {f.tell() - 12}")
                body = f.read(nbytes - 12)
                if len(body) != nbytes - 12:
                    break
                out.write(rh)
                out.write(body)
                records += 1
                seen += kind == frame_kind
        os.replace(tmp, dest)
    return {"frames": seen, "records": records, "bytes": dest.stat().st_size}


# ---------------------------------------------------------------- the builder

class Builder:
    def __init__(self, out: Path, jobs: int, tools: dict, abi: str = "arm64-v8a", game: Path | None = None,
                 run=subprocess.run, debuggable: bool = True):
        self.out = out
        self.debuggable = debuggable              # android:debuggable, so `adb shell run-as` reads the logs
        self.apk_path: Path | None = None         # --apk: another name for the signed APK
        self.b = out / "app" / abi                 # the build tree
        self.jobs = jobs
        self.tools = tools
        self.abi = abi
        self.game = game
        self.run = run
        self.sdk = ROOT / SDK_REL
        self.ndk = self.sdk / "ndk" / NDK_VERSION
        self.llvm = self.ndk / "toolchains/llvm/prebuilt/linux-x86_64/bin"
        self.triple = f"{ABIS[abi]}{MIN_SDK}"
        self.cc = [str(self.llvm / "clang"), f"--target={self.triple}"]
        self.cxx = [str(self.llvm / "clang++"), f"--target={self.triple}"]
        self.ar = str(self.llvm / "llvm-ar")
        self.strip = str(self.llvm / "llvm-strip")
        self.java_home = ROOT / tools["java"]["path"]
        self.bt = self.sdk / "build-tools" / BUILD_TOOLS
        self.android_jar = self.sdk / "platforms" / PLATFORM / "android.jar"
        for p in (self.ndk, self.bt, self.android_jar, self.java_home):
            if not p.exists():
                raise Refused(f"no {p} (private/android-sdk/install.sh; tools/env.sh's Java)")
        self.record: dict = {"schema": "sa2-recomp-s8-android-app/v1", "abi": abi, "triple": self.triple,
                             "ndk": NDK_VERSION, "build_tools": BUILD_TOOLS, "platform": PLATFORM,
                             "inputs": {}, "phases": {}, "outputs": {}}
        self._hashes: dict[str, str] = {}
        for d in ("src", "obj", "lib", "gen", "deps/include", "jni"):
            (self.b / d).mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ plumbing
    def sh(self, cmd, *, cwd=None, log=None, env=None, input_text=None) -> str:
        p = self.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None, capture_output=True, text=True,
                     env=env, input=input_text)
        if log is not None:
            Path(log).write_text(" ".join(shlex.quote(str(c)) for c in cmd) + "\n\n" + (p.stdout or "") + (p.stderr or ""))
        if p.returncode != 0:
            tail = ((p.stderr or "") + (p.stdout or ""))[-3000:]
            raise Refused(f"{Path(str(cmd[0])).name} {' '.join(map(str, cmd[1:4]))} ... failed ({p.returncode}):\n{tail}")
        return p.stdout or ""

    def java_env(self) -> dict:
        env = dict(os.environ)
        env["JAVA_HOME"] = str(self.java_home)
        env["PATH"] = f"{self.java_home / 'bin'}:{env.get('PATH', '')}"
        return env

    def file_hash(self, path: str) -> str:
        if path not in self._hashes:
            try:
                self._hashes[path] = sha256(Path(path))
            except OSError:
                self._hashes[path] = "missing"
        return self._hashes[path]

    def stamp_of(self, cmd: list[str], dep: Path) -> str | None:
        if not dep.is_file():
            return None
        h = hashlib.sha256(json.dumps(cmd).encode())
        for name in depfile_inputs(dep):
            h.update(f"{name}\0{self.file_hash(name)}\n".encode())
        return h.hexdigest()

    def compile_one(self, base, flags, src: Path, obj: Path) -> bool:
        dep, stamp = obj.with_suffix(".d"), obj.with_suffix(".stamp")
        cmd = [*base, *flags, "-MD", "-MF", str(dep), "-c", str(src), "-o", str(obj)]
        if obj.is_file() and stamp.is_file() and stamp.read_text().strip() == self.stamp_of(cmd, dep):
            return False
        for stale in (obj, dep, stamp):
            if stale.exists():
                stale.unlink()
        self.sh(cmd, log=obj.with_suffix(".log"))
        stamp.write_text((self.stamp_of(cmd, dep) or "") + "\n")
        return True

    def compile_many(self, phase: str, units) -> dict:
        wait_for_memory(phase)
        t0 = time.monotonic()
        with cf.ThreadPoolExecutor(self.jobs) as ex:
            done = list(ex.map(lambda u: self.compile_one(*u), units))
        return {"objects": len(units), "compiled": sum(done), "seconds": round(time.monotonic() - t0, 2)}

    def archive(self, lib: Path, objects) -> None:
        if lib.exists():
            lib.unlink()
        self.sh([self.ar, "rcsD", str(lib), *map(str, objects)])

    def extract(self, name: str) -> Path:
        tarball = pinned_archive(self.tools, name, ROOT)
        self.record["inputs"][name] = {"path": str(tarball.relative_to(ROOT)), "sha256": self.tools[name]["sha256"]}
        with tarfile.open(tarball) as t:
            top = {m.name.split("/", 1)[0] for m in t.getmembers()}
            if len(top) != 1:
                raise Refused(f"{tarball} has {len(top)} top-level entries")
            tree = self.b / "src" / top.pop()
            marker = tree / ".sa2-extracted"
            if marker.is_file() and marker.read_text().strip() == self.tools[name]["sha256"]:
                return tree
            if tree.exists():
                raise Refused(f"{tree} exists but is not this tarball's extraction: delete it "
                              f"(.venv/bin/python tools/rmtree.py {tree})")
            t.extractall(self.b / "src", filter="data")
        marker.write_text(self.tools[name]["sha256"] + "\n")
        return tree

    def wrappers(self) -> Path:
        """gen/acc and gen/acxx: the NDK's clang for the target, as one program (the translator's
        identity runs and hashes its compiler), answering -dumpfullversion with clang's version."""
        d = self.b / "gen"
        ver = re.search(r"clang version (\S+)", self.sh([self.llvm / "clang", "--version"])).group(1)
        answer = f'if [ "$#" = 1 ] && [ "$1" = -dumpfullversion ]; then echo {ver}; exit 0; fi\n'
        for name, prog in (("acc", "clang"), ("acxx", "clang++")):
            w = d / name
            text = f'#!/bin/sh\n{answer}exec "{self.llvm / prog}" --target={self.triple} "$@"\n'
            if not w.is_file() or w.read_text() != text:
                w.write_text(text)
            w.chmod(0o755)
        return d

    # ------------------------------------------------------------------ phases
    def deps(self) -> dict:
        inc = self.b / "deps/include"
        zlib, png, zstd = self.extract("zlib"), self.extract("libpng"), self.extract("zstd")
        o = self.b / "obj/deps"
        o.mkdir(parents=True, exist_ok=True)
        base = ["-O2", "-fPIC", "-DNDEBUG", "-DZ_HAVE_UNISTD_H"]
        units = [(self.cc, [*base, f"-I{zlib}"], zlib / f"{s}.c", o / f"zlib_{s}.o") for s in W.ZLIB_SOURCES]
        conf = self.b / "gen/pnglibconf"
        conf.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(png / "scripts/pnglibconf.h.prebuilt", conf / "pnglibconf.h")
        units += [(self.cc, [*base, "-DPNG_ARM_NEON_OPT=0", f"-I{conf}", f"-I{png}", f"-I{zlib}"], png / f"{s}.c",
                   o / f"png_{s}.o") for s in W.PNG_SOURCES]
        zs = sorted((zstd / "lib/common").glob("*.c")) + sorted((zstd / "lib/decompress").glob("*.c"))
        units += [(self.cc, [*base, "-DZSTD_DISABLE_ASM", "-DZSTD_LEGACY_SUPPORT=0", f"-I{zstd / 'lib'}",
                             f"-I{zstd / 'lib/common'}"], s, o / f"zstd_{s.parent.name}_{s.stem}.o") for s in zs]
        stats = self.compile_many("deps", units)
        for lib, prefix in (("libz.a", "zlib_"), ("libpng.a", "png_"), ("libzstd.a", "zstd_")):
            self.archive(self.b / "deps" / lib, [u[3] for u in units if u[3].name.startswith(prefix)])
        for f in (zlib / "zlib.h", zlib / "zconf.h", png / "png.h", png / "pngconf.h", conf / "pnglibconf.h",
                  zstd / "lib/zstd.h", zstd / "lib/zstd_errors.h"):
            shutil.copyfile(f, inc / f.name)
        return stats

    def sdl_tree(self) -> Path:
        return self.b / "src" / f"SDL3-{self.tools['sdl3']['version']}"

    def sdl3(self) -> dict:
        sdl = self.tools["sdl3"]
        tarball = pinned_archive(self.tools, "sdl3", ROOT)
        patches = [ROOT / p["path"] for p in sdl.get("patches", [])]
        for p, pin in zip(patches, sdl.get("patches", [])):
            if sha256(p) != pin["sha256"]:
                raise Refused(f"{p} does not hash to its pin (tools/pins.json sdl3.patches)")
        install = self.b / "sdl3"
        build = self.b / "build/sdl3"
        configure = [self.tools["cmake"]["path"], "-G", "Ninja", f"-DCMAKE_MAKE_PROGRAM={self.tools['ninja']['path']}",
                     f"-DCMAKE_TOOLCHAIN_FILE={self.ndk / 'build/cmake/android.toolchain.cmake'}",
                     f"-DANDROID_ABI={self.abi}", f"-DANDROID_PLATFORM=android-{MIN_SDK}", "-DANDROID_STL=c++_static",
                     f"-DCMAKE_INSTALL_PREFIX={install}", *W.SDL_CMAKE_OPTIONS, "-DSDL_ANDROID_JAR=OFF",
                     f"-DSDL_VENDOR_INFO={sdl['vendor_info']}"]
        stamp_text = json.dumps({"tarball": sdl["sha256"], "patches": [p["sha256"] for p in sdl.get("patches", [])],
                                 "configure": configure[1:]}, sort_keys=True)
        stamp = install / ".sa2-build"
        self.record["inputs"]["sdl3"] = {"path": str(tarball.relative_to(ROOT)), "sha256": sdl["sha256"],
                                         "patches": {str(p.relative_to(ROOT)): sha256(p) for p in patches},
                                         "vendor_info": sdl["vendor_info"]}
        lib = self.b / "lib/libSDL3.so"
        if stamp.is_file() and stamp.read_text() == stamp_text and lib.is_file():
            return {"rebuilt": False}
        wait_for_memory("sdl3")
        t0 = time.monotonic()
        tree = self.sdl_tree()
        for d in (tree, build, install):
            if d.exists():
                raise Refused(f"{d} is left from an earlier SDL build: delete it "
                              f"(.venv/bin/python tools/rmtree.py {d}) and run again")
        with tarfile.open(tarball) as t:
            t.extractall(self.b / "src", filter="data")
        py = str(ROOT / ".venv/bin/python")
        self.sh([py, ROOT / "tools/s5/sdl3_patches.py", "source", "--root", ROOT, "--tree", tree])
        self.sh([py, ROOT / "tools/s5/sdl3_patches.py", "apply", "--root", ROOT, "--tree", tree],
                log=self.b / "gen/sdl3-patch.log")
        build.mkdir(parents=True)
        self.sh([*configure, "-S", tree, "-B", build], log=self.b / "gen/sdl3-configure.log")
        self.sh([self.tools["cmake"]["path"], "--build", build, "-j", str(self.jobs)], log=self.b / "gen/sdl3-build.log")
        self.sh([self.tools["cmake"]["path"], "--install", build], log=self.b / "gen/sdl3-install.log")
        config = (build / "include-config-release/build_config/SDL_build_config.h").read_text()
        drivers = sorted(set(re.findall(r"^#define SDL_GPU_(D3D12|VULKAN|METAL) 1", config, re.M)))
        built = install / "lib/libSDL3.so"
        tmp = lib.with_name("libSDL3.so.tmp")
        self.sh([self.strip, "--strip-unneeded", "-o", tmp, built])
        os.replace(tmp, lib)
        stamp.write_text(stamp_text)
        return {"rebuilt": True, "seconds": round(time.monotonic() - t0, 2), "gpu_drivers": drivers}

    def sdl_include(self) -> list[str]:
        sdl = self.b / "sdl3"
        if not (sdl / "include/SDL3/SDL.h").is_file():
            raise Refused(f"no SDL3 headers under {sdl} (the sdl3 phase)")
        return [f"-I{sdl / 'include'}", f"-I{self.sdl_tree() / 'src/video/khronos'}"]

    def dxvk_flags(self, source: str, record: Path) -> list[str]:
        linux_record = record.with_name(record.stem + ".linux.record")
        out = self.sh([ROOT / ".venv/bin/python", EXTRACT, "flags", "--compile-commands", ROOT / CC_JSON_REL,
                       "--source", source, "--record", linux_record])
        flags = android_flags([l for l in out.splitlines() if l])
        record.write_text(json.dumps({"source": source, "linux_record": str(linux_record), "flags": flags}, indent=1) + "\n")
        return flags

    def dxbc(self) -> dict:
        cc_json = ROOT / CC_JSON_REL
        if not cc_json.is_file():
            raise Refused(f"no DXVK compile_commands.json at {cc_json} (tools/s5/build_host_deps.sh)")
        dxvk = ROOT / self.tools["dxvk"]["path"]
        entries = [e for e in json.loads(cc_json.read_text()) if "/subprojects/dxbc-spirv/" in e["file"]]
        o, f = self.b / "obj/dxbc", self.b / "gen/dxbc-flags"
        o.mkdir(parents=True, exist_ok=True)
        f.mkdir(parents=True, exist_ok=True)
        units = []
        for e in entries:
            src = Path(os.path.normpath(os.path.join(e["directory"], e["file"])))
            # resolved both: compile_commands.json names the checkout that built DXVK, and a worktree
            # reaches the same files through its private/ link
            rel = src.resolve().relative_to((dxvk / "subprojects/dxbc-spirv").resolve()).as_posix()
            name = rel.replace("/", "_")
            flags = self.dxvk_flags(f"subprojects/dxbc-spirv/{rel}", f / f"{name}.flags")
            units.append((self.cxx, ["-w", *flags], src, o / f"{name}.o"))
        if len(units) != 50:
            raise Refused(f"DXVK's build compiled {len(units)} dxbc-spirv units, not the archive's 50")
        stats = self.compile_many("dxbc", units)
        self.archive(self.b / "lib/libdxbc_spv.a", [u[3] for u in units])
        return stats

    def translator(self) -> dict:
        dxvk = ROOT / self.tools["dxvk"]["path"]
        g, o = self.b / "gen/translator", self.b / "obj/translator"
        g.mkdir(parents=True, exist_ok=True)
        o.mkdir(parents=True, exist_ok=True)
        py = ROOT / ".venv/bin/python"
        lower = g / "d9g_dxvk_lower.gen.cpp"
        self.sh([py, EXTRACT, "lower", "--dxvk", dxvk, "--repo", ROOT, "--out", lower, "--record", g / "d9g_dxvk_lower.record"])
        d3d9 = self.dxvk_flags("src/d3d9/d3d9_shader.cpp", g / "flags.d3d9_shader.flags")
        dxvkio = self.dxvk_flags("src/dxvk/dxvk_shader_io.cpp", g / "flags.dxvk_shader_io.flags")
        gen = self.b / "gen"
        units_src = {t: portable_source(f"host/d3d9gpu/{t}.cpp", gen) for t in W.TRANSLATOR_UNITS}
        inc_dirs = [H, H / "test", P]
        d3d9_units = [lower, dxvk / "src/d3d9/d3d9_shader_analysis.cpp", dxvk / "src/d3d9/d3d9_constant_copy.cpp",
                      *units_src.values()]
        unit_args = [f"--unit={g / 'flags.d3d9_shader.flags'}:{u}" for u in d3d9_units]
        unit_args.append(f"--unit={g / 'flags.dxvk_shader_io.flags'}:{dxvk / 'src/dxvk/dxvk_shader_io.cpp'}")
        wait_for_memory("translator identity")
        tid = self.sh([py, EXTRACT, "identity", "--compiler", self.wrappers() / "acxx",
                       "--archive", self.b / "lib/libdxbc_spv.a", "--repo", ROOT, "--dxvk", dxvk,
                       "--out", g / "translator_identity.record", *unit_args, *[f"--include={d}" for d in inc_dirs],
                       "--extra", Path(__file__).resolve(), "--extra", EXTRACT]).strip().splitlines()[-1]
        if not re.fullmatch(r"dxbc-spirv:[0-9a-f]{16}\+translator:[0-9a-f]{16}", tid):
            raise Refused(f"the translator identity reads {tid!r}")
        inc = [f"-I{d}" for d in inc_dirs]
        sect = ["-ffunction-sections", "-fdata-sections"]
        define = [f'-DD9G_TRANSLATOR_ID="{tid}"']
        common = ["-w", *d3d9, *sect, *inc, *define]
        units = [(self.cxx, common, lower, o / "dxvk_lower.o"),
                 (self.cxx, common, dxvk / "src/d3d9/d3d9_shader_analysis.cpp", o / "dxvk_d3d9_shader_analysis.o"),
                 (self.cxx, common, dxvk / "src/d3d9/d3d9_constant_copy.cpp", o / "dxvk_d3d9_constant_copy.o"),
                 (self.cxx, ["-w", *dxvkio, *sect], dxvk / "src/dxvk/dxvk_shader_io.cpp", o / "dxvk_shader_io.o")]
        units += [(self.cxx, common, s, o / f"{t}.o") for t, s in units_src.items()]
        stats = self.compile_many("translator", units)
        self.archive(self.b / "lib/libd9g_shader.a", [u[3] for u in units])
        (g / "translator_id.txt").write_text(tid + "\n")
        stats["translator_id"] = tid
        return stats

    def d9g_flags(self) -> list[str]:
        return [*WARN, f"-I{self.b / 'gen/d3d9gpu-gen'}", f"-I{H}", f"-I{P}", *self.sdl_include(),
                f"-I{S5_INSTALL / 'include/dxvk'}"]

    def d9g(self) -> dict:
        gen = self.b / "gen/d3d9gpu-gen"
        dxvk_d3d9 = S5_INSTALL / "include/dxvk/d3d9.h"
        self.sh([ROOT / ".venv/bin/python", H / "gen_com.py", "--header", dxvk_d3d9, "--out", gen])
        self.record["inputs"]["gen_com_header"] = {"path": str(dxvk_d3d9.relative_to(ROOT)), "sha256": sha256(dxvk_d3d9)}
        o = self.b / "obj/d9g"
        o.mkdir(parents=True, exist_ok=True)
        flags = self.d9g_flags()
        units = [(self.cxx, flags, portable_source(f"host/d3d9gpu/{t}.cpp", self.b / "gen"), o / f"{t}.o")
                 for t in W.D9G_UNITS]
        stats = self.compile_many("d9g", units)
        self.archive(self.b / "lib/libsa2_d3d9gpu.a", [u[3] for u in units])
        return stats

    def expectations(self) -> Path:
        """gen/expected-gpu/sa2_host_expectations.h for this build's libSDL3.so (the file the APK
        carries), in generate_expectations.py's format."""
        sdl = self.tools["sdl3"]
        inc = self.b / "sdl3/include/SDL3"
        version_src = (inc / "SDL_version.h").read_text()
        parts = []
        for name in ("MAJOR", "MINOR", "MICRO"):
            found = re.findall(r"^#define SDL_" + name + r"_VERSION\s+(\d+)\s*$", version_src, re.M)
            if len(found) != 1:
                raise Refused("ambiguous SDL version header")
            parts.append(int(found[0]))
        if ".".join(map(str, parts)) != sdl["version"]:
            raise Refused(f"SDL header version {parts} is not tools.sdl3.version {sdl['version']}")
        rev_src = (inc / "SDL_revision.h").read_text()
        revisions = re.findall(r'^#define SDL_REVISION "([^"\n]*)"\s*$', rev_src, re.M)
        vendors = re.findall(r'^#define SDL_VENDOR_INFO "([^"\n]*)"\s*$', rev_src, re.M)
        if len(revisions) != 1 or vendors != [sdl["vendor_info"]]:
            raise Refused(f"the SDL revision header reads {revisions} with vendor {vendors}")
        lib = self.b / "lib/libSDL3.so"
        values = {"sdl_sha256": sha256(lib), "sdl_version": parts[0] * 1000000 + parts[1] * 1000 + parts[2],
                  "sdl_revision": f"{revisions[0]} ({vendors[0]})"}
        d = self.b / "gen/expected-gpu"
        d.mkdir(parents=True, exist_ok=True)
        header = d / "sa2_host_expectations.h"
        text = ("#pragma once\nnamespace sa2_host_expected {\n"
                f"inline constexpr char sdl_sha256[] = \"{values['sdl_sha256']}\";\n"
                f"inline constexpr int sdl_version = {values['sdl_version']};\n"
                f"inline constexpr char sdl_revision[] = \"{values['sdl_revision']}\";\n}}\n")
        if not header.is_file() or header.read_text() != text:
            header.write_text(text)
        return d

    def install_table(self) -> Path:
        d = self.b / "gen/install"
        self.sh([ROOT / ".venv/bin/python", ROOT / "tools/s7/install_table.py", "--out-dir", d])
        return d / "sa2_install_table.cpp"

    def player_units(self) -> list[str]:
        return sorted(p.stem for p in P.glob("*.cpp") if p.stem not in PLAYER_EXCLUDE and not p.stem.startswith("test_"))

    def player(self) -> dict:
        exp = self.expectations()
        gen = self.b / "gen"
        sdl_inc = self.sdl_include()
        dep_inc = f"-I{self.b / 'deps/include'}"
        o = self.b / "obj/player"
        o.mkdir(parents=True, exist_ok=True)
        pflags = [*WARN, f"-I{exp}", "-DSA2_PLAYER_GPU=1", f"-I{ROOT / 'host'}", f"-I{P}", *sdl_inc, dep_inc,
                  f"-I{S5_INSTALL / 'include/dxvk'}"]
        units = []
        for t in self.player_units():
            extra = ["-DSDL_MAIN_HANDLED", "-Dmain=sa2_player_main"] if t == "player" else []
            units.append((self.cxx, [*pflags, *extra], portable_source(f"host/player/{t}.cpp", gen), o / f"{t}.o"))
        # Each host tweak of the player's (runtime/sa2_tweaks.h, "Add a host tweak"), by glob.
        units += [(self.cxx, pflags, s, o / f"tweak_{s.stem}.o") for s in sorted((P / "tweaks").glob("*.cpp"))]
        units.append((self.cxx, [*self.d9g_flags(), f"-I{H / 'test'}", "-DSA2_CLEAR_MATRIX_STATIC"],
                      H / "test/clear_matrix.cpp", o / "clear_matrix.o"))
        # The app's session, with the launcher's relay and POSIX layer and the installer's config.
        table = self.install_table()
        sflags = [*WARN, f"-I{A / 'native'}", f"-I{LAUNCH}", f"-I{INSTALL}", f"-I{P}", f"-I{ROOT / 'host'}", *sdl_inc]
        session = sorted((A / "native").glob("*.cpp"))
        if not session:
            raise Refused(f"no session sources under {A / 'native'}")
        os_ = self.b / "obj/session"
        os_.mkdir(parents=True, exist_ok=True)
        units += [(self.cxx, sflags, s, os_ / f"{s.stem}.o") for s in session]
        units += [(self.cxx, sflags, LAUNCH / f"{s}.cpp", os_ / f"{s}.o") for s in ("sa2_relay", "sa2_launch_posix", "sa2_session_files")]
        units += [(self.cxx, sflags, INSTALL / f"{s}.cpp", os_ / f"{s}.o") for s in ("sa2_config", "sa2_platform_posix")]
        units.append((self.cxx, sflags, table, os_ / "sa2_install_table.o"))
        stats = self.compile_many("player", units)
        libs = [self.b / "lib/libsa2_d3d9gpu.a", self.b / "lib/libd9g_shader.a", self.b / "lib/libdxbc_spv.a"]
        deps = [self.b / "deps/libpng.a", self.b / "deps/libz.a", self.b / "deps/libzstd.a"]
        wait_for_memory("link")
        out = self.b / "lib/libmain.so"
        tmp = out.with_name("libmain.so.tmp")
        self.sh([*self.cxx, "-shared", "-o", tmp, "-Wl,-soname,libmain.so", "-Wl,--no-undefined",
                 "-Wl,-z,max-page-size=16384", "-Wl,--gc-sections", "-static-libstdc++",
                 *[str(u[3]) for u in units], *map(str, libs), *map(str, deps),
                 f"-L{self.b / 'lib'}", "-lSDL3", "-llog", "-landroid", "-ldl", "-lm"], log=self.b / "gen/link-libmain.log")
        self.sh([self.strip, "--strip-unneeded", tmp])
        os.replace(tmp, out)
        syms = self.sh([self.llvm / "llvm-nm", "-D", "--defined-only", "-C", out])
        if not re.search(r"\bT sa2_android_main$", syms, re.M):
            raise Refused("libmain.so exports no sa2_android_main (Sa2Activity.getMainFunction)")
        return stats

    def installer(self) -> dict:
        table = self.install_table()
        o = self.b / "obj/installer"
        o.mkdir(parents=True, exist_ok=True)
        flags = ["-std=c++17", "-O2", "-fPIE", "-Wall", "-Wextra", f"-I{INSTALL}", f"-I{P}"]
        srcs = [s for s in sorted(INSTALL.glob("*.cpp"))
                if not re.match(r"test_|.*_test\.cpp$|.*_probe\.cpp$", s.name) and s.name != "sa2_platform_win32.cpp"]
        units = [(self.cxx, flags, s, o / f"{s.stem}.o") for s in srcs]
        units.append((self.cxx, flags, portable_source("host/player/sha256.cpp", self.b / "gen"), o / "sha256.o"))
        units.append((self.cxx, flags, table, o / "sa2_install_table.o"))
        stats = self.compile_many("installer", units)
        out = self.b / "lib/libsa2install.so"
        tmp = out.with_name("libsa2install.so.tmp")
        self.sh([*self.cxx, "-pie", "-o", tmp, "-Wl,-z,max-page-size=16384", "-static-libstdc++",
                 *[str(u[3]) for u in units]], log=self.b / "gen/link-installer.log")
        self.sh([self.strip, "--strip-unneeded", tmp])
        os.replace(tmp, out)
        return stats

    def java(self) -> dict:
        sdl_java = self.sdl_tree() / "android-project/app/src/main/java"
        if not (sdl_java / "org/libsdl/app/SDLActivity.java").is_file():
            raise Refused(f"no SDL Java sources under {sdl_java} (the sdl3 phase)")
        srcs = sorted(sdl_java.rglob("*.java")) + sorted((A / "java").rglob("*.java"))
        cls = self.b / "java/classes"
        dex = self.b / "java/dex"
        for d in (cls, dex):
            if d.exists():
                shutil.rmtree(d)
            d.mkdir(parents=True)
        env = self.java_env()
        self.sh([self.java_home / "bin/javac", "--release", "11", "-nowarn", "-Xlint:-options", "-encoding", "UTF-8",
                 "-classpath", self.android_jar, "-d", cls, *srcs], env=env, log=self.b / "gen/javac.log")
        classes = sorted(cls.rglob("*.class"))
        self.sh([self.bt / "d8", "--release", "--min-api", str(MIN_SDK), "--lib", self.android_jar,
                 "--output", dex, *classes], env=env, log=self.b / "gen/d8.log")
        return {"java_sources": len(srcs), "classes": len(classes), "dex_sha256": sha256(dex / "classes.dex")}

    def keystore(self) -> Path:
        ks = self.out / "debug.keystore"
        if not ks.is_file():
            self.sh([self.java_home / "bin/keytool", "-genkeypair", "-keystore", ks, "-storepass", KEY_PASS,
                     "-keypass", KEY_PASS, "-alias", KEY_ALIAS, "-keyalg", "RSA", "-keysize", "2048",
                     "-validity", "10000", "-dname", "CN=SA2 Recomp Debug,O=sa2-recomp,C=US"], env=self.java_env())
        return ks

    def game_env(self) -> str:
        rb = _load("release_build", "tools/s7/release_build.py")
        return "\n".join(rb.env_lines(rb.source_modules(ROOT))) + "\n"

    def apk(self) -> dict:
        g = self.b / "gen/apk"
        if g.exists():
            shutil.rmtree(g)
        (g / "flat").mkdir(parents=True)
        env = self.java_env()
        aapt2 = self.bt / "aapt2"
        res = sorted(p for p in (A / "res").rglob("*") if p.is_file())
        for r in res:
            self.sh([aapt2, "compile", "-o", g / "flat", r])
        flats = sorted((g / "flat").glob("*.flat"))
        base = g / "base.apk"
        self.sh([aapt2, "link", "-o", base, "-I", self.android_jar, "--manifest", A / "AndroidManifest.xml",
                 "--min-sdk-version", str(MIN_SDK), "--target-sdk-version", str(TARGET_SDK),
                 "--version-code", str(VERSION_CODE), "--version-name", VERSION_NAME,
                 *(["--debug-mode"] if self.debuggable else []), *flats], log=g / "aapt2-link.log")
        libs = {"libmain.so": self.b / "lib/libmain.so", "libSDL3.so": self.b / "lib/libSDL3.so",
                "libsa2install.so": self.b / "lib/libsa2install.so"}
        game = self.game
        if game is not None and game.is_file():
            libs["libsa2game.so"] = game
        for name, path in libs.items():
            if not path.is_file():
                raise Refused(f"no {path} (the {'player' if name == 'libmain.so' else 'sdl3' if 'SDL' in name else 'installer'} phase)")
        unsigned = g / "unsigned.apk"
        shutil.copyfile(base, unsigned)
        with zipfile.ZipFile(unsigned, "a", compression=zipfile.ZIP_DEFLATED) as z:
            z.write(self.b / "java/dex/classes.dex", "classes.dex")
            z.writestr("assets/game.env", self.game_env())
            for name, path in libs.items():
                z.write(path, f"lib/{self.abi}/{name}")
        aligned = g / "aligned.apk"
        self.sh([self.bt / "zipalign", "-f", "-p", "4", unsigned, aligned])
        ks = self.keystore()
        apk = self.apk_path or self.out / ("sa2recomp.apk" if self.abi == "arm64-v8a" else f"sa2recomp-{self.abi}.apk")
        tmp = apk.with_name(apk.name + ".tmp")
        self.sh([self.bt / "apksigner", "sign", "--ks", ks, "--ks-pass", f"pass:{KEY_PASS}", "--ks-key-alias", KEY_ALIAS,
                 "--key-pass", f"pass:{KEY_PASS}", "--min-sdk-version", str(MIN_SDK), "--out", tmp, aligned], env=env)
        self.sh([self.bt / "apksigner", "verify", "--min-sdk-version", str(MIN_SDK), tmp], env=env)
        os.replace(tmp, apk)
        idsig = apk.with_name(apk.name + ".tmp.idsig")
        if idsig.exists():
            idsig.unlink()
        # --out is resolved, which leaves ROOT in a worktree whose private/ is a link: then absolute
        return {"apk": str(apk.relative_to(ROOT)) if apk.is_relative_to(ROOT) else str(apk),
                "bytes": apk.stat().st_size, "sha256": sha256(apk),
                "libs": {n: sha256(p) for n, p in libs.items()}, "game_included": "libsa2game.so" in libs}


def portable_diff() -> str:
    out = []
    for rel in PORTABLE:
        text = (ROOT / rel).read_text()
        new = arm_portable(rel, text)
        out += difflib.unified_diff(text.splitlines(True), new.splitlines(True), f"a/{rel}", f"b/{rel}")
    return "".join(out)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["cut-stream"]:
        ap = argparse.ArgumentParser(prog="android_app.py cut-stream")
        ap.add_argument("src", type=Path)
        ap.add_argument("dest", type=Path)
        ap.add_argument("--frames", type=int, required=True)
        a = ap.parse_args(argv[1:])
        r = cut_stream(a.src, a.dest, a.frames)
        print("CUT_STREAM_OK", a.dest, json.dumps(r))
        return 0
    if argv[:1] == ["portable-diff"]:
        sys.stdout.write(portable_diff())
        return 0
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", default=str(ROOT / DEFAULT_OUT))
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--abi", default="arm64-v8a", choices=sorted(ABIS))
    ap.add_argument("--game", default=str(ROOT / DEFAULT_GAME), help="the game executable for lib/<abi>/libsa2game.so")
    ap.add_argument("--only", action="append", choices=PHASES)
    ap.add_argument("--apk", default=None, help="the signed APK's path (default <out>/sa2recomp.apk), "
                    "for a test APK beside the deliverable")
    ap.add_argument("--no-debuggable", action="store_true",
                    help="leave android:debuggable out (by default it is set, so `adb shell run-as "
                         "com.sa2recomp.app` reads the sessions' logs on a phone)")
    a = ap.parse_args(argv)
    if not 1 <= a.jobs <= 4:
        raise Refused("--jobs is 1 to 4 (the machine's memory rule)")
    out = Path(a.out).resolve()
    b = Builder(out, a.jobs, W.pins(), a.abi, Path(a.game), debuggable=not a.no_debuggable)
    if a.apk:
        b.apk_path = Path(a.apk).resolve()
    for phase in a.only or PHASES:
        t0 = time.monotonic()
        print(f"== {phase}", flush=True)
        result = getattr(b, phase)()
        result.setdefault("seconds", round(time.monotonic() - t0, 2))
        b.record["phases"][phase] = result
        print(f"   {json.dumps(result)}", flush=True)
    (b.b / "android_app.runrec").write_text(json.dumps(b.record, indent=1) + "\n")
    if "apk" in (a.only or PHASES):
        r = b.record["phases"]["apk"]
        print("ANDROID_APP_OK", ROOT / r["apk"], r["sha256"])
    else:
        print("ANDROID_APP_PHASES_OK", " ".join(a.only))
    return 0


if __name__ == "__main__":
    sys.exit(main())
