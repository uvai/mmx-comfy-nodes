# mmx-comfy-nodes

ComfyUI custom nodes that move prompt + LoRA preset selection into the canvas, backed by one
preset store shared with the mmx runner and mirrored to the NAS so presets survive a re-rent.

Installed by `additional_params.sh` in `uvai/base-image` next to `ComfyUI-MiniMaxRefPack`
(`git clone https://github.com/uvai/mmx-comfy-nodes` into `custom_nodes`). Manual install:

```
cd /ComfyUI/custom_nodes && git clone https://github.com/uvai/mmx-comfy-nodes && restart ComfyUI
```

## Preset store

`/workspace/mmx/presets.json` (env `MMX_PRESETS` overrides; the pack directory when `/workspace`
does not exist):

```json
{"version": 1, "updated": 1788600000.0,
 "presets": [{"name": "walk", "prompt": "<Picture 1> walks toward <Picture 9>.",
              "loras": [{"name": "H3_Motion_BoosterV2.safetensors", "strength": 0.7}],
              "notes": "", "created": 1788600000.0, "updated": 1788600000.0}]}
```

**NAS mirror.** On load the pack pulls `/volume1/subgenula/mmx/presets.json` from the pyramid
share over the instance's existing NAS ssh path (`/root/.ssh/mmx_nas_key`, `NAS_DEST` from the
Vast template, SOCKS via the userspace tailscaled) and merges it with the local file: union by
name, newer `updated` wins. Every save (node or route) pushes the merged file back. A locked
share or an unreachable NAS is a logged no-op, never an error in the graph. Env knobs:
`MMX_NAS`, `MMX_NAS_KEY`, `MMX_NAS_PROXY` (`none` to disable the SOCKS hop), `MMX_NAS_SHARE`,
`MMX_PRESETS_MIRROR=0` to turn the mirror off.

**Import the studio's prompt pool.** Either a "Save project" export or the raw `mmx.pool`
localStorage value (`copy(localStorage.getItem('mmx.pool'))` in DevTools):

```
python3 tools/import_studio_pool.py pool.json                      # writes the local store (+ --push to mirror)
python3 tools/import_studio_pool.py pool.json --server http://127.0.0.1:8188 --overwrite   # via the running ComfyUI
```

Pool items become presets (`text` → `prompt`, `loras` carried over); existing names are kept
unless `--overwrite`.

## Nodes (category `mmx`)

| node | inputs | outputs |
|---|---|---|
| **MMX Preset** | model, clip, preset (dropdown from the store), strength_scale | model + clip with the preset's LoRAs applied in order (core `LoraLoader`, strength × scale on model and clip), prompt STRING, preset_name STRING |
| **MMX Preset Save** | name, prompt STRING (type or connect), overwrite, notes, up to 3 × (lora dropdown, strength) | preset_name; OUTPUT_NODE — runs every time it is queued, writes the store and pushes to the NAS |
| **MMX Sequence** | model, clip, index INT, strength_scale, preset_1..preset_8 | model, clip, prompt, preset_name, slot, count — empty slots skipped, index wraps over the filled ones |
| **MMX Save Frame (fixed name)** | image, filename | writes the LAST image of the batch as `input/<filename>.png`, overwriting (OUTPUT_NODE) |
| **MMX Load Chain Frame** | fallback IMAGE, filename, use_fallback | the saved frame when it exists, else the fallback; re-executes when the file changes |
| **MMX First Frame Check** | images (VAE Decode batch), reference IMAGE (slot-9 image or previous last frame), threshold_db (24) | psnr FLOAT, ssim FLOAT, passed BOOLEAN, comparison IMAGE (reference \| frame 0 \| abs-diff heat-map, labelled); OUTPUT_NODE — numbers + PASS/FAIL shown in the node, strip previewed |
| **MMX Chain Gate** | images, passed BOOLEAN, filename (`mmx_chain_last.png`), stop_queue | path STRING, written BOOLEAN. passed → writes the LAST frame to `input/<filename>` (overwrite). not passed → writes `<stem>_REJECTED.png`, leaves the previous good frame untouched, clears the pending queue and raises `MMX Chain Gate: first-frame check FAILED …` |
| **MMX Library Image** | file (dropdown over `/workspace/mmx/library/{Subjects,VideoRef,Sets}/**`) | image IMAGE (mp4: first frame), filename STRING (the file copied into `ComfyUI/input` as `Subjects__j__j1.jpg`, for the References Manager), path STRING |
| **MMX References Builder** | image_1..9, video_1..3, audio_1 (STRING, optional), use_soundtrack | references_json STRING (exact `MiniMaxH3ReferencePack` schema, compacted in slot order), picture_map STRING (`slot 9 -> <Picture 5>`, `video 1 -> <Video 1> (+ soundtrack <Audio 1>)`) |

Selecting a preset changes the prompt and the LoRA stack together: the node re-executes whenever
the preset's content changes (`IS_CHANGED` hashes name, prompt, loras, updated). A LoRA file
named by a preset but missing from `models/loras` fails with that exact filename.

**Web extension.** MMX Preset / Sequence / Preset Save get a `↻ Refresh presets` button that
pulls from the NAS and re-reads the store without a page reload (dropdowns update in place; the
Save node's LoRA dropdowns re-list `models/loras`). MMX Preset and MMX Sequence show the selected
preset's LoRA list, notes and the start of the prompt in the node body; Sequence marks the slot
the current index resolves to.

HTTP routes on ComfyUI's port: `GET /mmx/presets`, `POST /mmx/presets/refresh`,
`POST /mmx/presets/import[?overwrite=1]`, `POST /mmx/presets/save`, `POST /mmx/presets/delete`,
`GET /mmx/presets/status`, `GET /mmx/loras`, `GET /mmx/library`, `POST /mmx/library/refresh[?sync=1]`,
`GET /mmx/library/thumb?path=<rel>[&w=320]`, `GET /mmx/library/sync`.

## First-frame verification (the check + gate pair)

`MMX First Frame Check` measures frame 0 of the decoded batch against the intended first frame
**in the guide's geometry**: the reference is cover-cropped (centre) to the frame's aspect and
lanczos-resized to the frame's size exactly as `MiniMaxH3AddGuide` does
(`comfy.utils.common_upscale(…, "lanczos", "center")` — the node calls the same function inside
ComfyUI). PSNR is over RGB in 0..1 (identical images report 100 dB), SSIM is Wang et al. on luma
with an 11×11 σ=1.5 window. `passed = psnr >= threshold_db`. The comparison strip is written to
the temp folder and previewed in the node; the abs-diff panel saturates at a mean per-pixel
difference of 0.25.

Calibration from the live joins (768×448, guide continuation): a correct reference measures
29–33 dB, adjacent frames of one clip 22–28 dB, an unrelated image < 15 dB. 24 dB is the default
threshold.

`MMX Chain Gate` takes that `passed` flag: only a passing segment overwrites `input/<filename>`
(the frame `MMX Load Chain Frame` / `LoadImage` reads for the next segment). A failing segment
writes `<stem>_REJECTED.png` for inspection, keeps the previous good frame byte-identical, clears
the pending queue (`stop_queue`, default on — the remaining segments would otherwise run from the
stale frame) and raises with the paths in the message; the node body shows the verdict either
way (a websocket event carries it before the exception aborts the node's normal ui output).

## Library (MMX Library Image)

`/workspace/mmx/library` (env `MMX_LIBRARY` overrides) mirrors `Subjects`, `VideoRef` and `Sets`
from `/volume1/subgenula` at boot: `additional_params.sh` section 3c copies
`tools/mmx_library_sync.sh` to `/root/mmx_library_sync.sh` and runs it detached with `--wait`
(it waits up to 10 min for the nas_worker to bring up tailscale + `/root/.ssh/mmx_nas_key`, then
`rsync -rt --delete --max-size 1500m` per folder over the same SOCKS ssh path). A locked share,
a missing key or an unreachable NAS is a logged skip (`/workspace/mmx_library_sync.log`), exit 0;
the node keeps working on whatever is mirrored. Folders absent on the share (`Sets`) are skipped.

In the node: the dropdown lists every image / video under the mirror as `Folder/sub/file`; the
`search` box filters it live; the thumbnail (first frame for videos) is drawn in the node;
`↻ Refresh library` re-scans without a page reload; `⇣ Mirror from NAS` re-runs the sync script
and re-scans when it finishes (progress from the log tail on the button). A file picked after a
Refresh queues fine: the node validates the path itself instead of the enum ComfyUI cached at
load. Executing copies the file into `ComfyUI/input` under its flat name (skipped when an
identical copy is there), so the References Manager can address it by filename.

## Example: `examples/chain_check.json`

`chain_3seg` plus the library + verification nodes (`tools/build_example.py` writes both):

1. **MMX Library Image ×2** (identity → `<Picture 1>`, the first-frame image → slot 9) →
   **MMX References Builder** → `references_json` into the **References Manager** (the RefPack's
   hidden widget accepts the link); `picture_map` is appended to the sequence's prompt with a
   `StringConcatenate` so the LLM sees which picture is the first frame.
2. The slot-9 image is also the fallback of **MMX Load Chain Frame** → **MiniMaxH3AddGuide** at
   frame 0: segment 1 opens exactly on it, later segments on the gated frame.
3. **MMX First Frame Check** compares the decoded frame 0 with the very image the guide anchored
   (the Load Chain Frame output) → **MMX Chain Gate** writes `mmx_chain_last.png` only on PASS.
4. Queue N runs as before; a failed join stops the chain at that segment with a red node and
   `mmx_chain_last_REJECTED.png` in `input/`.

Pick your own library files in the two dropdowns (the example carries placeholder paths).

## Verification

- `python3 tests/test_pack.py` — 52 checks with ComfyUI stubbed (check / gate / library /
  references need torch + PIL, the mp4 case ffmpeg).
- `python3 tools/live_check.py --server http://HOST:8188 --frame s1_first.png --good-ref
  first_frame.png --bad-ref identity.png --lib-image Subjects/j/identity.png --lib-video
  VideoRef/seg1.mp4` — 16 checks against a running ComfyUI: correct reference passes (31.6 dB on
  the recorded segment-1 frame vs its slot-9 image) and the gate writes the chain frame; a wrong
  reference (identity photo, 14.0 dB) fails, the gate writes `_REJECTED.png`, leaves the good
  frame byte-identical, and the two filler prompts queued behind it are dropped; the library →
  builder → References Manager path resolves image_1 / image_2 / video_1 from the copied files.
- Both were run 2026-09-07 on a CPU ComfyUI (master, frontend from the package) with the RefPack,
  rgthree, KJNodes, VHS and ComfyMath installed; the frontend loads `chain_check.json` with no
  missing types, the library node filters / thumbnails / refreshes / mirrors, and the check and
  gate nodes show PASS (green) / FAIL (red) with the strip in the node body. The full R2V graph
  itself (sampling + AddGuide) was not re-run there — no GPU — so re-run `tools/live_check.py`
  and the example on the next rented box.

## Chained segments on the canvas (the sequence pattern)

`examples/chain_3seg.json` (UI format) / `examples/chain_3seg_api.json` (API format), built from
the studio's R2V template by `tools/build_example.py`:

1. **MMX Sequence** with three presets in slots 1–3; its `index` comes from a **PrimitiveInt with
   `control_after_generate = increment`**. Its model/clip feed the acc + turbo LoRA chain and its
   prompt is the RefPack's `direction`.
2. **MMX Load Chain Frame** (`mmx_chain_last.png`, fallback = the slot-9 image loaded by
   `LoadImage`) → **MiniMaxH3AddGuide** at `frame_idx 0` between `MiniMaxH3ReferenceToVideo` and
   `BasicGuider`: the segment opens exactly on the previous segment's last frame (segment 1 on
   the slot-9 image). The slot-9 image also stays in the references (`<Picture 2>`), which the
   runner A/B found holds framing better than guide-only.
3. **ImageFromBatch(batch_index −1)** → **MMX Save Frame** writes the last frame under the fixed
   name, so the next queued run picks it up.
4. Queue **N runs** (Queue → "Queue N" / batch count 3): run 1 uses index 0 (slot 1), run 2 index 1,
   run 3 index 2. Each run's video lands in `output/mmx_chain/seg_*.mp4`; concatenate them with
   the runner or `ffmpeg -f concat`.

To start a new chain, tick `use_fallback` on MMX Load Chain Frame for the first run (or delete
`input/mmx_chain_last.png`) and reset the index to 0. The runner-side equivalent is the
`continuation: "guide"` option in the studio; both use the same guide node.

Put `identity.png` (`<Picture 1>`) and `first_frame.png` (slot 9) into ComfyUI's `input`
folder before queueing, or point the two LoadImage nodes at your own files.

## Verified 2026-09-06 (live, RTX PRO 6000, ComfyUI 0.34, frontend 1.49.6)

- Pack loads with no errors (5 nodes registered, routes under `/mmx/`, extension served).
- Studio pool import → 3 presets; `MMX Preset Save` queued as a prompt → 4th preset; Refresh
  re-reads the store and `MMX Preset`'s dropdown lists it; the NAS copy holds all four.
- Presets survive a runner restart; deleting `/workspace/mmx/presets.json` and pressing Refresh
  restored all four from the NAS (`pull merged 4`).
- `examples/chain_3seg.json` loads in the frontend with no missing node types; queued three times
  with index 0/1/2 (768×448, 3 s each, auto-prompt on) it selected establishing / close up / walk,
  applied their LoRAs (0.6 / — / 0.8) and rewrote `mmx_chain_last.png` after each run. Joins:

  | pair | PSNR |
  |---|---|
  | segment 1 → 2 (last frame vs first frame) | 33.3 dB |
  | segment 2 → 3 | 33.5 dB |
  | adjacent frames inside one segment (reference) | 27.7 dB |
  | segment 1 last vs segment 3 first (non-adjacent control) | 16.8 dB |
  | segment 1 frame 0 vs the slot-9 image (cover-cropped) | 31.3 dB |

  A join is closer than two consecutive frames of the same clip, i.e. no visible cut.

## Runner integration

`mmx_runner.py` (≥ 2.4) reads the same store: a segment may say `"preset": "walk"` instead of
inline `prompt` + `loras` (inline fields override the preset's when present), and `GET /presets`
lists the store. Batch runs from the studio and canvas runs therefore share one library.

## Tests

```
python3 tests/test_pack.py     # ComfyUI stubbed; NAS mirror through a fake ssh (27 checks; chain
                               # helper checks need torch + PIL and are skipped without them)
```
