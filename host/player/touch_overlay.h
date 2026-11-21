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
// after a concept by NoasYTOFC (Kaito), in one colour theme per character (by default the
// character being played: SA2_TOUCH_THEME=auto, "The game's state" below), and shows only the
// controls the game uses where it is. With the game's art (below) the face buttons and the
// stick's knob are the main menu's round striped button (its darker one while held), START, LT
// and RT its bar, and the stick's base the menu cursor's arc four ways round, faint (a guide; the
// knob is the focus), all turned to the theme's hue. Without it the shader draws the same look: round glossy buttons, lighter at the
// top and darker at the bottom, with thin stripes across them and a darker rim; the stick a
// glossy knob over a faint tinted disc and a hairline ring. No text yet (touch_overlay.cpp draw_adventure
// says where labels would go).
//
// The game's art. load_art reads it from the player's own copy of the game, never from this
// repository: resource/gd_PC/PRS/newMenu.pak under the install (each name matched without regard
// to case), whose members mainmenu_button_maru.dds, mainmenu_button_maru_i.dds,
// mainmenu_button_sikaku.dds and mainmenu_csol.dds (uncompressed or DXT DDS) it decodes into one
// 512x256 atlas. The player passes SA2_TOUCH_ART_ROOT, the install the session's game reads (the
// launcher and the Android session set it). A missing or unreadable file, member or format leaves
// the shader's look, and load_art says why.
//
// The game's state. The game's recorder (host/sa2_present_rec.c, SA2_REC_STATE) writes a state
// note into the stream whenever what it reads of the game changes: "state mode=M level=L
// character=C two=T alt=A", GameMode (0x0174B040), CurrentLevel (0x01934B70), player one's
// CurrentCharacter (0x01934B80), TwoPlayerMode (0x0174AFDE) and player one's costume byte
// (0x0174B015), -1 for one that did not read. The player hands each to set_game_state, which
// makes the context (context_of: a menu, a stage, Chao World or a cutscene; unknown until the
// first note) and, with SA2_TOUCH_THEME=auto, the theme (theme_of: the character's in a stage
// or Chao World, else sonic's, the menus' own blue). In the adventure style a control whose
// output the context does not use (kContextUses in touch_overlay.cpp) is neither drawn nor hit,
// and a finger on it sends nothing; an unknown context uses everything. The classic style shows
// every control whatever the context.
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
//   SA2_TOUCH_THEME=T             the adventure style's colours: auto (the default: the
//                                 character being played, "The game's state" above), sonic,
//                                 shadow, knuckles, tails, rouge, amy, eggman, metal or chao
//   SA2_TOUCH_FACE=F              four (the default: the A/B/X/Y diamond) or two: two bigger
//                                 buttons in the diamond's place, JUMP (the A button) below right
//                                 and ACTION (the B button) above left, and no X or Y; the group,
//                                 its size and its place are the diamond's
// With none of them set the overlay is exactly the one described above.
//
// draw() renders onto a target texture after the game's frame (the swapchain texture the
// device presents), translucent, in one render pass of one draw from one uniform block (no
// vertex buffer, no texture): touch_overlay_shaders.py's SPIR-V, so a device that takes no
// SPIR-V gets no drawing (the pad still works). The adventure style with the game's art uploads
// the atlas once (a copy pass before the render pass) and draws three times in that pass: the
// stick's base under the art, the art, and START's, LT's and RT's marks over it. Before the
// overlay is active, draw() returns at once and encodes nothing, so a run without touch input
// presents exactly what it did.
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
    bool auto_theme = true;                  // SA2_TOUCH_THEME=auto: the character's colours
    Theme theme = Theme::Sonic;              // the adventure style's colours when not auto
    bool two_buttons = false;                // SA2_TOUCH_FACE=two: JUMP and ACTION, no X or Y
};
// Reads one setting, by its variable's name, into `s`: false with `why` when the value is
// malformed (`s` is then unchanged) or the name is none of the header's; `why` is empty when
// the value is taken.
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

// The game's art (the header's "The game's art"): the atlas from <root>/resource/gd_PC/PRS/
// newMenu.pak, kept for the overlay's device (uploaded at the first draw that uses it, and again
// on another device). False with `why` when it could not be made; the overlay then draws the
// adventure style's own shapes, as it does before any art is loaded.
bool load_art(Overlay* o, const std::string& root, std::string* why);
bool art_loaded(const Overlay* o);

// The game's state (the header's "The game's state"), as the state note gives it.
struct GameState {
    int32_t mode = -1, level = -1, character = -1, two_player = -1, alt = -1;
};
enum class Context { Unknown, Menu, Stage, ChaoWorld, Cutscene };
constexpr int kContexts = 5;
const char* context_name(Context c);         // "unknown", "menu", "stage", "chao world", "cutscene"
const char* theme_name(Theme t);             // SA2_TOUCH_THEME's names
// A state note's text into `out` (fields it does not name stay -1): false when it is not one.
bool parse_state_note(const std::string& text, GameState* out);
// The context of a state: by GameMode, and Chao World a stage at level 90; a GameMode between two
// contexts (a load) keeps `last`.
Context context_of(const GameState& s, Context last);
// The theme auto picks: the character's (player one's, its costume's in a two-player game) in a
// stage or Chao World, else sonic's.
Theme theme_of(const GameState& s, Context c);
// Whether a context uses an output: an XInput button bit, or a trigger (1 LT, 2 RT); the stick.
bool context_uses(Context c, uint16_t bit, int trigger);
bool context_uses_stick(Context c);
// The game's state for the overlay: its context and, with auto, its theme.
void set_game_state(Overlay* o, const GameState& s);
Context context(const Overlay* o);
Theme theme(const Overlay* o);               // the adventure style's colours now

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
// Whether a control is on screen and hit in the overlay's style and context (X and Y with two
// face buttons never are); control_box gives its place either way.
bool control_shown(const Overlay* o, Control c);

}  // namespace sa2_touch
