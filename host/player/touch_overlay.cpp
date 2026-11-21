// touch_overlay.cpp: the on-screen gamepad (touch_overlay.h).
#include "touch_overlay.h"

#include <SDL3/SDL_error.h>
#include <SDL3/SDL_touch.h>

#include <algorithm>
#include <cctype>
#include <climits>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <utility>
#include <vector>

#include "texcodec.h"
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
    bool stick_shown = true;           // false: the context uses no stick (apply_context)
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

// ---------------------------------------------------------------- the game's state
// What each context uses (touch_overlay.h, "The game's state"), the one table to change: the
// stick, the XInput buttons (the outputs, after SA2_TOUCH_MAP) and the triggers. In a stage
// JUMP (A), ACTION (B) and START (the pause), and LT and RT, which turn the camera (PC SA2's
// triggers; host/player/tweaks/camera-invert.h swaps them); in a menu the stick, A to choose, B
// to go back and START; in Chao World every button; in a cutscene START alone, which skips it.
struct ContextUse {
    Context context;
    const char* name;
    bool stick;
    uint16_t buttons;            // XInput bits
    bool lt, rt;
};
const ContextUse kContextUses[kContexts] = {
    {Context::Unknown, "unknown", true, 0xFFFF, true, true},
    {Context::Menu, "menu", true, kA | kB | kStart, false, false},
    {Context::Stage, "stage", true, kA | kB | kStart, true, true},
    {Context::ChaoWorld, "chao world", true, kA | kB | kX | kY | kStart, true, true},
    {Context::Cutscene, "cutscene", false, kStart, false, false},
};
const ContextUse& use_of(Context c) {
    for (const ContextUse& u : kContextUses) if (u.context == c) return u;
    return kContextUses[0];
}

// GameMode (the int32 at 0x0174B040) by the arm of GameModeHandler (0x00434160, its jump table at
// 0x00434BD4) it runs: 0, 1, 2, 13, 14 and 25 the title and the menus; 5 and 10 a level
// (GameStateHandler each frame); the pairs that load and then run a module by name (0x004549C0):
// 7 and 8 EVENT, 15 and 16 ENDING, 21 and 22 OTHERPRINT (the intro), cutscenes; 17 and 18
// EMBLEMGET, 19, 20, 23 and 24 MCWARN, screens that wait for a button, as a menu does. The rest
// (3, 4, 6, 9, 11, 12) load or pass between two of these, and keep the context before.
struct ModeContext {
    int32_t mode;
    Context context;
};
const ModeContext kModeContexts[] = {
    {0, Context::Menu},      {1, Context::Menu},      {2, Context::Menu},      {13, Context::Menu},
    {14, Context::Menu},     {25, Context::Menu},     {5, Context::Stage},     {10, Context::Stage},
    {7, Context::Cutscene},  {8, Context::Cutscene},  {15, Context::Cutscene}, {16, Context::Cutscene},
    {21, Context::Cutscene}, {22, Context::Cutscene}, {17, Context::Menu},     {18, Context::Menu},
    {19, Context::Menu},     {20, Context::Menu},     {23, Context::Menu},     {24, Context::Menu},
};
constexpr int32_t kChaoWorldLevel = 90;      // CurrentLevel's Chao World, a level like a stage

// CurrentCharacter (the int32 at 0x01934B80) as LoadCharacters (0x0043D630) loads it: 0 Sonic,
// 1 Shadow, 2 Tails, 3 Eggman, 4 Knuckles, 5 Rouge, 6 Tails' walker, 7 Eggman's walker; and in a
// two-player game the costume each loader checks (the byte at 0x0174B015 + player): Amy, Metal
// Sonic, Tikal (Tails' orange), Chaos 0, the Chao walker and the dark Chao walker (Chao's blue).
struct CharacterTheme {
    int32_t character;
    Theme own, costume;
};
const CharacterTheme kCharacterThemes[] = {
    {0, Theme::Sonic, Theme::Amy},     {1, Theme::Shadow, Theme::Metal}, {2, Theme::Tails, Theme::Tails},
    {3, Theme::Eggman, Theme::Eggman}, {4, Theme::Knuckles, Theme::Tails}, {5, Theme::Rouge, Theme::Chao},
    {6, Theme::Tails, Theme::Chao},    {7, Theme::Eggman, Theme::Chao},
};

// The layout as the overlay's style and context show it: in the adventure style, a control whose
// output the context does not use is not there (never drawn, never hit).
void apply_context(Layout& l, const Settings& st, Context c) {
    if (st.style != Style::Adventure) return;
    l.stick_shown = context_uses_stick(c);
    for (Button& b : l.b)
        if (b.shown && !context_uses(c, b.bit, b.trigger)) b.shown = false;
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
struct ArtUniforms {             // touch_overlay_shaders.py's ART_VERTEX block
    float target[4];
    float geom[kMaxShapes][4];
    float uv[kMaxShapes][4];
    float tint[kMaxShapes][4];
};

// ---------------------------------------------------------------- the game's art
// The atlas (touch_overlay.h, "The game's art"): newMenu.pak's four members at fixed places, RGBA8
// premultiplied. Each region's own hue (turns) is measured when it is made, so a theme's hue is
// reached from whatever blue (or, for the cursor's arc, red) the art is.
constexpr uint32_t kAtlasW = 512, kAtlasH = 256;
struct ArtMember {
    const char* leaf;            // the member's name after its last backslash, lower case
    uint32_t w, h, x, y;         // its size, and where it goes in the atlas
};
const ArtMember kArtMembers[] = {
    {"mainmenu_button_maru.dds", 128, 128, 0, 0},       // the round striped button
    {"mainmenu_button_maru_i.dds", 128, 128, 128, 0},   // the same, darker: held
    {"mainmenu_csol.dds", 128, 128, 256, 0},            // the cursor: an arc top left, a bracket
    {"mainmenu_button_sikaku.dds", 256, 128, 0, 128},   // two bars, one over the other
};
// The regions drawn, in texels of the atlas. The cursor's arc is a quarter circle about the
// corner (72, 72) of its image, its middle line 66 texels out; the bars are the sikaku image's
// rows 0 to 41 and 43 to 84.
struct Region {
    float x0, y0, x1, y1;
};
enum ArtRegion { AR_BUTTON, AR_HELD, AR_ARC, AR_BAR, AR_BAR_HELD, AR_COUNT };
const Region kRegions[AR_COUNT] = {
    {0, 0, 128, 128}, {128, 0, 256, 128}, {256, 0, 328, 72}, {0, 128, 256, 169}, {0, 171, 256, 212}};
constexpr float kArcSide = 72, kArcLine = 66;
struct Art {
    std::vector<uint8_t> rgba;   // kAtlasW x kAtlasH
    float hue[AR_COUNT] = {};
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
    GameState game;                      // set_game_state
    Context ctx = Context::Unknown;
    Theme auto_theme = Theme::Sonic;     // theme_of the last state
    // The game's art: the atlas (load_art), and what it needs on the device, made at the first
    // draw that uses it.
    bool has_art = false;
    Art art;
    ArtUniforms au{};
    SDL_GPUShader* art_vs = nullptr;
    SDL_GPUShader* art_fs = nullptr;
    SDL_GPUGraphicsPipeline* art_pipe = nullptr;
    SDL_GPUTextureFormat art_format = SDL_GPU_TEXTUREFORMAT_INVALID;
    SDL_GPUTexture* art_tex = nullptr;
    SDL_GPUSampler* art_sampler = nullptr;
    bool art_failed = false;             // the device could not take it: the shader's look instead
};

namespace {

// layout() as the overlay is now: its settings, frame and picture, and its context.
Layout shown_layout(const Overlay* o, float w, float h) {
    Layout l = layout(o->settings, w, h, o->frame_w, o->frame_h, o->anchor);
    apply_context(l, o->settings, o->ctx);
    return l;
}

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
        const Layout l = shown_layout(o, w, h);
        for (const Finger& f : o->fingers) {
            if (f.role == R_BUTTON && f.button >= 0) {
                const Button& b = l.b[f.button];
                if (!b.shown) continue;      // the context took it away under the finger
                s.buttons |= b.bit;
                if (b.trigger == 1) s.lt = 255;
                if (b.trigger == 2) s.rt = 255;
            } else if (f.role == R_STICK && l.stick_shown) {
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

// The art's shaders, pipeline, texture and sampler on the overlay's device, and the atlas
// uploaded in a copy pass of `cb` (before the draw's render pass); after ensure_pipeline, whose
// target format it shares. False (and art_failed, said once in the stats' error) when the
// device will not take them: the overlay then draws the shader's own look.
bool ensure_art(Overlay* o, SDL_GPUCommandBuffer* cb) {
    if (!o->has_art || o->art_failed) return false;
    const auto fail = [o](const char* what) {
        note_error(o, std::string(what) + ": " + SDL_GetError() + " (the game's art is not drawn)");
        o->art_failed = true;
        return false;
    };
    if (!o->art_vs) {
        SDL_GPUShaderCreateInfo ci{};
        ci.code = reinterpret_cast<const Uint8*>(sa2_touch_spirv::kArtVertex);
        ci.code_size = sizeof sa2_touch_spirv::kArtVertex;
        ci.entrypoint = "main";
        ci.format = SDL_GPU_SHADERFORMAT_SPIRV;
        ci.stage = SDL_GPU_SHADERSTAGE_VERTEX;
        ci.num_uniform_buffers = 1;
        if (!(o->art_vs = SDL_CreateGPUShader(o->dev, &ci))) return fail("SDL_CreateGPUShader");
        ci.code = reinterpret_cast<const Uint8*>(sa2_touch_spirv::kArtFragment);
        ci.code_size = sizeof sa2_touch_spirv::kArtFragment;
        ci.stage = SDL_GPU_SHADERSTAGE_FRAGMENT;
        ci.num_uniform_buffers = 0;
        ci.num_samplers = 1;
        if (!(o->art_fs = SDL_CreateGPUShader(o->dev, &ci))) return fail("SDL_CreateGPUShader");
    }
    if (!o->art_pipe || o->art_format != o->pipe_format) {
        if (o->art_pipe) { SDL_ReleaseGPUGraphicsPipeline(o->dev, o->art_pipe); o->art_pipe = nullptr; }
        SDL_GPUColorTargetDescription ct{};
        ct.format = o->pipe_format;
        ct.blend_state.enable_blend = true;
        ct.blend_state.src_color_blendfactor = SDL_GPU_BLENDFACTOR_ONE;   // the shader's colour is premultiplied
        ct.blend_state.dst_color_blendfactor = SDL_GPU_BLENDFACTOR_ONE_MINUS_SRC_ALPHA;
        ct.blend_state.color_blend_op = SDL_GPU_BLENDOP_ADD;
        ct.blend_state.src_alpha_blendfactor = SDL_GPU_BLENDFACTOR_ZERO;
        ct.blend_state.dst_alpha_blendfactor = SDL_GPU_BLENDFACTOR_ONE;
        ct.blend_state.alpha_blend_op = SDL_GPU_BLENDOP_ADD;
        ct.blend_state.color_write_mask = SDL_GPU_COLORCOMPONENT_R | SDL_GPU_COLORCOMPONENT_G | SDL_GPU_COLORCOMPONENT_B;
        ct.blend_state.enable_color_write_mask = true;
        SDL_GPUGraphicsPipelineCreateInfo pi{};
        pi.vertex_shader = o->art_vs;
        pi.fragment_shader = o->art_fs;
        pi.primitive_type = SDL_GPU_PRIMITIVETYPE_TRIANGLELIST;
        pi.rasterizer_state.fill_mode = SDL_GPU_FILLMODE_FILL;
        pi.rasterizer_state.cull_mode = SDL_GPU_CULLMODE_NONE;
        pi.multisample_state.sample_count = SDL_GPU_SAMPLECOUNT_1;
        pi.target_info.color_target_descriptions = &ct;
        pi.target_info.num_color_targets = 1;
        if (!(o->art_pipe = SDL_CreateGPUGraphicsPipeline(o->dev, &pi))) return fail("SDL_CreateGPUGraphicsPipeline");
        o->art_format = o->pipe_format;
    }
    if (!o->art_sampler) {
        SDL_GPUSamplerCreateInfo si{};
        si.min_filter = si.mag_filter = SDL_GPU_FILTER_LINEAR;
        si.mipmap_mode = SDL_GPU_SAMPLERMIPMAPMODE_NEAREST;
        si.address_mode_u = si.address_mode_v = si.address_mode_w = SDL_GPU_SAMPLERADDRESSMODE_CLAMP_TO_EDGE;
        if (!(o->art_sampler = SDL_CreateGPUSampler(o->dev, &si))) return fail("SDL_CreateGPUSampler");
    }
    if (!o->art_tex) {
        SDL_GPUTextureCreateInfo ti{};
        ti.type = SDL_GPU_TEXTURETYPE_2D;
        ti.format = SDL_GPU_TEXTUREFORMAT_R8G8B8A8_UNORM;
        ti.usage = SDL_GPU_TEXTUREUSAGE_SAMPLER;
        ti.width = kAtlasW;
        ti.height = kAtlasH;
        ti.layer_count_or_depth = 1;
        ti.num_levels = 1;
        ti.sample_count = SDL_GPU_SAMPLECOUNT_1;
        if (!(o->art_tex = SDL_CreateGPUTexture(o->dev, &ti))) return fail("SDL_CreateGPUTexture");
        SDL_GPUTransferBufferCreateInfo bi{};
        bi.usage = SDL_GPU_TRANSFERBUFFERUSAGE_UPLOAD;
        bi.size = kAtlasW * kAtlasH * 4;
        SDL_GPUTransferBuffer* up = SDL_CreateGPUTransferBuffer(o->dev, &bi);
        if (!up) return fail("SDL_CreateGPUTransferBuffer");
        void* m = SDL_MapGPUTransferBuffer(o->dev, up, false);
        if (!m) { SDL_ReleaseGPUTransferBuffer(o->dev, up); return fail("SDL_MapGPUTransferBuffer"); }
        std::memcpy(m, o->art.rgba.data(), o->art.rgba.size());
        SDL_UnmapGPUTransferBuffer(o->dev, up);
        SDL_GPUCopyPass* cp = SDL_BeginGPUCopyPass(cb);
        if (!cp) { SDL_ReleaseGPUTransferBuffer(o->dev, up); return fail("SDL_BeginGPUCopyPass"); }
        SDL_GPUTextureTransferInfo from{};
        from.transfer_buffer = up;
        SDL_GPUTextureRegion to{};
        to.texture = o->art_tex;
        to.w = kAtlasW;
        to.h = kAtlasH;
        to.d = 1;
        SDL_UploadToGPUTexture(cp, &from, &to, false);
        SDL_EndGPUCopyPass(cp);
        SDL_ReleaseGPUTransferBuffer(o->dev, up);   // freed once the copy is done with it
    }
    return true;
}

void release_art_gpu(Overlay* o) {
    if (o->dev) {
        if (o->art_pipe) SDL_ReleaseGPUGraphicsPipeline(o->dev, o->art_pipe);
        if (o->art_vs) SDL_ReleaseGPUShader(o->dev, o->art_vs);
        if (o->art_fs) SDL_ReleaseGPUShader(o->dev, o->art_fs);
        if (o->art_tex) SDL_ReleaseGPUTexture(o->dev, o->art_tex);
        if (o->art_sampler) SDL_ReleaseGPUSampler(o->dev, o->art_sampler);
    }
    o->art_pipe = nullptr;
    o->art_vs = o->art_fs = nullptr;
    o->art_tex = nullptr;
    o->art_sampler = nullptr;
    o->art_format = SDL_GPU_TEXTUREFORMAT_INVALID;
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
    // The stick: a glossy knob over a quiet base, a faint disc tinted with the face colour and a
    // hairline ring of it, so the knob is what the eye finds.
    const float R = l.stick_r, line = std::max(1.5f, 0.004f * l.s), kr = 0.42f * R;
    if (l.stick_shown) {
        sh.add(sx, sy, R, R, R, 0, tc.face, stick ? 0.16f : 0.10f);
        sh.add(sx, sy, R, R, R, line, tc.face, stick ? 0.45f : 0.30f);
        button(sx + kx, sy + ky, kr, kr, kr, stick);
    }
    for (int i = 0; i < B_COUNT; ++i) {
        const Button& b = l.b[i];
        if (b.shown) button(b.x, b.y, b.hx, b.hy, b.r, held & (1u << i));
    }
    // Labels: none yet, until a font with a known licence is chosen. A label would be drawn here,
    // over each face (JUMP on B_A and ACTION on B_B with two face buttons), centred at b.x, b.y.
    // START's mark: two bars; LT and RT: one (the classic style's).
    const Button& st = l.b[B_START];
    if (st.shown) {
        sh.add(st.x, st.y - 0.3f * st.hy, 0.45f * st.hx, 0.1f * st.hy, 0.1f * st.hy, 0, white, 0.8f);
        sh.add(st.x, st.y + 0.3f * st.hy, 0.45f * st.hx, 0.1f * st.hy, 0.1f * st.hy, 0, white, 0.8f);
    }
    for (int i : {B_LT, B_RT}) {
        const Button& b = l.b[i];
        if (b.shown) sh.add(b.x, b.y, 0.5f * b.hx, 0.12f * b.hy, 0.12f * b.hy, 0, white, 0.8f);
    }
}

// The game's art turned to each theme: the hue it is turned to (degrees; negative: the button's
// own, the menu's blue), and its saturation and value scaled. The five from the game's
// two-player windows (batadvPlayer.pak 2p_window_*.dds): Sonic (0, 0, 255), Shadow (222, 0, 0),
// darker here so it is not Knuckles' (255, 32, 0), Tails (255, 182, 0), Rouge (230, 69, 255);
// the rest chosen to match: Amy pink, Eggman red-orange, Metal Sonic blue-grey, Chao light blue.
struct ThemeTint {
    float hue, sat, val;
};
const ThemeTint kThemeTints[kThemes] = {
    {-1, 1, 1},          // sonic
    {0, 1, 0.55f},       // shadow
    {7.5f, 1, 1},        // knuckles
    {43, 1, 1.05f},      // tails
    {292, 1, 1},         // rouge
    {330, 0.7f, 1},      // amy
    {20, 1, 1},          // eggman
    {-1, 0.3f, 0.8f},    // metal
    {195, 0.65f, 1.1f},  // chao
};

struct ArtList {
    ArtUniforms* u;
    const Art* art;
    Theme theme;
    float opacity;
    int n = 0;
    // Region `r` over the box (x, y, hx, hy), mirrored across x or y, turned to the theme.
    void add(float x, float y, float hx, float hy, ArtRegion r, bool flip_x, bool flip_y, float a) {
        if (n >= kMaxShapes) return;
        float* g = u->geom[n];
        g[0] = x; g[1] = y; g[2] = hx; g[3] = hy;
        // Half a texel in from the region's edges, so the filter never reaches its neighbour.
        const Region& rg = kRegions[r];
        const float x0 = rg.x0 + 0.5f, y0 = rg.y0 + 0.5f, x1 = rg.x1 - 0.5f, y1 = rg.y1 - 0.5f;
        float* t = u->uv[n];
        t[0] = (flip_x ? x1 : x0) / kAtlasW;
        t[1] = (flip_y ? y1 : y0) / kAtlasH;
        t[2] = (flip_x ? x0 : x1) / kAtlasW;
        t[3] = (flip_y ? y0 : y1) / kAtlasH;
        const ThemeTint& tt = kThemeTints[int(theme)];
        // The theme's hue, or the button's own; the cursor's red arc turned to it either way.
        float shift = 0;
        if (tt.hue >= 0) shift = tt.hue / 360.0f - art->hue[r];
        else if (r == AR_ARC) shift = art->hue[AR_BUTTON] - art->hue[r];
        shift -= std::floor(shift);
        float* c = u->tint[n];
        c[0] = shift; c[1] = tt.sat; c[2] = tt.val; c[3] = std::min(1.0f, a * opacity);
        ++n;
    }
    // A bar over a box: its ends kept square (each as wide as the box is tall, at most half of
    // it) and its middle stretched between them.
    void bar(const Button& b, bool down) {
        const ArtRegion r = down ? AR_BAR_HELD : AR_BAR;
        const Region& rg = kRegions[r];
        const float cap = std::min(b.hx, 2 * b.hy) / 2;   // half an end's width on screen
        const float tex_cap = rg.y1 - rg.y0;               // an end's width in texels: the bar's height
        const float a = down ? 1.0f : 0.8f;
        const auto part = [&](float x0, float x1, float u0, float u1) {
            if (n >= kMaxShapes || x1 <= x0) return;
            add((x0 + x1) / 2, b.y, (x1 - x0) / 2, b.hy, r, false, false, a);
            u->uv[n - 1][0] = (rg.x0 + std::max(u0, 0.5f)) / kAtlasW;
            u->uv[n - 1][2] = (rg.x0 + std::min(u1, rg.x1 - rg.x0 - 0.5f)) / kAtlasW;
        };
        const float l = b.x - b.hx, rr = b.x + b.hx, w = rg.x1 - rg.x0;
        part(l, l + 2 * cap, 0, tex_cap);
        part(l + 2 * cap, rr - 2 * cap, tex_cap, w - tex_cap);
        part(rr - 2 * cap, rr, w - tex_cap, w);
    }
};

// The adventure style with the game's art (touch_overlay.h): under it `under` (the stick's tinted
// disc), the art itself in `art`, and over it `over` (START's, LT's and RT's marks).
void draw_adventure_art(ShapeList& under, ArtList& art, ShapeList& over, const Layout& l, Theme theme, bool stick,
                        float sx, float sy, float kx, float ky, uint32_t held) {
    static const float white[3] = {1, 1, 1};
    const ThemeColours& tc = kThemeColours[int(theme)];
    const float R = l.stick_r;
    if (l.stick_shown) {
        under.add(sx, sy, R, R, R, 0, tc.face, stick ? 0.16f : 0.10f);
        // The base: the cursor's arc four ways round, its middle line on the stick's radius, faint
        // (a guide; the knob is the focus).
        const float q = R * kArcSide / kArcLine / 2;   // half a quarter's side
        for (int i = 0; i < 4; ++i) {
            const bool right = i & 1, down = i & 2;
            art.add(sx + (right ? q : -q), sy + (down ? q : -q), q, q, AR_ARC, right, down, stick ? 0.45f : 0.28f);
        }
        const float kr = 0.42f * R;
        art.add(sx + kx, sy + ky, kr, kr, stick ? AR_HELD : AR_BUTTON, false, false, stick ? 1.0f : 0.80f);
    }
    for (int i : {B_A, B_B, B_X, B_Y}) {
        const Button& b = l.b[i];
        if (!b.shown) continue;
        const bool down = held & (1u << i);
        const float k = down ? 0.92f : 1.0f;
        art.add(b.x, b.y, k * b.hx, k * b.hy, down ? AR_HELD : AR_BUTTON, false, false, down ? 1.0f : 0.80f);
    }
    // Labels: none yet (draw_adventure says where they would go).
    for (int i : {B_START, B_LT, B_RT}) {
        const Button& b = l.b[i];
        if (b.shown) art.bar(b, held & (1u << i));
    }
    const Button& st = l.b[B_START];
    if (st.shown) {
        over.add(st.x, st.y - 0.3f * st.hy, 0.45f * st.hx, 0.1f * st.hy, 0.1f * st.hy, 0, white, 0.9f);
        over.add(st.x, st.y + 0.3f * st.hy, 0.45f * st.hx, 0.1f * st.hy, 0.1f * st.hy, 0, white, 0.9f);
    }
    for (int i : {B_LT, B_RT}) {
        const Button& b = l.b[i];
        if (b.shown) over.add(b.x, b.y, 0.5f * b.hx, 0.12f * b.hy, 0.12f * b.hy, 0, white, 0.9f);
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
    const Layout l = shown_layout(o, w, h);
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
        else if ((t.x < 0.5f) == l.stick_left && l.stick_shown && !stick_held(o)) f->role = R_STICK;
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
    o->art_pipe = nullptr;
    o->art_vs = o->art_fs = nullptr;
    o->art_tex = nullptr;
    o->art_sampler = nullptr;
    o->art_format = SDL_GPU_TEXTUREFORMAT_INVALID;
    o->art_failed = false;
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
    const Layout l = shown_layout(o, fw, fh);
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
    if (o->settings.style == Style::Adventure && ensure_art(o, cb)) {
        // Three draws in one pass: the procedural shapes under the art, the art, and over it.
        ArtUniforms& au = o->au;
        au.target[0] = fw; au.target[1] = fh; au.target[2] = 0; au.target[3] = 0;
        ArtList art{&au, &o->art, theme(o), o->settings.opacity};
        Uniforms over_u{};
        over_u.target[0] = fw; over_u.target[1] = fh;
        ShapeList over{&over_u, o->settings.opacity};
        draw_adventure_art(sh, art, over, l, theme(o), stick, sx, sy, kx, ky, held);
        SDL_GPUColorTargetInfo ct{};
        ct.texture = target;
        ct.load_op = SDL_GPU_LOADOP_LOAD;
        ct.store_op = SDL_GPU_STOREOP_STORE;
        SDL_GPURenderPass* rp = SDL_BeginGPURenderPass(cb, &ct, 1, nullptr);
        if (!rp) { note_error(o, std::string("SDL_BeginGPURenderPass: ") + SDL_GetError()); ++o->st.draw_failures; return; }
        if (sh.n) {
            SDL_BindGPUGraphicsPipeline(rp, o->pipe);
            SDL_PushGPUVertexUniformData(cb, 0, &u, sizeof u);
            SDL_DrawGPUPrimitives(rp, Uint32(6 * sh.n), 1, 0, 0);
        }
        if (art.n) {
            SDL_BindGPUGraphicsPipeline(rp, o->art_pipe);
            const SDL_GPUTextureSamplerBinding tb{o->art_tex, o->art_sampler};
            SDL_BindGPUFragmentSamplers(rp, 0, &tb, 1);
            SDL_PushGPUVertexUniformData(cb, 0, &au, sizeof au);
            SDL_DrawGPUPrimitives(rp, Uint32(6 * art.n), 1, 0, 0);
        }
        if (over.n) {
            SDL_BindGPUGraphicsPipeline(rp, o->pipe);
            SDL_PushGPUVertexUniformData(cb, 0, &over_u, sizeof over_u);
            SDL_DrawGPUPrimitives(rp, Uint32(6 * over.n), 1, 0, 0);
        }
        SDL_EndGPURenderPass(rp);
        ++o->st.draws;
        return;
    }
    if (o->settings.style == Style::Adventure) {
        draw_adventure(sh, l, theme(o), stick, sx, sy, kx, ky, held);
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
    release_art_gpu(o);
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
            n.auto_theme = false;
        } else if (value == "auto") {
            n.auto_theme = true;
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

bool control_shown(const Overlay* o, Control c) {
    Layout l = layout_of(o, 1280, 720);
    if (o) apply_context(l, o->settings, o->ctx);
    switch (c) {
    case Control::Stick: return l.stick_shown;
    case Control::A: return l.b[B_A].shown;
    case Control::B: return l.b[B_B].shown;
    case Control::X: return l.b[B_X].shown;
    case Control::Y: return l.b[B_Y].shown;
    case Control::Start: return l.b[B_START].shown;
    case Control::LT: return l.b[B_LT].shown;
    case Control::RT: return l.b[B_RT].shown;
    }
    return false;
}

// ---------------------------------------------------------------- the game's state

const char* context_name(Context c) { return use_of(c).name; }

const char* theme_name(Theme t) { return int(t) >= 0 && int(t) < kThemes ? kThemeNames[int(t)] : "?"; }

bool parse_state_note(const std::string& text, GameState* out) {
    static const char kHead[] = "state ";
    if (text.compare(0, sizeof kHead - 1, kHead) != 0) return false;
    GameState g;
    for (const auto& [k, v] : items(text.substr(sizeof kHead - 1), ' ')) {
        if (k.empty() && v.empty()) continue;
        char* end = nullptr;
        const long n = std::strtol(v.c_str(), &end, 10);
        if (v.empty() || !end || *end || n < INT32_MIN || n > INT32_MAX) return false;
        if (k == "mode") g.mode = int32_t(n);
        else if (k == "level") g.level = int32_t(n);
        else if (k == "character") g.character = int32_t(n);
        else if (k == "two") g.two_player = int32_t(n);
        else if (k == "alt") g.alt = int32_t(n);
    }
    *out = g;
    return true;
}

Context context_of(const GameState& s, Context last) {
    if (s.mode < 0) return Context::Unknown;
    for (const ModeContext& m : kModeContexts)
        if (m.mode == s.mode)
            return m.context == Context::Stage && s.level == kChaoWorldLevel ? Context::ChaoWorld : m.context;
    return last;
}

Theme theme_of(const GameState& s, Context c) {
    if (c != Context::Stage && c != Context::ChaoWorld) return Theme::Sonic;
    for (const CharacterTheme& t : kCharacterThemes)
        if (t.character == s.character) return s.two_player > 0 && s.alt > 0 ? t.costume : t.own;
    return Theme::Sonic;
}

bool context_uses(Context c, uint16_t bit, int trigger) {
    const ContextUse& u = use_of(c);
    if (trigger == 1) return u.lt;
    if (trigger == 2) return u.rt;
    return bit && (u.buttons & bit) == bit;
}

bool context_uses_stick(Context c) { return use_of(c).stick; }

void set_game_state(Overlay* o, const GameState& s) {
    if (!o) return;
    o->game = s;
    o->ctx = context_of(s, o->ctx);
    o->auto_theme = theme_of(s, o->ctx);
    recompute(o);
}

Context context(const Overlay* o) { return o ? o->ctx : Context::Unknown; }

Theme theme(const Overlay* o) {
    if (!o) return Theme::Sonic;
    return o->settings.auto_theme ? o->auto_theme : o->settings.theme;
}

// ---------------------------------------------------------------- the game's art

namespace {

uint32_t rd32le(const uint8_t* p) { return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24; }

std::string lower(std::string v) {
    for (char& c : v) c = char(std::tolower(static_cast<unsigned char>(c)));
    return v;
}

// `rel` under `root`, each part matched as it is or else without regard to case (an install
// copied from Windows may have any case).
bool find_nocase(const std::filesystem::path& root, const std::vector<std::string>& rel, std::filesystem::path* out) {
    std::error_code ec;
    std::filesystem::path at = root;
    for (const std::string& part : rel) {
        if (std::filesystem::exists(at / part, ec)) { at /= part; continue; }
        bool found = false;
        for (std::filesystem::directory_iterator it(at, ec), end; !ec && it != end; it.increment(ec))
            if (lower(it->path().filename().string()) == lower(part)) {
                at = it->path();
                found = true;
                break;
            }
        if (!found) return false;
    }
    *out = at;
    return true;
}

// A PAK as the game's files hold one: 01 'pak' at its start, its member count the u32 at 0x39,
// then each member's header from 0x3D (a u32 length and a path, a u32 length and its name, its
// size and a second size, u32s), then the members' bytes one after another in that order, with
// nothing compressed. A member is found by its name's last part (after a backslash).
struct PakMember {
    std::string leaf;
    size_t at = 0, size = 0;
};
bool read_pak(const std::vector<uint8_t>& b, std::vector<PakMember>* out, std::string* why) {
    if (b.size() < 0x3D || b[0] != 1 || std::memcmp(&b[1], "pak", 3) != 0) {
        *why = "not a PAK (no 01 'pak' at its start)";
        return false;
    }
    const uint32_t n = rd32le(&b[0x39]);
    if (n > 100000) { *why = "a member count of " + std::to_string(n); return false; }
    size_t o = 0x3D;
    std::vector<PakMember> ms;
    for (uint32_t i = 0; i < n; ++i) {
        std::string strs[2];
        for (std::string& str : strs) {
            if (o + 4 > b.size()) { *why = "its member headers run past its end"; return false; }
            const uint32_t len = rd32le(&b[o]);
            o += 4;
            if (len > b.size() - o) { *why = "its member headers run past its end"; return false; }
            str.assign(reinterpret_cast<const char*>(&b[o]), len);
            o += len;
        }
        if (o + 8 > b.size()) { *why = "its member headers run past its end"; return false; }
        PakMember m;
        m.size = rd32le(&b[o]);
        o += 8;
        const size_t slash = strs[1].find_last_of("\\/");
        m.leaf = lower(slash == std::string::npos ? strs[1] : strs[1].substr(slash + 1));
        ms.push_back(m);
    }
    for (PakMember& m : ms) {
        if (m.size > b.size() - o) { *why = "member " + m.leaf + " runs past its end"; return false; }
        m.at = o;
        o += m.size;
    }
    *out = std::move(ms);
    return true;
}

// A region's own hue in turns: the mean, by angle, of its pixels' hues, each weighed by its
// saturation, value and alpha.
float region_hue(const std::vector<uint8_t>& rgba, const Region& r) {
    constexpr double kTwoPi = 6.283185307179586;
    double cs = 0, sn = 0;
    for (int y = int(r.y0); y < int(r.y1); ++y)
        for (int x = int(r.x0); x < int(r.x1); ++x) {
            const uint8_t* p = &rgba[(size_t(y) * kAtlasW + x) * 4];
            if (p[3] < 16) continue;
            const float a = p[3] / 255.0f, rr = p[0] / 255.0f / a, g = p[1] / 255.0f / a, bb = p[2] / 255.0f / a;
            const float mx = std::max(rr, std::max(g, bb)), mn = std::min(rr, std::min(g, bb)), d = mx - mn;
            if (d <= 0) continue;
            float h = mx == rr ? (g - bb) / d : mx == g ? 2 + (bb - rr) / d : 4 + (rr - g) / d;
            h /= 6;
            const double wgt = double(d) * a;   // saturation times value is the chroma, d
            cs += wgt * std::cos(kTwoPi * h);
            sn += wgt * std::sin(kTwoPi * h);
        }
    float h = float(std::atan2(sn, cs) / (kTwoPi));
    return h - std::floor(h);
}

}  // namespace

bool load_art(Overlay* o, const std::string& root, std::string* why) {
    why->clear();
    if (!o) { *why = "no overlay"; return false; }
    if (root.empty()) { *why = "no install named (SA2_TOUCH_ART_ROOT)"; return false; }
    std::filesystem::path pak;
    if (!find_nocase(root, {"resource", "gd_PC", "PRS", "newMenu.pak"}, &pak)) {
        *why = "no resource/gd_PC/PRS/newMenu.pak under " + root;
        return false;
    }
    std::ifstream f(pak, std::ios::binary);
    const std::vector<uint8_t> b((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
    if (!f.good() && !f.eof()) { *why = pak.string() + " does not read"; return false; }
    std::vector<PakMember> ms;
    std::string err;
    if (!read_pak(b, &ms, &err)) { *why = pak.string() + ": " + err; return false; }
    Art art;
    art.rgba.assign(size_t(kAtlasW) * kAtlasH * 4, 0);
    for (const ArtMember& want : kArtMembers) {
        const PakMember* m = nullptr;
        for (const PakMember& c : ms) if (c.leaf == want.leaf) { m = &c; break; }
        if (!m) { *why = pak.string() + " has no " + want.leaf; return false; }
        const uint8_t* d = b.data() + m->at;
        tex::DdsInfo info;
        if (!tex::parse_dds(d, m->size, &info)) { *why = std::string(want.leaf) + ": " + info.error; return false; }
        if (info.w != want.w || info.h != want.h) {
            *why = std::string(want.leaf) + " is " + std::to_string(info.w) + "x" + std::to_string(info.h) + ", not " +
                   std::to_string(want.w) + "x" + std::to_string(want.h);
            return false;
        }
        if (!tex::decodable(info.fmt) || info.data_offset > m->size ||
            tex::level_bytes(info.w, info.h, info.fmt) > m->size - info.data_offset) {
            *why = std::string(want.leaf) + ": a " + tex::format_name(info.fmt) + " level this cannot read";
            return false;
        }
        std::vector<uint8_t> bgra;
        if (!tex::decode_level(d + info.data_offset, info.w, info.h, info.fmt, 0, &bgra)) {
            *why = std::string(want.leaf) + ": its pixels do not decode";
            return false;
        }
        for (uint32_t y = 0; y < info.h; ++y)
            for (uint32_t x = 0; x < info.w; ++x) {
                const uint8_t* s = &bgra[(size_t(y) * info.w + x) * 4];
                uint8_t* t = &art.rgba[(size_t(want.y + y) * kAtlasW + want.x + x) * 4];
                const unsigned a = s[3];
                t[0] = uint8_t((s[2] * a + 127) / 255);
                t[1] = uint8_t((s[1] * a + 127) / 255);
                t[2] = uint8_t((s[0] * a + 127) / 255);
                t[3] = uint8_t(a);
            }
    }
    for (int r = 0; r < AR_COUNT; ++r) art.hue[r] = region_hue(art.rgba, kRegions[r]);
    release_art_gpu(o);
    o->art = std::move(art);
    o->has_art = true;
    o->art_failed = false;
    return true;
}

bool art_loaded(const Overlay* o) { return o && o->has_art; }

}  // namespace sa2_touch
