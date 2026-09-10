# mmx-comfy-nodes

ComfyUI custom nodes that move prompt + LoRA preset selection into the canvas, backed by one
preset store shared with the mmx runner and mirrored to the NAS so presets survive a re-rent.
0.3 adds the **deck** build for the daily graph: MMX Deck (prompt constructor + preset manager),
MMX LoRA Stack, Library → References Manager injection, an MMX References Manager drop-in, and a
skippable First Frame Check, the Manager's `first_frame` input — see "The deck build" below,
`examples/deck.json` and `examples/deck_chain.json`.

Installed by `additional_params.sh` in `uvai/base-image` next to `ComfyUI-MiniMaxRefPack`
(`git clone https://github.com/uvai/mmx-comfy-nodes` into `custom_nodes`, and on EVERY boot
`fetch --depth 1 origin main` + `reset --hard origin/main`, logging the commit as
`[additional_params] mmx-comfy-nodes at <sha> <date> <subject>`). Manual install:

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
CLIPLoader, MODEL out into the turbo LoRA #158, which is untouched); **MiniMaxH3ReferencePack #185
becomes MMX References Manager #185** (drop-in: same id, widgets and links) and gets its
`first_frame` input from **MMX Load Chain Frame #404**, whose fallback is the IMAGE of **MMX
Library Image #402** (the first-frame picture) — so the first frame is the LAST picture at run
time without an inject step, and **MMX First Frame Check #301** (with its `enabled` toggle)
compares against that very image (`reference` ← #404; the manual LoadImage #300 is gone);
**MMX Deck #400** is added unwired; **MMX Library Image #401** (identity, `Inject` → Picture 1)
and #402 ship with `file` EMPTY — pick yours in the node; an unset file is only an error when the
node is queued. `deck.json` keeps `use_fallback` on (one segment, always the library image).
`examples/deck_chain.json` is the same graph with `use_fallback` off and **MMX Chain Gate #406**
(images ← VAE Decode, passed / psnr / ssim ← the check, `strict` off) writing `mmx_chain_last.png`
after every run, so each queued run opens on the previous run's last frame; a failed check keeps
its `_REJECTED.png` evidence and shows the verdict in the gate, and the chain goes on (turn
`strict` on to stop the queue instead). Both deck graphs have no frame-0 guide, so their First
Frame Check ships at 12 dB — see the two regimes under "First-frame verification".

**MMX Deck** — one DOM panel; its state (prompt, preset name, the five LoRA rows, the chosen
target nodes) lives in four hidden widgets, so it rides in `widgets_values`.

- *Tag buttons* insert `<Picture 1..9>`, `<Subject 1..3>`, `<Video 1..3>`, `<Audio 1>` at the
  caret. Picture / Video / Audio buttons are enabled only for slots the target References
  Manager currently holds (read from its `references_json`, tags assigned by the RefPack's rule),
  so the numbering always matches what the model will see; the line under them says which
  Manager and how many of each. A linked `first_frame` input counts as the last picture (amber
  button, `<Picture N> = the first_frame input`). Subject buttons are always on.
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
- *Layout*: the panel is fluid — width 100 % of the node, no pixel widths, chips and rows wrap
  in the available width, each LoRA row is `on / dropdown / strength / ✎` on one grid line with
  the triggers under it. The node's minimum size is measured from the content (a ResizeObserver
  on the panel: on load, on every resize, when chips or the report reflow) and fed back as the
  DOM widget's min height, so the resize handle cannot clip it and the node grows to fit; the
  panel's top padding is the part of the title bar that would cover it (measured; the title
  height when it cannot be measured), so the toolbar is never under the title. Works in the
  classic canvas and in the frontend's Nodes 2.0 (Vue) mode; usable from 400 px up.

**MMX LoRA Stack** — model, clip in/out; five rows of `on_N` / `lora_N` (dropdown over
`models/loras`) / `strength_N` (0–2) as plain widgets, so injection is just setting widget
values (no rgthree internals). Enabled rows are applied in order with core `LoraLoader` (same
strength on model and clip); the body shows the effective stack live and after execution;
`↻ Refresh LoRAs` re-scans the folder into all five dropdowns. Validation is the node's own, so a
LoRA copied in after the page loaded queues fine.

**Refreshing the dropdowns.** `MMXLoRAStack.INPUT_TYPES`, `MMXPresetSave.INPUT_TYPES`,
`/mmx/loras` and `MMXLibraryImage.INPUT_TYPES` all scan at call time (the loras folder with
ComfyUI's per-folder cache dropped first, the library mirror bypassing its 20 s scan cache), so
`/object_info` — the frontend's **R** (refresh node definitions) — always returns the folder as it
is now. On the frontend the LoRA list has one owner (`window.mmx.loras`, event
`mmx-loras-changed`): R feeds it from the fresh definition through the `refreshComboInNodes`
extension hook (which also re-reads the library mirror and re-applies each Library node's search
filter), the Stack's `↻ Refresh LoRAs` and the Deck's `↻` feed it from `/mmx/loras`, and every
Stack row, Deck row and Preset Save dropdown repopulates from it — so the three paths always show
the identical list.

**Library → Manager injection** (MMX Library Image): a `slot` widget (Picture 1–9 / Video 1–3 /
Audio 1) and `⇢ Inject into Manager`: the file is copied into `ComfyUI/input`
(`POST /mmx/library/inject`) and written into the Manager's `references_json` at that slot; the
Manager's slot UI re-renders with the tile. The RefPack list is compact, so "Picture 5" with one
image present lands as `<Picture 2>` — the node says so ("asked for Picture 5 … it is
<Picture 2>"). The first frame needs no inject: it reaches the Manager through `first_frame`. A video injected into a Picture slot contributes its first frame
(`<flat>__frame0.png`); into a Video slot the mp4 itself (soundtrack on). `✕ Clear slot` removes
that entry (later ones move up); `⇢ Inject all (group)` injects every Library node inside the same
canvas group (or all of them when the node is in no group) in slot order and reports each. The
Manager used is the one remembered by the Deck's target dropdown (else the first in the graph).

**LoRA registry + automatic triggers** (0.4). `/workspace/mmx/loras.json` (env
`MMX_LORAS_REGISTRY`), mirrored to `/volume1/subgenula/mmx/loras.json` with the preset rules
(union by filename, newer wins, tombstones). One entry per LoRA file:
`{triggers: [...], phrases: [...], default_strength, notes, auto}`. Files without an entry are
pre-filled at load (after the NAS pull) and on `POST /mmx/registry/refresh` from the safetensors
header — `modelspec.trigger_phrase`, else the `ss_tag_frequency` tags present in ≥ 90 % of the
training images (max 5) — as `auto: true, updated: 0`, so an edit on any box or the NAS copy
always wins and a deleted entry is never re-added. Presets keep storing only (name, strength,
on); triggers come from the registry, so they stay consistent across presets.

- **MMX LoRA Stack** gains STRING outputs `triggers` (the enabled rows' trigger words, row
  order, case-insensitive dedupe, joined by `, `) and `phrases` (their phrases, same rule); the
  body lists them under each row and ends with `→ triggers out: …`. It re-executes when the
  registry file changes.
- **MMX Prompt Affix** (`deck.json` #403) sits between the Manager's `prompt` (185:18) and its
  consumers (R2V #184 `prompt`, Display #186): inputs `prompt`, `prefix`, `suffix`, `mode`
  (prepend | append, default prepend) and `auto_triggers` wired from the Stack's `triggers`.
  Output `[prefix\n] triggers, prompt [\nsuffix]` (append: `prompt, triggers`); the body shows the
  final text. No enabled row with triggers → the prompt passes through unchanged.
- **Deck**: each LoRA row shows its triggers (`auto` marked) and has a ✎ that opens the registry
  editor (triggers, phrases, default strength, notes, "from metadata" re-reads the file) writing
  through `/mmx/registry/set`; picking a LoRA in a row switches it on and takes the registry's
  default strength while the strength is still at 1. Under the rows: "trigger prefix for the
  enabled rows: …", and the Send report repeats the prefix that the Affix will apply.

Routes: `GET /mmx/registry`, `POST /mmx/registry/refresh|set|delete`, `GET /mmx/registry/metadata?name=`,
`POST /mmx/registry/for_rows`.

**MMX References Manager** — a drop-in for `MiniMaxH3ReferencePack` (node 185): identical inputs
and all 20 outputs, the RefPack's own widget (our extension hands the RefPack's
`beforeRegisterNodeDef` a nodeData wearing its name). The RefPack keeps its reference list in
`node._mmrpRefs` and re-reads `references_json` only inside its `onConfigure` wrapper, so a plain
widget write from outside never re-rendered its tiles. Now it does, on the stock node too: the
`mmx.manager` extension wraps the DOM widget's `options.setValue` (the `value` accessor is an own,
non-configurable property on the current frontend; a data-property fallback and a slow poll cover
other frontends) and queues one re-render through that wrapper. In the Python subclass a blank
`openrouter_api_key` falls back to `OPENROUTER_API_KEY`, then `LLM_KEY`, then `OPENROUTER_KEY`
(the Vast template's name), from this process's env or PID 1's. Both deck builds use MMX
References Manager as #185 (the `window.mmx` API works on the stock node too).

**`first_frame`** (MMX References Manager only): an optional IMAGE input, appended after every
widget so the RefPack's positional `widgets_values` are untouched. Not connected → exactly the
stock node. Connected → at run time the image is written into `ComfyUI/input` and used as the
**last picture slot**: Picture N with N = the widget's pictures + 1 (the 9th is replaced when the
list is already full), every other slot kept. The filename is unique per queue: the frontend
puts the entry into the exported `references_json` as `mmx_ff_<node>_<queue>.png` (the workflow's
own widget value is untouched), the node writes the tensor under that name (an API client that
sends no such entry gets `mmx_ff_<content hash>.png` appended instead) and reports it in `debug`
("first_frame: … used as <Picture N>" + the effective references_json). `IS_CHANGED` folds the
frame's hash in, so a new frame always re-runs. The Deck's tag buttons count the slot while the
input is linked. Wire **MMX Load Chain Frame** into it (its fallback = the first-frame Library
Image) and the same output into the First Frame Check's `reference`: segment 1 anchors on the
library picture, every later one on the Chain Gate's frame, and the check compares against
exactly the image the Manager used.

`window.mmx` (web/mmx_api.js): `findManagers / findStacks / defaultManager / defaultStack`,
`getReferences / setReferences(node, list) / tagsOf(node) / setReferenceSlot(node, "Picture 3", ref)
/ clearReferenceSlot`, `getDirection / setDirection`, `getStack / setStack(node, rows)`,
`injectLibrary(libNode, manager)`, `jumpTo / highlight`, `refreshManager`, `firstFrameLink(node) /
effectiveReferences(node) / withFirstFrame(list, file)` (the list as the run will see it).

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
| **MMX LoRA Stack** | model, clip, 5 × (on, lora, strength 0–2) | model, clip, stack STRING (the effective rows), triggers STRING, phrases STRING (registry words of the enabled rows) |
| **MMX Prompt Affix** | prompt (link), prefix, suffix, mode (prepend/append), auto_triggers (wire the Stack's triggers) | prompt STRING with the triggers prepended/appended; final text shown in the node |
| **MMX References Manager** | = MiniMaxH3ReferencePack + optional `first_frame` IMAGE (written into `input/` at run time as the LAST picture; not connected = stock behaviour) | = MiniMaxH3ReferencePack (20 outputs) + key fallback + re-render on external write; `debug` names the first-frame file and slot |
| **MMX Sequence** | model, clip, index INT, strength_scale, preset_1..preset_8 | model, clip, prompt, preset_name, slot, count — empty slots skipped, index wraps over the filled ones |
| **MMX Save Frame (fixed name)** | image, filename | writes the LAST image of the batch as `input/<filename>.png`, overwriting (OUTPUT_NODE) |
| **MMX Load Chain Frame** | fallback IMAGE, filename, use_fallback ("start new chain"), use_frame (`latest` or any history frame; call-time list, lazily validated) | image, from_file. The chosen chain frame when it exists, else the fallback; the frame it opened on is shown in the node (text + thumbnail); re-executes when that file changes. Buttons: `↻ Refresh frames`, `✕ Clear chain history` |
| **MMX First Frame Check** | images (VAE Decode batch), threshold_db (node default 24 = guided; the unguided deck examples ship 12), reference IMAGE (optional: slot-9 image or previous last frame), enabled | psnr FLOAT, ssim FLOAT, passed BOOLEAN, comparison IMAGE (reference \| frame 0 \| abs-diff heat-map, labelled); OUTPUT_NODE — numbers + PASS/FAIL shown in the node, strip previewed; no reference / disabled → skipped (passed=True, psnr=-1, comparison=frame 0) |
| **MMX Chain Gate** | images, passed BOOLEAN, filename (`mmx_chain_last.png`), stop_queue, strict (default on; `deck_chain.json` off), psnr / ssim FLOAT (optional, wire the check's) | path STRING, written BOOLEAN. passed → writes the LAST frame to `input/<filename>` (overwrite). not passed, strict → writes `<stem>_REJECTED.png`, leaves the previous good frame untouched, clears the pending queue and raises `MMX Chain Gate: first-frame check FAILED …`; not passed, lenient → writes `<filename>` anyway AND the `_REJECTED.png` copy, no raise. Every written chain frame also gets a numbered copy + thumbnail in `input/mmx_chain_history/<stem>/` (`mmx_chain_<segment>_<timestamp>.png`). Body shows the check's verdict (PSNR / SSIM), red / green |
| **MMX Library Image** | file (dropdown over `/workspace/mmx/library/{Subjects,VideoRef,Sets}/**`; first entry empty = nothing picked, an error only on queue), slot (`(none)` / Picture 1–9 / Video 1–3 / Audio 1 for Inject; the enum is the module's `SLOTS`, what the examples save) | image IMAGE (mp4: first frame), filename STRING (the file copied into `ComfyUI/input` as `Subjects__j__j1.jpg`, for the References Manager), path STRING |
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

**Two regimes for the threshold.** *Guided* — `MiniMaxH3AddGuide` pins the reference at frame 0
(`chain_check.json`, the studio chain): from the live joins (768×448) a correct reference
measures 29–33 dB, adjacent frames of one clip 22–28 dB, an unrelated image < 15 dB; **~24 dB**
is the node's default and the right threshold there. *Unguided* — the first frame only reaches
the model as the last picture with the "…is the absolute first frame" phrase (`deck.json`,
`deck_chain.json`: the Manager's `first_frame` input, no AddGuide): the model re-renders the
frame rather than copying it, so expect **~12–18 dB** for a faithful open and treat < 10 dB as
a wrong or ignored reference. The unguided examples therefore ship `threshold_db = 12`; raise it
once you have your own numbers from the check's body (it prints PSNR / SSIM every run).

`MMX Chain Gate` takes that `passed` flag and has two modes (`strict`). **Strict** (the node's
default, the pre-0.5 behaviour, `chain_check.json`): only a passing segment overwrites
`input/<filename>` (the frame `MMX Load Chain Frame` / `LoadImage` reads for the next segment).
A failing segment writes `<stem>_REJECTED.png` for inspection, keeps the previous good frame
byte-identical, clears the pending queue (`stop_queue`, default on — the remaining segments would
otherwise run from the stale frame) and raises with the paths in the message. **Lenient**
(`strict` off, what `deck_chain.json` ships): the frame is ALWAYS written under `<filename>` and
nothing raises, so the chain continues; a failed check additionally writes the `_REJECTED.png`
copy alongside so the evidence is kept. In both modes wire the check's `psnr` / `ssim` into the
gate's optional inputs and its body shows the verdict ("check FAIL  PSNR 13.20 dB  SSIM 0.610 …",
red on a fail, green on a pass); the node body shows the verdict either
way (a websocket event carries it before the exception aborts the node's normal ui output).

**Chain history.** Whenever the gate writes `input/<filename>` it also copies it to
`input/mmx_chain_history/<stem>/<label>_<segment>_<timestamp>.png` (`mmx_chain_last.png` →
`mmx_chain_001_20260910-094132.png`, segment numbers count up per chain) with a 192 px
`_thumb.jpg` beside it. `MMX Load Chain Frame` gains `use_frame`: `latest` (default) opens on the
fixed-name file as before; any history entry opens on that segment instead — the dropdown lists
the copies newest first (rebuilt from `GET /mmx/chain/history?filename=…` on load, on
`↻ Refresh frames`, and after every gate run), the selected entry's thumbnail is drawn in the
node, and the node body says which frame the run actually opened on ("chain frame: history
segment 1 (…)", "chain frame: latest (…)", "fallback image — no chain frame yet"). A history
frame picked after the page loaded queues fine (the node validates it itself); a frame that
has since been deleted fails the queue with its name. `use_fallback` ("start new chain") still
wins over everything and keeps the history; `✕ Clear chain history` (`POST /mmx/chain/clear`)
deletes the numbered copies and thumbnails of that chain — never `<filename>` itself — and
resets `use_frame` to `latest`. Segment numbering restarts at 001 afterwards.

## Library (MMX Library Image)

`/workspace/mmx/library` (env `MMX_LIBRARY` overrides) mirrors `Subjects`, `Sets` and `VideoRef`
(each recursively) from `/volume1/subgenula` at boot: `additional_params.sh` section 3c copies
`tools/mmx_library_sync.sh` to `/root/mmx_library_sync.sh` and runs it detached with `--wait`:
it polls every 60 s for up to 2 h (`MMX_LIBRARY_WAIT_SECS` / `MMX_LIBRARY_WAIT_TRIES`) until the
key exists, the NAS answers AND the share is unlocked — the share is normally still locked when
the instance comes up — then `rsync -rt --delete --max-size 1500m` per folder over the same SOCKS
ssh path. The log (`/workspace/mmx_library_sync.log`) gets one line per state change plus a
heartbeat every 10 attempts ("waiting: share … is LOCKED … (attempt 4/120)"), a "gave up after N
attempt(s)" line at the end of the window, and "done: N files". The node's `⇣ Mirror from NAS`
button re-runs the script as a single attempt. While the library is empty the node body shows
the last mirror log line, so it is visible in the graph why there is nothing to pick (locked
share, no key yet, unreachable NAS). Folders absent on the share are skipped.

**Validation is lazy.** The `file` dropdown's first entry is the empty string — "nothing picked
yet" — and that is what the examples ship, so a workflow loads with zero validation errors on a
clean instance whose mirror is still empty; the node body says so ("library empty — last mirror
log: …", or "no file selected — N files in the library"). Only queueing a node whose file is
unset (or not in the mirror) fails, with that same text; an unwired Library node is never
validated at all. `slot` is checked against the same `SLOTS` list the examples are built from.
The node's own widgets (`file`, `slot`) stay first in the body so `widgets_values` keeps its
two-entry shape on every frontend; the search box, buttons and result come after.

In the node: the dropdown lists every image / video under the mirror as `Folder/sub/file`; the
`search` box filters it live; the thumbnail (first frame for videos) is drawn in the node;
`↻ Refresh library` re-scans without a page reload; `⇣ Mirror from NAS` re-runs the sync script
and re-scans when it finishes (progress from the log tail on the button). Every rebuild of the
list (load, Refresh, R, a finished mirror) happens in place on every Library node and never
forces a re-pick: an unset value stays unset, a pick that is in the list stays selected, and a
saved pick the mirror does not hold yet is kept visible as an extra entry and becomes a normal
one the moment the mirror brings the file. A file picked after a Refresh queues fine: the node
validates the path itself instead of the enum ComfyUI cached at load. Executing copies the file
into `ComfyUI/input` under its flat name (skipped when an identical copy is there), so the
References Manager can address it by filename.

## Example: `examples/chain_check.json`

`chain_3seg` plus the library + verification nodes (`tools/build_example.py` writes both):

1. **MMX Library Image ×2** (identity → `<Picture 1>`, the first-frame image → slot 9 through
   the Builder; the deck builds do this through the Manager's `first_frame` input instead) →
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
- `python3 tools/ui_check_deck.py --server http://HOST:8188 [--shots DIR] [--empty-server http://HOST2:8188] [--vue-too] [--loras-dir …/models/loras --library-dir …/mmx/library]` — 95 frontend checks
  (playwright chromium) on `examples/deck.json`: no missing types; the stack took over #137's
  links with the turbo LoRA untouched; tag buttons follow the Manager's slots; Inject / Clear
  slot / Inject all land in the Manager's slot UI (tiles) and are reported honestly; Send lands
  in the Manager's direction (widget + textarea) and the Stack's rows (widgets + body), with
  jump links, highlight and Undo; `app.graphToPrompt()` and `graph.serialize()` carry the same
  direction / references_json / rows; preset Save / Load / Update / Delete round-trip with the
  on flags; phrase chip insert (ellipsis rule), add, delete; the MMX References Manager drop-in
  re-renders on a plain widget write; the check skips cleanly with no reference and when
  disabled, and still PASSes with one; ↻ Refresh; no extension errors. 0.4 adds: the Affix is
  wired Manager → Affix → R2V/Display with the Stack's `triggers` in the API JSON; editing a row's
  triggers from the Deck lands in the registry, the Stack body and the Send report; disabling the
  row drops them; a registry refresh keeps the user entry; executing the Affix prepends /
  appends / passes through. With `--loras-dir` / `--library-dir` it drops a file into each,
  calls `app.refreshComboInNodes()` (R) and asserts the new names appear in `/object_info`, in
  all five Stack rows, all five Deck rows and the Library dropdown, that the node's Refresh, the
  Deck's ↻ and R yield the identical list, and that the names disappear again after removal + R.
  0.5 adds: the Deck **layout** at node widths 400 / 700 / 1000 px and after a workflow reload
  (the panel fills the node, the node is at least its content, the strength input of every LoRA
  row is visible beside the dropdown, chips wrap without scrolling, the toolbar sits below the
  title; screenshots `deck_layout_*` with `--shots`; `--vue-too` repeats it in Nodes 2.0 mode);
  the **first_frame** slot: with the example loaded, `<Picture 1>` is the linked first frame, an
  injected picture pushes it to `<Picture 3>`, unlinking drops it; a two-run graph (Library →
  Load Chain Frame → Manager first_frame + check reference, Chain Gate on the frames): run 1
  anchors the library fallback (the written `mmx_ff_*` file IS that picture, `from_file` False),
  run 2 the gate's frame (`from_file` True, PSNR 100 against the check's reference), the exported
  API `references_json` and the executed prompt carry the override filename as the last picture,
  the widget's own value does not; with `--empty-server` (a ComfyUI whose mirror is empty)
  `deck.json` and `deck_chain.json` load as shipped with no validation toast, no console error
  about the pack, `file = ''` / the shipped slot inside their dropdowns, the body showing the
  mirror log, and queueing reports the unset file only as the lazy custom check on the wired
  #402 (never `value_not_in_list`, nothing on the unwired #401); the **lenient gate**: with
  `strict` off and a failing check (threshold 100 against different frames) the run succeeds,
  the chain frame is written anyway, the `_REJECTED.png` copy is byte-identical alongside, the
  gate body shows "check FAIL  PSNR … SSIM …" and the node turns red; **chain history**: the
  three runs left segments 001–003 with thumbnails, the loader's `use_frame` lists them, picking
  segment 001 shows its thumbnail and a fourth run opens on exactly that frame (the Manager's
  written first frame is run 1's, not the latest), `✕ Clear chain history` empties the list and
  keeps the fixed-name frame.
- 2026-09-10: `tests/test_pack.py` 115 (119 with the RefPack) and `ui_check_deck.py` 95/95 on the
  CPU ComfyUI 0.34 / frontend 1.51.9, classic + Nodes 2.0, populated and empty-library servers.
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
python3 tests/test_pack.py     # ComfyUI stubbed; NAS mirror through a fake ssh (115 checks; torch /
                               # PIL / ffmpeg / RefPack dependent ones are skipped without them)
python3 tools/ui_check_deck.py --server http://127.0.0.1:8188 --empty-server http://127.0.0.1:8189 --vue-too --shots /tmp/shots
                               # 95 playwright checks on deck.json + deck_chain.json (see Verification)
```
