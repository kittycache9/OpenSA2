#!/usr/bin/env python3
"""touch_overlay_shaders: the touch overlay's shaders (touch_overlay.cpp), GLSL compiled to
SPIR-V by the Android NDK's glslc and checked by its spirv-val, written as touch_overlay_spirv.h.

The overlay draws every control as a rounded box from one uniform block (no vertex buffer, no
texture): shape i is vertices 6i..6i+5, a quad one pixel larger than the box, whose fragment
shader takes the box's signed distance for an anti-aliased edge (a ring when it has a
thickness). A shape's finish is flat (one colour), or glossy for the adventure style
(touch_overlay.h SA2_TOUCH_STYLE): its colour lighter at the top and darker at the bottom, a soft
highlight in its upper half and thin horizontal stripes across it, all from that one colour. The
player uses SDL_gpu's Vulkan driver on Linux, Windows and Android, so SPIR-V is
the only format needed; SDL's SPIR-V resource sets: a vertex shader's uniform buffers are set 1,
a fragment shader's samplers set 2.

The second pair draws the game's own menu art (touch_overlay.h, "The game's art"): shape i is a
quad over its box sampling the art atlas (premultiplied RGBA8) over its rectangle there, tinted
by a hue turn and a saturation and value scale, the colour then written premultiplied (the
pipeline blends ONE, ONE_MINUS_SRC_ALPHA).

  .venv/bin/python host/player/touch_overlay_shaders.py           write the header
  .venv/bin/python host/player/touch_overlay_shaders.py --check   exit 1 when it is stale

glslc is private/android-sdk/ndk/<version>/shader-tools/linux-x86_64/glslc (SA2_GLSLC names
another); its output is a function of the source and its version, both named in the header.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
HEADER = HERE / "touch_overlay_spirv.h"
MAX_SHAPES = 32   # touch_overlay.cpp kMaxShapes

VERTEX = f"""#version 450
// One uniform block: the target's size in pixels, and per shape its box (centre, half size),
// style (corner radius, ring thickness: 0 filled, finish: 0 flat, 1 glossy, the glossy stripes'
// period in pixels) and straight-alpha colour.
layout(set = 1, binding = 0) uniform Shapes {{
    vec4 target;
    vec4 geom[{MAX_SHAPES}];
    vec4 style[{MAX_SHAPES}];
    vec4 color[{MAX_SHAPES}];
}};
layout(location = 0) out vec2 v_local;
layout(location = 1) flat out vec4 v_box;
layout(location = 2) flat out vec4 v_color;
layout(location = 3) flat out vec2 v_finish;
const vec2 corners[6] = vec2[6](vec2(-1.0, -1.0), vec2(1.0, -1.0), vec2(-1.0, 1.0),
                                vec2(-1.0, 1.0), vec2(1.0, -1.0), vec2(1.0, 1.0));
void main() {{
    int shape = gl_VertexIndex / 6;
    vec4 g = geom[shape];
    vec2 local = corners[gl_VertexIndex % 6] * (g.zw + vec2(1.0));
    vec2 px = g.xy + local;
    // SDL_gpu's NDC: (-1, -1) is the lower left; pixel rows run down.
    gl_Position = vec4(px.x / target.x * 2.0 - 1.0, 1.0 - px.y / target.y * 2.0, 0.0, 1.0);
    v_local = local;
    v_box = vec4(g.zw, style[shape].xy);
    v_color = color[shape];
    v_finish = style[shape].zw;
}}
"""

ART_VERTEX = f"""#version 450
// One uniform block: the target's size in pixels, and per shape its box (centre, half size), its
// rectangle in the atlas (u0, v0 at the box's top left, u1, v1 at its bottom right: a mirrored
// shape has u1 < u0 or v1 < v0) and its tint (hue turn, saturation scale, value scale, alpha).
layout(set = 1, binding = 0) uniform Shapes {{
    vec4 target;
    vec4 geom[{MAX_SHAPES}];
    vec4 uv[{MAX_SHAPES}];
    vec4 tint[{MAX_SHAPES}];
}};
layout(location = 0) out vec2 v_uv;
layout(location = 1) flat out vec4 v_tint;
const vec2 corners[6] = vec2[6](vec2(-1.0, -1.0), vec2(1.0, -1.0), vec2(-1.0, 1.0),
                                vec2(-1.0, 1.0), vec2(1.0, -1.0), vec2(1.0, 1.0));
void main() {{
    int shape = gl_VertexIndex / 6;
    vec2 c = corners[gl_VertexIndex % 6];
    vec4 g = geom[shape];
    vec2 px = g.xy + c * g.zw;
    gl_Position = vec4(px.x / target.x * 2.0 - 1.0, 1.0 - px.y / target.y * 2.0, 0.0, 1.0);
    v_uv = mix(uv[shape].xy, uv[shape].zw, c * 0.5 + vec2(0.5));
    v_tint = tint[shape];
}}
"""

ART_FRAGMENT = """#version 450
layout(set = 2, binding = 0) uniform sampler2D art;
layout(location = 0) in vec2 v_uv;
layout(location = 1) flat in vec4 v_tint;
layout(location = 0) out vec4 o_color;
vec3 to_hsv(vec3 c) {
    vec4 k = vec4(0.0, -1.0 / 3.0, 2.0 / 3.0, -1.0);
    vec4 p = mix(vec4(c.bg, k.wz), vec4(c.gb, k.xy), step(c.b, c.g));
    vec4 q = mix(vec4(p.xyw, c.r), vec4(c.r, p.yzx), step(p.x, c.r));
    float d = q.x - min(q.w, q.y);
    return vec3(abs(q.z + (q.w - q.y) / (6.0 * d + 1e-10)), d / (q.x + 1e-10), q.x);
}
vec3 to_rgb(vec3 c) {
    vec3 p = abs(fract(c.xxx + vec3(1.0, 2.0 / 3.0, 1.0 / 3.0)) * 6.0 - vec3(3.0));
    return c.z * mix(vec3(1.0), clamp(p - vec3(1.0), 0.0, 1.0), c.y);
}
void main() {
    vec4 t = texture(art, v_uv);
    float a = t.a * v_tint.w;
    if (a <= 0.0) discard;
    vec3 hsv = to_hsv(t.rgb / t.a);
    hsv = vec3(fract(hsv.x + v_tint.x), clamp(hsv.y * v_tint.y, 0.0, 1.0), clamp(hsv.z * v_tint.z, 0.0, 1.0));
    o_color = vec4(to_rgb(hsv) * a, a);
}
"""

FRAGMENT = """#version 450
layout(location = 0) in vec2 v_local;
layout(location = 1) flat in vec4 v_box;
layout(location = 2) flat in vec4 v_color;
layout(location = 3) flat in vec2 v_finish;
layout(location = 0) out vec4 o_color;
void main() {
    float r = min(v_box.z, min(v_box.x, v_box.y));
    vec2 q = abs(v_local) - (v_box.xy - vec2(r));
    float d = length(max(q, vec2(0.0))) + min(max(q.x, q.y), 0.0) - r;
    float cover = clamp(0.5 - d, 0.0, 1.0);
    if (v_box.w > 0.0) cover *= clamp(d + v_box.w + 0.5, 0.0, 1.0);
    if (cover <= 0.0) discard;
    vec3 rgb = v_color.rgb;
    if (v_finish.x > 0.5) {
        // Glossy: lighter at the top, darker at the bottom; a soft highlight across the upper
        // half; and a dark stripe a third of each period thick, every period pixels down.
        vec2 u = v_local / v_box.xy;
        rgb = mix(mix(rgb, vec3(1.0), 0.40), rgb * 0.55, smoothstep(-1.0, 1.0, u.y));
        float shine = 1.0 - smoothstep(0.25, 0.75, length((u - vec2(0.0, -0.5)) * vec2(0.8, 1.8)));
        rgb = mix(rgb, vec3(1.0), 0.30 * shine);
        float period = max(v_finish.y, 3.0);
        float off = abs(fract((v_local.y + v_box.y) / period) - 0.5) * period;
        rgb *= 1.0 - 0.14 * clamp(period / 6.0 + 0.5 - off, 0.0, 1.0);
    }
    o_color = vec4(rgb, v_color.a * cover);
}
"""


def tool(name: str) -> Path:
    env = os.environ.get("SA2_" + name.upper().replace("-", "_"))
    if env:
        return Path(env)
    found = sorted((ROOT / "private/android-sdk/ndk").glob(f"*/shader-tools/linux-x86_64/{name}"))
    if not found:
        sys.exit(f"no {name} under private/android-sdk/ndk/*/shader-tools (set SA2_{name.upper()})")
    return found[-1]


def compile_stage(glslc: Path, val: Path, stage: str, source: str, tmp: Path) -> bytes:
    src = tmp / f"overlay.{stage}"
    out = tmp / f"overlay.{stage}.spv"
    src.write_text(source)
    subprocess.run([str(glslc), "--target-env=vulkan1.0", "-O", "-fshader-stage=" + stage, str(src), "-o", str(out)],
                   check=True)
    subprocess.run([str(val), "--target-env", "vulkan1.0", str(out)], check=True)
    return out.read_bytes()


def words(blob: bytes) -> str:
    assert len(blob) % 4 == 0
    vals = [int.from_bytes(blob[i:i + 4], "little") for i in range(0, len(blob), 4)]
    lines = []
    for i in range(0, len(vals), 8):
        lines.append("    " + ", ".join(f"0x{v:08x}u" for v in vals[i:i + 8]) + ",")
    return "\n".join(lines)


def render() -> str:
    glslc, val = tool("glslc"), tool("spirv-val")
    version = subprocess.run([str(glslc), "--version"], check=True, capture_output=True, text=True).stdout
    version = version.splitlines()[0].strip()
    with tempfile.TemporaryDirectory() as t:
        vert = compile_stage(glslc, val, "vert", VERTEX, Path(t))
        frag = compile_stage(glslc, val, "frag", FRAGMENT, Path(t))
        art_vert = compile_stage(glslc, val, "vert", ART_VERTEX, Path(t))
        art_frag = compile_stage(glslc, val, "frag", ART_FRAGMENT, Path(t))
    return (f"// touch_overlay_spirv.h: generated by host/player/touch_overlay_shaders.py ({version}); do not edit.\n"
            f"// The GLSL is in that script; --check holds this file to it.\n"
            f"#pragma once\n#include <cstdint>\n\nnamespace sa2_touch_spirv {{\n\n"
            f"constexpr int kMaxShapes = {MAX_SHAPES};\n\n"
            f"alignas(4) constexpr uint32_t kVertex[] = {{\n{words(vert)}\n}};\n\n"
            f"alignas(4) constexpr uint32_t kFragment[] = {{\n{words(frag)}\n}};\n\n"
            f"alignas(4) constexpr uint32_t kArtVertex[] = {{\n{words(art_vert)}\n}};\n\n"
            f"alignas(4) constexpr uint32_t kArtFragment[] = {{\n{words(art_frag)}\n}};\n\n"
            f"}}  // namespace sa2_touch_spirv\n")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="exit 1 when the header is not this script's output")
    args = ap.parse_args()
    text = render()
    if args.check:
        same = HEADER.is_file() and HEADER.read_text() == text
        print(f"{HEADER.name}: {'current' if same else 'STALE'}")
        return 0 if same else 1
    HEADER.write_text(text)
    print(f"wrote {HEADER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
