/* sa2_stream.h: the Direct3D 9 call stream a presentation build of the lifted game
 * writes and the DXVK player reads (S5, the user's queue item b).
 *
 * Why a stream: the game binary is statically linked against musl and cannot load
 * DXVK, SDL3, the Vulkan loader or the NVIDIA driver (its dlopen is musl's stub), and
 * relinking it against glibc would change the host math the lifted x87 code calls.
 * So the calls the guest makes on the recording device's COM objects are copied out
 * at call time, in order, into this stream, and a separate glibc process replays them
 * into DXVK Native in an SDL3 window: from a file first, over a socket for live runs.
 * The stream is also the recorded draw list S5's second renderer reads.
 *
 * The writer is host/sa2_present_rec.c; tools/s5/stream_dump.py is the reference
 * reader, and Documentation/scripts/tests/test_s5_stream.py holds both to this file:
 * the rule lines below are parsed there and must equal the table the writer puts in
 * every stream's header, and stream_dump holds every record's blobs to that table.
 * tools/s5/present_run.py holds each stream it makes to these lines as well
 * (stream_dump --format).
 *
 * Framing (all little-endian; every record starts on a 4-byte boundary)
 * ----------------------------------------------------------------------
 *
 *   file header   char magic[8] = "SA2D3D9S"; u32 version = 1 (2 for a stream with
 *                 audio: below); u32 header_bytes (the whole header, these 16 bytes
 *                 included, a multiple of 4); then header_bytes - 16 bytes of UTF-8
 *                 JSON describing the run, padded with spaces (JSON whitespace) to
 *                 header_bytes.
 *   record        u32 kind; u32 bytes (the whole record, this head included, a
 *                 multiple of 4); u32 seq (0, 1, 2, ... over every record of the
 *                 file, whatever its kind); payload.
 *
 * Fields are packed at 4-byte alignment. A u64 is two u32 words, low word first, and
 * need not be 8-byte aligned in the file: read it with memcpy, never through a cast.
 * A reader skips a record of a kind it does not know by `bytes`; a writer never
 * changes the layout of a kind below without changing `version`.
 *
 * Versions. Version 1 holds the five kinds CALL, D3DX, FRAME, NOTE and END. Version 2
 * is version 1 and one kind more, SA2S_AUDIO, with every version 1 kind laid out as in
 * version 1, the header's schema "sa2-d3d9-stream/v2" and an "audio" table in it. The
 * writer writes version 2 exactly when it records audio (SA2_REC_AUDIO=1), so a stream
 * without audio is byte for byte the version 1 stream it was before audio existed. The
 * rule above would let a version 1 reader skip AUDIO records; this project's readers
 * do not skip a kind they cannot vouch for (host/player/player.cpp counts one as a
 * stream problem, tools/s5/stream_dump.py as a problem), so a stream that holds one
 * says so in its version, and a reader of version 1 alone refuses it at open, before
 * it reads a record, rather than failing a replay part way through.
 *
 * Kinds
 * -----
 *   SA2S_CALL     one COM method call through the recording device's vtable thunks
 *                 (runtime/sa2_d3d9.h, SA2_D3D9_THUNK_BASE), recorded after the
 *                 recording device answered it:
 *                   u16 type (the recording device's interface index, below)
 *                   u16 slot (the vtable slot)
 *                   u32 this_obj (the guest address of the object called)
 *                   u32 guest_thread (sa2_sched_current(): 0 is the main thread)
 *                   u32 argc; u32 args[argc] (the raw guest dwords after `this`;
 *                   argc is the recording device's own count for the slot,
 *                   sa2_d3d9_method_argc, and is also in the header's method table)
 *                   u32 eax (the answer the guest got)
 *                   u32 nblobs; blobs
 *   SA2S_D3DX     one call of an entry point the image imports that is not a COM
 *                 method -- the eleven d3dx9_42 functions and d3d9's
 *                 Direct3DCreate9 (SA2S_D3DX_* below) -- recorded after it answered:
 *                   u32 entry (SA2S_D3DX_* below)
 *                   u32 argc; u32 args[argc]; u32 eax; u32 nblobs; blobs
 *                 argc is the entry's own (the header's "entries" table). These
 *                 records carry no guest thread; the stream's order is the order the
 *                 calls finished in, which is all a player needs.
 *   SA2S_FRAME    at every Present (the device's, slot 17, and a swap chain's,
 *                 slot 3), as the record directly after that Present's SA2S_CALL:
 *                   u64 virtual_us (sa2_virtual_us(): the guest's clock)
 *                   u32 frames     (sa2_sched_frames(): frame boundaries crossed)
 *                   u32 polls      (sa2_window_stats().polls: PeekMessageW calls)
 *                 Neither virtual_us nor frames is ever below the FRAME before.
 *                 The writer flushes the file after every Present's records, so a
 *                 process that dies loses at most the calls since the last Present.
 *   SA2S_NOTE     u32 len; UTF-8 text; padded to 4. The writer's diagnostics:
 *                 never data a player needs, never a substitute for a blob. A note
 *                 about a call follows that call's record (and, for a Present, its
 *                 SA2S_FRAME). Every note says something was not recorded, so a
 *                 stream with any is not one a runner calls ready -- except a state
 *                 note, text beginning "state " (host/sa2_present_rec.c,
 *                 SA2_REC_STATE; live play only): the game's state after a FRAME, for
 *                 the player's touch controls, which leaves nothing out. Readers count
 *                 state notes apart (host/player/player.cpp's report "records" "state",
 *                 tools/s5/stream_dump.py's "state_records") and never fail a stream
 *                 for one; the drawing never depends on them.
 *   SA2S_END      the writer closed cleanly at process exit; no payload, nothing
 *                 follows. A stream without it was cut short (the process died,
 *                 or the writer disabled itself: its `.status` sidecar says why).
 *   SA2S_AUDIO    (version 2 only) one call of a DirectSound 8 or XAudio2 2.6 method
 *                 through the runtime's audio devices' vtable thunks (runtime/
 *                 sa2_dsound.h, SA2_DS_THUNK_BASE; runtime/sa2_xaudio2.h,
 *                 SA2_XA2_THUNK_BASE), recorded after the device answered it, for the
 *                 methods with an `audio` line below and only when the call
 *                 succeeded: its answer not negative, or any answer of a Release (a
 *                 count) and of a DestroyVoice (none natively):
 *                   u16 api (SA2S_AU_DSOUND or SA2S_AU_XAUDIO2)
 *                   u16 op (SA2S_AU_*: the op's line below names its method)
 *                   u32 this_obj (the guest address of the object called)
 *                   u32 guest_thread (sa2_sched_current())
 *                   u64 virtual_us (sa2_virtual_us() after the call; the device's
 *                   own anchors read the same clock inside it, and no audio method
 *                   lets the clock move)
 *                   u32 argc; u32 args[argc] (the raw guest dwords after `this`;
 *                   argc is the device's own count for the method, which must be
 *                   the op's line's, or the writer stops the stream)
 *                   u32 eax
 *                   u32 nblobs; blobs
 *                 Neither this record's virtual_us nor a FRAME's is ever below the
 *                 timed record (AUDIO or FRAME) before it.
 *
 * Blobs
 * -----
 * A blob is data a call referenced, copied at call time, because guest buffers are
 * stack, reused or freed as soon as the call returns:
 *   u16 role (SA2S_BLOB_*); u16 arg (the index into args[] the blob belongs to, or
 *   0xFFFF for none); u32 len; u8 data[len]; zero bytes to the next multiple of 4.
 * A blob the writer could not copy whole (a pointer outside guest memory, bytes that
 * do not read, a length over its cap, a region with more rectangles than it keeps)
 * is left out, never cut short, and a SA2S_NOTE says which and why.
 *
 * What each call carries
 * ----------------------
 * The table below is the writer's, one line per method, in a grammar the tests
 * parse: `rule <interface>::<method>  <op>; <op>; ...` (interface `*`: every
 * interface with that method, unless it has a line of its own), and
 * `entry <n> <name> <argc>  <op>; ...` for the SA2S_D3DX entries (`-` for none).
 * Text after `#` is a comment ("boot" and "mh" mark what the registered boot and
 * Metal Harbor replays call). The writer puts the same table in every stream's
 * header ("rules", "entries"), each op in the same words. Argument indices count
 * from 0 after `this`, a pointer argument that is 0 gets no blob, and every blob
 * but a pre_* one is read after the call returned. An `out` or `out_vec` blob is
 * read whatever the call answered: a call that failed wrote nothing there, so its
 * OUT blob holds what the guest's own variable held, not an output of the call.
 *
 *   in A N          IN blob of argument A: N bytes
 *   out A N         OUT blob of argument A: N bytes
 *   in_vec A B N    IN blob of argument A: args[B] * N bytes (none when that is 0)
 *   out_vec A B N   OUT blob of argument A: args[B] * N bytes (none when that is 0)
 *   out_sized A B N OUT blob of argument A: N times the dword argument B points at,
 *                   read after the call; only when the call answered 0 and B is not 0
 *   shader A        IN blob of argument A: the shader's tokens from the version token
 *                   (high half 0xFFFE or 0xFFFF) to the 0x0000FFFF end token, both
 *                   included, walked with each 0xFFFE comment skipped by its own
 *                   length and each instruction by its length (bits 24-27 from
 *                   shader model 2; below it, `def` by its five tokens and any other
 *                   instruction by its parameter tokens, which have bit 31 set), so
 *                   no comment or literal ends it early; at most 65536 tokens
 *   decl A          IN blob of argument A: 8-byte D3DVERTEXELEMENT9s up to and
 *                   including D3DDECL_END (Stream 0xFF), at most 65
 *   up              DrawPrimitiveUP: IN a2, vertices(a0, a1) * a3 bytes, where
 *                   vertices is n for a point list, 2n for a line list, n+1 for a
 *                   line strip, 3n for a triangle list and n+2 for a strip or a fan
 *   iup             DrawIndexedPrimitiveUP: IN a4, vertices(a0, a3) * (2 when a5 is
 *                   D3DFMT_INDEX16 (101), else 4) bytes; IN a6, (a1 + a2) * a7 bytes
 *   pre_pp A        IN blob of argument A: D3DPRESENT_PARAMETERS, 56 bytes (the
 *                   guest's 32-bit layout), read before the call, which may rewrite it
 *   pre_rect A      IN blob of argument A: a RECT, 16 bytes, read before the call
 *   pre_rgn A       IN blob of argument A: an RGNDATA, its 32-byte header and its
 *                   nCount rectangles of 16, read before the call; left out (with a
 *                   note) when nCount is over 16
 *   frame           a SA2S_FRAME is the next record
 *   device          DESC: sa2s_desc_device, 12 bytes
 *   file A B        FILE blob of argument A: args[B] bytes (the d3dx source file)
 *   file_desc A     DESC: sa2s_desc_file_texture, 88 bytes, for the texture written
 *                   through argument A, when the call answered 0
 *   object_eax      the answer is an object (Direct3DCreate9's IDirect3D9)
 *   unlock_surf, unlock_tex, unlock_buf
 *                   LOCK: sa2s_lock_info, 68 bytes, for the lock this Unlock closes,
 *                   and LOCKED: its `bytes` from its `bits` (below); neither when no
 *                   lock is open on the object (a note says so)
 *   new_tex A B C D, new_vol A B C D, new_cube A B D, new_surf A B C D, new_buf A B,
 *   level A, lock_surf, lock_tex, lock_buf
 *                   no blob: what the writer keeps to size a later lock. The object
 *                   written through argument A is B x C (a cube's edge B), format D;
 *                   a buffer's length is args[B]; level A: GetSurfaceLevel's surface
 *                   is that level of `this`. lock_surf is Surface::LockRect(pLockedRect
 *                   a0, pRect a1, Flags a2), lock_tex Texture::LockRect(Level a0,
 *                   pLockedRect a1, pRect a2, Flags a3), lock_buf Lock(OffsetToLock a0,
 *                   SizeToLock a1, ppbData a2, Flags a3); each opens a lock when the
 *                   call answered 0
 *
 *   rule IDirect3D9::GetAdapterIdentifier         out 2 1100        # D3DADAPTER_IDENTIFIER9; boot mh
 *   rule IDirect3D9::EnumAdapterModes             out 3 16          # D3DDISPLAYMODE
 *   rule IDirect3D9::GetAdapterDisplayMode        out 1 16          # boot mh
 *   rule IDirect3D9::CheckDeviceMultiSampleType   out 5 4           # quality levels; boot mh
 *   rule IDirect3D9::GetDeviceCaps                out 2 304         # D3DCAPS9
 *   rule IDirect3D9::CreateDevice                 pre_pp 4; out 5 4; device   # boot mh
 *   rule IDirect3DDevice9::GetDirect3D            out 0 4
 *   rule IDirect3DDevice9::GetDeviceCaps          out 0 304         # boot mh
 *   rule IDirect3DDevice9::GetDisplayMode         out 1 16
 *   rule IDirect3DDevice9::GetCreationParameters  out 0 16
 *   rule IDirect3DDevice9::CreateAdditionalSwapChain  pre_pp 0; out 1 4
 *   rule IDirect3DDevice9::GetSwapChain           out 1 4
 *   rule IDirect3DDevice9::Reset                  pre_pp 0
 *   rule IDirect3DDevice9::Present                pre_rect 0; pre_rect 1; pre_rgn 3; frame   # boot mh
 *   rule IDirect3DDevice9::GetBackBuffer          out 3 4           # mh
 *   rule IDirect3DDevice9::GetRasterStatus        out 1 8
 *   rule IDirect3DDevice9::SetGammaRamp           in 2 1536
 *   rule IDirect3DDevice9::GetGammaRamp           out 1 1536
 *   rule IDirect3DDevice9::CreateTexture          out 6 4; new_tex 6 0 1 4    # boot mh
 *   rule IDirect3DDevice9::CreateVolumeTexture    out 7 4; new_vol 7 0 1 5
 *   rule IDirect3DDevice9::CreateCubeTexture      out 5 4; new_cube 5 0 3
 *   rule IDirect3DDevice9::CreateVertexBuffer     out 4 4; new_buf 4 0        # mh
 *   rule IDirect3DDevice9::CreateIndexBuffer      out 4 4; new_buf 4 0
 *   rule IDirect3DDevice9::CreateRenderTarget     out 6 4; new_surf 6 0 1 2
 *   rule IDirect3DDevice9::CreateDepthStencilSurface  out 6 4; new_surf 6 0 1 2   # boot mh
 *   rule IDirect3DDevice9::UpdateSurface          in 1 16; in 3 8   # RECT, POINT
 *   rule IDirect3DDevice9::StretchRect            in 1 16; in 3 16
 *   rule IDirect3DDevice9::ColorFill              in 1 16
 *   rule IDirect3DDevice9::CreateOffscreenPlainSurface  out 4 4; new_surf 4 0 1 2
 *   rule IDirect3DDevice9::GetRenderTarget        out 1 4           # mh
 *   rule IDirect3DDevice9::GetDepthStencilSurface out 0 4           # mh
 *   rule IDirect3DDevice9::Clear                  in_vec 1 0 16     # D3DRECTs; Z (a4) is float bits; boot mh
 *   rule IDirect3DDevice9::SetTransform           in 1 64
 *   rule IDirect3DDevice9::GetTransform           out 1 64
 *   rule IDirect3DDevice9::MultiplyTransform      in 1 64
 *   rule IDirect3DDevice9::SetViewport            in 0 24           # D3DVIEWPORT9; boot mh
 *   rule IDirect3DDevice9::GetViewport            out 0 24          # boot mh
 *   rule IDirect3DDevice9::SetMaterial            in 0 68
 *   rule IDirect3DDevice9::GetMaterial            out 0 68
 *   rule IDirect3DDevice9::SetLight               in 1 104
 *   rule IDirect3DDevice9::GetLight               out 1 104
 *   rule IDirect3DDevice9::GetLightEnable         out 1 4
 *   rule IDirect3DDevice9::SetClipPlane           in 1 16
 *   rule IDirect3DDevice9::GetClipPlane           out 1 16
 *   rule IDirect3DDevice9::GetRenderState         out 1 4           # boot mh
 *   rule IDirect3DDevice9::CreateStateBlock       out 1 4
 *   rule IDirect3DDevice9::EndStateBlock          out 0 4
 *   rule IDirect3DDevice9::SetClipStatus          in 0 8
 *   rule IDirect3DDevice9::GetClipStatus          out 0 8
 *   rule IDirect3DDevice9::GetTexture             out 1 4
 *   rule IDirect3DDevice9::GetTextureStageState   out 2 4
 *   rule IDirect3DDevice9::GetSamplerState        out 2 4
 *   rule IDirect3DDevice9::ValidateDevice         out 0 4
 *   rule IDirect3DDevice9::SetPaletteEntries      in 1 1024
 *   rule IDirect3DDevice9::GetCurrentTexturePalette  out 0 4
 *   rule IDirect3DDevice9::SetScissorRect         in 0 16           # boot mh
 *   rule IDirect3DDevice9::GetScissorRect         out 0 16          # boot mh
 *   rule IDirect3DDevice9::DrawPrimitiveUP        up                # boot mh
 *   rule IDirect3DDevice9::DrawIndexedPrimitiveUP iup
 *   rule IDirect3DDevice9::CreateVertexDeclaration  decl 0; out 1 4   # boot mh
 *   rule IDirect3DDevice9::GetVertexDeclaration   out 0 4
 *   rule IDirect3DDevice9::GetFVF                 out 0 4
 *   rule IDirect3DDevice9::CreateVertexShader     shader 0; out 1 4 # boot mh
 *   rule IDirect3DDevice9::GetVertexShader        out 0 4
 *   rule IDirect3DDevice9::SetVertexShaderConstantF  in_vec 1 2 16  # boot mh
 *   rule IDirect3DDevice9::GetVertexShaderConstantF  out_vec 1 2 16
 *   rule IDirect3DDevice9::SetVertexShaderConstantI  in_vec 1 2 16
 *   rule IDirect3DDevice9::GetVertexShaderConstantI  out_vec 1 2 16
 *   rule IDirect3DDevice9::SetVertexShaderConstantB  in_vec 1 2 4
 *   rule IDirect3DDevice9::GetVertexShaderConstantB  out_vec 1 2 4
 *   rule IDirect3DDevice9::GetStreamSource        out 1 4; out 2 4; out 3 4
 *   rule IDirect3DDevice9::GetStreamSourceFreq    out 1 4
 *   rule IDirect3DDevice9::GetIndices             out 0 4
 *   rule IDirect3DDevice9::CreatePixelShader      shader 0; out 1 4 # boot mh
 *   rule IDirect3DDevice9::GetPixelShader         out 0 4
 *   rule IDirect3DDevice9::SetPixelShaderConstantF  in_vec 1 2 16   # boot mh
 *   rule IDirect3DDevice9::GetPixelShaderConstantF  out_vec 1 2 16
 *   rule IDirect3DDevice9::SetPixelShaderConstantI  in_vec 1 2 16
 *   rule IDirect3DDevice9::GetPixelShaderConstantI  out_vec 1 2 16
 *   rule IDirect3DDevice9::SetPixelShaderConstantB  in_vec 1 2 4
 *   rule IDirect3DDevice9::GetPixelShaderConstantB  out_vec 1 2 4
 *   rule IDirect3DDevice9::DrawRectPatch          in 1 16; in 2 28  # pNumSegs, D3DRECTPATCH_INFO
 *   rule IDirect3DDevice9::DrawTriPatch           in 1 12; in 2 16  # pNumSegs, D3DTRIPATCH_INFO
 *   rule IDirect3DDevice9::CreateQuery            out 1 4
 *   rule IDirect3DDevice9::GetRenderTargetData    -                 # the out-parameter is a surface the call fills
 *   rule IDirect3DDevice9::GetFrontBufferData     -                 # the same
 *   rule IDirect3DTexture9::GetLevelDesc          out 1 32          # D3DSURFACE_DESC; boot mh
 *   rule IDirect3DTexture9::GetSurfaceLevel       out 1 4; level 1  # boot mh
 *   rule IDirect3DTexture9::LockRect              out 1 8; in 2 16; lock_tex   # D3DLOCKED_RECT: Pitch, pBits
 *   rule IDirect3DTexture9::UnlockRect            unlock_tex
 *   rule IDirect3DTexture9::AddDirtyRect          in 0 16
 *   rule IDirect3DCubeTexture9::GetLevelDesc      out 1 32
 *   rule IDirect3DCubeTexture9::GetCubeMapSurface out 2 4
 *   rule IDirect3DCubeTexture9::AddDirtyRect      in 1 16
 *   rule IDirect3DVolumeTexture9::GetLevelDesc    out 1 28          # D3DVOLUME_DESC
 *   rule IDirect3DVolumeTexture9::AddDirtyBox     in 0 24
 *   rule IDirect3DSurface9::GetDesc               out 0 32
 *   rule IDirect3DSurface9::LockRect              out 0 8; in 1 16; lock_surf  # boot mh
 *   rule IDirect3DSurface9::UnlockRect            unlock_surf       # boot mh
 *   rule IDirect3DVertexBuffer9::Lock             out 2 4; lock_buf # the guest pointer; mh
 *   rule IDirect3DVertexBuffer9::Unlock           unlock_buf        # mh
 *   rule IDirect3DVertexBuffer9::GetDesc          out 0 24
 *   rule IDirect3DIndexBuffer9::Lock              out 2 4; lock_buf
 *   rule IDirect3DIndexBuffer9::Unlock            unlock_buf
 *   rule IDirect3DIndexBuffer9::GetDesc           out 0 20
 *   rule IDirect3DVertexDeclaration9::GetDeclaration  out 1 4; out_sized 0 1 8
 *   rule IDirect3DVertexShader9::GetFunction      out 1 4; out_sized 0 1 1   # boot mh
 *   rule IDirect3DPixelShader9::GetFunction       out 1 4; out_sized 0 1 1   # boot mh
 *   rule IDirect3DSwapChain9::Present             pre_rect 0; pre_rect 1; pre_rgn 3; frame
 *   rule IDirect3DSwapChain9::GetFrontBufferData  -                 # a surface the call fills
 *   rule IDirect3DSwapChain9::GetBackBuffer       out 2 4
 *   rule IDirect3DSwapChain9::GetRasterStatus     out 0 8
 *   rule IDirect3DSwapChain9::GetDisplayMode      out 0 16
 *   rule IDirect3DSwapChain9::GetPresentParameters  out 0 56
 *   rule ID3DXConstantTable::GetConstantDesc       out 1 48; out 2 4
 *   rule *::QueryInterface                        in 0 16; out 1 4  # the IID
 *   rule *::GetDevice                             out 0 4
 *   rule *::GetContainer                          in 0 16; out 1 4
 *   rule *::SetPrivateData                        in 0 16; in_vec 1 2 1
 *   rule *::GetPrivateData                        in 0 16; out 2 4; out_sized 1 2 1
 *   rule *::FreePrivateData                       in 0 16
 *
 *   entry 1 D3DXCreateTextureFromFileInMemoryEx 15   file 1 2; out 14 4; out 12 28; out 13 1024; file_desc 14   # boot mh
 *   entry 2 D3DXCreateTexture 8                      out 7 4; new_tex 7 1 2 5     # boot mh
 *   entry 3 D3DXFilterTexture 4                      in 1 1024        # boot mh
 *   entry 4 D3DXLoadSurfaceFromSurface 8             in 1 1024; in 2 16; in 4 1024; in 5 16   # mh
 *   entry 5 D3DXGetShaderConstantTable 2             shader 0; out 1 4   # boot mh
 *   entry 6 D3DXCreateCubeTextureFromFileInMemoryEx 14    file 1 2; out 13 4; out 11 28; out 12 1024; file_desc 13
 *   entry 7 D3DXCreateVolumeTextureFromFileInMemoryEx 16  file 1 2; out 15 4; out 13 28; out 14 1024; file_desc 15
 *   entry 8 D3DXCreateCubeTexture 7                  out 6 4; new_cube 6 1 4
 *   entry 9 D3DXCreateVolumeTexture 9                out 8 4; new_vol 8 1 2 6
 *   entry 10 D3DXSaveSurfaceToFileA 5                -
 *   entry 11 D3DXSaveTextureToFileA 4                -
 *   entry 12 Direct3DCreate9 0                       object_eax       # the SDK version the guest passed is not seen where this is recorded
 *
 * Anything with no line (AddRef, Release, every Set* of plain values, the draws with
 * no pointer, BeginScene, EndScene, ...) carries its args and eax and no blob. A
 * method the recording device flags as writing through a pointer ("out",
 * sa2_d3d9_method_flags) that has no line is a gap: the writer notes its first call.
 *
 * Locks. The recording device answers every Lock with a pointer into a store in the
 * guest heap and does nothing at Unlock, so the store is copied at the Unlock, with
 * a LOCK blob that says what it is. A texture keeps one lock per level, a surface
 * one, and a buffer a stack of them (an Unlock closes the last one opened). The
 * recording device ignores a Lock's pRect and Flags (runtime/sa2_d3d9.c, lock_rect):
 * pBits is always the level's origin, so a guest that locked a rectangle wrote the
 * rectangle's rows from pBits at the level's pitch, and a player places them at the
 * rectangle: row i of the rectangle is at pBits + i * pitch, and only its first
 * (right - left) pixels (block columns, for DXT) belong to the rectangle. rows is the
 * rectangle's height, or the level's, in pixel rows, or in 4x4 block rows for DXT1
 * to DXT5; the LOCKED blob is rows * pitch bytes from pBits. A buffer's LOCKED blob
 * is SizeToLock bytes from the pointer the Lock answered (0 means the rest of the
 * buffer from OffsetToLock). LOCK.bytes is always that length when the pitch and
 * rows (a buffer's size) are known: a LOCK that says 0 bytes then is an upload the
 * writer could not copy (its notes say why), and a reader reports it as lost.
 *
 * LOCKED is the store over the locked region at the Unlock: what the guest wrote
 * through the lock over whatever the store held, not a picture of the texture. The
 * recording device decodes nothing, so where it skipped work the store does not hold
 * the content: a texture loaded from a file has no store, and a lock of it reads
 * zeros; D3DXFilterTexture fills no level; a converting D3DXLoadSurfaceFromSurface
 * copies nothing. A lock with D3DLOCK_READONLY (0x10) in its flags is not an upload:
 * a player keeps its own pixels for it.
 *
 * What a player has to know that the recording device does, and D3D9 does not say:
 *   - IDirect3DDevice9::CreateTexture records a Levels of 0 as 1 level; the d3dx
 *     creators record 0 and D3DX_DEFAULT as the complete chain.
 *   - A texture loaded from a file holds no pixels in guest memory: the pixels are
 *     the FILE blob, whose header gives the file's own levels (sa2s_desc_file_texture:
 *     file_levels), which can be fewer than the texture's (levels).
 *   - D3DXFilterTexture and D3DXLoadSurfaceFromSurface produce no bytes in guest
 *     memory; a player that wants the levels or the copy makes them.
 *   - The device's implicit render target and depth buffer are guest objects too;
 *     CreateDevice's DESC names them.
 *   - GetBackBuffer answers the current render target, which is the implicit one
 *     only until the guest sets another (runtime/sa2_d3d9.c, m_GetBackBuffer): the
 *     object its OUT blob names is the one the guest then uses.
 *   - A vertex or index buffer holds each Unlock's LOCKED bytes at that LOCK's
 *     offset, a later Unlock's winning where two overlap. A write after an Unlock
 *     would be invisible to a stream, so the recorder hashes the range that the
 *     Unlock closing the buffer's last open lock copied, and hashes it again at
 *     every 64th FRAME until the buffer's next Lock or final Release; the counts
 *     file's "buffer_watch" says how many Unlocks set it, how many checks ran over
 *     how many bytes, how many buffers were watched at exit and how many changed,
 *     and a change is also a "buffer-changed" note. It cannot see a write undone
 *     before the next check, a write after the last check, a write the buffer's
 *     next Lock came before a check could see, or a write outside the range the
 *     last Unlock copied.
 *
 * What each audio call carries (version 2)
 * ----------------------------------------
 * Sound reaches no host device in the game process: the runtime's DirectSound and
 * XAudio2 devices record calls and derive their cursors from the virtual clock, and
 * the bytes the guest writes stay in guest memory. So the writer copies, at call
 * time, the bytes each buffer is given and the device's own description of each
 * buffer and voice after each call, and a player plays them by the device's own
 * clock (host/player/mixer.h). The table below is the writer's, one line per op, in
 * a grammar the tests parse:
 *   `audio <op> <NAME> <interface>::<method> <argc>  <aop>; <aop>; ...` (`-` for none)
 * The writer puts the same table in a version 2 stream's header ("audio": {"ops":
 * [[op, NAME, interface, method, argc, aops], ...]}), each aop in the same words, and
 * resolves each line to the device's (type, slot) by the device's own interface and
 * method names, so a slot transcribed wrong cannot attach an op to another method.
 * Every blob is read after the call returned, before the guest resumed, which no
 * other guest thread can come between: no audio method reaches the scheduler.
 *
 *   out A N        OUT blob of argument A: N bytes at the pointer the argument holds
 *                  (none when it is 0)
 *   in A B C       IN blob of argument A: args[B] * args[C] * 4 bytes (the level
 *                  matrix, float32 bit patterns copied as bytes; the writer computes
 *                  no floating point); none when args[B] or args[C] is over 64, a
 *                  matrix no device answers (the runtime refuses one over 12)
 *   pcm A B        PCM blob of argument A: args[B] bytes from the guest pointer args[A]
 *                  (none when either is 0): what the guest wrote through a Lock, at
 *                  the Unlock that closes it. The offset of the bytes in the buffer is
 *                  args[A] - the STATE's mem (sa2s_ds_state below): the runtime's Lock
 *                  hands back pointers into one backing store per buffer, mem, and
 *                  its Unlock copies nothing (runtime/sa2_dsound.c, m_Lock, m_Unlock)
 *   submit A       XAUDIO2_BUFFER at argument A: an IN blob of its 36 bytes, and a PCM
 *                  blob (arg A) of its AudioBytes bytes from its pAudioData, which the
 *                  guest's ring keeps valid from the submit until the voice consumes
 *                  the buffer (runtime/sa2_xaudio2.c, m_Submit)
 *   ds_state       STATE: sa2s_ds_state of the buffer called (this), after the call
 *   ds_new A       STATE: sa2s_ds_state of the buffer the call wrote through argument A
 *   xa_state       STATE: sa2s_xa_state of the voice called (this), after the call
 *   xa_new A       STATE: sa2s_xa_state of the voice the call wrote through argument A
 *   store_hash     HASH: 8 bytes, the 64-bit FNV-1a (offset basis 0xcbf29ce484222325,
 *                  prime 0x100000001b3, one byte at a time) of the buffer's whole store,
 *                  the STATE's mem_len bytes from its mem, read after the call, when the
 *                  store exists (mem is not 0) and the call left no Lock open on it;
 *                  none otherwise. Locks are counted per store, over every buffer that
 *                  shares it: each DS_LOCK record opens one, each DS_UNLOCK record
 *                  closes one (none below 0). A player that rebuilds each store from the
 *                  PCM blobs hashes its own copy at the same record, so a store the
 *                  stream does not hold -- PCM that is not the guest's bytes, an offset
 *                  that is not the guest's, a store two buffers share that the player
 *                  keeps apart, a guest write outside a Lock and its Unlock -- is a
 *                  difference it reports. One exception, a live session's stream only:
 *                  under SA2_REC_STORE_HASH_EVERY=n (host/sa2_present_rec.c; the
 *                  launcher's and the Android session's play give 64) the writer carries
 *                  the HASH on the first call that left no Lock open and on every n-th
 *                  after it, counted over every store, and on no other. A live player
 *                  holds each HASH it is given and counts a call without one as not
 *                  hashed (host/player/mixer.h, "The stores"); every other reader -- a
 *                  replay's player, sa2_audio_render, tools/s5/stream_dump.py -- holds
 *                  every such call to it, and reports one without it. Replays, the
 *                  selftest and every receipt's run carry every HASH
 *
 *   audio 1 DS_CREATE_BUFFER IDirectSound8::CreateSoundBuffer 3          out 1 4; ds_new 1
 *   audio 2 DS_DUPLICATE IDirectSound8::DuplicateSoundBuffer 2           out 1 4; ds_new 1
 *   audio 3 DS_COOPERATIVE IDirectSound8::SetCooperativeLevel 2          -
 *   audio 4 DS_LOCK IDirectSoundBuffer8::Lock 7           out 2 4; out 3 4; out 4 4; out 5 4; ds_state
 *   audio 5 DS_UNLOCK IDirectSoundBuffer8::Unlock 4       pcm 0 1; pcm 2 3; ds_state; store_hash
 *   audio 6 DS_PLAY IDirectSoundBuffer8::Play 3           ds_state
 *   audio 7 DS_STOP IDirectSoundBuffer8::Stop 0           ds_state
 *   audio 8 DS_SET_POSITION IDirectSoundBuffer8::SetCurrentPosition 1    ds_state
 *   audio 9 DS_SET_FORMAT IDirectSoundBuffer8::SetFormat 1               ds_state
 *   audio 10 DS_SET_VOLUME IDirectSoundBuffer8::SetVolume 1              ds_state
 *   audio 11 DS_SET_PAN IDirectSoundBuffer8::SetPan 1                    ds_state
 *   audio 12 DS_SET_FREQUENCY IDirectSoundBuffer8::SetFrequency 1        ds_state
 *   audio 13 DS_GET_POSITION IDirectSoundBuffer8::GetCurrentPosition 2   out 0 4; out 1 4; ds_state
 *   audio 14 DS_GET_STATUS IDirectSoundBuffer8::GetStatus 1              out 0 4; ds_state
 *   audio 15 DS_RELEASE IDirectSoundBuffer8::Release 0                   ds_state
 *   audio 16 XA_CREATE_MASTER IXAudio2::CreateMasteringVoice 6           out 0 4; xa_new 0
 *   audio 17 XA_CREATE_SOURCE IXAudio2::CreateSourceVoice 7              out 0 4; xa_new 0
 *   audio 18 XA_MASTER_DETAILS IXAudio2MasteringVoice::GetVoiceDetails 1 out 0 12; xa_state
 *   audio 19 XA_MASTER_DESTROY IXAudio2MasteringVoice::DestroyVoice 0    xa_state
 *   audio 20 XA_SET_VOLUME IXAudio2SourceVoice::SetVolume 2              xa_state            # the runtime refuses it
 *   audio 21 XA_SET_MATRIX IXAudio2SourceVoice::SetOutputMatrix 5        in 3 1 2; xa_state
 *   audio 22 XA_DESTROY IXAudio2SourceVoice::DestroyVoice 0              xa_state
 *   audio 23 XA_START IXAudio2SourceVoice::Start 2                       xa_state
 *   audio 24 XA_STOP IXAudio2SourceVoice::Stop 2                         xa_state
 *   audio 25 XA_SUBMIT IXAudio2SourceVoice::SubmitSourceBuffer 2         submit 0; xa_state
 *   audio 26 XA_FLUSH IXAudio2SourceVoice::FlushSourceBuffers 0          xa_state
 *   audio 27 XA_GET_STATE IXAudio2SourceVoice::GetState 1                out 0 16; xa_state
 *   audio 28 XA_SET_RATIO IXAudio2SourceVoice::SetFrequencyRatio 2       xa_state
 *
 * Every other audio method passes through the writer unrecorded: AddRef and
 * QueryInterface (a final Release's STATE says the buffer is gone), the Get* methods
 * that change nothing, the IDirectSound8 and IXAudio2 objects' own lifetimes.
 * DirectSoundCreate8 and CoCreateInstance are imports, not methods: the device and
 * the engine are named by the calls on them, never introduced.
 *
 * What a player has to know about audio that the devices do, and the APIs do not say:
 *   - A buffer's play cursor is a pure function of its STATE and the virtual clock
 *     (runtime/sa2_dsound.c, buf_play_cursor): pos = anchor_pos + floor((now -
 *     anchor_us) * avg_bytes_per_sec / 1e6); a looping buffer wraps, any other halts
 *     at its last block; the cursor reported is pos rounded down to block_align. The
 *     device moves the anchor only inside calls, and every call that moves one is an
 *     op above; a halt it settles inside GetCurrentPosition or GetStatus is the one
 *     the formula gives at that instant either way. SetFrequency sets avg_bytes_per_sec
 *     to the frequency times block_align, so the bytes play at the frequency's rate.
 *   - A duplicate shares its original's mem only when the original had one (a Lock
 *     made it) when it was duplicated; a duplicate locked first gets a store of its
 *     own. The STATE's mem says which store a buffer plays. A new buffer's volume and
 *     pan are 0 (full, centre), a duplicate's too: that is the runtime's model (its
 *     volume and pan are per buffer row, runtime/sa2_dsound.c g_scalar, and a new
 *     row's are 0), not DirectSound's, which starts a duplicate at its original's
 *     volume and pan. The registered boot and Metal Harbor runs make no Duplicate.
 *   - A store is the guest's memory: nothing stops the guest writing it outside a
 *     Lock and its Unlock, and a stream that copies bytes at the Unlock would not
 *     hold that write. So beside each Unlock's store_hash the writer watches every
 *     store whose last Lock has closed: it hashes the store again at every 64th FRAME
 *     and when the next Lock opens on it, and a store whose bytes moved is counted
 *     and noted (class "audio"). The counts file's "audio" "store_watch" says how many
 *     Unlocks set the watch, how many checks ran over how many bytes, how many stores
 *     were watched at exit and how many changed. A reader recomputes all but the last
 *     from the records: an Unlock that carries a HASH sets its store's watch, a Lock
 *     on a watched store is one check over its mem_len bytes and ends the watch, and
 *     each 64th FRAME is one check of every watched store. It cannot see a write
 *     undone before the next check or a write after the last one.
 *   - A source voice consumes its queue at samples_per_sec * ratio frames per virtual
 *     second (runtime/sa2_xaudio2.c, frames_after: the ratio's float32 bits as an
 *     exact integer fraction, floored): played = anchor_frame + frames_after(now -
 *     anchor_us) while it runs with buffers queued (played < queued_end), at most
 *     queued_end. Buffers are contiguous: a submit's frames end at the STATE's
 *     queued_end after it, AudioBytes / block_align after the end before it. A flush
 *     drops the buffers past the STATE's queued_end, which can make stream frames be
 *     submitted again.
 *   - The source format the image builds is six channels of float32 at 48 kHz
 *     (WAVE_FORMAT_EXTENSIBLE, mask 0x3F), mixed to the master by the level matrix
 *     SetOutputMatrix sets (the image calls it only when the master has two channels):
 *     level[dst * SourceChannels + src].
 *
 * Header JSON: "schema": "sa2-d3d9-stream/v1" ("sa2-d3d9-stream/v2" in version 2),
 * "writer" (the source path), "meta"
 * (a JSON string: the text the runner handed the writer in SA2_REC_META, a JSON
 * object naming the build, the replay, the commit and the hashes of what made the
 * run, or null), "object_base" and "object_stride" (the first block's: an object
 * of index below its count is at object_base + index * object_stride),
 * "object_blocks" (every block of the recording device's pool, runtime/sa2_d3d9.h,
 * as [base, first index, count]: an object of index i in a block is at
 * base + (i - first index) * object_stride; a reader of a stream without it takes
 * object_base and object_stride as the whole pool), "interfaces" (the recording device's
 * interface names, in type order), "methods" (one [type, slot, name, argc] per named
 * slot: the table a reader names calls and checks argc by), "rules" (one [type, slot,
 * ops] per method with a rule line above, the `*` lines applied), "entries" (one
 * [entry, name, argc, ops] per SA2S_D3DX entry) and "note_classes" (the writer's
 * note classes, in the counts file's order). Version 2 adds "audio": {"ops": one
 * [op, NAME, interface, method, argc, aops] per `audio` line, "ds_state_bytes",
 * "xa_state_bytes"}, and its note classes end with "audio".
 */
#ifndef SA2_STREAM_H
#define SA2_STREAM_H

#include <stdint.h>

#define SA2S_MAGIC   "SA2D3D9S"
#define SA2S_VERSION 1u
#define SA2S_SCHEMA  "sa2-d3d9-stream/v1"
/* A stream with audio (SA2S_AUDIO records): see "Versions" above. */
#define SA2S_VERSION_AUDIO 2u
#define SA2S_SCHEMA_AUDIO  "sa2-d3d9-stream/v2"

enum sa2s_kind {
    SA2S_CALL  = 1,
    SA2S_D3DX  = 2,
    SA2S_FRAME = 3,
    SA2S_NOTE  = 4,
    SA2S_END   = 5,
    SA2S_AUDIO = 6,     /* version 2 only */
};

enum sa2s_d3dx_entry {
    SA2S_D3DX_TEXTURE_FROM_MEMORY        = 1,   /* D3DXCreateTextureFromFileInMemoryEx */
    SA2S_D3DX_CREATE_TEXTURE             = 2,   /* D3DXCreateTexture */
    SA2S_D3DX_FILTER_TEXTURE             = 3,   /* D3DXFilterTexture */
    SA2S_D3DX_LOAD_SURFACE               = 4,   /* D3DXLoadSurfaceFromSurface */
    SA2S_D3DX_GET_CONSTANT_TABLE         = 5,   /* D3DXGetShaderConstantTable */
    SA2S_D3DX_CUBE_TEXTURE_FROM_MEMORY   = 6,   /* D3DXCreateCubeTextureFromFileInMemoryEx */
    SA2S_D3DX_VOLUME_TEXTURE_FROM_MEMORY = 7,   /* D3DXCreateVolumeTextureFromFileInMemoryEx */
    SA2S_D3DX_CREATE_CUBE_TEXTURE        = 8,   /* D3DXCreateCubeTexture */
    SA2S_D3DX_CREATE_VOLUME_TEXTURE      = 9,   /* D3DXCreateVolumeTexture */
    SA2S_D3DX_SAVE_SURFACE_TO_FILE       = 10,  /* D3DXSaveSurfaceToFileA */
    SA2S_D3DX_SAVE_TEXTURE_TO_FILE       = 11,  /* D3DXSaveTextureToFileA */
    SA2S_D3DX_DIRECT3D_CREATE9           = 12,  /* d3d9.dll!Direct3DCreate9 */
};

enum sa2s_blob_role {
    SA2S_BLOB_IN      = 1,  /* bytes an argument points at, as the guest passed them */
    SA2S_BLOB_OUT     = 2,  /* bytes the call wrote through an out-parameter */
    SA2S_BLOB_LOCKED  = 3,  /* a locked region's store at Unlock time */
    SA2S_BLOB_FILE    = 4,  /* a d3dx source blob (DDS, PNG, ...) */
    SA2S_BLOB_DESC    = 5,  /* the recording device's own description of a new object */
    SA2S_BLOB_LOCK    = 6,  /* sa2s_lock_info: the lock an Unlock closes */
    SA2S_BLOB_PCM     = 7,  /* audio bytes the guest gave a buffer (SA2S_AUDIO only) */
    SA2S_BLOB_STATE   = 8,  /* sa2s_ds_state or sa2s_xa_state (SA2S_AUDIO only) */
    SA2S_BLOB_HASH    = 9,  /* u64 FNV-1a of a DirectSound buffer's store (SA2S_AUDIO only) */
};

/* SA2S_AUDIO's api and op: the `audio` lines above, by number. */
enum sa2s_audio_api {
    SA2S_AU_DSOUND  = 1,    /* runtime/sa2_dsound.c: IDirectSound8, IDirectSoundBuffer8 */
    SA2S_AU_XAUDIO2 = 2,    /* runtime/sa2_xaudio2.c: IXAudio2 and its two voice interfaces */
};
enum sa2s_audio_op {
    SA2S_AU_DS_CREATE_BUFFER  = 1,
    SA2S_AU_DS_DUPLICATE      = 2,
    SA2S_AU_DS_COOPERATIVE    = 3,
    SA2S_AU_DS_LOCK           = 4,
    SA2S_AU_DS_UNLOCK         = 5,
    SA2S_AU_DS_PLAY           = 6,
    SA2S_AU_DS_STOP           = 7,
    SA2S_AU_DS_SET_POSITION   = 8,
    SA2S_AU_DS_SET_FORMAT     = 9,
    SA2S_AU_DS_SET_VOLUME     = 10,
    SA2S_AU_DS_SET_PAN        = 11,
    SA2S_AU_DS_SET_FREQUENCY  = 12,
    SA2S_AU_DS_GET_POSITION   = 13,
    SA2S_AU_DS_GET_STATUS     = 14,
    SA2S_AU_DS_RELEASE        = 15,
    SA2S_AU_XA_CREATE_MASTER  = 16,
    SA2S_AU_XA_CREATE_SOURCE  = 17,
    SA2S_AU_XA_MASTER_DETAILS = 18,
    SA2S_AU_XA_MASTER_DESTROY = 19,
    SA2S_AU_XA_SET_VOLUME     = 20,
    SA2S_AU_XA_SET_MATRIX     = 21,
    SA2S_AU_XA_DESTROY        = 22,
    SA2S_AU_XA_START          = 23,
    SA2S_AU_XA_STOP           = 24,
    SA2S_AU_XA_SUBMIT         = 25,
    SA2S_AU_XA_FLUSH          = 26,
    SA2S_AU_XA_GET_STATE      = 27,
    SA2S_AU_XA_SET_RATIO      = 28,
    SA2S_AU_OP_COUNT          = 29,    /* ops are 1 .. SA2S_AU_OP_COUNT - 1 */
};

#define SA2S_NO_ARG 0xFFFFu

typedef struct sa2s_record_head {
    uint32_t kind;
    uint32_t bytes;
    uint32_t seq;
} sa2s_record_head;

typedef struct sa2s_blob_head {
    uint16_t role;
    uint16_t arg;
    uint32_t len;
} sa2s_blob_head;

/* SA2S_BLOB_DESC of IDirect3D9::CreateDevice: the device and the two objects the
 * recording device creates inside the call (runtime/sa2_d3d9.c, m_CreateDevice), each
 * named only after its guest bytes were read back and found to be that object: the
 * 'D3D9' tag, the surface type (5) and its own pool index. 0 for one that is not
 * there (no automatic depth buffer) or did not read back. */
typedef struct sa2s_desc_device {
    uint32_t device;
    uint32_t render_target;     /* the implicit back buffer: pp BackBufferWidth x Height,
                                   BackBufferFormat */
    uint32_t depth_stencil;     /* the automatic depth buffer: AutoDepthStencilFormat */
} sa2s_desc_device;

/* SA2S_BLOB_DESC of the three d3dx loads from a file in memory: the recording
 * device's row for the call (runtime/sa2_d3d9.h, sa2_d3d9_texsrc), whose `object`
 * is the texture the call wrote to its out-pointer. */
typedef struct sa2s_desc_file_texture {
    uint32_t object;            /* the texture; 0 when the call refused */
    uint32_t container;         /* SA2_TEXC_*: 1 DDS, 2 PNG, 3 BMP, 4 JPEG, ... */
    uint32_t kind;              /* SA2_TEXK_*: 0 2D, 1 cube, 2 volume */
    uint32_t width, height, depth, levels, format;               /* as created */
    uint32_t file_width, file_height, file_depth, file_levels, file_format;  /* the header's */
    uint32_t levels_from, format_from, dims_from;                /* SA2_TEXP_* */
    uint32_t png_bit_depth, png_colour_type, png_interlace, png_trns;
    uint32_t chain_bytes, level0_bytes;
} sa2s_desc_file_texture;

/* SA2S_BLOB_LOCK: the Lock the Unlock carrying it closes, as the writer sized the
 * SA2S_BLOB_LOCKED beside it. The object is the one the Lock was called on; a level
 * surface's texture and level are in `container` and `level`. */
typedef struct sa2s_lock_info {
    uint32_t object;            /* the surface, texture or buffer the Lock was called on */
    uint32_t container;         /* a level surface's texture, else 0 */
    uint32_t level;             /* the texture level locked, 0 for a plain surface */
    uint32_t bits;              /* the guest address the Lock answered */
    uint32_t pitch;             /* bytes per row (per 4x4 block row for DXT); 0 for a buffer */
    uint32_t rows;              /* rows copied (block rows for DXT); 1 for a buffer */
    uint32_t bytes;             /* the SA2S_BLOB_LOCKED blob's len */
    uint32_t format;            /* the level's D3DFORMAT; 0 for a buffer */
    uint32_t width, height;     /* the level's size in pixels; 0 for a buffer */
    uint32_t has_rect;          /* 1 when the Lock named a rectangle */
    int32_t  rect[4];           /* that rectangle (left, top, right, bottom), else the level */
    uint32_t flags;             /* the Lock's Flags */
    uint32_t offset;            /* a buffer Lock's OffsetToLock, else 0 */
} sa2s_lock_info;

/* SA2S_BLOB_STATE of a DirectSound buffer: the runtime's own record of it after the
 * call (runtime/sa2_dsound.h, sa2_ds_buffer), field for field. A u64 is its two
 * words, low first. 84 bytes. */
typedef struct sa2s_ds_state {
    uint32_t buffer;            /* the buffer's guest address */
    uint32_t index;             /* its row in the runtime's buffer table */
    uint32_t flags;             /* DSBUFFERDESC.dwFlags as the guest passed them */
    uint32_t bytes;             /* dwBufferBytes; a primary's, the runtime's named length */
    uint32_t primary;           /* DSBCAPS_PRIMARYBUFFER: the device's mix, not a source */
    uint32_t format_tag, channels, samples_per_sec;
    uint32_t avg_bytes_per_sec; /* the cursor's rate: the frequency x block_align */
    uint32_t block_align, bits_per_sample;
    uint32_t playing, looping, halted, released;
    uint32_t anchor_pos;        /* the cursor's byte offset at anchor_us */
    uint32_t anchor_us_lo, anchor_us_hi;
    uint32_t mem, mem_len;      /* the backing store Lock points into; 0 until a Lock */
    uint32_t write_lead;        /* the write cursor's fixed lead, in bytes */
} sa2s_ds_state;

/* SA2S_BLOB_STATE of an XAudio2 voice: the runtime's own record of it after the call
 * (runtime/sa2_xaudio2.h, sa2_xa2_voice), field for field. 176 bytes. */
typedef struct sa2s_xa_state {
    uint32_t voice;             /* the voice's guest address */
    uint32_t index;             /* its row in the runtime's voice table */
    uint32_t kind;              /* 1 mastering, 2 source (SA2_XA2_VOICE_*) */
    uint32_t engine;            /* the IXAudio2 it was made on */
    uint32_t flags;             /* the creation Flags */
    uint32_t destroyed;
    uint32_t details_flags, input_channels, input_sample_rate;   /* a mastering voice's */
    uint32_t format_tag, channels, samples_per_sec, avg_bytes_per_sec, block_align,
             bits_per_sample, valid_bits, channel_mask;           /* a source voice's */
    uint32_t max_ratio_bits, ratio_bits;   /* float32 bit patterns */
    uint32_t output;            /* the voice SetOutputMatrix named; 0 when none */
    uint32_t matrix_src, matrix_dst;
    uint32_t matrix[12];        /* float32 bit patterns: level[dst * matrix_src + src] */
    uint32_t running, queued;
    uint32_t anchor_us_lo, anchor_us_hi;
    uint32_t anchor_frame_lo, anchor_frame_hi;
    uint32_t played_lo, played_hi;          /* frames consumed (GetState's SamplesPlayed) */
    uint32_t queued_end_lo, queued_end_hi;  /* the stream frame the queue ends at */
} sa2s_xa_state;

#endif
