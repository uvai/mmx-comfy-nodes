#!/usr/bin/env python3
"""Unit tests for mmx-comfy-nodes without ComfyUI: `folder_paths`, `nodes` and `server` are
stubbed; the NAS mirror is exercised through a fake ssh (MMX_NAS_PROXY=none + a PATH shim) that
stores the remote file in a temp dir.

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
remote = share_dir + "/mmx/presets.json"
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
os.environ["MMX_NAS_SHARE"] = "/volume1/subgenula"

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
        st.save({"name": "bad", "loras": [{"name": "a"}, {"name": "b"}, {"name": "c"}, {"name": "d"}]}); check("max 3 loras", False)
    except store.PresetError:
        check("max 3 loras", True)

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
    r = sv.run("close up", "<Subject 1> turns", True, "", LORAS[0], 0.6, NONE, 0.85, LORAS[2], 1.0)
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

    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} passed")
    shutil.rmtree(TMP, ignore_errors=True)
    sys.exit(1 if failed else 0)

if __name__ == "__main__":
    main()
