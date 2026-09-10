#!/usr/bin/env python3
"""Unit tests for mmx-comfy-nodes without ComfyUI: `folder_paths`, `nodes` and `server` are
stubbed; the NAS mirror is exercised through a fake ssh (MMX_NAS_PROXY=none + a PATH shim) that
stores the remote file in a temp dir. The check / gate / library / references nodes need torch +
PIL (+ ffmpeg for the video case) and are skipped without them.

    python3 tests/test_pack.py
"""
import importlib, json, os, shutil, subprocess, sys, tempfile, time, types

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TMP = tempfile.mkdtemp(prefix="mmx_nodes_test_")
results = []

def check(name, cond, detail=""):
    results.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f"  — {detail}" if detail and not cond else ""), flush=True)

# ── stubs for the ComfyUI modules the nodes import ─────────────────────────────
LORAS = ["H3_Motion_BoosterV2.safetensors", "MiniMax-H3-Ref2VA-Acc-8Step.safetensors", "side_fuck_h3_000000750.safetensors"]
fp = types.ModuleType("folder_paths")
fp.get_filename_list = lambda kind: list(LORAS) if kind == "loras" else []
fp.get_input_directory = lambda: os.path.join(TMP, "input")
fp.get_temp_directory = lambda: os.path.join(TMP, "temp")
fp.get_full_path = lambda kind, name: os.path.join(TMP, "loras", name) if os.path.isfile(os.path.join(TMP, "loras", name)) else None
fp.filename_list_cache = {}
os.makedirs(fp.get_input_directory(), exist_ok=True)
sys.modules["folder_paths"] = fp
nd = types.ModuleType("nodes")
class LoraLoader:
    def load_lora(self, model, clip, name, sm, sc):
        return (model + [(name, round(sm, 4))], clip + [(name, round(sc, 4))])
nd.LoraLoader = LoraLoader
sys.modules["nodes"] = nd
# fake ssh: "remote" = TMP/nas; supports the exact commands store.py sends
SHIM = os.path.join(TMP, "bin"); os.makedirs(SHIM, exist_ok=True)
open(os.path.join(SHIM, "ssh"), "w").write(f'''#!/usr/bin/env python3
import os, sys
NAS = {TMP!r} + "/nas"
cmd = sys.argv[-1]
share_dir = NAS + "/volume1/subgenula"
os.makedirs(share_dir, exist_ok=True)
if os.environ.get("NAS_LOCKED") == "1":
    print("MMX_LOCKED"); sys.exit(0)
import re
m = re.search(r"/volume1/subgenula/(mmx/[a-z]+\\.json)", cmd)
remote = NAS + "/volume1/subgenula/" + (m.group(1) if m else "mmx/presets.json")
if "cat >" in cmd:                         # push
    os.makedirs(os.path.dirname(remote), exist_ok=True)
    open(remote + ".tmp", "wb").write(sys.stdin.buffer.read()); os.replace(remote + ".tmp", remote); print("MMX_PUSHED"); sys.exit(0)
if os.path.isfile(remote):                 # pull
    sys.stdout.write("MMX_FILE\\n"); sys.stdout.flush(); sys.stdout.buffer.write(open(remote, "rb").read()); sys.exit(0)
print("MMX_NOFILE")
''')
os.chmod(os.path.join(SHIM, "ssh"), 0o755)
os.environ["PATH"] = SHIM + os.pathsep + os.environ["PATH"]
os.environ["MMX_NAS_PROXY"] = "none"
os.environ["MMX_NAS_KEY"] = os.path.abspath(__file__)          # any existing file = "configured"
os.environ["MMX_PRESETS"] = os.path.join(TMP, "presets.json")
os.environ["MMX_PHRASES"] = os.path.join(TMP, "phrases.json")
os.environ["MMX_LORAS_REGISTRY"] = os.path.join(TMP, "loras.json")
os.environ["MMX_NAS_SHARE"] = "/volume1/subgenula"
os.environ["MMX_LIBRARY"] = os.path.join(TMP, "library")
os.environ["MMX_LIBRARY_THUMBS"] = os.path.join(TMP, "library_thumbs")
os.environ["MMX_LIBRARY_SYNC"] = os.path.join(TMP, "fake_sync.sh")
os.environ["MMX_LIBRARY_SYNC_LOG"] = os.path.join(TMP, "sync.log")

sys.path.insert(0, os.path.dirname(ROOT))
pack = importlib.import_module(os.path.basename(ROOT).replace("-", "_")) if False else None
sys.path.insert(0, ROOT)
store = importlib.import_module("mmx_presets.store")
nodes = importlib.import_module("mmx_presets.nodes")
NONE = nodes.NONE

def main():
    st = store.get_store()
    check("store path from MMX_PRESETS", st.path == os.environ["MMX_PRESETS"], st.path)
    check("empty store -> no names", st.names() == [] and nodes._preset_names() == [NONE])

    # save + schema
    p = st.save({"name": "walk", "prompt": "<Picture 1> walks in", "loras": [{"name": LORAS[0], "strength": 0.7}, LORAS[2]], "notes": "n"})
    check("save normalizes loras (string -> default strength)", p["loras"] == [{"name": LORAS[0], "strength": 0.7}, {"name": LORAS[2], "strength": 0.85}] and p["created"] > 0)
    d = json.load(open(st.path))
    check("file schema {version, updated, presets[]}", d["version"] == 1 and d["updated"] > 0 and d["presets"][0]["name"] == "walk" and set(d["presets"][0]) == {"name", "prompt", "loras", "notes", "created", "updated"}, json.dumps(d)[:200])
    try:
        st.save({"name": "walk", "prompt": "x"}, overwrite=False); check("overwrite=False refuses an existing name", False)
    except store.PresetError as e:
        check("overwrite=False refuses an existing name", "exists" in str(e))
    try:
        st.save({"name": "bad", "loras": [{"name": c} for c in "abcdef"]}); check("max 5 loras", False)
    except store.PresetError:
        check("max 5 loras", True)
    p5 = st.save({"name": "five", "prompt": "x", "loras": [{"name": f"l{i}.safetensors", "strength": 0.5, "on": i != 3} for i in range(1, 6)]})
    check("5 lora rows kept; on=false stored only where set", len(p5["loras"]) == 5 and p5["loras"][2].get("on") is False and "on" not in p5["loras"][0], str(p5["loras"]))
    st.delete("five")

    # studio pool import (both shapes)
    pool = [{"id": "p1", "name": "establishing", "text": "<Subject 1> wide shot", "loras": [{"name": LORAS[1], "strength": 0.5}]},
            {"id": "p2", "name": "walk", "text": "SHOULD NOT REPLACE", "loras": []}]
    n = st.import_pool(pool)
    check("import bare pool list: new names added, existing kept", n == 1 and st.get("establishing")["prompt"] == "<Subject 1> wide shot" and st.get("walk")["prompt"] == "<Picture 1> walks in")
    n = st.import_pool({"v": 2, "pool": pool, "lanes": []}, overwrite=True)
    check("import project export with overwrite", n == 2 and st.get("walk")["prompt"] == "SHOULD NOT REPLACE")
    st.save({"name": "walk", "prompt": "<Picture 1> walks in", "loras": [{"name": LORAS[0], "strength": 0.7}]})

    # MMXPreset: preset change updates prompt AND lora stack together
    node = nodes.MMXPreset()
    out = node.run([], [], "walk", 1.0)
    m, c, prompt, name = out["result"]
    check("MMXPreset applies loras in order to model and clip, returns prompt + name",
          m == [(LORAS[0], 0.7)] and c == [(LORAS[0], 0.7)] and prompt == "<Picture 1> walks in" and name == "walk", str(out))
    out2 = node.run([], [], "establishing", 2.0)
    check("preset change -> different prompt and lora stack (strength_scale applied)",
          out2["result"][2] == "<Subject 1> wide shot" and out2["result"][0] == [(LORAS[1], 1.0)] and out2["result"][3] == "establishing")
    check("IS_CHANGED differs between presets and after an edit",
          nodes.MMXPreset.IS_CHANGED([], [], "walk", 1.0) != nodes.MMXPreset.IS_CHANGED([], [], "establishing", 1.0))
    sig = nodes.MMXPreset.IS_CHANGED([], [], "walk", 1.0); time.sleep(0.01); st.save({"name": "walk", "prompt": "edited", "loras": []})
    check("editing a preset changes IS_CHANGED", nodes.MMXPreset.IS_CHANGED([], [], "walk", 1.0) != sig)
    check("VALIDATE_INPUTS rejects unknown preset", nodes.MMXPreset.VALIDATE_INPUTS("nope") != True and nodes.MMXPreset.VALIDATE_INPUTS("walk") is True)
    try:
        st.save({"name": "broken", "prompt": "x", "loras": [{"name": "Missing.safetensors"}]}); nodes.MMXPreset().run([], [], "broken", 1.0); check("missing lora file named", False)
    except ValueError as e:
        check("missing lora file named", "Missing.safetensors" in str(e))
    check("INPUT_TYPES preset combo lists store names", nodes.MMXPreset.INPUT_TYPES()["required"]["preset"][0] == st.names())

    # MMXPresetSave node
    sv = nodes.MMXPresetSave()
    r = sv.run("close up", "<Subject 1> turns", True, "", lora_1=LORAS[0], strength_1=0.6, lora_2=NONE, strength_2=0.85, lora_3=LORAS[2], strength_3=1.0, lora_4=NONE, strength_4=0.85, lora_5=NONE, strength_5=0.85)
    check("MMXPresetSave writes the preset (none slots skipped) and reports", r["result"] == ("close up",) and st.get("close up")["loras"] == [{"name": LORAS[0], "strength": 0.6}, {"name": LORAS[2], "strength": 1.0}] and "saved preset" in r["ui"]["text"][0])
    time.sleep(0.5)   # push_async
    nas_file = os.path.join(TMP, "nas/volume1/subgenula/mmx/presets.json")
    check("save triggers the NAS mirror push", os.path.isfile(nas_file) and any(p["name"] == "close up" for p in json.load(open(nas_file))["presets"]))
    check("MMXPreset sees the saved preset after re-reading the store", "close up" in nodes._preset_names())

    # MMXSequence
    sq = nodes.MMXSequence()
    kw = {f"preset_{i}": NONE for i in range(1, 9)}; kw["preset_2"] = "walk"; kw["preset_5"] = "close up"
    r0 = sq.run([], [], 0, 1.0, **kw); r1 = sq.run([], [], 1, 1.0, **kw); r2 = sq.run([], [], 2, 1.0, **kw)
    check("sequence skips empty slots, index wraps", r0["result"][3] == "walk" and r1["result"][3] == "close up" and r2["result"][3] == "walk" and r1["result"][4:] == (2, 2))
    check("sequence applies the selected preset's loras", r1["result"][0] == [(LORAS[0], 0.6), (LORAS[2], 1.0)] and r1["result"][2] == "<Subject 1> turns")
    check("sequence VALIDATE: no slots -> error, unknown -> error", nodes.MMXSequence.VALIDATE_INPUTS(**{f"preset_{i}": NONE for i in range(1, 9)}) != True
          and nodes.MMXSequence.VALIDATE_INPUTS(**{**kw, "preset_2": "ghost"}) != True and nodes.MMXSequence.VALIDATE_INPUTS(**kw) is True)
    check("sequence IS_CHANGED follows the index", nodes.MMXSequence.IS_CHANGED([], [], 0, 1.0, **kw) != nodes.MMXSequence.IS_CHANGED([], [], 1, 1.0, **kw))

    # persistence: a fresh store object re-reads the file; deleting the file and pulling restores from the NAS
    st2 = store.PresetStore(path=st.path)
    check("presets persist across a restart (fresh store reads the file)", "close up" in st2.names() and "walk" in st2.names())
    os.remove(st.path); st3 = store.PresetStore(path=st.path)
    check("local file gone -> empty until pull", st3.names() == [])
    res = st3.pull()
    check("re-mirror from the NAS restores presets", res["ok"] and "close up" in st3.names(), json.dumps(res))
    os.environ["NAS_LOCKED"] = "1"; res = store.PresetStore(path=st.path).pull(); os.environ.pop("NAS_LOCKED")
    check("locked share -> pull is a no-op with a reason, local presets untouched", not res["ok"] and res["reason"] == "share is locked" and "walk" in store.PresetStore(path=st.path).names(), json.dumps(res))
    # merge: newer wins per name
    a = {"presets": [{"name": "x", "prompt": "old", "updated": 1}, {"name": "y", "prompt": "only-a", "updated": 1}]}
    b = {"presets": [{"name": "x", "prompt": "new", "updated": 2}]}
    mg = store.merge(store.parse(json.dumps(a)), store.parse(json.dumps(b)))
    check("merge: union by name, newer updated wins", [p["prompt"] for p in mg["presets"]] == ["new", "only-a"])

    # delete survives the mirror: tombstone beats the NAS copy on the pull-before-push
    st.save({"name": "doomed", "prompt": "x"}); st.push()
    nas_names = lambda: [p["name"] for p in json.load(open(nas_file))["presets"]]
    check("preset pushed to the NAS before the delete", "doomed" in nas_names())
    time.sleep(0.01); st.delete("doomed"); res = st.push()
    check("delete + push: the preset stays deleted locally AND on the NAS (tombstone)", res["ok"] and "doomed" not in st.names() and "doomed" not in nas_names()
          and any(t["name"] == "doomed" for t in json.load(open(nas_file))["deleted"]), f"{res} local={st.names()} nas={nas_names()}")
    time.sleep(0.01); st.save({"name": "doomed", "prompt": "back"}); st.push()
    check("re-saving after a delete clears the tombstone on both sides", "doomed" in st.names() and "doomed" in nas_names() and not any(t["name"] == "doomed" for t in json.load(open(nas_file))["deleted"]) and not any(t["name"] == "doomed" for t in st.data["deleted"]))
    st.delete("doomed"); st.push()
    # a lora row with on=false is skipped by apply_loras
    st.save({"name": "halfoff", "prompt": "x", "loras": [{"name": LORAS[0], "strength": 0.5, "on": False}, {"name": LORAS[2], "strength": 0.4}]})
    out = nodes.MMXPreset().run([], [], "halfoff", 1.0)
    check("MMXPreset skips lora rows with on=false", out["result"][0] == [(LORAS[2], 0.4)], str(out["result"][0]))

    # phrases store: seed, add / delete, bulk replace, mirror round trip with tombstones
    phrases = importlib.import_module("mmx_presets.phrases")
    ph = phrases.get_store()
    g = ph.groups()
    check("phrases seeded on first load (First frame / Camera / Lighting / Pacing)", [x["name"] for x in g] == ["First frame", "Camera", "Lighting", "Pacing"]
          and g[0]["phrases"][0]["text"] == "…is the absolute first frame of the video." and os.path.isfile(ph.path), str([x["name"] for x in g]))
    ph.add("Camera", "slow dolly left"); ph.push()
    nas_ph = os.path.join(TMP, "nas/volume1/subgenula/mmx/phrases.json")
    check("phrase add + push mirrors phrases.json to the NAS", os.path.isfile(nas_ph) and any(p["text"] == "slow dolly left" for gg in json.load(open(nas_ph))["groups"] for p in gg["phrases"]))
    time.sleep(0.01); ph.remove("Camera", "slow dolly left"); ph.push()
    check("phrase delete survives the pull-before-push (tombstone)", not any(p["text"] == "slow dolly left" for gg in ph.groups() for p in gg["phrases"])
          and not any(p["text"] == "slow dolly left" for gg in json.load(open(nas_ph))["groups"] for p in gg["phrases"]))
    ph.replace_all([{"name": "Camera", "phrases": ["Wide shot, slow push in", "crash zoom"]}, {"name": "Mood", "phrases": [{"text": "tense"}]}])
    keys = {(gg["name"], p["text"]) for gg in ph.groups() for p in gg["phrases"]}
    check("bulk replace: kept, added and removed phrases; removed ones tombstoned", ("Camera", "crash zoom") in keys and ("Mood", "tense") in keys and ("Camera", "close up") not in keys
          and ("Lighting", "film grain") not in keys and any(t["text"] == "film grain" for t in ph.data["deleted"]), str(sorted(keys)))
    ph.push(); os.remove(ph.path); ph2 = phrases.PhraseStore(path=ph.path); ph2.load()
    check("local phrases.json gone -> reseeded", any(p["text"] == "film grain" for gg in ph2.groups() for p in gg["phrases"]))
    res = ph2.pull()
    keys2 = {(gg["name"], p["text"]) for gg in ph2.groups() for p in gg["phrases"]}
    check("re-mirror from the NAS restores the edited set (seed entries deleted before stay deleted)", res["ok"] and ("Camera", "crash zoom") in keys2 and ("Lighting", "film grain") not in keys2, f"{res} {sorted(keys2)}")

    # LoRA stack: rows applied in order, off / none / zero rows skipped, VALIDATE names missing files
    ls = importlib.import_module("mmx_presets.lora_stack")
    kw = {}
    for i in range(1, 6):
        kw[f"on_{i}"] = True; kw[f"lora_{i}"] = ls.NONE; kw[f"strength_{i}"] = 1.0
    kw.update(lora_1=LORAS[1], strength_1=0.5, lora_2=LORAS[0], strength_2=0.7, on_2=False, lora_3=LORAS[2], strength_3=0.0, lora_5=LORAS[0], strength_5=1.2)
    out = ls.MMXLoRAStack().run([], [], **kw)
    check("MMXLoRAStack applies enabled non-zero rows in order to model and clip", out["result"][0] == [(LORAS[1], 0.5), (LORAS[0], 1.2)] and out["result"][1] == [(LORAS[1], 0.5), (LORAS[0], 1.2)], str(out["result"][:2]))
    check("stack text lists the effective rows", out["result"][2].startswith("1. " + LORAS[1]) and "2. " + LORAS[0] + " @ 1.20" in out["result"][2], out["result"][2])
    check("stack VALIDATE_INPUTS names a missing file, accepts the rest", ls.MMXLoRAStack.VALIDATE_INPUTS(**kw) is True and "ghost.safetensors" in str(ls.MMXLoRAStack.VALIDATE_INPUTS(**{**kw, "lora_1": "ghost.safetensors"})))
    LORAS.append("Dropped_Later.safetensors")
    check("stack INPUT_TYPES scans models/loras at call time (a file added after import is listed)", "Dropped_Later.safetensors" in ls.MMXLoRAStack.INPUT_TYPES()["required"]["lora_1"][0]
          and "Dropped_Later.safetensors" in nodes.MMXPresetSave.INPUT_TYPES()["required"]["lora_1"][0] and "Dropped_Later.safetensors" in ls.lora_names())
    LORAS.pop()
    check("stack INPUT_TYPES: 5 x (on, lora, strength) after model/clip", list(ls.MMXLoRAStack.INPUT_TYPES()["required"])[:5] == ["model", "clip", "on_1", "lora_1", "strength_1"] and len(ls.MMXLoRAStack.INPUT_TYPES()["required"]) == 17)

    # LoRA registry: metadata pre-fill, edits win over auto, tombstones survive the mirror, stack outputs, affix
    reg = importlib.import_module("mmx_presets.registry")
    affix = importlib.import_module("mmx_presets.affix")
    import struct
    def write_st(name, meta):
        os.makedirs(os.path.join(TMP, "loras"), exist_ok=True)
        hdr = json.dumps({"__metadata__": meta}).encode()
        open(os.path.join(TMP, "loras", name), "wb").write(struct.pack("<Q", len(hdr)) + hdr)
    write_st(LORAS[0], {"ss_tag_frequency": json.dumps({"10_h3": {"h3motion": 40, "fast pan": 38, "blurry": 3, "1girl": 40}})})
    write_st(LORAS[1], {"modelspec.trigger_phrase": "accel8, turbo mode"})
    write_st(LORAS[2], {})
    rg = reg.get_store()
    n = rg.prefill(LORAS)
    e0, e1, e2 = rg.get(LORAS[0]), rg.get(LORAS[1]), rg.get(LORAS[2])
    check("registry pre-fill: ss_tag_frequency top tags (>= 90% of max), modelspec.trigger_phrase split, empty entry for no metadata; all auto/updated 0",
          n == 3 and e0["triggers"] == ["1girl", "h3motion", "fast pan"] and e0["auto"] and e0["updated"] == 0 and "ss_tag_frequency" in e0["notes"]
          and e1["triggers"] == ["accel8", "turbo mode"] and e2["triggers"] == [] and e2["auto"], f"{e0} {e1} {e2}")
    check("pre-fill is idempotent", rg.prefill(LORAS) == 0)
    e = rg.set(LORAS[0], {"triggers": ["h3motion"], "phrases": ["fast pan", "whip pan", "fast pan"], "default_strength": 0.6, "notes": "mine"})
    check("registry set: user entry (auto false, updated now), phrases deduped, strength clamped", not e["auto"] and e["updated"] > 0 and e["phrases"] == ["fast pan", "whip pan"] and e["default_strength"] == 0.6 and rg.get(LORAS[0])["triggers"] == ["h3motion"])
    # merge: the NAS copy (edited elsewhere, newer) wins over a local auto entry; an auto entry never overrides a user edit
    remote = reg.parse(json.dumps({"updated": 5, "loras": {LORAS[2]: {"triggers": ["sidef"], "phrases": [], "default_strength": 0.9, "notes": "", "updated": 5}}}))
    m = reg.merge(rg.load(force=True), remote)
    check("merge: NAS entry (updated 5) beats the local auto entry (updated 0); local user edit kept", m["loras"][LORAS[2]]["triggers"] == ["sidef"] and m["loras"][LORAS[0]]["triggers"] == ["h3motion"])
    rg.push(); nas_reg = os.path.join(TMP, "nas/volume1/subgenula/mmx/loras.json")
    check("registry pushed to the NAS", os.path.isfile(nas_reg) and LORAS[0] in json.load(open(nas_reg))["loras"])
    time.sleep(0.01); rg.delete(LORAS[1]); rg.push()
    check("registry delete + push: gone on both sides with a tombstone; pre-fill does not re-add it", LORAS[1] not in rg.all() and LORAS[1] not in json.load(open(nas_reg))["loras"]
          and any(t["name"] == LORAS[1] for t in json.load(open(nas_reg))["deleted"]) and rg.prefill(LORAS) == 0)
    os.remove(rg.path); rg2 = reg.LoraRegistry(path=rg.path); rg2.load(); rg2.prefill(LORAS); res = rg2.pull()
    check("registry survives a re-mirror: local file lost -> re-prefilled (auto) -> pull restores the edited entry and keeps the deletion",
          res["ok"] and rg2.get(LORAS[0])["triggers"] == ["h3motion"] and not rg2.get(LORAS[0])["auto"] and LORAS[1] not in rg2.all(), f"{res} {rg2.all()}")
    # stack outputs: enabled rows in order, deduped, disabled rows excluded
    rg.set(LORAS[2], {"triggers": ["sidef", "H3MOTION"], "phrases": ["low angle"]})
    kw = {}
    for i in range(1, 6):
        kw[f"on_{i}"] = False; kw[f"lora_{i}"] = ls.NONE; kw[f"strength_{i}"] = 1.0
    kw.update(on_1=True, lora_1=LORAS[2], strength_1=0.8, on_2=True, lora_2=LORAS[0], strength_2=0.5, on_3=False, lora_3=LORAS[1], strength_3=1.0)
    out = ls.MMXLoRAStack().run([], [], **kw)
    check("Stack outputs triggers (row order, case-insensitive dedupe) and phrases for ENABLED rows only", out["result"][3] == "sidef, H3MOTION" and out["result"][4] == "low angle, fast pan, whip pan", str(out["result"][3:]))
    check("Stack body lists triggers / phrases under each row", "1. " + LORAS[2] in out["result"][2] and "triggers: sidef, H3MOTION" in out["result"][2] and "phrases: fast pan, whip pan" in out["result"][2] and "→ triggers out: sidef, H3MOTION" in out["result"][2], out["result"][2])
    kw2 = {**kw, "on_1": False}
    out2 = ls.MMXLoRAStack().run([], [], **kw2)
    check("disabling a row removes its triggers from the output", out2["result"][3] == "h3motion", out2["result"][3])
    check("Stack IS_CHANGED follows the registry file and the rows", ls.MMXLoRAStack.IS_CHANGED([], [], **kw) != ls.MMXLoRAStack.IS_CHANGED([], [], **kw2))
    # affix
    check("affix prepend / append / prefix+suffix / no triggers / empty prompt",
          affix.affix("<Subject 1> walks", auto_triggers=out["result"][3]) == "sidef, H3MOTION, <Subject 1> walks"
          and affix.affix("<Subject 1> walks", auto_triggers="a, b", mode="append") == "<Subject 1> walks, a, b"
          and affix.affix("p", "PRE", "SUF", "t") == "PRE\nt, p\nSUF" and affix.affix("p", auto_triggers="") == "p" and affix.affix("", auto_triggers="t") == "t")
    a_out = affix.MMXPromptAffix().run("<Subject 1> walks", "", "", "prepend", out["result"][3])
    a_off = affix.MMXPromptAffix().run("<Subject 1> walks", "", "", "prepend", out2["result"][3])
    check("Stack -> Affix end to end: enabled row's triggers land in front of the prompt; disabled row's do not", a_out["result"][0].startswith("sidef, H3MOTION, <Subject 1>") and a_off["result"][0] == "h3motion, <Subject 1> walks" and a_out["ui"]["text"][0].startswith("triggers (prepend): sidef, H3MOTION"), str(a_out) + str(a_off))

    # Deck node: passthrough, never touches the store
    deck = importlib.import_module("mmx_presets.deck")
    n_before = len(st.names())
    out = deck.MMXDeck().run("<Picture 1> walks", "walk", json.dumps([{"name": LORAS[0], "strength": 0.6, "on": True}]), "{}")
    check("MMXDeck run is a passthrough (prompt out, store untouched)", out["result"][0] == "<Picture 1> walks" and len(st.names()) == n_before and "1 LoRA row" in out["ui"]["text"][0], str(out))

    # References Manager subclass (only when the RefPack is importable)
    mgr = importlib.import_module("mmx_presets.manager")
    if mgr._BASE is not None:
        cls = mgr.NODE_CLASS_MAPPINGS["MMXReferencesManager"]
        it, bt = cls.INPUT_TYPES(), mgr._BASE.INPUT_TYPES()
        check("MMXReferencesManager: the RefPack's inputs + first_frame (IMAGE, optional, appended LAST so widget positions are unchanged) and the same 20 outputs",
              it["required"] == bt["required"] and list(it["optional"])[:-1] == list(bt["optional"]) and list(it["optional"])[-1] == "first_frame" and it["optional"]["first_frame"][0] == "IMAGE"
              and {k: v for k, v in it["optional"].items() if k != "first_frame"} == bt["optional"] and len(cls.RETURN_TYPES) == 20 and cls.RETURN_NAMES == mgr._BASE.RETURN_NAMES, str(list(it["optional"])[-3:]))
        os.environ["OPENROUTER_KEY"] = "sk-test-fallback"; os.environ.pop("OPENROUTER_API_KEY", None); os.environ.pop("LLM_KEY", None)
        check("key fallback: blank -> OPENROUTER_KEY; explicit wins", mgr.resolve_key("") == "sk-test-fallback" and mgr.resolve_key(" sk-x ") == "sk-x")
        os.environ.pop("OPENROUTER_KEY")
        out = cls().build(direction="hello", references_json="", prompt_provider="none")
        check("manager subclass builds (provider none): prompt passthrough, 20 outputs", len(out) == 20 and out[18] == "hello", str(out[18:]))
    else:
        print(f"skip References Manager checks ({mgr._REASON})")

    # chain helpers (numpy/torch/PIL needed only here)
    try:
        import numpy as np, torch
        from PIL import Image
        img = torch.zeros((2, 8, 8, 3)); img[1, :, :, 0] = 1.0   # batch of 2: last frame red
        r = nodes.MMXSaveFrame().run(img, "chain_test")
        path = os.path.join(fp.get_input_directory(), "chain_test.png")
        check("MMXSaveFrame writes the LAST frame under a fixed name in the input dir", os.path.isfile(path) and Image.open(path).getpixel((0, 0)) == (255, 0, 0))
        fb = torch.ones((1, 4, 4, 3)) * 0.5
        out = nodes.MMXLoadChainFrame().run(fb, "chain_test", False)
        check("MMXLoadChainFrame loads the saved frame", out[1] is True and tuple(out[0].shape) == (1, 8, 8, 3) and float(out[0][0, 0, 0, 0]) > 0.99)
        out = nodes.MMXLoadChainFrame().run(fb, "absent_file", False)
        check("MMXLoadChainFrame falls back when the file is absent", out[1] is False and out[0] is fb)
        out = nodes.MMXLoadChainFrame().run(fb, "chain_test", True)
        check("use_fallback forces the fallback", out[1] is False and out[0] is fb)
        s1 = nodes.MMXLoadChainFrame.IS_CHANGED(fb, "chain_test", False); time.sleep(0.02); nodes.MMXSaveFrame().run(img * 0.5, "chain_test")
        check("IS_CHANGED tracks the file", nodes.MMXLoadChainFrame.IS_CHANGED(fb, "chain_test", False) != s1)
    except ImportError as e:
        print(f"skip chain helper checks (no torch/PIL here: {e})")

    # references builder (pure python)
    refs = importlib.import_module("mmx_presets.references")
    rj, pmap, lst = refs.build(["a.png", "", "", "", "", "", "", "", "z.png"], ["v.mp4", "", ""], ["s.wav"], True)
    d = json.loads(rj)
    check("references_json in the RefPack schema, compacted in slot order",
          d == {"references": [{"kind": "image", "file": "a.png"}, {"kind": "image", "file": "z.png"}, {"kind": "video", "file": "v.mp4", "use_soundtrack": True}, {"kind": "audio", "file": "s.wav"}]}, rj)
    check("picture_map: slot 9 -> <Picture 2>, soundtrack <Audio 1> before <Video 1>, standalone audio <Audio 2>",
          "slot 1 -> <Picture 1>" in pmap and "slot 9 -> <Picture 2>" in pmap and "video 1 -> <Video 1> (+ soundtrack <Audio 1>)" in pmap and "audio 1 -> <Audio 2>" in pmap, pmap)
    rj2, pmap2, _ = refs.build(["a.png"], ["v.mp4"], [""], False)
    check("use_soundtrack off: video keeps the flag false, no <Audio>", json.loads(rj2)["references"][1]["use_soundtrack"] is False and "<Audio" not in pmap2, pmap2)
    out = refs.MMXReferencesBuilder().run(image_1="a.png", image_9="z.png")
    check("MMXReferencesBuilder node: empty slots ignored, missing files reported in the ui text", json.loads(out["result"][0])["references"][1]["file"] == "z.png" and "NOT in ComfyUI/input" in out["ui"]["text"][0], str(out))
    try:
        from minimax_refpack import refs as rp_refs   # the real RefPack when it is importable: tags must agree
        tags = [t.tag for t in rp_refs.ReferenceSet.from_json(rj).assign_tags()]
        check("RefPack assigns the same tags the picture_map claims", tags == ["<Picture 1>", "<Picture 2>", "<Video 1>", "<Audio 2>"], str(tags))
    except ImportError:
        pass

    # first-frame check + gate + library (torch / PIL / ffmpeg)
    try:
        import numpy as np, torch
        from PIL import Image
        chk = importlib.import_module("mmx_presets.check")
        lib = importlib.import_module("mmx_presets.library")
        # geometry: a 4:3 reference into a 16:9 frame is centre-cropped (width) then resized like AddGuide
        ref = torch.zeros((1, 300, 400, 3)); ref[:, :, :200, :] = 1.0        # left half white
        g = chk.guide_geometry(ref, 320, 180)
        check("guide_geometry cover-crops to the frame aspect and resizes (left half stays white, no letterbox)",
              tuple(g.shape) == (1, 180, 320, 3) and float(g[0, 90, 40, 0]) > 0.99 and float(g[0, 90, 280, 0]) < 0.01 and float(g[0, 2, 40, 0]) > 0.99, str(g.shape))
        check("cover_crop_box reports the kept region", chk.cover_crop_box(400, 300, 320, 180) == (0, 38, 400, 224) and chk.cover_crop_box(768, 816, 768, 448) == (0, 184, 768, 448))
        a = torch.rand((1, 64, 96, 3)); noise = a + torch.randn_like(a) * 0.02
        check("psnr: identical = 100 cap, tiny noise ~34 dB, ssim ordering", chk.psnr(a[0], a[0]) == 100.0 and 30 < chk.psnr(a[0], noise.clamp(0, 1)[0]) < 40 and chk.ssim(a[0], a[0]) > 0.999 and chk.ssim(a[0], noise.clamp(0, 1)[0]) < chk.ssim(a[0], a[0]))
        node = chk.MMXFirstFrameCheck()
        frames = torch.cat([a, a * 0.5])
        r_ok = node.run(frames, 24.0, reference=a)
        r_bad = node.run(frames, 24.0, reference=torch.rand((1, 64, 96, 3)))
        # the same image comes back > 50 dB, not 100: the guide geometry re-samples through 8-bit lanczos like AddGuide
        check("FirstFrameCheck: same image passes (> 50 dB, passed True), random fails; strip is reference|frame|diff wide",
              r_ok["result"][2] is True and r_ok["result"][0] > 50 and r_bad["result"][2] is False and r_bad["result"][0] < 24 and tuple(r_ok["result"][3].shape) == (1, 64, 96 * 3 + 12, 3), f"{r_ok['result'][:3]} {r_bad['result'][:3]}")
        check("FirstFrameCheck ui: text PASS/FAIL + preview image in temp", r_ok["ui"]["text"][0].startswith("PASS") and r_bad["ui"]["text"][0].startswith("FAIL") and r_ok["ui"]["passed"] == [True] and os.path.isfile(os.path.join(fp.get_temp_directory(), r_ok["ui"]["images"][0]["filename"])))
        r_skip = node.run(frames, 24.0, reference=None, enabled=True)
        r_off = node.run(frames, 24.0, reference=a, enabled=False)
        check("FirstFrameCheck skips with no reference / disabled: passed=True, psnr=-1, comparison=frame 0, body says skipped (first segment)",
              r_skip["result"][:3] == (-1.0, -1.0, True) and torch.equal(r_skip["result"][3], frames[0:1]) and r_skip["ui"]["text"][0].startswith("skipped (first segment)")
              and r_off["result"][:3] == (-1.0, -1.0, True) and "check disabled" in r_off["ui"]["text"][0] and r_skip["ui"]["skipped"] == [True], str(r_skip["ui"]["text"]))
        check("FirstFrameCheck INPUT_TYPES: reference optional, enabled toggle", "reference" in chk.MMXFirstFrameCheck.INPUT_TYPES()["optional"] and "enabled" in chk.MMXFirstFrameCheck.INPUT_TYPES()["optional"])
        gate = chk.MMXChainGate()
        good, rejected = chk.gate_paths("gate_test")
        r = gate.run(frames, True, "gate_test")
        check("ChainGate pass: writes the LAST frame under input/<name>.png", r["result"] == (good, True) and os.path.isfile(good) and abs(Image.open(good).getpixel((0, 0))[0] - int(frames[-1, 0, 0, 0] * 255)) <= 1 and good in r["ui"]["text"][0])
        before = open(good, "rb").read()
        try:
            gate.run(frames * 0.1, False, "gate_test"); check("ChainGate fail raises", False)
        except RuntimeError as e:
            check("ChainGate fail: raises naming the REJECTED file, writes it, keeps the good frame byte-identical",
                  rejected in str(e) and "kept" in str(e) and os.path.isfile(rejected) and open(good, "rb").read() == before, str(e))
        # library: image + video (ffmpeg) -> input copy, first frame, thumbs, refresh, sync trigger
        L = lib.root(); os.makedirs(os.path.join(L, "Subjects", "j"), exist_ok=True); os.makedirs(os.path.join(L, "VideoRef"), exist_ok=True)
        Image.fromarray((np.stack([np.full((48, 64), 255), np.zeros((48, 64)), np.zeros((48, 64))], -1)).astype(np.uint8)).save(os.path.join(L, "Subjects", "j", "red.png"))
        open(os.path.join(L, "Subjects", "j", "notes.txt"), "w").write("ignored")
        items = lib.scan(force=True)
        check("library scan lists images/videos only, folder-aware paths", [i["path"] for i in items] == ["Subjects/j/red.png"] and items[0]["kind"] == "image", str(items))
        ln = lib.MMXLibraryImage()
        check("VALIDATE_INPUTS accepts existing, rejects unknown / traversal", ln.VALIDATE_INPUTS("Subjects/j/red.png") is True and ln.VALIDATE_INPUTS("Subjects/j/nope.png") is not True and ln.VALIDATE_INPUTS("../etc/passwd") is not True)
        out = ln.run("Subjects/j/red.png")
        img, name, path = out["result"]
        check("MMXLibraryImage: image tensor, flat input filename, library path; file copied into input/",
              tuple(img.shape) == (1, 48, 64, 3) and float(img[0, 0, 0, 0]) > 0.99 and name == "Subjects__j__red.png" and os.path.isfile(os.path.join(fp.get_input_directory(), name)) and path == os.path.join(L, "Subjects/j/red.png"), str(out["result"][1:]))
        check("thumb jpeg", lib.thumb_jpeg("Subjects/j/red.png", 32)[:2] == b"\xff\xd8")
        lib.scan(force=True)   # warm the 20 s scan cache, then drop a file: INPUT_TYPES must list it at once
        Image.fromarray(np.zeros((8, 8, 3), np.uint8)).save(os.path.join(L, "Subjects", "j", "dropped_later.png"))
        check("library INPUT_TYPES scans the mirror at call time (a file added inside the cache TTL is listed)", "Subjects/j/dropped_later.png" in lib.MMXLibraryImage.INPUT_TYPES()["required"]["file"][0])
        os.remove(os.path.join(L, "Subjects", "j", "dropped_later.png")); lib.scan(force=True)
        inj = lib.inject_file("Subjects/j/red.png", fp.get_input_directory(), "Picture 2")
        check("inject_file: image copied into input/, kind image, slot echoed", inj["filename"] == "Subjects__j__red.png" and inj["kind"] == "image" and inj["frame_png"] is None and inj["slot"] == "Picture 2", str(inj))
        check("library slot widget lists (none) + Picture 1-9 + Video 1-3 + Audio 1", lib.SLOTS == ["(none)"] + [f"Picture {i}" for i in range(1, 10)] + [f"Video {i}" for i in range(1, 4)] + ["Audio 1"] and lib.MMXLibraryImage.INPUT_TYPES()["optional"]["slot"][0] == lib.SLOTS)
        if shutil.which("ffmpeg"):
            vp = os.path.join(L, "VideoRef", "blue.mp4")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=c=blue:s=64x48:d=0.5:r=24", "-pix_fmt", "yuv420p", vp], check=True)
            lib.scan(force=True)
            out = ln.run("VideoRef/blue.mp4")
            img, name, path = out["result"]
            check("MMXLibraryImage mp4: first frame as IMAGE (blue), the mp4 itself copied into input/",
                  tuple(img.shape) == (1, 48, 64, 3) and float(img[0, 24, 32, 2]) > 0.8 and float(img[0, 24, 32, 0]) < 0.2 and name == "VideoRef__blue.mp4" and os.path.isfile(os.path.join(fp.get_input_directory(), name)) and "first frame" in out["ui"]["text"][0], str(out))
            check("video thumb jpeg", lib.thumb_jpeg("VideoRef/blue.mp4", 32)[:2] == b"\xff\xd8")
            inj = lib.inject_file("VideoRef/blue.mp4", fp.get_input_directory(), "Picture 1")
            check("inject_file mp4: the mp4 copied AND its first frame written as __frame0.png for a Picture slot",
                  inj["kind"] == "video" and inj["frame_png"] == "VideoRef__blue__frame0.png" and os.path.isfile(os.path.join(fp.get_input_directory(), inj["frame_png"])), str(inj))
        else:
            print("skip mp4 library checks (no ffmpeg)")
        # sync trigger: script absent -> clean error; present -> runs detached and the log fills
        st = lib.start_sync()
        check("Mirror from NAS without the script: reported, not raised", st.get("error") and "not present" in st["error"], str(st))
        open(os.environ["MMX_LIBRARY_SYNC"], "w").write(f"#!/usr/bin/env bash\necho '[library-sync] done: 1 files' >> {os.environ['MMX_LIBRARY_SYNC_LOG']}\n")
        st = lib.start_sync(); time.sleep(0.6); st2 = lib.sync_status()
        check("Mirror from NAS with the script: started, finished rc 0, log tail visible", st["started_now"] and not st2["running"] and st2["last"]["rc"] == 0 and "done: 1 files" in st2["log_tail"], str(st2))
        open(os.environ["MMX_LIBRARY_SYNC_LOG"], "a").write("2026-09-09T07:00:00Z [library-sync] waiting: share /volume1/subgenula is LOCKED (unlock it in the vgo dashboard) — retry in 60s (attempt 3/120)\n")
        # lazy file validation: unset ("" / the legacy placeholder) is a queue-time error that names the
        # library's size, or — with an empty mirror — the last mirror log line
        v_unset, v_legacy = lib.MMXLibraryImage.VALIDATE_INPUTS(""), lib.MMXLibraryImage.VALIDATE_INPUTS(lib.NONE)
        check("file unset with files in the mirror: 'no file selected — pick one of the N library files' (both the empty string and the legacy placeholder)",
              "no file selected" in str(v_unset) and "pick one of the" in str(v_unset) and str(v_legacy) == str(v_unset), str(v_unset))
        empty_root = os.path.join(TMP, "library_empty"); os.makedirs(empty_root, exist_ok=True)
        saved_root = os.environ["MMX_LIBRARY"]; os.environ["MMX_LIBRARY"] = empty_root
        try:
            v_empty = lib.MMXLibraryImage.VALIDATE_INPUTS("")
            check("file unset with an EMPTY mirror: the error carries the last mirror log line", "library is empty" in str(v_empty) and "attempt 3/120" in str(v_empty) and "last_line" in lib.sync_status(), str(v_empty))
            check("empty mirror: the file dropdown is [UNSET] (no placeholder text in the enum), default UNSET",
                  lib.paths(force=True) == [lib.UNSET] and lib.MMXLibraryImage.INPUT_TYPES()["required"]["file"][1]["default"] == lib.UNSET)
        finally:
            os.environ["MMX_LIBRARY"] = saved_root
        ps = lib.paths(force=True)
        check("file dropdown = UNSET first, then the mirror's files; is_unset reads '', the legacy placeholder and any '(…)' as unset",
              ps[0] == lib.UNSET and "Subjects/j/red.png" in ps and all(lib.is_unset(x) for x in ("", None, lib.NONE, "(no match)")) and not lib.is_unset("Subjects/j/red.png"), str(ps))
        check("slot outside SLOTS is refused by VALIDATE_INPUTS, a listed slot passes", lib.MMXLibraryImage.VALIDATE_INPUTS("Subjects/j/red.png", "Picture 12") != True and lib.MMXLibraryImage.VALIDATE_INPUTS("Subjects/j/red.png", "Picture 9") is True)
        # first_frame helpers of the Manager (module-level, no RefPack needed)
        mg = importlib.import_module("mmx_presets.manager")
        j, slot, rep_ = mg.apply_first_frame("", "mmx_ff_abc.png")
        check("apply_first_frame on an empty list: the frame is <Picture 1>", slot == 1 and rep_ is None and json.loads(j)["references"] == [{"kind": "image", "file": "mmx_ff_abc.png"}], j)
        base_refs = json.dumps({"references": [{"kind": "image", "file": "id.png"}, {"kind": "video", "file": "v.mp4", "use_soundtrack": True}]})
        j, slot, rep_ = mg.apply_first_frame(base_refs, "mmx_ff_abc.png")
        check("apply_first_frame keeps every slot and appends the frame as the LAST picture (before the videos)", slot == 2 and rep_ is None and [r["file"] for r in json.loads(j)["references"]] == ["id.png", "mmx_ff_abc.png", "v.mp4"], j)
        stale = json.dumps({"references": [{"kind": "image", "file": "id.png"}, {"kind": "image", "file": "mmx_ff_old.png"}]})
        j, slot, rep_ = mg.apply_first_frame(stale, "mmx_ff_new.png")
        check("a stale mmx_ff_* entry is replaced, not stacked; find_marker returns the frontend's name", slot == 2 and [r["file"] for r in json.loads(j)["references"]] == ["id.png", "mmx_ff_new.png"] and mg.find_marker(stale) == "mmx_ff_old.png" and mg.find_marker(base_refs) is None, j)
        full = json.dumps({"references": [{"kind": "image", "file": f"p{i}.png"} for i in range(1, 10)]})
        j, slot, rep_ = mg.apply_first_frame(full, "mmx_ff_x.png")
        check("with 9 pictures the 9th is replaced (cap kept)", slot == 9 and rep_ == "p9.png" and [r["file"] for r in json.loads(j)["references"]][-2:] == ["p8.png", "mmx_ff_x.png"], j)
        t = torch.zeros((1, 8, 12, 3)); t[:, :, :6, 0] = 1.0
        name, (w, h) = mg.write_first_frame(t, TMP)
        name2, _ = mg.write_first_frame(t, TMP, "mmx_ff_7_abc.png")
        name3, _ = mg.write_first_frame(t, TMP, "not_a_marker.png")
        px = Image.open(os.path.join(TMP, name)).convert("RGB")
        check("write_first_frame: mmx_ff_<content hash>.png by default, the frontend's mmx_ff_* marker name when given, PNG pixels match the tensor",
              name.startswith("mmx_ff_") and name == name3 and name2 == "mmx_ff_7_abc.png" and (w, h) == (12, 8) and px.size == (12, 8) and px.getpixel((0, 0)) == (255, 0, 0) and px.getpixel((11, 0)) == (0, 0, 0)
              and os.path.isfile(os.path.join(TMP, "mmx_ff_7_abc.png")), f"{name} {name2} {name3} {(w, h)}")
        check("frame_bytes hash is content-stable", mg.frame_bytes(t)[1] == mg.frame_bytes(t.clone())[1] and mg.frame_bytes(t)[1] != mg.frame_bytes(t * 0)[1])
    except ImportError as e:
        print(f"skip check/gate/library checks (no torch/PIL here: {e})")

    # shipped examples: both deck builds carry exactly what the nodes validate
    lib_mod = importlib.import_module("mmx_presets.library")
    for name in ("deck.json", "deck_chain.json"):
        path = os.path.join(ROOT, "examples", name)
        w = json.load(open(path)); raw = open(path).read()
        by = {n["id"]: n for n in w["nodes"]}
        links = {l[0]: l for l in w["links"]}
        src = lambda nid, inp: (lambda i: (links[i["link"]][1], links[i["link"]][2]) if i and i.get("link") in links else None)(next((i for i in by[nid]["inputs"] if i["name"] == inp), None))
        libs = [n for n in w["nodes"] if n["type"] == "MMXLibraryImage"]
        check(f"{name}: Library nodes ship file = UNSET and a slot from SLOTS; no key",
              len(libs) == 2 and all(n["widgets_values"][0] == lib_mod.UNSET and n["widgets_values"][1] in lib_mod.SLOTS for n in libs) and "sk-or" not in raw, str([n["widgets_values"] for n in libs]))
        check(f"{name}: #185 is MMX References Manager with first_frame <- Load Chain Frame #404 <- Library #402; First Frame Check reference <- #404; LoadImage #300 gone",
              by[185]["type"] == "MMXReferencesManager" and src(185, "first_frame") == (404, 0) and src(404, "fallback") == (402, 0) and src(301, "reference") == (404, 0) and 300 not in by, str((src(185, "first_frame"), src(404, "fallback"), src(301, "reference"))))
        dangling = [l for l in w["links"] if not any(i.get("link") == l[0] for i in by[l[3]]["inputs"]) or l[0] not in [x for o in by[l[1]]["outputs"] for x in (o.get("links") or [])]]
        check(f"{name}: every link is referenced by its source output and target input", not dangling, str(dangling)[:200])
        if name == "deck_chain.json":
            check("deck_chain.json: Chain Gate #406 <- decoded frames 133 + check passed 301:2; Load Chain Frame use_fallback false; deck.json true",
                  src(406, "images") == (133, 0) and src(406, "passed") == (301, 2) and by[404]["widgets_values"] == ["mmx_chain_last.png", False] and by[406]["widgets_values"] == ["mmx_chain_last.png", True] and w["id"] == "mmx-deck-chain")
        else:
            check("deck.json: no Chain Gate, Load Chain Frame always uses the fallback (one segment)", 406 not in by and by[404]["widgets_values"] == ["mmx_chain_last.png", True] and w["id"] == "mmx-deck")

    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} passed")
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if failed else 0)

if __name__ == "__main__":
    main()
