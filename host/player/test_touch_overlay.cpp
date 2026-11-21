// test_touch_overlay.cpp: the touch overlay (touch_overlay.h) on its own: the pad it maps
// from finger events, its settings (size, opacity, places, what each button sends) and its
// controls in the band under a portrait window's picture, and what it draws on a real SDL_gpu
// device (Vulkan, no window): an offscreen target cleared to black, the overlay drawn over it,
// read back and its pixels checked where each control is and where none is; nothing drawn while
// inactive or at opacity 0; the adventure style (its theme's colour, the glossy faces' gradient and
// stripes, two face buttons); the game's state (its note parsed, the context and auto theme it
// makes, the controls each context shows and hides, a hidden control's touches doing nothing);
// the game's art from a PAK this test writes itself (its pictures drawn by code, no game file):
// drawn, turned to each theme, its held look, refused with a reason when missing or malformed
// (and the shader's look drawn then); and its cost per frame, CPU encoding and GPU time, over
// many frames at 1920x1080.
//
//   test_touch_overlay [OUT_DIR [INSTALL]]
//       OUT_DIR gets overlay.png (the drawn frame), overlay-portrait.png, overlay-adventure.png
//       (sonic), overlay-adventure-knuckles.png, overlay-adventure-two.png (two face buttons),
//       overlay-art.png and overlay-art-knuckles.png (the test's own art) when given. INSTALL, a
//       game install on this machine, is also loaded and drawn (overlay-real-sonic.png and
//       overlay-real-knuckles.png in OUT_DIR, which must not be a tracked folder: they hold the
//       game's art).
//
// Exit 0 and "TOUCH_OVERLAY_OK" when every check passes.
#include "touch_overlay.h"
#include "texcodec.h"

#include <SDL3/SDL.h>

#include <algorithm>
#include <chrono>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <functional>
#include <string>
#include <utility>
#include <vector>

using sa2_touch::Control;
using sa2_touch::Style;
using sa2_touch::Theme;

static int fails = 0;
#define CHECK(c, ...)                                                          \
    do {                                                                       \
        if (!(c)) {                                                            \
            std::printf("FAIL %s:%d: %s: ", __FILE__, __LINE__, #c);           \
            std::printf(__VA_ARGS__);                                          \
            std::printf("\n");                                                 \
            ++fails;                                                           \
        }                                                                      \
    } while (0)

constexpr uint32_t W = 1280, H = 720;   // the overlay's layout without a window

static SDL_Event finger(SDL_EventType t, SDL_FingerID id, float x, float y, SDL_TouchID touch = 7) {
    SDL_Event e{};
    e.type = t;
    e.tfinger.touchID = touch;
    e.tfinger.fingerID = id;
    e.tfinger.x = x;
    e.tfinger.y = y;
    return e;
}
static void at(Control c, float* x, float* y) {
    sa2_touch::control_centre(nullptr, W, H, c, x, y);
    *x /= float(W);
    *y /= float(H);
}
static sa2_input::PadState pad_of(const sa2_touch::Overlay* o) {
    sa2_input::PadState p;
    sa2_touch::pad(o, &p);
    return p;
}

struct Gpu {
    SDL_GPUDevice* dev = nullptr;
    SDL_GPUTexture* tex = nullptr;
    SDL_GPUTransferBuffer* down = nullptr;
    uint32_t w = 0, h = 0;
};

static bool make_target(Gpu* g, uint32_t w, uint32_t h) {
    SDL_GPUTextureCreateInfo ti{};
    ti.type = SDL_GPU_TEXTURETYPE_2D;
    ti.format = SDL_GPU_TEXTUREFORMAT_B8G8R8A8_UNORM;
    ti.usage = SDL_GPU_TEXTUREUSAGE_COLOR_TARGET;
    ti.width = w;
    ti.height = h;
    ti.layer_count_or_depth = 1;
    ti.num_levels = 1;
    ti.sample_count = SDL_GPU_SAMPLECOUNT_1;
    g->tex = SDL_CreateGPUTexture(g->dev, &ti);
    SDL_GPUTransferBufferCreateInfo bi{SDL_GPU_TRANSFERBUFFERUSAGE_DOWNLOAD, w * h * 4, 0};
    g->down = SDL_CreateGPUTransferBuffer(g->dev, &bi);
    g->w = w;
    g->h = h;
    return g->tex && g->down;
}

static void release_target(Gpu* g) {
    if (g->tex) SDL_ReleaseGPUTexture(g->dev, g->tex);
    if (g->down) SDL_ReleaseGPUTransferBuffer(g->dev, g->down);
    g->tex = nullptr;
    g->down = nullptr;
}

// The target cleared to black, the overlay over it, read back as BGRA rows.
static bool frame(Gpu* g, sa2_touch::Overlay* o, std::vector<uint8_t>* px) {
    SDL_GPUCommandBuffer* cb = SDL_AcquireGPUCommandBuffer(g->dev);
    if (!cb) return false;
    SDL_GPUColorTargetInfo ct{};
    ct.texture = g->tex;
    ct.load_op = SDL_GPU_LOADOP_CLEAR;
    ct.store_op = SDL_GPU_STOREOP_STORE;
    ct.clear_color = SDL_FColor{0, 0, 0, 1};
    SDL_EndGPURenderPass(SDL_BeginGPURenderPass(cb, &ct, 1, nullptr));
    sa2_touch::draw(o, cb, g->tex, g->w, g->h);
    SDL_GPUCopyPass* cp = SDL_BeginGPUCopyPass(cb);
    SDL_GPUTextureRegion from{};
    from.texture = g->tex;
    from.w = g->w;
    from.h = g->h;
    from.d = 1;
    SDL_GPUTextureTransferInfo to{g->down, 0, 0, 0};
    SDL_DownloadFromGPUTexture(cp, &from, &to);
    SDL_EndGPUCopyPass(cp);
    SDL_GPUFence* f = SDL_SubmitGPUCommandBufferAndAcquireFence(cb);
    if (!f) return false;
    SDL_WaitForGPUFences(g->dev, true, &f, 1);
    SDL_ReleaseGPUFence(g->dev, f);
    const uint8_t* p = static_cast<const uint8_t*>(SDL_MapGPUTransferBuffer(g->dev, g->down, false));
    if (!p) return false;
    px->assign(p, p + size_t(g->w) * g->h * 4);
    SDL_UnmapGPUTransferBuffer(g->dev, g->down);
    return true;
}

struct Bgr { int b, g, r; };
static int fails_png = 0;
static void save_png(const std::string& dir, const std::string& name, const std::vector<uint8_t>& px, uint32_t w,
                     uint32_t h) {
    if (dir.empty()) return;
    std::string err;
    const std::string path = dir + "/" + name;
    if (!tex::write_png_rgb(path.c_str(), px.data(), w, h, w * 4, &err)) {
        std::printf("FAIL png %s: %s\n", path.c_str(), err.c_str());
        ++fails_png;
    } else {
        std::printf("wrote %s\n", path.c_str());
    }
}
static Bgr pixel(const std::vector<uint8_t>& px, uint32_t w, float x, float y) {
    const size_t i = (size_t(std::lround(y)) * w + size_t(std::lround(x))) * 4;
    return {px[i], px[i + 1], px[i + 2]};
}
// The mean colour of the (2 rad + 1) square around (x, y): a glossy face's stripes averaged out.
static Bgr patch(const std::vector<uint8_t>& px, uint32_t w, float x, float y, int rad) {
    int n = 0, b = 0, g = 0, r = 0;
    for (int dy = -rad; dy <= rad; ++dy)
        for (int dx = -rad; dx <= rad; ++dx) {
            const Bgr p = pixel(px, w, x + dx, y + dy);
            b += p.b; g += p.g; r += p.r; ++n;
        }
    return {b / n, g / n, r / n};
}
static uint64_t lit(const std::vector<uint8_t>& px) {
    uint64_t n = 0;
    for (size_t i = 0; i < px.size(); i += 4) n += (px[i] | px[i + 1] | px[i + 2]) != 0;
    return n;
}

// ---------------------------------------------------------------- a test PAK, drawn by code
static void put32(std::vector<uint8_t>* b, uint32_t v) {
    for (int i = 0; i < 4; ++i) b->push_back(uint8_t(v >> (8 * i)));
}
// An uncompressed A8R8G8B8 DDS of w x h whose pixel (x, y) is f's {b, g, r, a}.
static std::vector<uint8_t> dds(uint32_t w, uint32_t h, const std::function<void(int, int, uint8_t*)>& f) {
    std::vector<uint8_t> b = {'D', 'D', 'S', ' '};
    const uint32_t head[31] = {124, 0x100F, h, w, w * 4, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
                               32, 0x41, 0, 32, 0x00FF0000, 0x0000FF00, 0x000000FF, 0xFF000000, 0x1000, 0, 0, 0, 0};
    for (uint32_t v : head) put32(&b, v);
    for (uint32_t y = 0; y < h; ++y)
        for (uint32_t x = 0; x < w; ++x) {
            uint8_t px[4] = {0, 0, 0, 0};
            f(int(x), int(y), px);
            b.insert(b.end(), px, px + 4);
        }
    return b;
}
static float edge(float d) { return std::max(0.0f, std::min(1.0f, 0.5f - d)); }   // inside a distance's edge
// A PAK of members {name, bytes}, as newMenu.pak is laid out (touch_overlay.cpp read_pak).
static std::vector<uint8_t> pak(const std::vector<std::pair<std::string, std::vector<uint8_t>>>& members) {
    std::vector<uint8_t> b(0x39, 0);
    b[0] = 1; b[1] = 'p'; b[2] = 'a'; b[3] = 'k';
    put32(&b, uint32_t(members.size()));
    for (const auto& [name, data] : members) {
        const std::string path = "c:\\test\\newmenu\\" + name, rel = "newmenu\\" + name;
        for (const std::string& str : {path, rel}) {
            put32(&b, uint32_t(str.size()));
            b.insert(b.end(), str.begin(), str.end());
        }
        put32(&b, uint32_t(data.size()));
        put32(&b, uint32_t(data.size()));
    }
    for (const auto& m : members) b.insert(b.end(), m.second.begin(), m.second.end());
    return b;
}
// The test's art, the four members' sizes and their parts where the game's are: a blue striped
// disc and a darker one, a red arc about (72, 72) with a white middle line 66 out, two blue bars.
static const uint8_t kArtBlue[3] = {230, 90, 30}, kArtHeld[3] = {140, 45, 15};   // BGR
static std::vector<std::pair<std::string, std::vector<uint8_t>>> test_members() {
    const auto disc = [](const uint8_t* bgr) {
        return dds(128, 128, [bgr](int x, int y, uint8_t* p) {
            const float d = std::hypot(x + 0.5f - 64, y + 0.5f - 64) - 62;
            const float k = (y % 6 < 2) ? 0.8f : 1.0f;   // stripes
            for (int i = 0; i < 3; ++i) p[i] = uint8_t(bgr[i] * k);
            p[3] = uint8_t(255 * edge(d));
        });
    };
    const auto arc = dds(128, 128, [](int x, int y, uint8_t* p) {
        const float r = std::hypot(x + 0.5f - 72, y + 0.5f - 72);
        if (x >= 72 || y >= 72) return;
        const bool middle = std::fabs(r - 66) < 1.5f;
        p[0] = middle ? 255 : 0; p[1] = middle ? 255 : 0; p[2] = 255;
        p[3] = uint8_t(255 * edge(std::fabs(r - 66) - 5));
    });
    const auto bars = dds(256, 128, [](int x, int y, uint8_t* p) {
        const bool one = y < 41, two = y >= 43 && y < 84;
        if (!one && !two) return;
        p[0] = one ? 230 : 160; p[1] = one ? 150 : 80; p[2] = one ? 20 : 10; p[3] = 255;
    });
    return {{"mainmenu_button_maru.dds", disc(kArtBlue)}, {"mainmenu_button_maru_i.dds", disc(kArtHeld)},
            {"mainmenu_csol.dds", arc}, {"mainmenu_button_sikaku.dds", bars}};
}
static void write_file(const std::filesystem::path& p, const std::vector<uint8_t>& b) {
    std::filesystem::create_directories(p.parent_path());
    std::ofstream f(p, std::ios::binary | std::ios::trunc);
    f.write(reinterpret_cast<const char*>(b.data()), std::streamsize(b.size()));
}

int main(int argc, char** argv) {
    const std::string out = argc > 1 ? argv[1] : "";
    const std::string real_install = argc > 2 ? argv[2] : "";
    // ---------------------------------------------------------------- the pad
    sa2_touch::Overlay* o = sa2_touch::create(nullptr, nullptr);
    CHECK(!sa2_touch::active(o), "fresh overlay active");
    CHECK(!pad_of(o).connected, "fresh overlay connected");
    float ax, ay, bx, by, ltx, lty, rtx, rty, stx, sty, yx, yy;
    at(Control::A, &ax, &ay);
    at(Control::B, &bx, &by);
    at(Control::Y, &yx, &yy);
    at(Control::LT, &ltx, &lty);
    at(Control::RT, &rtx, &rty);
    at(Control::Start, &stx, &sty);
    // A mouse's synthesized touch is not a touch.
    CHECK(!sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 1, ax, ay, SDL_MOUSE_TOUCHID)), "mouse touch taken");
    CHECK(!sa2_touch::active(o), "mouse touch activated the overlay");
    SDL_Event key{};
    key.type = SDL_EVENT_KEY_DOWN;
    CHECK(!sa2_touch::handle_event(o, key), "a key event taken");
    CHECK(sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 1, ax, ay)), "finger down not taken");
    sa2_input::PadState p = pad_of(o);
    CHECK(sa2_touch::active(o) && p.connected, "first finger did not activate");
    CHECK(p.buttons == 0x1000 && p.lx == 0 && p.ly == 0 && p.lt == 0 && p.rt == 0, "A: buttons %04x", p.buttons);
    // The stick: a finger on the left half, away from every button, is its origin.
    const float R = 0.12f * float(H);        // 12% of the short side
    const float sx = 0.25f, sy = 0.55f;
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 2, sx, sy));
    p = pad_of(o);
    CHECK(p.lx == 0 && p.ly == 0 && p.buttons == 0x1000, "stick at rest: %d %d", p.lx, p.ly);
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_MOTION, 2, sx + R / W, sy));
    p = pad_of(o);
    CHECK(p.lx == 32767 && p.ly == 0, "stick right: %d %d", p.lx, p.ly);
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_MOTION, 2, sx, sy - 0.5f * R / H));
    p = pad_of(o);
    CHECK(std::abs(p.lx) <= 1 && std::abs(p.ly - 16384) <= 2, "stick half up (y up positive): %d %d", p.lx, p.ly);
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_MOTION, 2, sx - 0.5f * R / W, sy + 0.5f * R / H));
    p = pad_of(o);
    CHECK(std::abs(p.lx + 16384) <= 2 && std::abs(p.ly + 16384) <= 2, "stick down-left: %d %d", p.lx, p.ly);
    // Pulled past the radius, the stick is a full push in the finger's direction from where it
    // landed, and that origin stays put: back where the finger landed is the centre again.
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_MOTION, 2, sx + 3 * R / W, sy));
    p = pad_of(o);
    CHECK(p.lx == 32767 && std::abs(p.ly) <= 1, "stick pulled far right: %d %d", p.lx, p.ly);
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_MOTION, 2, sx - 1.2f * R / W, sy - 1.6f * R / H));
    p = pad_of(o);   // two radii out, (-0.6, 0.8) of a radius each (y up positive): a full push
    CHECK(std::abs(p.lx + 19660) <= 3 && std::abs(p.ly - 26214) <= 3, "stick pulled far up-left: %d %d", p.lx,
          p.ly);
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_MOTION, 2, sx, sy));
    p = pad_of(o);
    CHECK(p.lx == 0 && p.ly == 0, "the origin moved after the finger: %d %d", p.lx, p.ly);
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_MOTION, 2, sx + 0.5f * R / W, sy));
    // A second finger on the left half while the stick is held is no second stick.
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 3, 0.1f, 0.5f));
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_MOTION, 3, 0.3f, 0.5f));
    p = pad_of(o);
    CHECK(std::abs(p.lx - 16384) <= 2 && p.buttons == 0x1000, "second left finger moved the stick: %d", p.lx);
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_UP, 3, 0.3f, 0.5f));
    // The A finger slides onto B; then LT, RT and START join.
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_MOTION, 1, bx, by));
    CHECK(pad_of(o).buttons == 0x2000, "slide to B: %04x", pad_of(o).buttons);
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 4, ltx, lty));
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 5, rtx, rty));
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 6, stx, sty));
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 7, yx, yy));
    p = pad_of(o);
    CHECK(p.buttons == (0x2000 | 0x0010 | 0x8000) && p.lt == 255 && p.rt == 255, "B+START+Y, LT, RT: %04x %u %u",
          p.buttons, p.lt, p.rt);
    for (SDL_FingerID id : {4, 5, 6, 7}) sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_UP, id, 0, 0));
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_CANCELED, 1, bx, by));
    p = pad_of(o);
    CHECK(p.connected && p.buttons == 0 && p.lt == 0 && p.rt == 0 && std::abs(p.lx - 16384) <= 2,
          "after the ups: %04x %u %u lx %d", p.buttons, p.lt, p.rt, p.lx);
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_UP, 2, 0, 0));
    p = pad_of(o);
    CHECK(p.connected && p.lx == 0 && p.ly == 0, "stick let go: %d %d", p.lx, p.ly);
    // A new touch is a new origin: where it lands, not where the last one did.
    const float nx = 0.1f, ny = 0.4f;
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 8, nx, ny));
    p = pad_of(o);
    CHECK(p.lx == 0 && p.ly == 0, "a new stick touch at rest: %d %d", p.lx, p.ly);
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_MOTION, 8, nx - 0.5f * R / W, ny));
    p = pad_of(o);
    CHECK(std::abs(p.lx + 16384) <= 2 && std::abs(p.ly) <= 1, "not re-centred at the new touch: %d %d", p.lx, p.ly);
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_UP, 8, 0, 0));
    sa2_touch::deactivate(o);
    CHECK(!sa2_touch::active(o) && !pad_of(o).connected, "deactivate");
    const sa2_touch::Stats st = sa2_touch::stats(o);
    CHECK(st.ignored_fingers == 1 && st.activations == 1 && st.draws == 0 && !st.gpu, "stats %llu %llu",
          (unsigned long long)st.ignored_fingers, (unsigned long long)st.activations);
    sa2_touch::destroy(o);
    std::printf("pad mapping: %s\n", fails ? "FAILED" : "ok");

    // ---------------------------------------------------------------- the settings
    {
        sa2_touch::Settings s;
        std::string why;
        const auto parse = [&](const char* name, const char* value) {
            return sa2_touch::parse_setting(name, value, &s, &why);
        };
        CHECK(parse("SA2_TOUCH_SCALE", "1.5") && s.scale == 1.5f, "scale 1.5: %s", why.c_str());
        for (const char* bad : {"", "0.4", "2.5", "-1", "1x", "nan", "1e0"})
            CHECK(!parse("SA2_TOUCH_SCALE", bad) && s.scale == 1.5f, "scale \"%s\" taken", bad);
        CHECK(parse("SA2_TOUCH_OPACITY", "0") && s.opacity == 0, "opacity 0: %s", why.c_str());
        CHECK(!parse("SA2_TOUCH_OPACITY", "2.1") && s.opacity == 0, "opacity 2.1 taken");
        CHECK(parse("SA2_TOUCH_LAYOUT", "STICK=0.8,0.7;FACE=0.2,0.6") && s.landscape[0].set &&
              s.landscape[0].x == 0.8f && s.landscape[0].y == 0.7f && s.landscape[1].set && s.landscape[1].x == 0.2f &&
              !s.landscape[2].set && !s.portrait[0].set, "layout: %s", why.c_str());
        for (const char* bad : {"STICK=0.8", "PAD=0.1,0.1", "STICK=1.2,0.5", "STICK=0.1,0.2;", "STICK=0.1;0.2"})
            CHECK(!parse("SA2_TOUCH_LAYOUT_PORTRAIT", bad) && !s.portrait[0].set, "layout \"%s\" taken", bad);
        CHECK(parse("SA2_TOUCH_MAP", "A=B,LT=START") && s.mapped[0] && s.sends[0].bit == 0x2000 && s.mapped[5] &&
              s.sends[5].bit == 0x0010 && s.sends[5].trigger == 0 && !s.mapped[1], "map: %s", why.c_str());
        for (const char* bad : {"A=JUMP", "Z=A", "A", "A=B,"})
            CHECK(!parse("SA2_TOUCH_MAP", bad) && !s.mapped[1], "map \"%s\" taken", bad);
        CHECK(!parse("SA2_TOUCH_SIZE", "1"), "an unknown name taken");
        // The style, its theme and the face buttons: exact lower-case names, a bad one refused with
        // why and the setting unchanged.
        CHECK(s.style == Style::Classic && s.auto_theme && s.theme == Theme::Sonic && !s.two_buttons,
              "not classic and auto by default");
        CHECK(parse("SA2_TOUCH_STYLE", "adventure") && s.style == Style::Adventure && why.empty(), "style: %s",
              why.c_str());
        for (const char* bad : {"", "Adventure", "modern", "adventure ", "1"})
            CHECK(!parse("SA2_TOUCH_STYLE", bad) && s.style == Style::Adventure && why == "not classic or adventure",
                  "style \"%s\" taken (%s)", bad, why.c_str());
        CHECK(parse("SA2_TOUCH_STYLE", "classic") && s.style == Style::Classic, "style classic: %s", why.c_str());
        CHECK(parse("SA2_TOUCH_THEME", "knuckles") && s.theme == Theme::Knuckles && !s.auto_theme && why.empty(),
              "theme: %s", why.c_str());
        CHECK(parse("SA2_TOUCH_THEME", "auto") && s.auto_theme && why.empty(), "theme auto: %s", why.c_str());
        CHECK(parse("SA2_TOUCH_THEME", "metal") && s.theme == Theme::Metal && !s.auto_theme && why.empty(),
              "theme metal: %s", why.c_str());
        for (const char* bad : {"", "Sonic", "blue", "auto,", "knuckles;", "silver"})
            CHECK(!parse("SA2_TOUCH_THEME", bad) && s.theme == Theme::Metal && why.find("not one of") == 0,
                  "theme \"%s\" taken (%s)", bad, why.c_str());
        CHECK(parse("SA2_TOUCH_FACE", "two") && s.two_buttons, "face two: %s", why.c_str());
        for (const char* bad : {"", "2", "Two", "four,two"})
            CHECK(!parse("SA2_TOUCH_FACE", bad) && s.two_buttons && why == "not four or two", "face \"%s\" taken",
                  bad);
        CHECK(parse("SA2_TOUCH_FACE", "four") && !s.two_buttons, "face four: %s", why.c_str());
    }
    {
        // Two face buttons: JUMP (A) below right and ACTION (B) above left in the diamond's own box,
        // its centre where the diamond's is (the layout editor's FACE group does not move); no X
        // or Y; nothing between the two; START still there.
        sa2_touch::Overlay* q = sa2_touch::create(nullptr, nullptr);
        sa2_touch::Settings s;
        std::string why;
        sa2_touch::parse_setting("SA2_TOUCH_FACE", "two", &s, &why);
        sa2_touch::set_settings(q, s);
        float jx, jy, acx, acy, a4x, a4y, y4x, y4y, hw, hh, qx, qy;
        sa2_touch::control_centre(q, W, H, Control::A, &jx, &jy);
        sa2_touch::control_centre(q, W, H, Control::B, &acx, &acy);
        sa2_touch::control_centre(nullptr, W, H, Control::A, &a4x, &a4y);
        sa2_touch::control_centre(nullptr, W, H, Control::Y, &y4x, &y4y);
        const float fx = a4x, fy = (a4y + y4y) / 2, o2 = 0.11f * H;
        CHECK(std::fabs(jx - (fx + o2)) < 0.01f && std::fabs(jy - (fy + o2)) < 0.01f, "JUMP at %.1f,%.1f", jx, jy);
        CHECK(std::fabs(acx - (fx - o2)) < 0.01f && std::fabs(acy - (fy - o2)) < 0.01f, "ACTION at %.1f,%.1f", acx,
              acy);
        sa2_touch::control_box(q, W, H, Control::A, &qx, &qy, &hw, &hh);
        CHECK(std::fabs(hw - 0.10f * H) < 0.01f && hw == hh, "JUMP's half size %.2f", hw);
        for (Control c : {Control::X, Control::Y}) {
            sa2_touch::control_box(q, W, H, c, &qx, &qy, &hw, &hh);
            CHECK(hw == 0 && hh == 0 && std::fabs(qx - fx) < 0.01f && std::fabs(qy - fy) < 0.01f,
                  "control %d is there: %.1f,%.1f %.1fx%.1f", int(c), qx, qy, hw, hh);
        }
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_DOWN, 1, jx / W, jy / H));
        CHECK(pad_of(q).buttons == 0x1000, "JUMP sends %04x", pad_of(q).buttons);
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_DOWN, 2, acx / W, acy / H));
        CHECK(pad_of(q).buttons == (0x1000 | 0x2000), "JUMP and ACTION send %04x", pad_of(q).buttons);
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_UP, 1, 0, 0));
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_UP, 2, 0, 0));
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_DOWN, 3, fx / W, fy / H));
        CHECK(pad_of(q).buttons == 0 && pad_of(q).lx == 0, "between the two: %04x", pad_of(q).buttons);
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_UP, 3, 0, 0));
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_DOWN, 4, stx, sty));
        CHECK(pad_of(q).buttons == 0x0010, "START with two face buttons: %04x", pad_of(q).buttons);
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_UP, 4, 0, 0));
        // SA2_TOUCH_MAP still applies: JUMP sends what A is mapped to.
        sa2_touch::parse_setting("SA2_TOUCH_MAP", "A=Y", &s, &why);
        sa2_touch::set_settings(q, s);
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_DOWN, 5, jx / W, jy / H));
        CHECK(pad_of(q).buttons == 0x8000, "JUMP mapped to Y sends %04x", pad_of(q).buttons);
        sa2_touch::destroy(q);
    }
    {
        // Bigger, moved and remapped: the stick on the right, the diamond on the left, A sends B
        // and LT sends START.
        sa2_touch::Overlay* q = sa2_touch::create(nullptr, nullptr);
        sa2_touch::Settings s;
        std::string why;
        sa2_touch::parse_setting("SA2_TOUCH_SCALE", "1.5", &s, &why);
        sa2_touch::parse_setting("SA2_TOUCH_LAYOUT", "STICK=0.8,0.7;FACE=0.2,0.6", &s, &why);
        sa2_touch::parse_setting("SA2_TOUCH_MAP", "A=B,LT=START", &s, &why);
        sa2_touch::set_settings(q, s);
        CHECK(std::fabs(sa2_touch::stick_radius(q, W, H) - 1.5f * R) < 0.01f, "scaled stick radius %.2f",
              sa2_touch::stick_radius(q, W, H));
        float qx, qy;
        sa2_touch::control_centre(q, W, H, Control::Stick, &qx, &qy);
        CHECK(std::fabs(qx - 0.8f * W) < 0.01f && std::fabs(qy - 0.7f * H) < 0.01f, "stick placed at %.1f,%.1f", qx, qy);
        sa2_touch::control_centre(q, W, H, Control::A, &qx, &qy);
        CHECK(std::fabs(qx - 0.2f * W) < 0.01f && std::fabs(qy - (0.6f * H + 0.135f * 1.5f * H)) < 0.01f,
              "A placed at %.1f,%.1f", qx, qy);
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_DOWN, 1, qx / W, qy / H));
        p = pad_of(q);
        CHECK(p.buttons == 0x2000 && p.lt == 0, "A mapped to B: %04x %u", p.buttons, p.lt);
        sa2_touch::control_centre(q, W, H, Control::LT, &qx, &qy);
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_DOWN, 2, qx / W, qy / H));
        p = pad_of(q);
        CHECK(p.buttons == (0x2000 | 0x0010) && p.lt == 0, "LT mapped to START: %04x %u", p.buttons, p.lt);
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_UP, 1, 0, 0));
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_UP, 2, 0, 0));
        // The stick takes the right half now, and the left half has none.
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_DOWN, 3, 0.6f, 0.4f));
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_MOTION, 3, 0.6f + 1.5f * R / W, 0.4f));
        p = pad_of(q);
        CHECK(p.lx == 32767 && p.buttons == 0, "no stick on the right half: %d %04x", p.lx, p.buttons);
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_UP, 3, 0, 0));
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_DOWN, 4, 0.45f, 0.3f));
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_MOTION, 4, 0.45f - 1.5f * R / W, 0.3f));
        p = pad_of(q);
        CHECK(p.lx == 0 && p.ly == 0 && p.buttons == 0, "a stick on the left half: %d %d %04x", p.lx, p.ly, p.buttons);
        // A place set at a smaller size is held inside the window: the diamond in the bottom-left
        // corner at twice the size reaches 0.21 x 1440 from its centre.
        sa2_touch::parse_setting("SA2_TOUCH_SCALE", "2", &s, &why);
        sa2_touch::parse_setting("SA2_TOUCH_LAYOUT", "FACE=0,1", &s, &why);
        sa2_touch::set_settings(q, s);
        sa2_touch::control_centre(q, W, H, Control::B, &qx, &qy);
        const float reach = 0.21f * 1440, d2 = 0.135f * 1440;
        CHECK(std::fabs(qx - (reach + d2)) < 0.01f && std::fabs(qy - (H - reach)) < 0.01f, "B held in at %.1f,%.1f",
              qx, qy);
        sa2_touch::destroy(q);
    }
    {
        // A portrait window and a 4:3 frame: the picture is centred, 720x540 over rows 510 to
        // 1050, and the controls are in the 510 rows below it, at the window's short side's size
        // (the band needs 0.6 x 720 = 432).
        sa2_touch::Overlay* q = sa2_touch::create(nullptr, nullptr);
        sa2_touch::set_size(q, 720, 1560);
        CHECK(!sa2_touch::below_picture(q, 720, 1560), "below the picture with no frame");
        sa2_touch::set_frame(q, 1440, 1080);
        CHECK(sa2_touch::below_picture(q, 720, 1560), "not below a portrait window's picture");
        CHECK(std::fabs(sa2_touch::stick_radius(q, 720, 1560) - 0.12f * 720) < 0.01f, "band stick radius %.2f",
              sa2_touch::stick_radius(q, 720, 1560));
        const float m = 0.04f * 720, d = 0.135f * 720, r = 0.075f * 720;
        float qx, qy;
        sa2_touch::control_centre(q, 720, 1560, Control::A, &qx, &qy);
        CHECK(std::fabs(qx - (720 - m - d - r)) < 0.01f && std::fabs(qy - (1560 - m - r)) < 0.01f, "A at %.1f,%.1f", qx,
              qy);
        sa2_touch::control_centre(q, 720, 1560, Control::LT, &qx, &qy);
        CHECK(std::fabs(qx - (m + 0.09f * 720)) < 0.01f && std::fabs(qy - (1050 + m + 0.04f * 720)) < 0.01f,
              "LT at %.1f,%.1f", qx, qy);
        for (Control c : {Control::Stick, Control::A, Control::B, Control::X, Control::Y, Control::Start, Control::LT,
                          Control::RT}) {
            sa2_touch::control_centre(q, 720, 1560, c, &qx, &qy);
            CHECK(qy > 1050 && qy < 1560 && qx > 0 && qx < 720, "control %d at %.1f,%.1f is not in the band", int(c),
                  qx, qy);
        }
        sa2_touch::control_centre(q, 720, 1560, Control::A, &qx, &qy);
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_DOWN, 1, qx / 720, qy / 1560));
        CHECK(pad_of(q).buttons == 0x1000, "A in the band: %04x", pad_of(q).buttons);
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_UP, 1, 0, 0));
        // A shorter band makes them smaller: a 16:9 portrait window leaves 370 rows (910 to 1280).
        CHECK(sa2_touch::below_picture(q, 720, 1280), "not below a 16:9 portrait window's picture");
        CHECK(std::fabs(sa2_touch::stick_radius(q, 720, 1280) - 0.12f * 370 / 0.6f) < 0.01f,
              "short band stick radius %.2f", sa2_touch::stick_radius(q, 720, 1280));
        // A landscape window with the same frame: over the picture, the places of no frame.
        CHECK(!sa2_touch::below_picture(q, 1560, 720), "below a landscape window's picture");
        float nx2, ny2;
        sa2_touch::control_centre(q, 1560, 720, Control::A, &qx, &qy);
        sa2_touch::control_centre(nullptr, 1560, 720, Control::A, &nx2, &ny2);
        CHECK(qx == nx2 && qy == ny2, "landscape A at %.1f,%.1f, not %.1f,%.1f", qx, qy, nx2, ny2);
        // SA2_TOUCH_LAYOUT_PORTRAIT places in an upright window (here in its band), and
        // SA2_TOUCH_LAYOUT in a landscape one.
        sa2_touch::Settings s;
        std::string why;
        sa2_touch::parse_setting("SA2_TOUCH_LAYOUT_PORTRAIT", "START=0.5,0.5", &s, &why);
        sa2_touch::parse_setting("SA2_TOUCH_LAYOUT", "START=0.1,0.9", &s, &why);
        sa2_touch::set_settings(q, s);
        sa2_touch::control_centre(q, 720, 1560, Control::Start, &qx, &qy);
        CHECK(std::fabs(qx - 360) < 0.01f && std::fabs(qy - 1305) < 0.01f, "START below at %.1f,%.1f", qx, qy);
        sa2_touch::control_centre(q, 1560, 720, Control::Start, &qx, &qy);
        CHECK(std::fabs(qx - 156) < 0.01f && std::fabs(qy - 648) < 0.01f, "START over at %.1f,%.1f", qx, qy);
        // The player's picture place (fit_rect's anchor, test_d3d9gpu's numbers): a 4:3 frame in
        // a 1440x3120 window, its centre a quarter of the way down, sits at rows 240 to 1320, or
        // under a safe area's top, never lower than centred; the band starts under it.
        {
            sa2_touch::Overlay* a = sa2_touch::create(nullptr, nullptr);
            sa2_touch::set_frame(a, 1440, 1080);
            uint32_t top = 0, bottom = 0;
            const auto rows = [&](float y, uint32_t min_top) {
                sa2_touch::set_picture(a, y, min_top);
                sa2_touch::picture_rows(a, 1440, 3120, &top, &bottom);
                return std::make_pair(top, bottom);
            };
            CHECK((rows(-1, 0) == std::make_pair(1020u, 2100u)), "centred rows %u-%u", top, bottom);
            CHECK((rows(0.25f, 0) == std::make_pair(240u, 1320u)), "upper-half rows %u-%u", top, bottom);
            CHECK((rows(0.25f, 300) == std::make_pair(300u, 1380u)), "under the safe area %u-%u", top, bottom);
            CHECK((rows(0.9f, 0) == std::make_pair(1020u, 2100u)), "below centred %u-%u", top, bottom);
            // A landscape window letterboxing the picture (a screen narrower than 4:3) keeps it
            // centred: 2176x1812 shows 4:3 as 2176x1632 at row 90.
            sa2_touch::set_picture(a, 0.25f, 0);
            sa2_touch::picture_rows(a, 2176, 1812, &top, &bottom);
            CHECK(top == 90 && bottom == 1722, "landscape letterbox rows %u-%u", top, bottom);
            rows(0.25f, 0);
            CHECK(sa2_touch::below_picture(a, 1440, 3120), "no band under the raised picture");
            float sx, sy;
            sa2_touch::control_centre(a, 1440, 3120, Control::LT, &sx, &sy);
            CHECK(sy > 1320 && sy < 1320 + 0.2f * 1440, "LT not at the band's top: %.1f", sy);
            CHECK(std::fabs(sa2_touch::stick_radius(a, 1440, 3120) - 0.12f * 1440) < 0.01f,
                  "the raised picture's band shrank the stick: %.2f", sa2_touch::stick_radius(a, 1440, 3120));
            sa2_touch::destroy(a);
        }
        // An upright window whose band is too short (a foldable's inner screen) has the controls
        // over the picture, and still the portrait places.
        CHECK(!sa2_touch::below_picture(q, 1812, 2176), "below a near-square window's picture");
        sa2_touch::control_centre(q, 1812, 2176, Control::Start, &qx, &qy);
        CHECK(std::fabs(qx - 906) < 0.01f && std::fabs(qy - 1088) < 0.01f, "START upright at %.1f,%.1f", qx, qy);
        sa2_touch::destroy(q);
    }
    std::printf("settings and the band: %s\n", fails ? "FAILED" : "ok");

    // ---------------------------------------------------------------- the game's state
    {
        using sa2_touch::Context;
        sa2_touch::GameState g;
        CHECK(sa2_touch::parse_state_note("state mode=5 level=13 character=4 two=0 alt=0", &g) && g.mode == 5 &&
              g.level == 13 && g.character == 4 && g.two_player == 0 && g.alt == 0, "a state note");
        CHECK(sa2_touch::parse_state_note("state mode=13", &g) && g.mode == 13 && g.level == -1 && g.character == -1,
              "a short state note");
        CHECK(sa2_touch::parse_state_note("state mode=-1 level=-1 character=-1 two=-1 alt=-1 later=7", &g) &&
              g.mode == -1, "unread fields and a field to come");
        for (const char* bad : {"", "record 4: blob: the 4 bytes did not read", "state mode=x", "state mode=",
                                "states mode=5", "state mode=5 level=1.5"})
            CHECK(!sa2_touch::parse_state_note(bad, &g), "\"%s\" is a state note", bad);
        // GameMode to context: every row of the table, a load keeping the context before, Chao
        // World a stage at level 90, and no mode unknown.
        const std::pair<int, Context> modes[] = {
            {0, Context::Menu},      {1, Context::Menu},      {2, Context::Menu},     {13, Context::Menu},
            {14, Context::Menu},     {25, Context::Menu},     {5, Context::Stage},    {10, Context::Stage},
            {7, Context::Cutscene},  {8, Context::Cutscene},  {15, Context::Cutscene}, {16, Context::Cutscene},
            {21, Context::Cutscene}, {22, Context::Cutscene}, {17, Context::Menu},    {18, Context::Menu},
            {19, Context::Menu},     {20, Context::Menu},     {23, Context::Menu},    {24, Context::Menu}};
        for (const auto& [mode, want] : modes) {
            sa2_touch::GameState st;
            st.mode = mode;
            st.level = 13;
            CHECK(sa2_touch::context_of(st, Context::Unknown) == want, "mode %d is %s", mode,
                  sa2_touch::context_name(sa2_touch::context_of(st, Context::Unknown)));
        }
        for (int mode : {3, 4, 6, 9, 11, 12, 26, 1000}) {
            sa2_touch::GameState st;
            st.mode = mode;
            CHECK(sa2_touch::context_of(st, Context::Cutscene) == Context::Cutscene &&
                  sa2_touch::context_of(st, Context::Stage) == Context::Stage, "mode %d does not keep the context", mode);
        }
        sa2_touch::GameState chao;
        chao.mode = 5;
        chao.level = 90;
        CHECK(sa2_touch::context_of(chao, Context::Menu) == Context::ChaoWorld, "level 90 is not chao world");
        chao.mode = 13;
        CHECK(sa2_touch::context_of(chao, Context::Stage) == Context::Menu, "the menus at level 90");
        CHECK(sa2_touch::context_of(sa2_touch::GameState{}, Context::Stage) == Context::Unknown, "no mode read");
        // The character's theme in a stage or Chao World (the costume's in a two-player game), sonic's elsewhere.
        const std::pair<int, Theme> own[] = {{0, Theme::Sonic},  {1, Theme::Shadow},   {2, Theme::Tails},
                                             {3, Theme::Eggman}, {4, Theme::Knuckles}, {5, Theme::Rouge},
                                             {6, Theme::Tails},  {7, Theme::Eggman}};
        for (const auto& [ch, want] : own) {
            sa2_touch::GameState st;
            st.mode = 5;
            st.character = ch;
            st.two_player = 0;
            st.alt = 1;                      // a costume byte left set outside a two-player game
            CHECK(sa2_touch::theme_of(st, Context::Stage) == want && sa2_touch::theme_of(st, Context::ChaoWorld) == want,
                  "character %d is %s", ch, sa2_touch::theme_name(sa2_touch::theme_of(st, Context::Stage)));
            CHECK(sa2_touch::theme_of(st, Context::Menu) == Theme::Sonic &&
                  sa2_touch::theme_of(st, Context::Cutscene) == Theme::Sonic &&
                  sa2_touch::theme_of(st, Context::Unknown) == Theme::Sonic, "character %d outside a stage", ch);
        }
        const std::pair<int, Theme> costume[] = {{0, Theme::Amy}, {1, Theme::Metal}, {4, Theme::Tails},
                                                 {5, Theme::Chao}, {6, Theme::Chao}, {7, Theme::Chao}};
        for (const auto& [ch, want] : costume) {
            sa2_touch::GameState st;
            st.character = ch;
            st.two_player = 1;
            st.alt = 1;
            CHECK(sa2_touch::theme_of(st, Context::Stage) == want, "character %d's costume is %s", ch,
                  sa2_touch::theme_name(sa2_touch::theme_of(st, Context::Stage)));
        }
        sa2_touch::GameState nobody;
        nobody.character = 12;
        CHECK(sa2_touch::theme_of(nobody, Context::Stage) == Theme::Sonic, "an unknown character");
        // The table of what each context uses.
        CHECK(sa2_touch::context_uses(Context::Stage, 0x1000, 0) && sa2_touch::context_uses(Context::Stage, 0x2000, 0) &&
              sa2_touch::context_uses(Context::Stage, 0x0010, 0) && !sa2_touch::context_uses(Context::Stage, 0x4000, 0) &&
              !sa2_touch::context_uses(Context::Stage, 0x8000, 0) && sa2_touch::context_uses(Context::Stage, 0, 1) &&
              sa2_touch::context_uses(Context::Stage, 0, 2) && sa2_touch::context_uses_stick(Context::Stage), "stage");
        CHECK(sa2_touch::context_uses(Context::Menu, 0x1000, 0) && sa2_touch::context_uses(Context::Menu, 0x2000, 0) &&
              sa2_touch::context_uses(Context::Menu, 0x0010, 0) && !sa2_touch::context_uses(Context::Menu, 0x4000, 0) &&
              !sa2_touch::context_uses(Context::Menu, 0, 1) && !sa2_touch::context_uses(Context::Menu, 0, 2) &&
              sa2_touch::context_uses_stick(Context::Menu), "menu");
        CHECK(!sa2_touch::context_uses(Context::Cutscene, 0x1000, 0) && sa2_touch::context_uses(Context::Cutscene, 0x0010, 0) &&
              !sa2_touch::context_uses(Context::Cutscene, 0, 1) && !sa2_touch::context_uses_stick(Context::Cutscene),
              "cutscene");
        for (uint16_t bit : {0x1000, 0x2000, 0x4000, 0x8000, 0x0010})
            CHECK(sa2_touch::context_uses(Context::ChaoWorld, bit, 0) && sa2_touch::context_uses(Context::Unknown, bit, 0),
                  "chao world or unknown without %04x", bit);
        CHECK(sa2_touch::context_uses(Context::Unknown, 0, 1) && sa2_touch::context_uses_stick(Context::Unknown) &&
              sa2_touch::context_uses(Context::ChaoWorld, 0, 2), "unknown or chao world without a trigger");

        // On the overlay: the adventure style shows what the context uses, and nothing else is hit.
        sa2_touch::Overlay* q = sa2_touch::create(nullptr, nullptr);
        sa2_touch::Settings s;
        std::string why;
        sa2_touch::parse_setting("SA2_TOUCH_STYLE", "adventure", &s, &why);
        sa2_touch::set_settings(q, s);
        float xx, xy;
        at(Control::X, &xx, &xy);
        const auto shown = [&](std::initializer_list<Control> want) {
            std::string got;
            for (Control c : {Control::Stick, Control::A, Control::B, Control::X, Control::Y, Control::Start, Control::LT,
                              Control::RT})
                if (sa2_touch::control_shown(q, c)) got += std::to_string(int(c));
            std::string w;
            for (Control c : want) w += std::to_string(int(c));
            return got == w;
        };
        CHECK(sa2_touch::context(q) == Context::Unknown && sa2_touch::theme(q) == Theme::Sonic &&
              shown({Control::Stick, Control::A, Control::B, Control::X, Control::Y, Control::Start, Control::LT,
                     Control::RT}), "before any state: everything, sonic");
        sa2_touch::GameState st;
        st.mode = 5; st.level = 13; st.character = 4; st.two_player = 0; st.alt = 0;
        sa2_touch::set_game_state(q, st);
        CHECK(sa2_touch::context(q) == Context::Stage && sa2_touch::theme(q) == Theme::Knuckles &&
              shown({Control::Stick, Control::A, Control::B, Control::Start, Control::LT, Control::RT}),
              "a stage as knuckles: %s %s", sa2_touch::context_name(sa2_touch::context(q)),
              sa2_touch::theme_name(sa2_touch::theme(q)));
        // X is not in a stage: a finger there sends nothing (and is no stick: it is on the right).
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_DOWN, 1, xx, xy));
        CHECK(pad_of(q).connected && pad_of(q).buttons == 0, "hidden X sends %04x", pad_of(q).buttons);
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_UP, 1, xx, xy));
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_DOWN, 2, ltx, lty));
        CHECK(pad_of(q).lt == 255, "LT in a stage");
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_UP, 2, ltx, lty));
        // A held when a cutscene starts: it sends nothing from then on; START alone is there, and
        // a finger on the stick's half is no stick.
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_DOWN, 3, ax, ay));
        CHECK(pad_of(q).buttons == 0x1000, "A in a stage: %04x", pad_of(q).buttons);
        st.mode = 8;
        sa2_touch::set_game_state(q, st);
        CHECK(sa2_touch::context(q) == Context::Cutscene && sa2_touch::theme(q) == Theme::Sonic && shown({Control::Start}),
              "a cutscene");
        CHECK(pad_of(q).buttons == 0, "A held into a cutscene still sends %04x", pad_of(q).buttons);
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_DOWN, 4, sx, sy));
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_MOTION, 4, sx + R / W, sy));
        CHECK(pad_of(q).lx == 0 && pad_of(q).buttons == 0, "a stick in a cutscene: %d", pad_of(q).lx);
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_DOWN, 5, stx, sty));
        CHECK(pad_of(q).buttons == 0x0010, "START in a cutscene: %04x", pad_of(q).buttons);
        for (SDL_FingerID id : {3, 4, 5}) sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_UP, id, 0, 0));
        // A load (mode 4) keeps the cutscene; the menus have no triggers and no X or Y.
        st.mode = 4;
        sa2_touch::set_game_state(q, st);
        CHECK(sa2_touch::context(q) == Context::Cutscene, "a load changed the context");
        st.mode = 13;
        sa2_touch::set_game_state(q, st);
        CHECK(sa2_touch::context(q) == Context::Menu && sa2_touch::theme(q) == Theme::Sonic &&
              shown({Control::Stick, Control::A, Control::B, Control::Start}), "the menus");
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_DOWN, 6, rtx, rty));
        CHECK(pad_of(q).rt == 0, "RT in the menus");
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_UP, 6, 0, 0));
        st.mode = 5; st.level = 90; st.character = 1;
        sa2_touch::set_game_state(q, st);
        CHECK(sa2_touch::context(q) == Context::ChaoWorld && sa2_touch::theme(q) == Theme::Shadow &&
              shown({Control::Stick, Control::A, Control::B, Control::X, Control::Y, Control::Start, Control::LT,
                     Control::RT}), "chao world as shadow");
        // A button is shown by what it sends: X mapped to B is there in a stage; with two face
        // buttons X and Y never are.
        st.level = 13;
        sa2_touch::set_game_state(q, st);
        sa2_touch::parse_setting("SA2_TOUCH_MAP", "X=B", &s, &why);
        sa2_touch::set_settings(q, s);
        CHECK(sa2_touch::control_shown(q, Control::X) && !sa2_touch::control_shown(q, Control::Y), "X mapped to B");
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_DOWN, 7, xx, xy));
        CHECK(pad_of(q).buttons == 0x2000, "X mapped to B in a stage sends %04x", pad_of(q).buttons);
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_UP, 7, 0, 0));
        // An explicit theme stays whatever the character; the classic style shows everything.
        sa2_touch::parse_setting("SA2_TOUCH_THEME", "rouge", &s, &why);
        sa2_touch::set_settings(q, s);
        CHECK(sa2_touch::theme(q) == Theme::Rouge, "an explicit theme followed the character");
        st.mode = 7;
        sa2_touch::set_game_state(q, st);
        sa2_touch::set_settings(q, sa2_touch::Settings{});
        CHECK(shown({Control::Stick, Control::A, Control::B, Control::X, Control::Y, Control::Start, Control::LT,
                     Control::RT}), "the classic style hid a control");
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_DOWN, 8, ax, ay));
        CHECK(pad_of(q).buttons == 0x1000, "classic A in a cutscene: %04x", pad_of(q).buttons);
        sa2_touch::destroy(q);
    }
    std::printf("the game's state: %s\n", fails ? "FAILED" : "ok");

    // ---------------------------------------------------------------- drawing
    if (!SDL_Init(SDL_INIT_VIDEO)) { std::printf("FAIL SDL_Init: %s\n", SDL_GetError()); return 1; }
    Gpu g;
    g.dev = SDL_CreateGPUDevice(SDL_GPU_SHADERFORMAT_SPIRV, false, "vulkan");
    if (!g.dev) { std::printf("FAIL SDL_CreateGPUDevice: %s\n", SDL_GetError()); return 1; }
    std::printf("gpu: %s\n", SDL_GetGPUDeviceDriver(g.dev));
    if (!make_target(&g, W, H)) { std::printf("FAIL target: %s\n", SDL_GetError()); return 1; }
    o = sa2_touch::create(g.dev, nullptr);
    sa2_touch::set_target_format(o, SDL_GPU_TEXTUREFORMAT_B8G8R8A8_UNORM);
    std::vector<uint8_t> px;
    CHECK(frame(&g, o, &px), "frame: %s", SDL_GetError());
    CHECK(lit(px) == 0 && sa2_touch::stats(o).draws == 0, "inactive overlay drew %llu pixels",
          (unsigned long long)lit(px));
    // A held, the stick's finger pulled two radii up and right of where it landed: its base stays
    // there and its knob is at the rim, a radius out.
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 1, ax, ay));
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 2, sx, sy));
    sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_MOTION, 2, sx + 1.4f * R / W, sy - 1.4f * R / H));
    CHECK(frame(&g, o, &px), "frame: %s", SDL_GetError());
    CHECK(sa2_touch::stats(o).draws == 1 && sa2_touch::stats(o).gpu, "draws %llu, error %s",
          (unsigned long long)sa2_touch::stats(o).draws, sa2_touch::stats(o).error.c_str());
    if (!out.empty()) {
        std::string err;
        const std::string path = out + "/overlay.png";
        CHECK(tex::write_png_rgb(path.c_str(), px.data(), W, H, W * 4, &err), "png: %s", err.c_str());
        std::printf("wrote %s\n", path.c_str());
    }
    float x, y;
    const auto colour = [&](Control c) { sa2_touch::control_centre(o, W, H, c, &x, &y); return pixel(px, W, x, y); };
    const Bgr a = colour(Control::A), b = colour(Control::B), xb = colour(Control::X), yb = colour(Control::Y);
    std::printf("A %d,%d,%d  B %d,%d,%d  X %d,%d,%d  Y %d,%d,%d (BGR)\n", a.b, a.g, a.r, b.b, b.g, b.r, xb.b, xb.g,
                xb.r, yb.b, yb.g, yb.r);
    CHECK(a.g > 150 && a.g > a.r + 60 && a.g > a.b + 60, "A (held) is not bright green");
    CHECK(b.r > 60 && b.r > b.g + 30 && b.r > b.b + 30, "B is not red");
    CHECK(xb.b > 60 && xb.b > xb.r + 30, "X is not blue");
    CHECK(yb.r > 60 && yb.g > 50 && yb.b < yb.r - 30, "Y is not yellow");
    CHECK(a.g > b.r + 40, "the held button is not brighter than an idle one");
    // The knob, a radius' 0.7 up and right of the origin; and the pixels no control covers.
    const Bgr knob = pixel(px, W, sx * W + 0.7f * R, sy * H - 0.7f * R);
    CHECK(knob.r > 140 && knob.g > 140 && knob.b > 140, "the stick's knob is not white: %d,%d,%d", knob.b, knob.g,
          knob.r);
    // The base's rim on the side away from the push is where the finger landed, and nothing is
    // under the finger itself, out past the rim.
    const float line = std::max(2.0f, 0.008f * float(H));
    const Bgr rim = pixel(px, W, sx * W - (R - 0.5f * line), sy * H);
    CHECK(rim.r > 100 && rim.g > 100 && rim.b > 100, "the stick's base moved: its rim %d,%d,%d", rim.b, rim.g, rim.r);
    const Bgr under = pixel(px, W, sx * W + 1.4f * R, sy * H - 1.4f * R);
    CHECK(under.r == 0 && under.g == 0 && under.b == 0, "drawn under the finger past the rim: %d,%d,%d", under.b,
          under.g, under.r);
    const Bgr mid = pixel(px, W, W * 0.5f, H * 0.5f), corner = pixel(px, W, W - 3.0f, H * 0.5f);
    CHECK(mid.r == 0 && mid.g == 0 && mid.b == 0 && corner.r == 0 && corner.g == 0 && corner.b == 0,
          "pixels away from the controls were drawn");
    const Bgr start = colour(Control::Start), lt = colour(Control::LT);
    CHECK(start.r > 40 && lt.r > 40, "START or LT not drawn: %d %d", start.r, lt.r);
    const double cover = double(lit(px)) / (double(W) * H);
    std::printf("overlay covers %.1f%% of the frame\n", 100.0 * cover);
    CHECK(cover > 0.03 && cover < 0.30, "coverage %.3f", cover);
    sa2_touch::deactivate(o);
    CHECK(frame(&g, o, &px) && lit(px) == 0, "a deactivated overlay drew");
    // Opacity: twice the default makes an idle button brighter; 0 draws nothing at all.
    {
        sa2_touch::Settings s;
        std::string why;
        sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 1, 0.5f, 0.6f));
        CHECK(frame(&g, o, &px), "frame: %s", SDL_GetError());
        const Bgr b1 = colour(Control::B);
        sa2_touch::parse_setting("SA2_TOUCH_OPACITY", "2", &s, &why);
        sa2_touch::set_settings(o, s);
        CHECK(frame(&g, o, &px), "frame: %s", SDL_GetError());
        const Bgr b2 = colour(Control::B);
        CHECK(b2.r > b1.r + 40, "B at opacity 2 is not brighter: %d, %d at 1", b2.r, b1.r);
        sa2_touch::parse_setting("SA2_TOUCH_OPACITY", "0", &s, &why);
        sa2_touch::set_settings(o, s);
        const uint64_t draws = sa2_touch::stats(o).draws;
        CHECK(frame(&g, o, &px) && lit(px) == 0 && sa2_touch::stats(o).draws == draws, "opacity 0 drew %llu pixels",
              (unsigned long long)lit(px));
        sa2_touch::set_settings(o, sa2_touch::Settings{});
        sa2_touch::deactivate(o);
    }
    // The adventure style: A held and the stick pushed as above. Each face in the theme's colour,
    // lighter at its top than its bottom, striped across, inside a rim of the darker colour; the
    // held one darker; the stick's base tinted; nothing away from the controls.
    {
        sa2_touch::Settings s;
        std::string why;
        sa2_touch::parse_setting("SA2_TOUCH_STYLE", "adventure", &s, &why);
        sa2_touch::set_settings(o, s);
        const auto save = [&](const char* name) {
            if (out.empty()) return;
            std::string err;
            const std::string path = out + "/" + name;
            CHECK(tex::write_png_rgb(path.c_str(), px.data(), W, H, W * 4, &err), "png: %s", err.c_str());
            std::printf("wrote %s\n", path.c_str());
        };
        sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 1, ax, ay));
        sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 2, sx, sy));
        sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_MOTION, 2, sx + 1.4f * R / W, sy - 1.4f * R / H));
        CHECK(frame(&g, o, &px), "frame: %s", SDL_GetError());
        save("overlay-adventure.png");
        const int rad = 7;   // over a stripe's period (0.014 of 720: 10 pixels)
        const auto face = [&](Control c) { sa2_touch::control_centre(o, W, H, c, &x, &y); return patch(px, W, x, y, rad); };
        for (Control c : {Control::A, Control::B, Control::X, Control::Y, Control::Start, Control::LT, Control::RT}) {
            const Bgr f = face(c);
            CHECK(f.b > 70 && f.b > f.r + 40 && f.b > f.g + 25, "control %d is not sonic's blue: %d,%d,%d", int(c),
                  f.b, f.g, f.r);
        }
        const Bgr held = face(Control::A), idle = face(Control::B);
        CHECK(held.b + 8 < idle.b, "the held face is not darker: %d, idle %d", held.b, idle.b);
        // B's column: its top brighter than its bottom; and dips, a stripe each, between.
        float b_x, b_y;
        sa2_touch::control_centre(o, W, H, Control::B, &b_x, &b_y);
        const float rb = 0.075f * H;
        const Bgr top = patch(px, W, b_x, b_y - 0.6f * rb, 3), bottom = patch(px, W, b_x, b_y + 0.6f * rb, 3);
        CHECK(top.b > bottom.b + 30 && top.g > bottom.g + 15, "no gradient: top %d,%d,%d bottom %d,%d,%d", top.b,
              top.g, top.r, bottom.b, bottom.g, bottom.r);
        int dips = 0;
        bool in_dip = false;
        for (int row = int(b_y - 0.5f * rb); row <= int(b_y + 0.5f * rb); ++row) {
            const int v = pixel(px, W, b_x, float(row)).b, up = pixel(px, W, b_x, float(row - 3)).b,
                      down = pixel(px, W, b_x, float(row + 3)).b;
            const bool dip = v + 6 < up && v + 6 < down;
            if (dip && !in_dip) ++dips;
            in_dip = dip;
        }
        CHECK(dips >= 4, "%d stripes across B's middle", dips);
        // The rim, just inside B's edge: the dark rim colour (#0b2f8a), little red.
        const Bgr rim = pixel(px, W, b_x + rb - 2.0f, b_y);
        CHECK(rim.b > 70 && rim.r < 30 && rim.b > rim.r + 50, "B's rim %d,%d,%d", rim.b, rim.g, rim.r);
        const Bgr base = pixel(px, W, sx * W - 0.6f * R, sy * H + 0.2f * R);
        CHECK(base.b > 20 && base.b > base.r + 15, "the stick's base is not tinted: %d,%d,%d", base.b, base.g, base.r);
        const Bgr knob = patch(px, W, sx * W + 0.7f * R, sy * H - 0.7f * R, 3);
        CHECK(knob.b > 70 && knob.b > knob.r + 30, "the knob is not glossy blue: %d,%d,%d", knob.b, knob.g, knob.r);
        const Bgr mid = pixel(px, W, W * 0.5f, H * 0.5f), corner = pixel(px, W, W - 3.0f, H * 0.5f);
        CHECK(mid.r + mid.g + mid.b == 0 && corner.r + corner.g + corner.b == 0, "adventure drew away from the controls");
        const double cover = double(lit(px)) / (double(W) * H);
        std::printf("adventure: A held %d,%d,%d  B %d,%d,%d  B's top %d bottom %d, %d stripes; covers %.1f%%\n",
                    held.b, held.g, held.r, idle.b, idle.g, idle.r, top.b, bottom.b, dips, 100.0 * cover);
        CHECK(cover > 0.03 && cover < 0.35, "adventure coverage %.3f", cover);
        // Knuckles: red.
        sa2_touch::parse_setting("SA2_TOUCH_THEME", "knuckles", &s, &why);
        sa2_touch::set_settings(o, s);
        CHECK(frame(&g, o, &px), "frame: %s", SDL_GetError());
        save("overlay-adventure-knuckles.png");
        const Bgr kb = face(Control::B);
        CHECK(kb.r > 70 && kb.r > kb.b + 40 && kb.r > kb.g + 40, "B is not knuckles' red: %d,%d,%d", kb.b, kb.g, kb.r);
        // Two face buttons: JUMP and ACTION drawn, and nothing where X and Y were (outside both).
        sa2_touch::parse_setting("SA2_TOUCH_THEME", "sonic", &s, &why);
        sa2_touch::parse_setting("SA2_TOUCH_FACE", "two", &s, &why);
        sa2_touch::set_settings(o, s);
        sa2_touch::deactivate(o);
        float jx, jy;
        sa2_touch::control_centre(o, W, H, Control::A, &jx, &jy);
        sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 1, jx / W, jy / H));
        CHECK(frame(&g, o, &px), "frame: %s", SDL_GetError());
        save("overlay-adventure-two.png");
        const Bgr jump = face(Control::A), action = face(Control::B);
        CHECK(jump.b > 70 && jump.b > jump.r + 40 && action.b > 70 && action.b > action.r + 40 && jump.b + 8 < action.b,
              "JUMP (held) %d,%d,%d and ACTION %d,%d,%d", jump.b, jump.g, jump.r, action.b, action.g, action.r);
        const Bgr was_x = pixel(px, W, bx * W - 2 * 0.135f * H, by * H), was_y = pixel(px, W, yx * W, yy * H);
        CHECK(was_x.r + was_x.g + was_x.b == 0 && was_y.r + was_y.g + was_y.b == 0,
              "drawn where X or Y were: %d,%d,%d %d,%d,%d", was_x.b, was_x.g, was_x.r, was_y.b, was_y.g, was_y.r);
        sa2_touch::set_settings(o, sa2_touch::Settings{});
        sa2_touch::deactivate(o);
    }
    // The game's art, from a PAK of this test's own drawing (no game file). Refused with a reason
    // when it is not there or not right, and the shader's look drawn then, pixel for pixel.
    {
        namespace fs = std::filesystem;
        const fs::path root = fs::temp_directory_path() / ("sa2-touch-art-" + std::to_string(SDL_GetCurrentThreadID()));
        fs::remove_all(root);
        const fs::path pak_path = root / "resource" / "gd_PC" / "PRS" / "newMenu.pak";
        sa2_touch::Settings s;
        std::string why;
        sa2_touch::parse_setting("SA2_TOUCH_STYLE", "adventure", &s, &why);
        sa2_touch::set_settings(o, s);
        sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 1, ax, ay));
        sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 2, sx, sy));
        CHECK(frame(&g, o, &px), "frame: %s", SDL_GetError());
        const std::vector<uint8_t> shader_look = px;
        CHECK(!sa2_touch::load_art(o, "", &why) && why.find("SA2_TOUCH_ART_ROOT") != std::string::npos, "no root: %s",
              why.c_str());
        CHECK(!sa2_touch::load_art(o, root.string(), &why) && why.find("no resource/gd_PC/PRS/newMenu.pak") == 0,
              "no PAK: %s", why.c_str());
        write_file(pak_path, {1, 'z', 'i', 'p', 0, 0});
        CHECK(!sa2_touch::load_art(o, root.string(), &why) && why.find("not a PAK") != std::string::npos, "junk: %s",
              why.c_str());
        auto members = test_members();
        auto three = members;
        three.pop_back();
        write_file(pak_path, pak(three));
        CHECK(!sa2_touch::load_art(o, root.string(), &why) && why.find("has no mainmenu_button_sikaku.dds") !=
              std::string::npos, "a member short: %s", why.c_str());
        auto small = members;
        small[0].second = dds(64, 64, [](int, int, uint8_t* p) { p[3] = 255; });
        write_file(pak_path, pak(small));
        CHECK(!sa2_touch::load_art(o, root.string(), &why) && why.find("is 64x64, not 128x128") != std::string::npos,
              "a member's size: %s", why.c_str());
        auto cut = pak(members);
        cut.resize(cut.size() - 100);
        write_file(pak_path, cut);
        CHECK(!sa2_touch::load_art(o, root.string(), &why) && why.find("runs past its end") != std::string::npos,
              "a cut PAK: %s", why.c_str());
        CHECK(!sa2_touch::art_loaded(o), "art loaded from a bad PAK");
        CHECK(frame(&g, o, &px) && px == shader_look, "a refused PAK changed the shader's look");
        // The good one, under a root whose folders' case differs from the game's (an install copied
        // from Windows): found, and drawn instead of the shader's look.
        const fs::path upper = root / "RESOURCE" / "GD_PC" / "prs" / "NEWMENU.PAK";
        fs::remove_all(root);
        write_file(upper, pak(members));
        CHECK(sa2_touch::load_art(o, root.string(), &why) && why.empty() && sa2_touch::art_loaded(o), "load: %s",
              why.c_str());
        CHECK(frame(&g, o, &px), "frame: %s", SDL_GetError());
        CHECK(px != shader_look && sa2_touch::stats(o).error.empty(), "the art was not drawn: %s",
              sa2_touch::stats(o).error.c_str());
        save_png(out, "overlay-art.png", px, W, H);
        const auto face = [&](Control c) {
            float fx, fy;
            sa2_touch::control_centre(o, W, H, c, &fx, &fy);
            return patch(px, W, fx, fy, 6);
        };
        // Sonic's colours are the art's own: B the test's blue (B,G,R 230,90,30 at 0.8 over black,
        // its stripes averaged in); A, held, the held disc's darker blue, at full opacity.
        const Bgr b_idle = face(Control::B), a_held = face(Control::A);
        std::printf("art: B %d,%d,%d  A held %d,%d,%d\n", b_idle.b, b_idle.g, b_idle.r, a_held.b, a_held.g, a_held.r);
        CHECK(std::abs(b_idle.b - 170) < 20 && std::abs(b_idle.g - 66) < 12 && b_idle.r < 35, "B is not the art's blue");
        CHECK(std::abs(a_held.b - 130) < 20 && a_held.g < 50 && a_held.r < 25, "A held is not the held disc");
        // START, LT and RT are the bars; START's mark is white over its middle.
        float tx, ty;
        sa2_touch::control_centre(o, W, H, Control::LT, &tx, &ty);
        const Bgr lt_bar = pixel(px, W, tx - 0.07f * H, ty + 0.03f * H);   // off the mark, inside the bar
        CHECK(lt_bar.b > 140 && lt_bar.g > 90 && lt_bar.r < 40, "LT is not the bar: %d,%d,%d", lt_bar.b, lt_bar.g, lt_bar.r);
        // The stick's base: the red arc turned to the button's blue, its middle line on the radius,
        // four ways round; the knob the button.
        for (const auto& [dx, dy] : {std::make_pair(-1.0f, 0.0f), std::make_pair(1.0f, 0.0f), std::make_pair(0.0f, -1.0f),
                                     std::make_pair(0.0f, 1.0f), std::make_pair(-0.7071f, -0.7071f)}) {
            const Bgr ring = pixel(px, W, sx * W + dx * (R + 4.5f), sy * H + dy * (R + 4.5f));   // beside the white line
            CHECK(ring.b > 100 && ring.b > ring.r + 60, "the ring at %.1f,%.1f is %d,%d,%d, not blue", dx, dy, ring.b,
                  ring.g, ring.r);
        }
        const Bgr knob = patch(px, W, sx * W, sy * H, 4);
        CHECK(knob.b > 100 && knob.r < 40, "the knob %d,%d,%d", knob.b, knob.g, knob.r);
        // Knuckles: the same art turned red, striped as before.
        sa2_touch::parse_setting("SA2_TOUCH_THEME", "knuckles", &s, &why);
        sa2_touch::set_settings(o, s);
        CHECK(frame(&g, o, &px), "frame: %s", SDL_GetError());
        save_png(out, "overlay-art-knuckles.png", px, W, H);
        const Bgr kb = face(Control::B);
        CHECK(kb.r > 140 && kb.g < 50 && kb.b < 40, "B is not knuckles' red: %d,%d,%d", kb.b, kb.g, kb.r);
        const Bgr kring = pixel(px, W, sx * W - (R + 4.5f), sy * H);
        CHECK(kring.r > 100 && kring.r > kring.b + 60, "knuckles' ring %d,%d,%d", kring.b, kring.g, kring.r);
        // Auto in a cutscene: START's bar alone, and sonic's blue.
        sa2_touch::parse_setting("SA2_TOUCH_THEME", "auto", &s, &why);
        sa2_touch::set_settings(o, s);
        sa2_touch::GameState st;
        st.mode = 8;
        sa2_touch::set_game_state(o, st);
        CHECK(frame(&g, o, &px), "frame: %s", SDL_GetError());
        float qx, qy;
        sa2_touch::control_centre(o, W, H, Control::Start, &qx, &qy);
        const Bgr start = pixel(px, W, qx - 0.06f * H, qy);
        CHECK(start.b > 140 && start.r < 40, "START in a cutscene %d,%d,%d", start.b, start.g, start.r);
        uint64_t elsewhere = 0;
        for (uint32_t y = 0; y < H; ++y)
            for (uint32_t x = 0; x < W; ++x) {
                const size_t i = (size_t(y) * W + x) * 4;
                if ((px[i] | px[i + 1] | px[i + 2]) && (std::fabs(float(x) - qx) > 0.1f * H || std::fabs(float(y) - qy) > 0.05f * H))
                    ++elsewhere;
            }
        CHECK(elsewhere == 0, "%llu pixels drawn away from START in a cutscene", (unsigned long long)elsewhere);
        // A stage as Tails: X, Y gone; the theme orange.
        st.mode = 5; st.level = 13; st.character = 2; st.two_player = 0; st.alt = 0;
        sa2_touch::set_game_state(o, st);
        CHECK(frame(&g, o, &px), "frame: %s", SDL_GetError());
        const Bgr tb = face(Control::B), tx_ = face(Control::X);
        CHECK(tb.r > 150 && tb.g > 80 && tb.b < 50 && tx_.r + tx_.g + tx_.b == 0, "tails' B %d,%d,%d, X %d,%d,%d", tb.b,
              tb.g, tb.r, tx_.b, tx_.g, tx_.r);
        sa2_touch::set_game_state(o, sa2_touch::GameState{});
        // The real game's, when this machine has an install: it parses and draws (its pictures go
        // only to OUT_DIR, never a tracked file).
        if (!real_install.empty()) {
            const bool ok = sa2_touch::load_art(o, real_install, &why);
            CHECK(ok, "the install's newMenu.pak: %s", why.c_str());
            std::printf("real art: %s\n", ok ? "loaded" : why.c_str());
            for (const char* t : {"sonic", "knuckles"}) {
                sa2_touch::parse_setting("SA2_TOUCH_THEME", t, &s, &why);
                sa2_touch::set_settings(o, s);
                CHECK(frame(&g, o, &px), "frame: %s", SDL_GetError());
                const Bgr rb = face(Control::B);
                std::printf("real art %s: B %d,%d,%d\n", t, rb.b, rb.g, rb.r);
                if (t[0] == 's') CHECK(rb.b > rb.r + 40, "the real button is not blue: %d,%d,%d", rb.b, rb.g, rb.r);
                else CHECK(rb.r > rb.b + 40, "the real button is not turned red: %d,%d,%d", rb.b, rb.g, rb.r);
                save_png(out, std::string("overlay-real-") + t + ".png", px, W, H);
            }
        }
        fs::remove_all(root);
        // Without art again (a new overlay): the shader's look.
        sa2_touch::set_settings(o, sa2_touch::Settings{});
        sa2_touch::deactivate(o);
        CHECK(sa2_touch::stats(o).draw_failures == 0, "draw failures with the art: %s", sa2_touch::stats(o).error.c_str());
    }
    // Below a portrait window's 4:3 picture (rows 510 to 1050 of 720x1560): every control in the
    // band, none over the picture.
    {
        release_target(&g);
        if (!make_target(&g, 720, 1560)) { std::printf("FAIL target: %s\n", SDL_GetError()); return 1; }
        sa2_touch::Overlay* q = sa2_touch::create(g.dev, nullptr);
        sa2_touch::set_target_format(q, SDL_GPU_TEXTUREFORMAT_B8G8R8A8_UNORM);
        sa2_touch::set_size(q, 720, 1560);
        sa2_touch::set_frame(q, 1440, 1080);
        float qx, qy;
        sa2_touch::control_centre(q, 720, 1560, Control::A, &qx, &qy);
        sa2_touch::handle_event(q, finger(SDL_EVENT_FINGER_DOWN, 1, qx / 720, qy / 1560));
        CHECK(frame(&g, q, &px), "frame: %s", SDL_GetError());
        const Bgr ab = pixel(px, 720, qx, qy);
        CHECK(ab.g > 150 && ab.g > ab.r + 60, "A (held) in the band is not bright green: %d,%d,%d", ab.b, ab.g, ab.r);
        uint64_t over = 0, band = 0;
        for (uint32_t row = 0; row < 1560; ++row)
            for (uint32_t col = 0; col < 720; ++col) {
                const size_t i = (size_t(row) * 720 + col) * 4;
                if (px[i] | px[i + 1] | px[i + 2]) (row < 1050 ? over : band)++;
            }
        CHECK(over == 0 && band > 20000, "portrait: %llu pixels drawn above the band, %llu in it",
              (unsigned long long)over, (unsigned long long)band);
        if (!out.empty()) {
            std::string err;
            const std::string path = out + "/overlay-portrait.png";
            CHECK(tex::write_png_rgb(path.c_str(), px.data(), 720, 1560, 720 * 4, &err), "png: %s", err.c_str());
            std::printf("wrote %s\n", path.c_str());
        }
        sa2_touch::destroy(q);
    }

    // ---------------------------------------------------------------- cost, at 1920x1080
    release_target(&g);
    if (!make_target(&g, 1920, 1080)) { std::printf("FAIL target: %s\n", SDL_GetError()); return 1; }
    // Each frame submitted and waited for alone, with the overlay and (inactive) without: the
    // difference is what the overlay costs a frame; the encoding is timed on the CPU.
    const int N = 300;
    const auto run = [&](bool with, double* encode_s) {
        if (with) {
            sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 1, ax, ay));
            sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_DOWN, 2, sx, sy));
        } else {
            sa2_touch::deactivate(o);
        }
        *encode_s = 0;
        const auto t0 = std::chrono::steady_clock::now();
        for (int i = 0; i < N; ++i) {
            SDL_GPUCommandBuffer* cb = SDL_AcquireGPUCommandBuffer(g.dev);
            if (with) sa2_touch::handle_event(o, finger(SDL_EVENT_FINGER_MOTION, 2, sx + 0.001f * (i % 50), sy));
            const auto e0 = std::chrono::steady_clock::now();
            sa2_touch::draw(o, cb, g.tex, g.w, g.h);
            *encode_s += std::chrono::duration<double>(std::chrono::steady_clock::now() - e0).count();
            SDL_GPUFence* f = SDL_SubmitGPUCommandBufferAndAcquireFence(cb);
            SDL_WaitForGPUFences(g.dev, true, &f, 1);
            SDL_ReleaseGPUFence(g.dev, f);
        }
        return std::chrono::duration<double>(std::chrono::steady_clock::now() - t0).count() / N;
    };
    double enc_with = 0, enc_without = 0;
    run(true, &enc_with);                         // warm: the pipeline is made on the first draw
    const double without = run(false, &enc_without);
    const double with = run(true, &enc_with);
    const double cost = with - without;
    std::printf("cost at 1920x1080 over %d frames: encode %.1f us/frame; a frame %.1f us with the overlay, "
                "%.1f us without: %.1f us\n", N, 1e6 * enc_with / N, 1e6 * with, 1e6 * without, 1e6 * cost);
    CHECK(enc_with / N < 0.0001, "encoding costs %.1f us", 1e6 * enc_with / N);
    CHECK(cost < 0.0005, "the overlay costs %.1f us a frame", 1e6 * cost);
    CHECK(sa2_touch::stats(o).draw_failures == 0, "draw failures: %s", sa2_touch::stats(o).error.c_str());
    sa2_touch::destroy(o);
    release_target(&g);
    SDL_DestroyGPUDevice(g.dev);
    SDL_Quit();
    fails += fails_png;
    if (fails) { std::printf("%d checks failed\n", fails); return 1; }
    std::printf("TOUCH_OVERLAY_OK\n");
    return 0;
}
