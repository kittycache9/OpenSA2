// touch_overlay.cpp: the on-screen gamepad (touch_overlay.h).
#include "touch_overlay.h"

#include <SDL3/SDL_error.h>
#include <SDL3/SDL_touch.h>

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <cstring>
#include <utility>
#include <vector>

#include "touch_overlay_spirv.h"

namespace sa2_touch {

namespace {

constexpr int kMaxFingers = 10;
constexpr int kMaxShapes = sa2_touch_spirv::kMaxShapes;
// XInput's XINPUT_GAMEPAD_* bits.
constexpr uint16_t kA = 0x1000, kB = 0x2000, kX = 0x4000, kY = 0x8000, kStart = 0x0010;

// The controls, in pixels of a w x h surface, all sized by s (the short side, times the scale).
struct Button {
    float x, y, hx, hy, r;       // centre, half size, corner radius
    uint16_t bit;                // an XInput button, or 0
    int trigger;                 // 1 LT, 2 RT, 0 none
    float rgb[3];
    bool round;                  // hit by distance from the centre, else by the box
    bool shown = true;           // false: not there (X and Y with two face buttons), never hit
};
enum { B_A, B_B, B_X, B_Y, B_START, B_LT, B_RT, B_COUNT };
static_assert(B_COUNT == kButtons, "Settings::sends is by button");
struct Layout {
    float s;
    float stick_x, stick_y, stick_r;   // the stick's resting place and radius
    bool stick_left;                   // the stick takes the left half of the window, else the right
    Button b[B_COUNT];
};

// The band the controls take below the picture, and how tall they need it in units of s: the top
// row (START, LT, RT) ends 0.12 s down, the diamond starts 0.46 s up from the bottom, 0.02 s apart.
constexpr float kBelowNeed = 0.60f;
constexpr float kBelowMin = 0.30f;      // the least band, in the window's short side, worth it

// The picture's top and bottom rows as the device presents a fw x fh frame in a w x h window
// (host/d3d9gpu/d9g_gpu.h fit_rect: centred at its own aspect, the whole window when the two
// agree to within 2 px; with an anchor in a window taller than wide, its centre at anchor of the
// height, its top no higher than min_top and no lower than centred); the whole height when there is
// no frame or it fills it.
void picture_rows(float w, float h, uint32_t fw, uint32_t fh, float anchor, float min_top, float* top,
                  float* bottom) {
    *top = 0;
    *bottom = h;
    if (!fw || !fh || w <= 0 || h <= 0) return;
    if (double(w) * fh > double(h) * fw) return;               // wider than the frame: bars at the sides
    const float ph = std::min(h, std::round(w * float(fh) / float(fw)));
    if (h - ph <= 2.0f) return;
    *top = std::floor((h - ph) / 2);
    if (anchor >= 0 && h > w)
        *top = std::min(*top, std::max(min_top, float(std::llround(double(anchor) * h - double(ph) / 2))));
    *bottom = *top + ph;
}

// Half a group's width and height at size s: the stick's base, the diamond's reach, START's, LT's
// and RT's boxes.
void group_half(Group g, float s, float* hw, float* hh) {
    switch (g) {
    case Group::Stick: *hw = *hh = 0.12f * s; return;
    case Group::Face: *hw = *hh = 0.21f * s; return;
    case Group::Start: *hw = 0.075f * s; *hh = 0.035f * s; return;
    default: *hw = 0.09f * s; *hh = 0.04f * s; return;
    }
}

// Where the picture goes in a taller window (set_picture): no anchor, centred.
struct Anchor {
    float y = -1, min_top = 0;
};

Layout layout(const Settings& st, float w, float h, uint32_t fw, uint32_t fh, const Anchor& an) {
    Layout l{};
    const float short_side = std::min(w, h);
    float s = short_side * st.scale;
    // The control area: the window, or the band under the picture.
    float ax = 0, ay = 0, aw = w, ah = h;
    float top, bottom;
    picture_rows(w, h, fw, fh, an.y, an.min_top, &top, &bottom);
    const bool below = h - bottom >= kBelowMin * short_side;
    if (below) {
        ay = bottom;
        ah = h - bottom;
        s = std::min(s, ah / kBelowNeed);
    }
    // A group's place: its own, or the one set for the window's orientation, held whole inside the
    // control area at this size (a place set at a smaller size could reach past an edge).
    const Place* place = h > w ? st.portrait : st.landscape;
    const auto at = [&](Group g, float dx, float dy, float* x, float* y) {
        const Place& p = place[int(g)];
        if (!p.set) {
            *x = dx;
            *y = dy;
            return;
        }
        float hw, hh;
        group_half(g, s, &hw, &hh);
        hw = std::min(hw, aw / 2);
        hh = std::min(hh, ah / 2);
        *x = std::max(ax + hw, std::min(ax + aw - hw, ax + p.x * aw));
        *y = std::max(ay + hh, std::min(ay + ah - hh, ay + p.y * ah));
    };
    const float m = 0.04f * s;
    l.s = s;
    l.stick_r = 0.12f * s;
    at(Group::Stick, ax + m + 1.6f * l.stick_r, ay + ah - m - 1.6f * l.stick_r, &l.stick_x, &l.stick_y);
    l.stick_left = l.stick_x < 0.5f * w;
    const float r = 0.075f * s, d = 0.135f * s;
    float cx, cy;
    at(Group::Face, ax + aw - m - d - r, ay + ah - m - d - r, &cx, &cy);
    if (st.two_buttons) {
        // JUMP (A) and ACTION (B) in the diamond's box (0.21 s each way), in its bottom-right and
        // top-left corners; their reach (1.3 radii) leaves a gap between them.
        const float rb = 0.10f * s, o = 0.21f * s - rb;
        l.b[B_A] = {cx + o, cy + o, rb, rb, rb, kA, 0, {0.30f, 0.80f, 0.35f}, true};
        l.b[B_B] = {cx - o, cy - o, rb, rb, rb, kB, 0, {0.92f, 0.30f, 0.30f}, true};
        l.b[B_X] = {cx, cy, 0, 0, 0, 0, 0, {0, 0, 0}, true, false};
        l.b[B_Y] = {cx, cy, 0, 0, 0, 0, 0, {0, 0, 0}, true, false};
    } else {
        l.b[B_A] = {cx, cy + d, r, r, r, kA, 0, {0.30f, 0.80f, 0.35f}, true};
        l.b[B_B] = {cx + d, cy, r, r, r, kB, 0, {0.92f, 0.30f, 0.30f}, true};
        l.b[B_X] = {cx - d, cy, r, r, r, kX, 0, {0.30f, 0.55f, 0.95f}, true};
        l.b[B_Y] = {cx, cy - d, r, r, r, kY, 0, {0.95f, 0.80f, 0.25f}, true};
    }
    const float ph = 0.035f * s;
    at(Group::Start, ax + aw * 0.5f, ay + m + ph, &cx, &cy);
    l.b[B_START] = {cx, cy, 0.075f * s, ph, ph, kStart, 0, {0.85f, 0.85f, 0.85f}, false};
    const float th = 0.04f * s, tw = 0.09f * s;
    at(Group::LT, ax + m + tw, ay + m + th, &cx, &cy);
    l.b[B_LT] = {cx, cy, tw, th, th, 0, 1, {0.85f, 0.85f, 0.85f}, false};
    at(Group::RT, ax + aw - m - tw, ay + m + th, &cx, &cy);
    l.b[B_RT] = {cx, cy, tw, th, th, 0, 2, {0.85f, 0.85f, 0.85f}, false};
    for (int i = 0; i < B_COUNT; ++i)
        if (st.mapped[i] && l.b[i].shown) {
            l.b[i].bit = st.sends[i].bit;
            l.b[i].trigger = st.sends[i].trigger;
        }
    return l;
}

// The button under (x, y), or -1: the nearest round one within 1.3 radii, else a box with a
// margin of 2% of s.
int hit(const Layout& l, float x, float y) {
    int best = -1;
    float best_d = 0;
    for (int i = 0; i < B_COUNT; ++i) {
        const Button& b = l.b[i];
        if (b.round && b.shown) {
            const float d = std::hypot(x - b.x, y - b.y);
            if (d < 1.3f * b.r && (best < 0 || d < best_d)) { best = i; best_d = d; }
        }
    }
    if (best >= 0) return best;
    const float pad = 0.02f * l.s;
    for (int i = 0; i < B_COUNT; ++i) {
        const Button& b = l.b[i];
        if (!b.round && b.shown && std::fabs(x - b.x) <= b.hx + pad && std::fabs(y - b.y) <= b.hy + pad) return i;
    }
    return -1;
}

enum Role : uint8_t { R_FREE, R_NONE, R_STICK, R_BUTTON };
struct Finger {
    Role role = R_FREE;
    SDL_TouchID touch = 0;
    SDL_FingerID id = 0;
    int button = -1;
    float x = 0, y = 0;          // where it is, normalised to the window
    float ox = 0, oy = 0;        // R_STICK: the stick's origin, where it landed, normalised
};

struct Uniforms {                // touch_overlay_shaders.py's block, std140: vec4s throughout
    float target[4];
    float geom[kMaxShapes][4];
    float style[kMaxShapes][4];
    float color[kMaxShapes][4];
};

}  // namespace

struct Overlay {
    SDL_GPUDevice* dev = nullptr;
    SDL_Window* win = nullptr;
    bool active = false;
    Finger fingers[kMaxFingers];
    sa2_input::PadState state;   // recomputed at every event the overlay takes
    SDL_GPUShader* vs = nullptr;
    SDL_GPUShader* fs = nullptr;
    SDL_GPUGraphicsPipeline* pipe = nullptr;
    SDL_GPUTextureFormat pipe_format = SDL_GPU_TEXTUREFORMAT_INVALID;
    SDL_GPUTextureFormat override_format = SDL_GPU_TEXTUREFORMAT_INVALID;
    bool gpu_failed = false;
    Stats st;
    Uniforms u{};
    Settings settings;
    uint32_t frame_w = 0, frame_h = 0;   // the game's frame at the last Present (set_frame)
    Anchor anchor;                       // set_picture
    uint32_t no_window_w = 1280, no_window_h = 720;   // set_size
};

namespace {

void window_size(const Overlay* o, float* w, float* h) {
    int iw = 0, ih = 0;
    if (!o->win || !SDL_GetWindowSizeInPixels(o->win, &iw, &ih) || iw <= 0 || ih <= 0) {
        iw = int(o->no_window_w);
        ih = int(o->no_window_h);
    }
    *w = float(iw);
    *h = float(ih);
}

// The stick's displacement in pixels of a w x h surface: from its origin, which stays where the
// finger landed, to the finger, clamped to its radius, so a finger at or past the edge is a full
// push in its direction.
void stick_offset(const Finger& f, const Layout& l, float w, float h, float* dx, float* dy) {
    float x = (f.x - f.ox) * w, y = (f.y - f.oy) * h;
    const float d = std::hypot(x, y);
    if (d > l.stick_r && d > 0) { x *= l.stick_r / d; y *= l.stick_r / d; }
    *dx = x;
    *dy = y;
}

int16_t axis(float v) {
    const float c = std::max(-1.0f, std::min(1.0f, v));
    return int16_t(std::lround(c * 32767.0f));
}

void recompute(Overlay* o) {
    sa2_input::PadState s;
    if (o->active) {
        s.connected = true;
        float w, h;
        window_size(o, &w, &h);
        const Layout l = layout(o->settings, w, h, o->frame_w, o->frame_h, o->anchor);
        for (const Finger& f : o->fingers) {
            if (f.role == R_BUTTON && f.button >= 0) {
                const Button& b = l.b[f.button];
                s.buttons |= b.bit;
                if (b.trigger == 1) s.lt = 255;
                if (b.trigger == 2) s.rt = 255;
            } else if (f.role == R_STICK) {
                float dx, dy;
                stick_offset(f, l, w, h, &dx, &dy);
                float nx = dx / l.stick_r, ny = -dy / l.stick_r;   // XInput: up is positive
                if (std::hypot(nx, ny) < 0.06f) nx = ny = 0;       // a resting thumb's jitter
                s.lx = axis(nx);
                s.ly = axis(ny);
            }
        }
    }
    o->state = s;
}

Finger* find(Overlay* o, SDL_TouchID touch, SDL_FingerID id) {
    for (Finger& f : o->fingers)
        if (f.role != R_FREE && f.touch == touch && f.id == id) return &f;
    return nullptr;
}

bool stick_held(const Overlay* o) {
    for (const Finger& f : o->fingers) if (f.role == R_STICK) return true;
    return false;
}

void note_error(Overlay* o, const std::string& why) {
    if (o->st.error.empty()) o->st.error = why;
}

bool ensure_pipeline(Overlay* o) {
    if (o->gpu_failed) return false;
    SDL_GPUTextureFormat fmt = o->override_format;
    if (fmt == SDL_GPU_TEXTUREFORMAT_INVALID && o->win) fmt = SDL_GetGPUSwapchainTextureFormat(o->dev, o->win);
    if (fmt == SDL_GPU_TEXTUREFORMAT_INVALID) {
        note_error(o, "no target format (the window is not claimed and none was set)");
        o->gpu_failed = true;
        return false;
    }
    if (o->pipe && o->pipe_format == fmt) return true;
    if (!(SDL_GetGPUShaderFormats(o->dev) & SDL_GPU_SHADERFORMAT_SPIRV)) {
        note_error(o, "the device takes no SPIR-V");
        o->gpu_failed = true;
        return false;
    }
    if (!o->vs) {
        SDL_GPUShaderCreateInfo ci{};
        ci.code = reinterpret_cast<const Uint8*>(sa2_touch_spirv::kVertex);
        ci.code_size = sizeof sa2_touch_spirv::kVertex;
        ci.entrypoint = "main";
        ci.format = SDL_GPU_SHADERFORMAT_SPIRV;
        ci.stage = SDL_GPU_SHADERSTAGE_VERTEX;
        ci.num_uniform_buffers = 1;
        o->vs = SDL_CreateGPUShader(o->dev, &ci);
        ci.code = reinterpret_cast<const Uint8*>(sa2_touch_spirv::kFragment);
        ci.code_size = sizeof sa2_touch_spirv::kFragment;
        ci.stage = SDL_GPU_SHADERSTAGE_FRAGMENT;
        ci.num_uniform_buffers = 0;
        o->fs = o->vs ? SDL_CreateGPUShader(o->dev, &ci) : nullptr;
        if (!o->vs || !o->fs) {
            note_error(o, std::string("SDL_CreateGPUShader: ") + SDL_GetError());
            o->gpu_failed = true;
            return false;
        }
    }
    if (o->pipe) { SDL_ReleaseGPUGraphicsPipeline(o->dev, o->pipe); o->pipe = nullptr; }
    SDL_GPUColorTargetDescription ct{};
    ct.format = fmt;
    ct.blend_state.enable_blend = true;
    ct.blend_state.src_color_blendfactor = SDL_GPU_BLENDFACTOR_SRC_ALPHA;
    ct.blend_state.dst_color_blendfactor = SDL_GPU_BLENDFACTOR_ONE_MINUS_SRC_ALPHA;
    ct.blend_state.color_blend_op = SDL_GPU_BLENDOP_ADD;
    ct.blend_state.src_alpha_blendfactor = SDL_GPU_BLENDFACTOR_ZERO;
    ct.blend_state.dst_alpha_blendfactor = SDL_GPU_BLENDFACTOR_ONE;
    ct.blend_state.alpha_blend_op = SDL_GPU_BLENDOP_ADD;
    // The target's alpha is left as the frame wrote it (a compositor may read it).
    ct.blend_state.color_write_mask = SDL_GPU_COLORCOMPONENT_R | SDL_GPU_COLORCOMPONENT_G | SDL_GPU_COLORCOMPONENT_B;
    ct.blend_state.enable_color_write_mask = true;
    SDL_GPUGraphicsPipelineCreateInfo pi{};
    pi.vertex_shader = o->vs;
    pi.fragment_shader = o->fs;
    pi.primitive_type = SDL_GPU_PRIMITIVETYPE_TRIANGLELIST;
    pi.rasterizer_state.fill_mode = SDL_GPU_FILLMODE_FILL;
    pi.rasterizer_state.cull_mode = SDL_GPU_CULLMODE_NONE;
    pi.multisample_state.sample_count = SDL_GPU_SAMPLECOUNT_1;
    pi.target_info.color_target_descriptions = &ct;
    pi.target_info.num_color_targets = 1;
    o->pipe = SDL_CreateGPUGraphicsPipeline(o->dev, &pi);
    if (!o->pipe) {
        note_error(o, std::string("SDL_CreateGPUGraphicsPipeline: ") + SDL_GetError());
        o->gpu_failed = true;
        return false;
    }
    o->pipe_format = fmt;
    return true;
}

struct ShapeList {
    Uniforms* u;
    float opacity;               // SA2_TOUCH_OPACITY: every alpha times this, at most 1
    int n = 0;
    // A flat shape, or with `stripes` (a period in pixels) a glossy one (touch_overlay_shaders.py).
    void add(float x, float y, float hx, float hy, float r, float ring, const float rgb[3], float a,
             float stripes = 0) {
        if (n >= kMaxShapes) return;
        float* g = u->geom[n];
        g[0] = x; g[1] = y; g[2] = hx; g[3] = hy;
        float* s = u->style[n];
        s[0] = r; s[1] = ring; s[2] = stripes > 0 ? 1.0f : 0.0f; s[3] = stripes;
        float* c = u->color[n];
        c[0] = rgb[0]; c[1] = rgb[1]; c[2] = rgb[2]; c[3] = std::min(1.0f, a * opacity);
        ++n;
    }
};

// The adventure style's colours by Theme: a face and a darker rim.
struct ThemeColours {
    float face[3], rim[3];
};
constexpr float c8(int v) { return float(v) / 255.0f; }
#define SA2_RGB(h) {c8(((h) >> 16) & 255), c8(((h) >> 8) & 255), c8((h) & 255)}
const ThemeColours kThemeColours[kThemes] = {
    {SA2_RGB(0x1e5bd8), SA2_RGB(0x0b2f8a)},   // sonic: blue
    {SA2_RGB(0x3c3d46), SA2_RGB(0xb3141b)},   // shadow: dark grey, a red rim
    {SA2_RGB(0xd8261e), SA2_RGB(0x7a0b0b)},   // knuckles: red
    {SA2_RGB(0xf2a01e), SA2_RGB(0x9a5406)},   // tails: orange-yellow
    {SA2_RGB(0xb24ec4), SA2_RGB(0x5a1a6c)},   // rouge: pink-purple
    {SA2_RGB(0xef68a8), SA2_RGB(0x96205c)},   // amy: pink
    {SA2_RGB(0xe2561c), SA2_RGB(0x7c2408)},   // eggman: red-orange
    {SA2_RGB(0x5c7092), SA2_RGB(0x27334c)},   // metal: blue-grey
    {SA2_RGB(0x78c4f0), SA2_RGB(0x2c76a8)},   // chao: light blue
};
#undef SA2_RGB
const char* const kThemeNames[kThemes] = {"sonic", "shadow", "knuckles", "tails", "rouge",
                                          "amy", "eggman", "metal", "chao"};

// The adventure style's controls (touch_overlay.h): `stick` the stick's finger or null, its base at
// (sx, sy) and its knob (kx, ky) from there; `held` the buttons held, a bit by B_*.
void draw_adventure(ShapeList& sh, const Layout& l, Theme theme, bool stick, float sx, float sy, float kx,
                    float ky, uint32_t held) {
    static const float white[3] = {1, 1, 1};
    const ThemeColours& tc = kThemeColours[int(theme)];
    const float stripes = std::max(3.0f, 0.014f * l.s), rim = std::max(2.0f, 0.012f * l.s);
    // A glossy face and its rim; held, a little darker and smaller.
    const auto button = [&](float x, float y, float hx, float hy, float r, bool down) {
        const float k = down ? 0.92f : 1.0f, dim = down ? 0.72f : 1.0f;
        const float face[3] = {tc.face[0] * dim, tc.face[1] * dim, tc.face[2] * dim};
        sh.add(x, y, k * hx, k * hy, k * r, 0, face, down ? 0.80f : 0.72f, stripes);
        sh.add(x, y, k * hx, k * hy, k * r, rim, tc.rim, down ? 0.95f : 0.85f);
    };
    // The stick: a disc tinted with the face colour and a thin ring of it, and a glossy knob.
    const float R = l.stick_r, line = std::max(2.0f, 0.008f * l.s), kr = 0.42f * R;
    sh.add(sx, sy, R, R, R, 0, tc.face, stick ? 0.32f : 0.22f);
    sh.add(sx, sy, R, R, R, line, tc.face, stick ? 0.90f : 0.70f);
    button(sx + kx, sy + ky, kr, kr, kr, stick);
    for (int i = 0; i < B_COUNT; ++i) {
        const Button& b = l.b[i];
        if (b.shown) button(b.x, b.y, b.hx, b.hy, b.r, held & (1u << i));
    }
    // Labels: none yet, until a font with a known licence is chosen. A label would be drawn here,
    // over each face (JUMP on B_A and ACTION on B_B with two face buttons), centred at b.x, b.y.
    // START's mark: two bars; LT and RT: one (the classic style's).
    const Button& st = l.b[B_START];
    sh.add(st.x, st.y - 0.3f * st.hy, 0.45f * st.hx, 0.1f * st.hy, 0.1f * st.hy, 0, white, 0.8f);
    sh.add(st.x, st.y + 0.3f * st.hy, 0.45f * st.hx, 0.1f * st.hy, 0.1f * st.hy, 0, white, 0.8f);
    for (int i : {B_LT, B_RT}) {
        const Button& b = l.b[i];
        sh.add(b.x, b.y, 0.5f * b.hx, 0.12f * b.hy, 0.12f * b.hy, 0, white, 0.8f);
    }
}

}  // namespace

Overlay* create(SDL_GPUDevice* device, SDL_Window* window) {
    Overlay* o = new Overlay;
    o->dev = device;
    o->win = window;
    return o;
}

bool handle_event(Overlay* o, const SDL_Event& e) {
    if (!o) return false;
    if (e.type != SDL_EVENT_FINGER_DOWN && e.type != SDL_EVENT_FINGER_UP && e.type != SDL_EVENT_FINGER_MOTION &&
        e.type != SDL_EVENT_FINGER_CANCELED)
        return false;
    const SDL_TouchFingerEvent& t = e.tfinger;
    if (t.touchID == SDL_MOUSE_TOUCHID ||
        (o->win && t.windowID != 0 && t.windowID != SDL_GetWindowID(o->win))) {
        ++o->st.ignored_fingers;
        return false;
    }
    ++o->st.finger_events;
    float w, h;
    window_size(o, &w, &h);
    const Layout l = layout(o->settings, w, h, o->frame_w, o->frame_h, o->anchor);
    Finger* f = find(o, t.touchID, t.fingerID);
    if (e.type == SDL_EVENT_FINGER_DOWN) {
        if (!o->active) { o->active = true; ++o->st.activations; }
        if (!f)
            for (Finger& free : o->fingers) if (free.role == R_FREE) { f = &free; break; }
        if (!f) return true;          // more fingers than tracked: this one does nothing
        *f = Finger{};
        f->touch = t.touchID;
        f->id = t.fingerID;
        f->x = f->ox = t.x;
        f->y = f->oy = t.y;
        f->button = hit(l, t.x * w, t.y * h);
        if (f->button >= 0) f->role = R_BUTTON;
        else if ((t.x < 0.5f) == l.stick_left && !stick_held(o)) f->role = R_STICK;
        else f->role = R_NONE;
    } else if (e.type == SDL_EVENT_FINGER_MOTION) {
        if (!f) return true;
        f->x = t.x;
        f->y = t.y;
        // A stick finger's origin stays where it landed, however far the finger goes.
        if (f->role == R_BUTTON) f->button = hit(l, t.x * w, t.y * h);
    } else {                          // up or cancelled
        if (f) *f = Finger{};
    }
    recompute(o);
    return true;
}

bool active(const Overlay* o) { return o && o->active; }

void pad(const Overlay* o, sa2_input::PadState* out) {
    if (!out) return;
    *out = o ? o->state : sa2_input::PadState{};
}

void deactivate(Overlay* o) {
    if (!o) return;
    o->active = false;
    for (Finger& f : o->fingers) f = Finger{};
    recompute(o);
}

void attach_device(Overlay* o, SDL_GPUDevice* device) {
    if (!o || o->dev == device) return;
    o->pipe = nullptr;
    o->vs = o->fs = nullptr;
    o->pipe_format = SDL_GPU_TEXTUREFORMAT_INVALID;
    o->gpu_failed = false;
    o->dev = device;
}

void set_target_format(Overlay* o, SDL_GPUTextureFormat format) {
    if (o) { o->override_format = format; o->gpu_failed = false; }
}

void draw(Overlay* o, SDL_GPUCommandBuffer* cb, SDL_GPUTexture* target, uint32_t w, uint32_t h) {
    if (!o || !o->active || o->settings.opacity <= 0) return;
    if (!o->dev || !cb || !target || !w || !h) return;
    if (!ensure_pipeline(o)) { ++o->st.draw_failures; return; }
    const float fw = float(w), fh = float(h);
    const Layout l = layout(o->settings, fw, fh, o->frame_w, o->frame_h, o->anchor);
    Uniforms& u = o->u;
    u.target[0] = fw; u.target[1] = fh; u.target[2] = 0; u.target[3] = 0;
    ShapeList sh{&u, o->settings.opacity};
    static const float white[3] = {1, 1, 1}, dark[3] = {0.05f, 0.05f, 0.08f};
    // The stick: its base where the finger landed, or at its resting place, and its knob at the
    // push, clamped to the base's rim.
    const Finger* stick = nullptr;
    for (const Finger& f : o->fingers) if (f.role == R_STICK) stick = &f;
    float sx = l.stick_x, sy = l.stick_y, kx = 0, ky = 0;
    if (stick) {
        sx = stick->ox * fw;
        sy = stick->oy * fh;
        stick_offset(*stick, l, fw, fh, &kx, &ky);
    }
    uint32_t held = 0;
    for (const Finger& f : o->fingers) if (f.role == R_BUTTON && f.button >= 0) held |= 1u << f.button;
    if (o->settings.style == Style::Adventure) {
        draw_adventure(sh, l, o->settings.theme, stick, sx, sy, kx, ky, held);
    } else {
        const float R = l.stick_r, line = std::max(2.0f, 0.008f * l.s);
        sh.add(sx, sy, R, R, R, 0, dark, stick ? 0.30f : 0.18f);
        sh.add(sx, sy, R, R, R, line, white, stick ? 0.55f : 0.35f);
        sh.add(sx + kx, sy + ky, 0.42f * R, 0.42f * R, 0.42f * R, 0, white, stick ? 0.70f : 0.40f);
        for (int i = 0; i < B_COUNT; ++i) {
            const Button& b = l.b[i];
            if (!b.shown) continue;
            const bool down = held & (1u << i);
            sh.add(b.x, b.y, b.hx, b.hy, b.r, 0, b.rgb, down ? 0.80f : 0.38f);
            sh.add(b.x, b.y, b.hx, b.hy, b.r, std::max(1.5f, 0.005f * l.s), white, down ? 0.85f : 0.45f);
        }
        // START's mark: two bars; LT and RT: one.
        const Button& st = l.b[B_START];
        sh.add(st.x, st.y - 0.3f * st.hy, 0.45f * st.hx, 0.1f * st.hy, 0.1f * st.hy, 0, white, 0.8f);
        sh.add(st.x, st.y + 0.3f * st.hy, 0.45f * st.hx, 0.1f * st.hy, 0.1f * st.hy, 0, white, 0.8f);
        for (int i : {B_LT, B_RT}) {
            const Button& b = l.b[i];
            sh.add(b.x, b.y, 0.5f * b.hx, 0.12f * b.hy, 0.12f * b.hy, 0, white, 0.8f);
        }
    }
    SDL_GPUColorTargetInfo ct{};
    ct.texture = target;
    ct.load_op = SDL_GPU_LOADOP_LOAD;
    ct.store_op = SDL_GPU_STOREOP_STORE;
    SDL_GPURenderPass* rp = SDL_BeginGPURenderPass(cb, &ct, 1, nullptr);
    if (!rp) { note_error(o, std::string("SDL_BeginGPURenderPass: ") + SDL_GetError()); ++o->st.draw_failures; return; }
    SDL_BindGPUGraphicsPipeline(rp, o->pipe);
    SDL_PushGPUVertexUniformData(cb, 0, &u, sizeof u);
    SDL_DrawGPUPrimitives(rp, Uint32(6 * sh.n), 1, 0, 0);
    SDL_EndGPURenderPass(rp);
    ++o->st.draws;
}

void destroy(Overlay* o) {
    if (!o) return;
    if (o->dev) {
        if (o->pipe) SDL_ReleaseGPUGraphicsPipeline(o->dev, o->pipe);
        if (o->vs) SDL_ReleaseGPUShader(o->dev, o->vs);
        if (o->fs) SDL_ReleaseGPUShader(o->dev, o->fs);
    }
    delete o;
}

Stats stats(const Overlay* o) {
    if (!o) return {};
    Stats s = o->st;
    s.gpu = o->dev && !o->gpu_failed && (SDL_GetGPUShaderFormats(o->dev) & SDL_GPU_SHADERFORMAT_SPIRV);
    return s;
}

namespace {

const Settings kNoSettings{};

Layout layout_of(const Overlay* o, uint32_t w, uint32_t h) {
    return o ? layout(o->settings, float(w), float(h), o->frame_w, o->frame_h, o->anchor)
             : layout(kNoSettings, float(w), float(h), 0, 0, Anchor{});
}

// A decimal number from lo to hi, digits and a point only (no sign, no exponent).
bool parse_number(const std::string& v, float lo, float hi, float* out) {
    if (v.empty() || v.size() > 12 || v.find_first_not_of("0123456789.") != std::string::npos) return false;
    char* end = nullptr;
    const float f = std::strtof(v.c_str(), &end);
    if (!end || *end || !std::isfinite(f) || f < lo || f > hi) return false;
    *out = f;
    return true;
}

// The KEY=VALUE items of `v`, `sep` between them (an item with no '=' has an empty value).
std::vector<std::pair<std::string, std::string>> items(const std::string& v, char sep) {
    std::vector<std::pair<std::string, std::string>> out;
    size_t at = 0;
    while (at <= v.size()) {
        size_t end = v.find(sep, at);
        if (end == std::string::npos) end = v.size();
        const std::string item = v.substr(at, end - at);
        const size_t eq = item.find('=');
        out.emplace_back(item.substr(0, eq), eq == std::string::npos ? "" : item.substr(eq + 1));
        at = end + 1;
    }
    return out;
}

}  // namespace

bool parse_setting(const std::string& name, const std::string& value, Settings* s, std::string* why) {
    Settings n = *s;
    why->clear();
    if (name == "SA2_TOUCH_SCALE") {
        if (!parse_number(value, 0.5f, 2.0f, &n.scale)) { *why = "not a number from 0.5 to 2"; return false; }
    } else if (name == "SA2_TOUCH_OPACITY") {
        if (!parse_number(value, 0.0f, 2.0f, &n.opacity)) { *why = "not a number from 0 to 2"; return false; }
    } else if (name == "SA2_TOUCH_LAYOUT" || name == "SA2_TOUCH_LAYOUT_PORTRAIT") {
        static const char* const groups[kGroups] = {"STICK", "FACE", "START", "LT", "RT"};
        Place* places = name == "SA2_TOUCH_LAYOUT" ? n.landscape : n.portrait;
        for (const auto& [k, v] : items(value, ';')) {
            int g = 0;
            while (g < kGroups && k != groups[g]) ++g;
            const size_t comma = v.find(',');
            Place p;
            p.set = true;
            if (g == kGroups || comma == std::string::npos || !parse_number(v.substr(0, comma), 0, 1, &p.x) ||
                !parse_number(v.substr(comma + 1), 0, 1, &p.y)) {
                *why = "\"" + k + "\" is not GROUP=X,Y with GROUP one of STICK FACE START LT RT and X and Y 0 to 1";
                return false;
            }
            places[g] = p;
        }
    } else if (name == "SA2_TOUCH_MAP") {
        static const char* const buttons[kButtons] = {"A", "B", "X", "Y", "START", "LT", "RT"};
        for (const auto& [k, v] : items(value, ',')) {
            int b = 0;
            while (b < kButtons && k != buttons[b]) ++b;
            sa2_input::PadOutput out;
            if (b == kButtons || !sa2_input::pad_output_named(v, &out)) {
                *why = "\"" + k + "\" is not BUTTON=OUTPUT with BUTTON one of A B X Y START LT RT and OUTPUT a game button";
                return false;
            }
            n.sends[b] = out;
            n.mapped[b] = true;
        }
    } else if (name == "SA2_TOUCH_STYLE") {
        if (value == "classic") n.style = Style::Classic;
        else if (value == "adventure") n.style = Style::Adventure;
        else { *why = "not classic or adventure"; return false; }
    } else if (name == "SA2_TOUCH_THEME") {
        int t = 0;
        while (t < kThemes && value != kThemeNames[t]) ++t;
        if (t < kThemes) {
            n.theme = Theme(t);
        } else if (value == "auto") {
            n.theme = Theme::Sonic;
            *why = "auto: the character being played is not known to the player yet, so sonic's colours";
        } else {
            *why = "not one of sonic shadow knuckles tails rouge amy eggman metal chao auto";
            return false;
        }
    } else if (name == "SA2_TOUCH_FACE") {
        if (value == "four") n.two_buttons = false;
        else if (value == "two") n.two_buttons = true;
        else { *why = "not four or two"; return false; }
    } else {
        *why = "not a touch setting";
        return false;
    }
    *s = n;
    return true;
}

void set_settings(Overlay* o, const Settings& s) {
    if (!o) return;
    o->settings = s;
    recompute(o);
}

void set_size(Overlay* o, uint32_t w, uint32_t h) {
    if (!o || !w || !h) return;
    o->no_window_w = w;
    o->no_window_h = h;
}

void set_frame(Overlay* o, uint32_t frame_w, uint32_t frame_h) {
    if (!o) return;
    o->frame_w = frame_w;
    o->frame_h = frame_h;
}

void set_picture(Overlay* o, float anchor_y, uint32_t min_top) {
    if (!o) return;
    o->anchor.y = anchor_y;
    o->anchor.min_top = float(min_top);
}

bool picture_rows(const Overlay* o, uint32_t w, uint32_t h, uint32_t* top, uint32_t* bottom) {
    if (!o || !o->frame_w || !o->frame_h) return false;
    float t, b;
    picture_rows(float(w), float(h), o->frame_w, o->frame_h, o->anchor.y, o->anchor.min_top, &t, &b);
    *top = uint32_t(t);
    *bottom = uint32_t(b);
    return true;
}

bool below_picture(const Overlay* o, uint32_t w, uint32_t h) {
    if (!o) return false;
    float top, bottom;
    picture_rows(float(w), float(h), o->frame_w, o->frame_h, o->anchor.y, o->anchor.min_top, &top, &bottom);
    return float(h) - bottom >= kBelowMin * float(std::min(w, h));
}

void control_box(const Overlay* o, uint32_t w, uint32_t h, Control c, float* x, float* y, float* hw, float* hh) {
    const Layout l = layout_of(o, w, h);
    int i = -1;
    switch (c) {
    case Control::Stick: *x = l.stick_x; *y = l.stick_y; *hw = *hh = l.stick_r; return;
    case Control::A: i = B_A; break;
    case Control::B: i = B_B; break;
    case Control::X: i = B_X; break;
    case Control::Y: i = B_Y; break;
    case Control::Start: i = B_START; break;
    case Control::LT: i = B_LT; break;
    case Control::RT: i = B_RT; break;
    }
    *x = l.b[i].x;
    *y = l.b[i].y;
    *hw = l.b[i].hx;
    *hh = l.b[i].hy;
}

void control_centre(const Overlay* o, uint32_t w, uint32_t h, Control c, float* x, float* y) {
    float hw, hh;
    control_box(o, w, h, c, x, y, &hw, &hh);
}

float stick_radius(const Overlay* o, uint32_t w, uint32_t h) { return layout_of(o, w, h).stick_r; }

}  // namespace sa2_touch
