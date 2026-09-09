# mmx-comfy-nodes

ComfyUI custom nodes that move prompt + LoRA preset selection into the canvas, backed by one
preset store shared with the mmx runner and mirrored to the NAS so presets survive a re-rent.
0.3 adds the **deck** build for the daily graph: MMX Deck (prompt constructor + preset manager),
MMX LoRA Stack, Library → References Manager injection, an MMX References Manager drop-in, and a
skippable First Frame Check — see "The deck build" below and `examples/deck.json`.

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
              "loras": [{"name": "H3_Motion_BoosterV2.safetensors", "strength": 0.7},
                        {"name": "MiniMax-H3-Ref2VA-Acc-8Step.safetensors", "strength": 0.5, "on": false}],
              "notes": "", "created": 1788600000.0, "updated": 1788600000.0}],
 "deleted": [{"name": "old preset", "at": 1788500000.0}]}
```

Up to **5** LoRA rows per preset (0.3; was 3). A row may carry `"on": false` (the Deck's
on/off switch): MMX Preset / Sequence and the runner skip it, the Deck's Load restores it as an
off row. `deleted` holds tombstones: a delete used to be undone by the mirror's pull-before-push
(the NAS copy still had the preset); now a tombstone newer than the preset wins on both sides,
and a preset saved after the tombstone clears it. Older readers ignore both additions.

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

## The deck build (0.3)

`examples/deck.json` is the user's daily graph (`base_v10_check.json` = v10 + First Frame Check)
with exactly these changes, made by `tools/build_deck.py`: the **Power Lora Loader (rgthree) #137
is replaced by MMX LoRA Stack #137** (same links: model from ModelPreviewOverrideKJ, clip from
CLIPLoader, MODEL out into the turbo LoRA #158, which is untouched), **MMX Deck #400** is added
unwired, **MMX Library Image #401 / #402** (slots Picture 1 / Picture 9) are added unwired, and
**MMX First Frame Check #301** gets its `enabled` toggle. No chaining automation is in the graph.

**MMX Deck** — one DOM panel; its state (prompt, preset name, the five LoRA rows, the chosen
target nodes) lives in four hidden widgets, so it rides in `widgets_values`.

- *Tag buttons* insert `<Picture 1..9>`, `<Subject 1..3>`, `<Video 1..3>`, `<Audio 1>` at the
  caret. Picture / Video / Audio buttons are enabled only for slots the target References
  Manager currently holds (read from its `references_json`, tags assigned by the RefPack's rule),
  so the numbering always matches what the model will see; the line under them says which
  Manager and how many of each. Subject buttons are always on.
- *Phrase chips*, grouped, user-editable (`+ phrase`, `✎ edit` → ✕ on each chip). Stored in
  `/workspace/mmx/phrases.json` (env `MMX_PHRASES`), mirrored to `/volume1/subgenula/mmx/phrases.json`
  exactly like presets.json (same transport, union by (group, text), newer wins, tombstones).
  Seeded with the two studio constraints — "…is the absolute first frame of the video." and
  "Construct the character reference using …" — plus the camera / lighting fragments of the
  studio's default pool and three pacing phrases. A phrase with an ellipsis at either end is
  inserted without it, so `<Picture 9>` + the first chip reads
  `<Picture 9> is the absolute first frame of the video.`
- *Presets*: dropdown + name + Load / Save / Update / Delete / ↻. Load fills the editor AND the
  five LoRA rows; Save writes a new preset (refuses an existing name), Update overwrites, Delete
  asks first. All through `/mmx/presets/*` (NAS push after each). Nothing is saved on queue.
- *Send to graph* writes the editor into the Manager's `direction` and the five rows into the
  MMX LoRA Stack, highlights both nodes for 2.5 s, prints a landed report with `[jump]` links,
  and arms *Undo* (restores both). *Pull from graph* reads both back into the panel. Targets are
  two dropdowns over the graph's Managers / Stacks, remembered per browser (`localStorage`
  `mmx.targets`) and in the node's `targets_json`.

**MMX LoRA Stack** — model, clip in/out; five rows of `on_N` / `lora_N` (dropdown over
`models/loras`) / `strength_N` (0–2) as plain widgets, so injection is just setting widget
values (no rgthree internals). Enabled rows are applied in order with core `LoraLoader` (same
strength on model and clip); the body shows the effective stack live and after execution;
`↻ Refresh LoRAs` re-scans the folder into all five dropdowns. Validation is the node's own, so a
LoRA copied in after the page loaded queues fine.

**Library → Manager injection** (MMX Library Image): a `slot` widget (Picture 1–9 / Video 1–3 /
Audio 1) and `⇢ Inject into Manager`: the file is copied into `ComfyUI/input`
(`POST /mmx/library/inject`) and written into the Manager's `references_json` at that slot; the
Manager's slot UI re-renders with the tile. The RefPack list is compact, so "Picture 9" with one
image present lands as `<Picture 2>` — the node says so ("asked for Picture 9 … it is
<Picture 2>"). A video injected into a Picture slot contributes its first frame
(`<flat>__frame0.png`); into a Video slot the mp4 itself (soundtrack on). `✕ Clear slot` removes
that entry (later ones move up); `⇢ Inject all (group)` injects every Library node inside the same
canvas group (or all of them when the node is in no group) in slot order and reports each. The
Manager used is the one remembered by the Deck's target dropdown (else the first in the graph).

**MMX References Manager** — a drop-in for `MiniMaxH3ReferencePack` (node 185): identical inputs
and all 20 outputs, the RefPack's own widget (our extension hands the RefPack's
`beforeRegisterNodeDef` a nodeData wearing its name). The RefPack keeps its reference list in
`node._mmrpRefs` and re-reads `references_json` only inside its `onConfigure` wrapper, so a plain
widget write from outside never re-rendered its tiles. Now it does, on the stock node too: the
`mmx.manager` extension wraps the DOM widget's `options.setValue` (the `value` accessor is an own,
non-configurable property on the current frontend; a data-property fallback and a slow poll cover
other frontends) and queues one re-render through that wrapper. In the Python subclass a blank
`openrouter_api_key` falls back to `OPENROUTER_API_KEY`, then `LLM_KEY`, then `OPENROUTER_KEY`
(the Vast template's name), from this process's env or PID 1's. `deck.json` keeps the stock node
185 (the API works on both); swap it for MMX References Manager when you want the key fallback
inside the node.

`window.mmx` (web/mmx_api.js): `findManagers / findStacks / defaultManager / defaultStack`,
`getReferences / setReferences(node, list) / tagsOf(node) / setReferenceSlot(node, "Picture 3", ref)
/ clearReferenceSlot`, `getDirection / setDirection`, `getStack / setStack(node, rows)`,
`injectLibrary(libNode, manager)`, `jumpTo / highlight`, `refreshManager`.

**First Frame Check**: `reference` is optional and there is an `enabled` toggle. Disabled, or no
reference connected (the first segment): no error, `passed=True`, `psnr=ssim=-1`,
`comparison` = the frame-0 image, body says `skipped (first segment): …` in a neutral colour.
Chain Gate is unchanged.

Routes added: `GET /mmx/phrases`, `POST /mmx/phrases/refresh|add|delete|replace`,
`POST /mmx/library/inject`, `GET /mmx/loras?refresh=1`, `GET /mmx/status` (key present,
RefPack found, Manager registered, store paths).

## Nodes (category `mmx`)

| node | inputs | outputs |
|---|---|---|
| **MMX Preset** | model, clip, preset (dropdown from the store), strength_scale | model + clip with the preset's LoRAs applied in order (core `LoraLoader`, strength × scale on model and clip), prompt STRING, preset_name STRING |
| **MMX Preset Save** | name, prompt STRING (type or connect), overwrite, notes, up to 5 × (lora dropdown, strength) | preset_name; OUTPUT_NODE — runs every time it is queued, writes the store and pushes to the NAS |
| **MMX Deck** | prompt, preset, loras_json, targets_json (hidden; the panel edits them) | prompt STRING, loras_json STRING — executing it is a passthrough; Send / Save are buttons |
| **MMX LoRA Stack** | model, clip, 5 × (on, lora, strength 0–2) | model, clip, stack STRING (the effective rows) |
| **MMX References Manager** | = MiniMaxH3ReferencePack | = MiniMaxH3ReferencePack (20 outputs) + key fallback + re-render on external write |
| **MMX Sequence** | model, clip, index INT, strength_scale, preset_1..preset_8 | model, clip, prompt, preset_name, slot, count — empty slots skipped, index wraps over the filled ones |
| **MMX Save Frame (fixed name)** | image, filename | writes the LAST image of the batch as `input/<filename>.png`, overwriting (OUTPUT_NODE) |
| **MMX Load Chain Frame** | fallback IMAGE, filename, use_fallback | the saved frame when it exists, else the fallback; re-executes when the file changes |
| **MMX First Frame Check** | images (VAE Decode batch), threshold_db (24), reference IMAGE (optional: slot-9 image or previous last frame), enabled | psnr FLOAT, ssim FLOAT, passed BOOLEAN, comparison IMAGE (reference \| frame 0 \| abs-diff heat-map, labelled); OUTPUT_NODE — numbers + PASS/FAIL shown in the node, strip previewed; no reference / disabled → skipped (passed=True, psnr=-1, comparison=frame 0) |
| **MMX Chain Gate** | images, passed BOOLEAN, filename (`mmx_chain_last.png`), stop_queue | path STRING, written BOOLEAN. passed → writes the LAST frame to `input/<filename>` (overwrite). not passed → writes `<stem>_REJECTED.png`, leaves the previous good frame untouched, clears the pending queue and raises `MMX Chain Gate: first-frame check FAILED …` |
| **MMX Library Image** | file (dropdown over `/workspace/mmx/library/{Subjects,VideoRef,Sets}/**`), slot (Picture 1–9 / Video 1–3 / Audio 1 for Inject) | image IMAGE (mp4: first frame), filename STRING (the file copied into `ComfyUI/input` as `Subjects__j__j1.jpg`, for the References Manager), path STRING |
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
`GET /mmx/presets/status`, `GET /mmx/loras[?refresh=1]`, `GET /mmx/library`, `POST /mmx/library/refresh[?sync=1]`,
`GET /mmx/library/thumb?path=<rel>[&w=320]`, `GET /mmx/library/sync`, `POST /mmx/library/inject`,
`GET /mmx/phrases`, `POST /mmx/phrases/refresh|add|delete|replace`, `GET /mmx/status`.

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

- `python3 tests/test_pack.py` — 76 checks with ComfyUI stubbed (check / gate / library /
  references need torch + PIL, the mp4 case ffmpeg; the References Manager checks need the
  RefPack importable: `MMX_REFPACK_DIR=/path/to/ComfyUI-MiniMaxRefPack`). Covers the 5-row
  presets, delete tombstones surviving a NAS pull, the phrase store (seed, add / delete / bulk
  replace, mirror round trip, re-seed after a local loss keeping deletions), the LoRA Stack,
  the Deck passthrough, the Manager subclass + key fallback, the check's skip path and inject.
- `python3 tools/ui_check_deck.py --server http://HOST:8188 [--shots DIR]` — 42 frontend checks
  (playwright chromium) on `examples/deck.json`: no missing types; the stack took over #137's
  links with the turbo LoRA untouched; tag buttons follow the Manager's slots; Inject / Clear
  slot / Inject all land in the Manager's slot UI (tiles) and are reported honestly; Send lands
  in the Manager's direction (widget + textarea) and the Stack's rows (widgets + body), with
  jump links, highlight and Undo; `app.graphToPrompt()` and `graph.serialize()` carry the same
  direction / references_json / rows; preset Save / Load / Update / Delete round-trip with the
  on flags; phrase chip insert (ellipsis rule), add, delete; the MMX References Manager drop-in
  re-renders on a plain widget write; the check skips cleanly with no reference and when
  disabled, and still PASSes with one; ↻ Refresh; no extension errors.
- 2026-09-09: both run green on a CPU ComfyUI 0.34 / frontend 1.51.9 with the RefPack, rgthree,
  KJNodes, VHS and ComfyMath installed (no H3 weights, so the model loaders show the frontend's
  "6 errors" — environmental). Not run on a GPU box: the LoRA Stack actually loading a LoRA
  into the H3 model, the Manager's OpenRouter call through the key fallback, and a full
  sampling run of `deck.json` are unverified live.
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
python3 tests/test_pack.py     # ComfyUI stubbed; NAS mirror through a fake ssh (76 checks; torch /
                               # PIL / ffmpeg / RefPack dependent ones are skipped without them)
python3 tools/ui_check_deck.py --server http://127.0.0.1:8188   # 42 playwright checks on deck.json
```
