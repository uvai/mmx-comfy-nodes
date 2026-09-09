#!/usr/bin/env python3
"""Build examples/deck.json from the user's daily graph (base_v10_check.json = v10 + First Frame
Check) with the 0.3 "deck" nodes slotted in and NOTHING else touched:

  * Power Lora Loader (rgthree) #137  ->  MMX LoRA Stack #137 (same id, same position, same links:
    model from ModelPreviewOverrideKJ, clip from CLIPLoader, MODEL out into the turbo LoRA #158,
    which stays untouched)
  * MMX Deck #400 added, NOT wired (it pushes into the Manager #185 and the Stack #137)
  * MMX Library Image #401 (slot Picture 1) and #402 (slot Picture 9), not wired: Inject pushes
  * MMX First Frame Check #301: widgets [threshold_db, enabled=true]; its `reference` input is
    optional now, so with LoadImage #300 empty the check skips cleanly

    python3 tools/build_deck.py --base /opt/subgenula/transfer/base_v10_check.json
"""
import argparse, copy, json, os, re

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "examples", "deck.json")
ROWS = 5
LORAS = ["H3_Motion_BoosterV2.safetensors"]     # row 1 of the stack; edit in the node / send from the Deck
LIB_IDENTITY = "Subjects/j/identity.png"
LIB_FIRST = "Sets/room/first_frame.png"


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
                         {"name": "stack", "type": "STRING", "links": [], "slot_index": 2}],
             "properties": {"Node name for S&R": "MMXLoRAStack"}, "widgets_values": stack_widgets([{"name": LORAS[0], "strength": 0.7}]),
             "title": "MMX LoRA Stack", "color": pl.get("color", "#223"), "bgcolor": pl.get("bgcolor", "#335")}
    w["nodes"] = [stack if n["id"] == 137 else n for n in w["nodes"]]
    # widen the "LoRAs And Settings" group so the stack fits
    for g in w["groups"]:
        if g["title"] == "LoRAs And Settings":
            g["bounding"][2] = max(g["bounding"][2], 480)
    # 2. First Frame Check: enabled toggle
    chk = nodes[301]
    assert chk["type"] == "MMXFirstFrameCheck"
    chk["widgets_values"] = [float(chk["widgets_values"][0]) if chk.get("widgets_values") else 24.0, True]
    chk["size"] = [360, 230]
    for i in chk["inputs"]:
        if i["name"] == "reference":
            i["shape"] = 7   # optional
    # 3. Deck (not wired) below the References Manager (the area above it holds the HearmemanAI note)
    mgr = nodes[185]
    base_y = mgr["pos"][1] + mgr["size"][1] + 120
    deck = {"id": 400, "type": "MMXDeck", "pos": [mgr["pos"][0], base_y], "size": [840, 850], "flags": {}, "order": 0, "mode": 0,
            "inputs": [], "outputs": [{"name": "prompt", "type": "STRING", "links": [], "slot_index": 0}, {"name": "loras_json", "type": "STRING", "links": [], "slot_index": 1}],
            "properties": {"Node name for S&R": "MMXDeck"},
            "widgets_values": ["<Subject 1> hugs <Subject 2>", "", json.dumps([{"name": LORAS[0], "strength": 0.7, "on": True}]), json.dumps({"manager": 185, "stack": 137})],
            "title": "MMX Deck"}
    # 4. two Library Image nodes (not wired): identity -> Picture 1, first frame -> Picture 9
    libs = []
    for i, (path, slot, title) in enumerate([(LIB_IDENTITY, "Picture 1", "MMX Library Image — identity → Picture 1"),
                                              (LIB_FIRST, "Picture 9", "MMX Library Image — first frame → Picture 9")]):
        libs.append({"id": 401 + i, "type": "MMXLibraryImage", "pos": [mgr["pos"][0] + 880 + i * 420, base_y], "size": [400, 560], "flags": {}, "order": 1 + i, "mode": 0,
                     "inputs": [], "outputs": [{"name": "image", "type": "IMAGE", "links": [], "slot_index": 0}, {"name": "filename", "type": "STRING", "links": [], "slot_index": 1},
                                               {"name": "path", "type": "STRING", "links": [], "slot_index": 2}],
                     "properties": {"Node name for S&R": "MMXLibraryImage"}, "widgets_values": [path, slot], "title": title})
    w["nodes"] += [deck] + libs
    w["groups"].append({"id": 6, "title": "Deck / Library (push into the Manager)", "bounding": [deck["pos"][0] - 20, deck["pos"][1] - 60, 880 + 840 + 40, 920], "color": "#a1309b", "flags": {}})
    # never ship a key: node 193 (OpenRouter API Key primitive) is blanked in BOTH value arrays —
    # the frontend also restores from widgets_values_named — and any sk-or-… string anywhere is dropped
    key = nodes.get(193)
    if key and key["type"] == "PrimitiveString":
        key["widgets_values"] = [""]
        if "widgets_values_named" in key:
            key["widgets_values_named"] = {k: "" for k in key["widgets_values_named"]}
    scrubbed = re.sub(r"sk-or-v1-[A-Za-z0-9]+", "", json.dumps(w))
    w = json.loads(scrubbed)
    w["last_node_id"] = max(w["last_node_id"], 402)
    w["id"] = "mmx-deck"
    w["revision"] = 0
    return w


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True); ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    ui = build(json.load(open(a.base)))
    json.dump(ui, open(a.out, "w"), indent=1)
    print(f"wrote {a.out}: {len(ui['nodes'])} nodes, {len(ui['links'])} links")


if __name__ == "__main__":
    main()
