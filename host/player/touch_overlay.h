// touch_overlay.h: the player's on-screen gamepad for touch screens (Android first).
//
// The overlay is inactive and draws nothing until the first SDL finger event; from then on
// it is a virtual XInput pad, which the player sends in slot 0 of its gamepad records
// (input.h send_pads; player.cpp "Gamepads" says how it shares the slot): a stick on the left
// half of the screen whose origin is wherever the finger lands and stays there until it lifts
// (radius 12% of the screen's short side; a finger pulled past the radius is a full push in its
// direction from the origin, which does not move), the A/B/X/Y diamond bottom right, START top
// centre, LT and RT top left and right.
// The adventure style (SA2_TOUCH_STYLE below) draws the same controls in the game's own menu look,
// after a concept by NoasYTOFC (Kaito): round glossy buttons, lighter at the top and darker at the
// bottom, with thin stripes across them and a darker rim, in one colour theme per character; the
// stick a tinted disc with a thin ring and a glossy knob. Everything is drawn by the shader: no
// image, and no text yet (touch_overlay.cpp draw() says where labels would go).
// Every finger is tracked by its id, so the stick and any buttons are held together; a finger
// that lands on a button can slide onto another. Fingers of SDL's mouse-touch device are not
// touches and are ignored.
//
// Where the controls go. They lie over the picture, in the window's corners, unless the picture
// leaves a band under it at least 30% of the window's short side tall (a portrait screen, where a
// 4:3 picture across the width leaves a third of the height free below it): they then go in that
// band, laid out as they would be in a window of the band's shape, at the window's short side's
// size, made smaller only when the band is too short for them. The picture is the frame's
// rectangle as the device presents it (host/d3d9gpu/d9g_gpu.h fit_rect: centred at its own
// aspect, or higher with the player's anchor, set_picture), from the frame size the player passes
// at each Present (set_frame); until a frame is known the controls lie over the whole window.
//
// Settings (the player's environment: Android's settings screen through its session, or
// game.env.extra; parse_setting):
//   SA2_TOUCH_SCALE=F             every control's size times F, 0.5 to 2 (default 1)
//   SA2_TOUCH_OPACITY=F           every control's opacity times F, 0 to 2 (default 1; 0 draws
//                                 nothing, and the controls still work)
//   SA2_TOUCH_LAYOUT=G=X,Y;...    where a group's centre is in a landscape window (no taller than
//                                 wide): X and Y fractions (0 to 1) of the control area (the
//                                 window, or the band under the picture); G is STICK (the stick's
//                                 resting place, which also chooses the half of the screen the
//                                 stick takes), FACE (A, B, X and Y), START, LT or RT; a group left
//                                 out keeps its own place, and a placed one is held whole inside
//                                 the area at the current size
//   SA2_TOUCH_LAYOUT_PORTRAIT=... the same, in a window taller than wide (the band under the
//                                 picture when there is one, else the window)
//   SA2_TOUCH_MAP=B=OUT,...       what an on-screen button sends: B one of A B X Y START LT RT,
//                                 OUT a game button by input.h pad_output_named's names
//   SA2_TOUCH_STYLE=S             classic (the default: flat translucent shapes, each face button
//                                 its own colour) or adventure (the glossy look above)
//   SA2_TOUCH_THEME=T             the adventure style's colours: sonic (the default), shadow,
//                                 knuckles, tails, rouge, amy, eggman, metal or chao; auto is
//                                 meant to follow the character being played, which the player
//                                 does not know yet, so it is sonic's (parse_setting says so)
//   SA2_TOUCH_FACE=F              four (the default: the A/B/X/Y diamond) or two: two bigger
//                                 buttons in the diamond's place, JUMP (the A button) below right
//                                 and ACTION (the B button) above left, and no X or Y; the group,
//                                 its size and its place are the diamond's
// With none of them set the overlay is exactly the one described above.
//
// draw() renders onto a target texture after the game's frame (the swapchain texture the
// device presents), translucent, in one render pass of one draw from one uniform block (no
// vertex buffer, no texture): touch_overlay_shaders.py's SPIR-V, so a device that takes no
// SPIR-V gets no drawing (the pad still works). Before the overlay is active, draw() returns
// at once and encodes nothing, so a run without touch input presents exactly what it did.
// A null device makes an overlay that only maps touches (no drawing at all).
#pragma once

#include <SDL3/SDL_events.h>
#include <SDL3/SDL_gpu.h>
#include <SDL3/SDL_video.h>

#include <cstdint>
#include <string>

#include "input.h"

namespace sa2_touch {

struct Overlay;

// Borrowed device and window, both outliving the overlay. The window sizes the touch layout
// (its size in pixels) and, once a device has claimed it, gives the target format.
Overlay* create(SDL_GPUDevice* device, SDL_Window* window);
// True when the event was a touch event the overlay took (finger down, motion, up, cancel).
bool handle_event(Overlay* o, const SDL_Event& e);
// Whether the overlay is in use: from the first finger down until deactivate().
bool active(const Overlay* o);
// The virtual pad: connected only while active.
void pad(const Overlay* o, sa2_input::PadState* out);
// Encodes the overlay onto `target` (w x h pixels) in `cb`; nothing while inactive.
void draw(Overlay* o, SDL_GPUCommandBuffer* cb, SDL_GPUTexture* target, uint32_t w, uint32_t h);
void destroy(Overlay* o);

// Beyond contract C. Inactive, hidden and every finger let go (the player calls it when a
// physical pad's button is pressed); the next finger down reactivates it.
void deactivate(Overlay* o);
// The format draw() targets when the window has no claimed swapchain (a test's texture).
void set_target_format(Overlay* o, SDL_GPUTextureFormat format);
// The touch layout's size in pixels when the overlay has no window (a test's; 1280x720 until set).
void set_size(Overlay* o, uint32_t w, uint32_t h);
// The device to draw with, when create() had none (the player learns it at the first Present).
// Another device than the one it drew with makes its shaders and pipeline afresh; what it made
// with the old one is forgotten, not released, since that device may be gone already.
void attach_device(Overlay* o, SDL_GPUDevice* device);

struct Stats {
    uint64_t finger_events = 0, ignored_fingers = 0, activations = 0, draws = 0, draw_failures = 0;
    bool gpu = false;            // a device that takes SPIR-V, and a pipeline made when one was needed
    std::string error;           // the first thing that went wrong while drawing
};
Stats stats(const Overlay* o);

// The settings (the header's list). A Settings{} is the overlay with none set.
enum class Group { Stick, Face, Start, LT, RT };
constexpr int kGroups = 5;
constexpr int kButtons = 7;                  // A B X Y START LT RT, in Control's order after Stick
struct Place {
    bool set = false;
    float x = 0, y = 0;                      // fractions of the control area
};
enum class Style { Classic, Adventure };
enum class Theme { Sonic, Shadow, Knuckles, Tails, Rouge, Amy, Eggman, Metal, Chao };
constexpr int kThemes = 9;
struct Settings {
    float scale = 1, opacity = 1;
    Place landscape[kGroups], portrait[kGroups];   // by Group, by the window's orientation
    sa2_input::PadOutput sends[kButtons];    // what each on-screen button sends
    bool mapped[kButtons] = {};              // false: the button's own (A sends A)
    Style style = Style::Classic;
    Theme theme = Theme::Sonic;              // the adventure style's colours
    bool two_buttons = false;                // SA2_TOUCH_FACE=two: JUMP and ACTION, no X or Y
};
// Reads one setting, by its variable's name, into `s`: false with `why` when the value is
// malformed (`s` is then unchanged) or the name is none of the header's.
// A value taken may still leave a remark in `why` for the log (SA2_TOUCH_THEME=auto's); else it is
// left empty.
bool parse_setting(const std::string& name, const std::string& value, Settings* s, std::string* why);
void set_settings(Overlay* o, const Settings& s);
// The game's frame size, for where the picture is; the player passes it at each Present.
void set_frame(Overlay* o, uint32_t frame_w, uint32_t frame_h);
// Where a letterboxed picture sits, as the device places it (host/d3d9gpu/d9g_gpu.h fit_rect's
// anchor, which the player sets only for an upright window): its centre at anchor_y of the height
// of a window taller than wide (negative: centred, the default), its top no higher than min_top
// pixels and no lower than centred.
void set_picture(Overlay* o, float anchor_y, uint32_t min_top);
// The picture's top and bottom rows in a w x h window; false before a frame is known.
bool picture_rows(const Overlay* o, uint32_t w, uint32_t h, uint32_t* top, uint32_t* bottom);
// Whether the controls are in the band under the picture, for the last frame size given.
bool below_picture(const Overlay* o, uint32_t w, uint32_t h);

// The layout's shapes in pixels, for tests and the player's --synthetic-input: the centre of
// each control on a w x h target with `o`'s settings and frame (a null `o`: no settings, no frame).
enum class Control { Stick, A, B, X, Y, Start, LT, RT };
void control_centre(const Overlay* o, uint32_t w, uint32_t h, Control c, float* x, float* y);
// The stick's radius in pixels on a w x h target: a full push is this far from its origin.
float stick_radius(const Overlay* o, uint32_t w, uint32_t h);
// The box a control is drawn in on a w x h target, centre (x, y) and half its width and height
// (the stick: its base at its resting place), for what else draws over the window to keep clear of.
// With two face buttons, A is JUMP and B is ACTION, and X and Y are not there: their box is the
// face group's centre, of no size.
void control_box(const Overlay* o, uint32_t w, uint32_t h, Control c, float* x, float* y, float* hw, float* hh);

}  // namespace sa2_touch
