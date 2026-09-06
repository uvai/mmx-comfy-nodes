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
`GET /mmx/presets/status`, `GET /mmx/loras`.

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
