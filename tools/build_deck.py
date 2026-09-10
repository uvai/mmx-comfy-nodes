#!/usr/bin/env python3
"""Build examples/deck.json and examples/deck_chain.json from the user's daily graph
(base_v10_check.json = v10 + First Frame Check) with the deck nodes slotted in and NOTHING
else touched:

  * Power Lora Loader (rgthree) #137  ->  MMX LoRA Stack #137 (same id, same position, same links:
    model from ModelPreviewOverrideKJ, clip from CLIPLoader, MODEL out into the turbo LoRA #158,
    which stays untouched)
  * MiniMaxH3ReferencePack #185 -> MMX References Manager #185 (drop-in: same id, widgets, links)
    plus its `first_frame` IMAGE input, fed by MMX Load Chain Frame #404
  * MMX Deck #400 added, NOT wired (it pushes into the Manager #185 and the Stack #137)
  * MMX Library Image #401 (slot Picture 1, Inject pushes the identity) and #402 (the first-frame
    image, its IMAGE output is the Load Chain Frame's fallback). Both ship with `file` EMPTY:
    pick yours in the node — an unset file is only an error when the node is queued.
  * MMX Load Chain Frame #404: fallback <- #402 image; image -> Manager first_frame (the last
    picture at run time) AND -> First Frame Check #301 reference (so the check compares against
    the very image the run anchored). deck.json: use_fallback = true (one segment, always the
    library image); deck_chain.json: use_fallback = false (mmx_chain_last.png when present).
    LoadImage #300 (the manual "load the same image" step) is dropped: #404 replaces it.
  * MMX First Frame Check #301: widgets [threshold_db, enabled=true]; `reference` optional
  * MMX Prompt Affix #403 between the Manager's prompt (185:18) and its consumers (184 prompt,
    186 display): prompt <- 185:18, auto_triggers <- Stack 137:3 (triggers), mode prepend
  * deck_chain.json only: MMX Chain Gate #406 (images <- VAEDecode 133, passed <- #301) writes
    mmx_chain_last.png for the next segment when the check passed, else stops the queue.

    python3 tools/build_deck.py --base /opt/subgenula/transfer/base_v10_check.json
"""
import argparse, copy, json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "examples", "deck.json")
OUT_CHAIN = os.path.join(ROOT, "examples", "deck_chain.json")
OUT_GUIDED = os.path.join(ROOT, "examples", "deck_chain_guided.json")
THRESHOLD_GUIDED = 20.0        # deck_chain_guided.json: AddGuide pins frame 0, so the check can be stricter
ROWS = 5
LORAS = ["H3_Motion_BoosterV2.safetensors"]     # row 1 of the stack; edit in the node / send from the Deck
CHAIN_FILE = "mmx_chain_last.png"
THRESHOLD_UNGUIDED = 12.0      # First Frame Check threshold without a frame-0 guide (with MiniMaxH3AddGuide use ~24)


def library_enums():
    """SLOTS / UNSET straight from the node module, so the example can only ever save what the
    node's own dropdown lists (the regex fallback reads the same source when torch-free import fails)."""
    try:
        sys.path.insert(0, ROOT)
        from mmx_presets.library import SLOTS, UNSET
        return list(SLOTS), UNSET
    except Exception:
        src = open(os.path.join(ROOT, "mmx_presets", "library.py")).read()
        ns = {}
        for name in ("NONE", "UNSET", "SLOT_NONE", "SLOTS"):
            exec(re.search(rf"^{name} = .*$", src, re.M).group(0), ns)
        return list(ns["SLOTS"]), ns["UNSET"]


SLOTS, UNSET = library_enums()


def stack_widgets(rows):
    out = []
    for i in range(ROWS):
        r = rows[i] if i < len(rows) else None
        out += [bool(r), r["name"] if r else "(none)", float(r["strength"]) if r else 1.0]
    return out


def build(base: dict) -> dict:
    w = copy.deepcopy(base)
    nodes = {n["id"]: n for n in w["nodes"]}
    # 1. Power Lora Loader -> MMX LoRA Stack (same id/pos/links)
    pl = nodes[137]
    assert pl["type"] == "Power Lora Loader (rgthree)", pl["type"]
    stack = {"id": 137, "type": "MMXLoRAStack", "pos": pl["pos"], "size": [460, 330], "flags": {}, "order": pl["order"], "mode": 0,
             "inputs": [{"name": "model", "type": "MODEL", "link": pl["inputs"][0]["link"]}, {"name": "clip", "type": "CLIP", "link": pl["inputs"][1]["link"]}],
             "outputs": [{"name": "model", "type": "MODEL", "links": pl["outputs"][0]["links"], "slot_index": 0},
                         {"name": "clip", "type": "CLIP", "links": [], "slot_index": 1},
                         {"name": "stack", "type": "STRING", "links": [], "slot_index": 2},
                         {"name": "triggers", "type": "STRING", "links": [], "slot_index": 3},
                         {"name": "phrases", "type": "STRING", "links": [], "slot_index": 4}],
             "properties": {"Node name for S&R": "MMXLoRAStack"}, "widgets_values": stack_widgets([{"name": LORAS[0], "strength": 0.7}]),
             "title": "MMX LoRA Stack", "color": pl.get("color", "#223"), "bgcolor": pl.get("bgcolor", "#335")}
    w["nodes"] = [stack if n["id"] == 137 else n for n in w["nodes"]]
    nodes[137] = stack
    for g in w["groups"]:
        if g["title"] == "LoRAs And Settings":
            g["bounding"][2] = max(g["bounding"][2], 480)
    # 2. First Frame Check: enabled toggle, reference optional
    chk = nodes[301]
    assert chk["type"] == "MMXFirstFrameCheck"
    chk["widgets_values"] = [THRESHOLD_UNGUIDED, True]   # no AddGuide in this graph: ~12-18 dB expected, see README
    chk["size"] = [360, 230]
    for i in chk["inputs"]:
        if i["name"] == "reference":
            i["shape"] = 7   # optional
    # 3. the Manager: MiniMaxH3ReferencePack -> MMX References Manager, drop-in + first_frame input
    mgr = nodes[185]
    assert mgr["type"] == "MiniMaxH3ReferencePack", mgr["type"]
    mgr["type"] = "MMXReferencesManager"
    mgr["properties"]["Node name for S&R"] = "MMXReferencesManager"
    mgr.setdefault("title", "MMX References Manager")
    lid = w["last_link_id"]
    l_fb, l_ff, l_ref = lid + 1, lid + 2, lid + 3
    mgr["inputs"].append({"name": "first_frame", "type": "IMAGE", "link": l_ff, "shape": 7})
    # 4. Deck (not wired) below the Manager (the area above it holds the HearmemanAI note)
    base_y = mgr["pos"][1] + mgr["size"][1] + 220   # clear of the Prompt Affix under the Display node
    deck = {"id": 400, "type": "MMXDeck", "pos": [mgr["pos"][0], base_y], "size": [880, 1000], "flags": {}, "order": 0, "mode": 0,
            "inputs": [], "outputs": [{"name": "prompt", "type": "STRING", "links": [], "slot_index": 0}, {"name": "loras_json", "type": "STRING", "links": [], "slot_index": 1}],
            "properties": {"Node name for S&R": "MMXDeck"},
            "widgets_values": ["<Subject 1> hugs <Subject 2>", "", json.dumps([{"name": LORAS[0], "strength": 0.7, "on": True}]), json.dumps({"manager": 185, "stack": 137})],
            "title": "MMX Deck"}
    # 5. two Library Image nodes, file EMPTY: identity -> Picture 1 (Inject), first frame -> Load Chain Frame fallback
    for slot in ("Picture 1", "(none)"):
        assert slot in SLOTS, slot
    lib_x = mgr["pos"][0] + 900
    libs = [{"id": 401, "type": "MMXLibraryImage", "pos": [lib_x, base_y], "size": [400, 520], "flags": {}, "order": 1, "mode": 0,
             "inputs": [], "outputs": [{"name": "image", "type": "IMAGE", "links": [], "slot_index": 0}, {"name": "filename", "type": "STRING", "links": [], "slot_index": 1},
                                       {"name": "path", "type": "STRING", "links": [], "slot_index": 2}],
             "properties": {"Node name for S&R": "MMXLibraryImage"}, "widgets_values": [UNSET, "Picture 1"], "title": "MMX Library Image — identity → Picture 1 (Inject)"},
            {"id": 402, "type": "MMXLibraryImage", "pos": [lib_x + 420, base_y], "size": [400, 520], "flags": {}, "order": 2, "mode": 0,
             "inputs": [], "outputs": [{"name": "image", "type": "IMAGE", "links": [l_fb], "slot_index": 0}, {"name": "filename", "type": "STRING", "links": [], "slot_index": 1},
                                       {"name": "path", "type": "STRING", "links": [], "slot_index": 2}],
             "properties": {"Node name for S&R": "MMXLibraryImage"}, "widgets_values": [UNSET, "(none)"], "title": "MMX Library Image — first frame → Load Chain Frame"}]
    # 6. Load Chain Frame: fallback <- #402, image -> Manager first_frame + First Frame Check reference
    load_chain = {"id": 404, "type": "MMXLoadChainFrame", "pos": [lib_x + 420, base_y + 560], "size": [400, 420], "flags": {}, "order": 3, "mode": 0,
                  "inputs": [{"name": "fallback", "type": "IMAGE", "link": l_fb}],
                  "outputs": [{"name": "image", "type": "IMAGE", "links": [l_ff, l_ref], "slot_index": 0}, {"name": "from_file", "type": "BOOLEAN", "links": [], "slot_index": 1}],
                  "properties": {"Node name for S&R": "MMXLoadChainFrame"}, "widgets_values": [CHAIN_FILE, True, "latest"],
                  "title": "MMX Load Chain Frame → Manager first_frame (last picture)"}
    old_ref = next(i for i in chk["inputs"] if i["name"] == "reference")
    old_link = old_ref["link"]
    old_ref["link"] = l_ref
    w["links"] = [l for l in w["links"] if l[0] != old_link]
    w["nodes"] = [n for n in w["nodes"] if n["id"] != 300]      # LoadImage "load the SAME image" — replaced by #404
    nodes.pop(300, None)
    w["links"] += [[l_fb, 402, 0, 404, 0, "IMAGE"], [l_ff, 404, 0, 185, len(mgr["inputs"]) - 1, "IMAGE"], [l_ref, 404, 0, 301, chk["inputs"].index(old_ref), "IMAGE"]]
    # 7. Prompt Affix between the Manager's prompt and its consumers; Stack triggers -> auto_triggers
    to_display = [l for l in w["links"] if l[1] == 185 and l[2] == 18 and l[3] == 186]
    to_r2v = [l for l in w["links"] if l[1] == 185 and l[2] == 18 and l[3] == 184]
    assert to_display and to_r2v, "prompt links 185:18 -> 186 / 184 not found"
    l_in, l_disp, l_r2v, l_trig = l_ref + 1, l_ref + 2, l_ref + 3, l_ref + 4
    disp = nodes[186]
    affix = {"id": 403, "type": "MMXPromptAffix", "pos": [disp["pos"][0], disp["pos"][1] + disp["size"][1] + 40], "size": [520, 330], "flags": {}, "order": 12, "mode": 0,
             "inputs": [{"name": "prompt", "type": "STRING", "link": l_in},
                        {"name": "auto_triggers", "type": "STRING", "widget": {"name": "auto_triggers"}, "link": l_trig}],
             "outputs": [{"name": "prompt", "type": "STRING", "links": [l_disp, l_r2v], "slot_index": 0}],
             "properties": {"Node name for S&R": "MMXPromptAffix"}, "widgets_values": ["", "", "prepend", ""], "title": "MMX Prompt Affix (triggers → prompt)"}
    for l in (to_display + to_r2v):
        w["links"].remove(l)
    w["links"] += [[l_in, 185, 18, 403, 0, "STRING"], [l_disp, 403, 0, 186, to_display[0][4], "STRING"],
                   [l_r2v, 403, 0, 184, to_r2v[0][4], "STRING"], [l_trig, 137, 3, 403, 1, "STRING"]]
    mgr_out = mgr["outputs"][18]
    mgr_out["links"] = [x for x in mgr_out["links"] if x not in (to_display[0][0], to_r2v[0][0])] + [l_in]
    for i in disp["inputs"]:
        if i["link"] == to_display[0][0]:
            i["link"] = l_disp
    for i in nodes[184]["inputs"]:
        if i["link"] == to_r2v[0][0]:
            i["link"] = l_r2v
    stack["outputs"][3]["links"] = [l_trig]
    w["last_link_id"] = l_trig
    w["nodes"] += [deck] + libs + [load_chain, affix]
    w["groups"].append({"id": 6, "title": "Deck / Library (push into the Manager)", "bounding": [deck["pos"][0] - 20, deck["pos"][1] - 60, 880 + 860 + 60, 1090], "color": "#a1309b", "flags": {}})
    # never ship a key: node 193 (OpenRouter API Key primitive) is blanked in BOTH value arrays —
    # the frontend also restores from widgets_values_named — and any sk-or-… string anywhere is dropped
    key = nodes.get(193)
    if key and key["type"] == "PrimitiveString":
        key["widgets_values"] = [""]
        if "widgets_values_named" in key:
            key["widgets_values_named"] = {k: "" for k in key["widgets_values_named"]}
    scrubbed = re.sub(r"sk-or-v1-[A-Za-z0-9]+", "", json.dumps(w))
    w = json.loads(scrubbed)
    w["last_node_id"] = max(w["last_node_id"], 404)
    w["id"] = "mmx-deck"
    w["revision"] = 0
    return w


def build_chain(deck: dict) -> dict:
    """deck.json + auto-chain: Load Chain Frame reads mmx_chain_last.png when present, MMX Chain
    Gate writes it from the decoded frames when the First Frame Check passed."""
    w = copy.deepcopy(deck)
    nodes = {n["id"]: n for n in w["nodes"]}
    lc, chk, dec = nodes[404], nodes[301], nodes[133]
    lc["widgets_values"] = [CHAIN_FILE, False, "latest"]
    lc["title"] = "MMX Load Chain Frame → Manager first_frame (chain)"
    lid = w["last_link_id"]
    l_gi, l_gp, l_ps, l_ss = lid + 1, lid + 2, lid + 3, lid + 4
    # lenient by default (strict = false): every run writes the chain frame, a failed check keeps its _REJECTED.png evidence
    gate = {"id": 406, "type": "MMXChainGate", "pos": [chk["pos"][0], chk["pos"][1] + chk["size"][1] + 40], "size": [360, 230], "flags": {}, "order": 13, "mode": 0,
            "inputs": [{"name": "images", "type": "IMAGE", "link": l_gi}, {"name": "passed", "type": "BOOLEAN", "link": l_gp},
                       {"name": "psnr", "type": "FLOAT", "link": l_ps}, {"name": "ssim", "type": "FLOAT", "link": l_ss}],
            "outputs": [{"name": "path", "type": "STRING", "links": [], "slot_index": 0}, {"name": "written", "type": "BOOLEAN", "links": [], "slot_index": 1}],
            "properties": {"Node name for S&R": "MMXChainGate"}, "widgets_values": [CHAIN_FILE, True, False], "title": "MMX Chain Gate → next segment (lenient)"}
    dec["outputs"][0]["links"].append(l_gi)
    chk["outputs"][0]["links"] = [l_ps]; chk["outputs"][1]["links"] = [l_ss]; chk["outputs"][2]["links"] = [l_gp]
    w["links"] += [[l_gi, 133, 0, 406, 0, "IMAGE"], [l_gp, 301, 2, 406, 1, "BOOLEAN"], [l_ps, 301, 0, 406, 2, "FLOAT"], [l_ss, 301, 1, 406, 3, "FLOAT"]]
    w["last_link_id"] = l_ss
    w["nodes"].append(gate)
    w["last_node_id"] = max(w["last_node_id"], 406)
    w["id"] = "mmx-deck-chain"
    return w


def build_guided(chain: dict) -> dict:
    """deck_chain.json + MiniMaxH3AddGuide #407: the Load Chain Frame image is pinned at frame 0
    (positive <- R2V, latent <- R2V, vae <- video VAE, image <- #404) and its conditioning feeds
    the BasicGuider instead of the R2V's; the check runs at THRESHOLD_GUIDED."""
    w = copy.deepcopy(chain)
    nodes = {n["id"]: n for n in w["nodes"]}
    r2v, guider, vae, lc, chk = nodes[184], nodes[149], nodes[121], nodes[404], nodes[301]
    links = {l[0]: l for l in w["links"]}
    cond_in = next(i for i in guider["inputs"] if i["name"] == "conditioning")
    old = links[cond_in["link"]]
    assert old[1] == 184 and old[2] == 0, old
    lid = w["last_link_id"]
    l_pos, l_lat, l_vae, l_img, l_out = lid + 1, lid + 2, lid + 3, lid + 4, lid + 5
    guide = {"id": 407, "type": "MiniMaxH3AddGuide", "pos": [guider["pos"][0] + 380, guider["pos"][1] + 400], "size": [340, 200], "flags": {}, "order": 14, "mode": 0,
             "inputs": [{"name": "positive", "type": "CONDITIONING", "link": l_pos}, {"name": "latent", "type": "LATENT", "link": l_lat},
                        {"name": "vae", "type": "VAE", "link": l_vae, "shape": 7}, {"name": "audio_vae", "type": "VAE", "link": None, "shape": 7},
                        {"name": "image", "type": "IMAGE", "link": l_img, "shape": 7}, {"name": "audio", "type": "AUDIO", "link": None, "shape": 7}],
             "outputs": [{"name": "positive", "type": "CONDITIONING", "links": [l_out], "slot_index": 0}],
             "properties": {"Node name for S&R": "MiniMaxH3AddGuide"}, "widgets_values": [0], "title": "MiniMaxH3AddGuide — chain frame pinned at frame 0"}
    w["links"] = [l for l in w["links"] if l[0] != old[0]]
    w["links"] += [[l_pos, 184, 0, 407, 0, "CONDITIONING"], [l_lat, 184, 1, 407, 1, "LATENT"], [l_vae, 121, 0, 407, 2, "VAE"], [l_img, 404, 0, 407, 4, "IMAGE"],
                   [l_out, 407, 0, 149, guider["inputs"].index(cond_in), "CONDITIONING"]]
    r2v["outputs"][0]["links"] = [x for x in r2v["outputs"][0]["links"] if x != old[0]] + [l_pos]
    r2v["outputs"][1]["links"].append(l_lat)
    vae["outputs"][0]["links"].append(l_vae)
    lc["outputs"][0]["links"].append(l_img)
    cond_in["link"] = l_out
    chk["widgets_values"] = [THRESHOLD_GUIDED, True]
    w["nodes"].append(guide)
    w["last_link_id"] = l_out
    w["last_node_id"] = max(w["last_node_id"], 407)
    w["id"] = "mmx-deck-chain-guided"
    return w


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True); ap.add_argument("--out", default=OUT); ap.add_argument("--out-chain", default=OUT_CHAIN); ap.add_argument("--out-guided", default=OUT_GUIDED)
    a = ap.parse_args()
    ui = build(json.load(open(a.base)))
    json.dump(ui, open(a.out, "w"), indent=1)
    print(f"wrote {a.out}: {len(ui['nodes'])} nodes, {len(ui['links'])} links")
    chain = build_chain(ui)
    json.dump(chain, open(a.out_chain, "w"), indent=1)
    print(f"wrote {a.out_chain}: {len(chain['nodes'])} nodes, {len(chain['links'])} links")
    guided = build_guided(chain)
    json.dump(guided, open(a.out_guided, "w"), indent=1)
    print(f"wrote {a.out_guided}: {len(guided['nodes'])} nodes, {len(guided['links'])} links")


if __name__ == "__main__":
    main()
