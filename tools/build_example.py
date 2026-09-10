#!/usr/bin/env python3
"""Build the example chain workflow from the studio's R2V API template.

Produces four files in examples/:
  chain_3seg_api.json  — API format (POST /prompt body["prompt"]); what the live check submits
  chain_3seg.json      — UI format for the canvas (Load / drag-drop), auto-laid-out
  chain_check_api.json / chain_check.json — the same chain with the library + verification nodes:
      MMX Library Image x2 -> MMX References Builder -> References Manager (references_json +
      picture_map appended to the preset prompt), MMX Load Chain Frame -> MiniMaxH3AddGuide,
      MMX First Frame Check on the decoded frames -> MMX Chain Gate writing the next segment's
      frame only when the check passed

Graph = the R2V template with:
  * ResolutionSelector + key primitive removed (literal width/height, empty key -> env key)
  * MMX Sequence (3 preset slots, index from a PrimitiveInt with control_after_generate=increment)
    -> model/clip into the LoRA chain, prompt into the RefPack's direction
  * MMX Load Chain Frame (fallback = the slot-9 reference image) -> MiniMaxH3AddGuide at frame 0
  * ImageFromBatch(-1) -> MMX Save Frame (fixed name) so the next queued run continues exactly

    python3 tools/build_example.py --template "../mmx/MiniMax - R2V - Auto Prompt v6 API v2.json" --object-info oi_dir
"""
import argparse, copy, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(os.path.dirname(HERE), "examples")

PRESETS = ["establishing", "close up", "walk"]     # slot names; edit in the node
FIRST_IMAGE = "first_frame.png"                    # the slot-9 image in ComfyUI/input
REF_IMAGE = "identity.png"                         # <Picture 1>
CHAIN_FILE = "mmx_chain_last.png"
LIB_IDENTITY = "Subjects/j/identity.png"     # library paths (relative to /workspace/mmx/library); pick yours in the node
LIB_FIRST = "Sets/room/first_frame.png"
THRESHOLD_DB = 24.0


def build_api(template: dict) -> dict:
    p = copy.deepcopy(template)
    p.pop("115", None); p.pop("193", None)
    W, H = 768, 448   # both multiples of 32: the DiT patchifies 16px latents by 2, so 432 (latent 27) fails to reshape
    # direction comes from the sequence; references: identity + chain frame both as pictures
    p["185"]["inputs"].update({"width": W, "height": H, "openrouter_api_key": "", "prompt_provider": "openrouter",
                               "references_json": json.dumps({"references": [{"kind": "image", "file": REF_IMAGE}, {"kind": "image", "file": FIRST_IMAGE}]}),
                               "direction": ["300", 2]})
    p["184"]["inputs"].update({"width": W, "height": H})
    p["132"]["inputs"]["value"] = 3
    # model/clip through the sequence node before the acc / turbo LoRA chain
    p["300"] = {"class_type": "MMXSequence", "_meta": {"title": "MMX Sequence (3 presets)"},
                "inputs": {"model": ["135", 0], "clip": ["127", 0], "index": ["301", 0], "strength_scale": 1.0,
                           **{f"preset_{i}": (PRESETS[i - 1] if i <= len(PRESETS) else "(none)") for i in range(1, 9)}}}
    p["301"] = {"class_type": "PrimitiveInt", "_meta": {"title": "segment index (control_after_generate = increment)"},
                "inputs": {"value": 0, "control_after_generate": "increment"}}
    p["141"]["inputs"]["model"] = ["300", 0]      # ModelPreviewOverrideKJ took UNETLoader before
    p["137"]["inputs"]["clip"] = ["300", 1]       # Power Lora Loader clip
    # chain frame: fallback = slot-9 image; then hard guide at frame 0
    p["302"] = {"class_type": "LoadImage", "_meta": {"title": "first frame (slot 9) — fallback for segment 1"}, "inputs": {"image": FIRST_IMAGE}}
    p["303"] = {"class_type": "MMXLoadChainFrame", "_meta": {"title": "MMX Load Chain Frame"},
                "inputs": {"fallback": ["302", 0], "filename": CHAIN_FILE, "use_fallback": False}}
    p["304"] = {"class_type": "MiniMaxH3AddGuide", "_meta": {"title": "frame-0 guide"},
                "inputs": {"positive": ["184", 0], "latent": ["184", 1], "vae": ["121", 0], "image": ["303", 0], "frame_idx": 0}}
    p["149"]["inputs"]["conditioning"] = ["304", 0]
    # last frame -> fixed-name save (keeps the template's SaveImage 195 as well)
    p["305"] = {"class_type": "MMXSaveFrame", "_meta": {"title": "MMX Save Frame (chain)"}, "inputs": {"image": ["194", 0], "filename": CHAIN_FILE}}
    p["119"]["inputs"]["filename_prefix"] = "mmx_chain/seg"
    p["195"]["inputs"]["filename_prefix"] = "mmx_chain/last"
    p["186"]["inputs"]["source"] = ["185", 18]
    return p


def build_check_api(template: dict) -> dict:
    """chain_3seg + library / references / first-frame verification."""
    p = build_api(template)
    p.pop("302"); p.pop("305")                       # LoadImage fallback + fixed-name save: replaced below
    p["310"] = {"class_type": "MMXLibraryImage", "_meta": {"title": "MMX Library Image — identity (<Picture 1>)"}, "inputs": {"file": LIB_IDENTITY}}
    p["311"] = {"class_type": "MMXLibraryImage", "_meta": {"title": "MMX Library Image — first frame (slot 9)"}, "inputs": {"file": LIB_FIRST}}
    p["312"] = {"class_type": "MMXReferencesBuilder", "_meta": {"title": "MMX References Builder"},
                "inputs": {"image_1": ["310", 1], "image_9": ["311", 1], "use_soundtrack": True}}
    p["313"] = {"class_type": "StringConcatenate", "_meta": {"title": "prompt + picture map"},
                "inputs": {"string_a": ["300", 2], "string_b": ["312", 1], "delimiter": "\n"}}
    p["185"]["inputs"].update({"references_json": ["312", 0], "direction": ["313", 0]})
    p["303"]["inputs"]["fallback"] = ["311", 0]      # segment 1 opens on the slot-9 image, later ones on the gated frame
    p["320"] = {"class_type": "MMXFirstFrameCheck", "_meta": {"title": "MMX First Frame Check"},
                "inputs": {"images": ["133", 0], "reference": ["303", 0], "threshold_db": THRESHOLD_DB}}
    p["321"] = {"class_type": "MMXChainGate", "_meta": {"title": "MMX Chain Gate -> next segment"},
                "inputs": {"images": ["133", 0], "passed": ["320", 2], "filename": CHAIN_FILE, "stop_queue": True}}
    return p


# ── API -> UI conversion ──────────────────────────────────────────────────────
def load_oi(d: str) -> dict:
    oi = {}
    for f in os.listdir(d):
        if f.endswith(".json"):
            try:
                oi.update(json.load(open(os.path.join(d, f))))
            except Exception:
                pass
    return oi


LOCAL_OI = {   # our own nodes (not in an object_info dump taken before the pack was installed)
    "MMXSequence": {"input": {"required": {"model": ["MODEL"], "clip": ["CLIP"], "index": ["INT", {}], "strength_scale": ["FLOAT", {}],
                                            **{f"preset_{i}": [["(none)"], {}] for i in range(1, 9)}}},
                    "input_order": {"required": ["model", "clip", "index", "strength_scale"] + [f"preset_{i}" for i in range(1, 9)]},
                    "output": ["MODEL", "CLIP", "STRING", "STRING", "INT", "INT"], "output_name": ["model", "clip", "prompt", "preset_name", "slot", "count"]},
    "MMXLoadChainFrame": {"input": {"required": {"fallback": ["IMAGE"], "filename": ["STRING", {}], "use_fallback": ["BOOLEAN", {}]}, "optional": {"use_frame": [["latest"], {"default": "latest"}]}},
                          "input_order": {"required": ["fallback", "filename", "use_fallback"], "optional": ["use_frame"]}, "output": ["IMAGE", "BOOLEAN"], "output_name": ["image", "from_file"]},
    "MMXSaveFrame": {"input": {"required": {"image": ["IMAGE"], "filename": ["STRING", {}]}}, "input_order": {"required": ["image", "filename"]},
                     "output": ["STRING"], "output_name": ["path"]},
    "MMXLibraryImage": {"input": {"required": {"file": [[LIB_IDENTITY, LIB_FIRST], {}]}}, "input_order": {"required": ["file"]},
                        "output": ["IMAGE", "STRING", "STRING"], "output_name": ["image", "filename", "path"]},
    "MMXReferencesBuilder": {"input": {"required": {}, "optional": {**{f"image_{i}": ["STRING", {}] for i in range(1, 10)}, **{f"video_{i}": ["STRING", {}] for i in range(1, 4)},
                                                                    "audio_1": ["STRING", {}], "use_soundtrack": ["BOOLEAN", {}]}},
                             "input_order": {"required": [], "optional": [f"image_{i}" for i in range(1, 10)] + [f"video_{i}" for i in range(1, 4)] + ["audio_1", "use_soundtrack"]},
                             "output": ["STRING", "STRING"], "output_name": ["references_json", "picture_map"]},
    "MMXFirstFrameCheck": {"input": {"required": {"images": ["IMAGE"], "reference": ["IMAGE"], "threshold_db": ["FLOAT", {}]}},
                           "input_order": {"required": ["images", "reference", "threshold_db"]},
                           "output": ["FLOAT", "FLOAT", "BOOLEAN", "IMAGE"], "output_name": ["psnr", "ssim", "passed", "comparison"]},
    "MMXChainGate": {"input": {"required": {"images": ["IMAGE"], "passed": ["BOOLEAN", {"forceInput": True}], "filename": ["STRING", {}]}, "optional": {"stop_queue": ["BOOLEAN", {}]}},
                     "input_order": {"required": ["images", "passed", "filename"], "optional": ["stop_queue"]},
                     "output": ["STRING", "BOOLEAN"], "output_name": ["path", "written"]},
    "StringConcatenate": {"input": {"required": {"string_a": ["STRING", {}], "string_b": ["STRING", {}], "delimiter": ["STRING", {}]}},
                          "input_order": {"required": ["string_a", "string_b", "delimiter"]}, "output": ["STRING"], "output_name": ["STRING"]},
}
LINK_TYPES = {"MODEL", "CLIP", "VAE", "IMAGE", "LATENT", "CONDITIONING", "AUDIO", "SIGMAS", "NOISE", "GUIDER", "SAMPLER", "STRING", "INT", "FLOAT", "BOOLEAN", "*"}


def to_ui(api: dict, oi: dict, wid: str = "mmx-chain-3seg") -> dict:
    oi = {**LOCAL_OI, **oi}   # a real object_info dump (taken with the pack installed) wins over the built-in stubs
    nodes, links = [], []
    link_id = 1
    # topological depth for layout
    deps = {nid: [str(v[0]) for v in n["inputs"].values() if isinstance(v, list) and len(v) == 2] for nid, n in api.items()}
    depth = {}
    def d(nid, seen=()):
        if nid in depth: return depth[nid]
        if nid in seen: return 0
        depth[nid] = 1 + max([d(x, seen + (nid,)) for x in deps.get(nid, []) if x in api] or [-1])
        return depth[nid]
    for nid in api: d(nid)
    cols = {}
    for nid in sorted(api, key=lambda x: (depth[x], int(x) if x.isdigit() else 0)):
        cols.setdefault(depth[nid], []).append(nid)
    pos = {}
    for c, ids in cols.items():
        for r, nid in enumerate(ids):
            pos[nid] = [80 + c * 420, 80 + r * 260]
    out_links = {}   # (src, slot) -> [link ids]
    ui_nodes = {}
    for nid, n in api.items():
        cls = n["class_type"]; info = oi.get(cls)
        if not info:
            raise SystemExit(f"no object_info for {cls}; dump it with GET /object_info/{cls}")
        req = info["input"].get("required", {}); opt = info["input"].get("optional", {})
        order = (info.get("input_order") or {}).get("required", list(req)) + (info.get("input_order") or {}).get("optional", list(opt))
        allspec = {**req, **opt}
        inputs, widgets_values = [], []
        for name in order:
            spec = allspec.get(name); v = n["inputs"].get(name)
            typ = spec[0] if spec else "*"
            is_widget = (isinstance(typ, list) or typ in ("INT", "FLOAT", "STRING", "BOOLEAN")) and not (spec and len(spec) > 1 and isinstance(spec[1], dict) and spec[1].get("forceInput"))
            if isinstance(v, list) and len(v) == 2 and str(v[0]) in api:
                entry = {"name": name, "type": typ if isinstance(typ, str) else "COMBO", "link": None, "_src": (str(v[0]), int(v[1]))}
                if is_widget:
                    entry["widget"] = {"name": name}
                    # a widget converted to an input keeps a placeholder value in widgets_values …
                    widgets_values.append(typ[0] if isinstance(typ, list) and typ else {"INT": 0, "FLOAT": 0.0, "STRING": "", "BOOLEAN": False}.get(typ, ""))
                    # … and an INT/FLOAT with control_after_generate keeps its control value too
                    if len(spec) > 1 and isinstance(spec[1], dict) and spec[1].get("control_after_generate") is not None and typ in ("INT", "FLOAT"):
                        widgets_values.append(spec[1].get("control_after_generate") or "fixed")
                inputs.append(entry)
            elif is_widget:
                if name in n["inputs"]:
                    widgets_values.append(v)
                elif name != "control_after_generate":
                    # an unset optional widget still occupies its positional slot: use the spec default
                    opts = spec[1] if spec and len(spec) > 1 and isinstance(spec[1], dict) else {}
                    widgets_values.append(opts.get("default", typ[0] if isinstance(typ, list) and typ else {"INT": 0, "FLOAT": 0.0, "STRING": "", "BOOLEAN": False}.get(typ, "")))
                if name != "control_after_generate" and spec and len(spec) > 1 and isinstance(spec[1], dict) and spec[1].get("control_after_generate") is not None and typ in ("INT", "FLOAT"):
                    widgets_values.append(spec[1].get("control_after_generate") or "fixed")
            else:
                inputs.append({"name": name, "type": typ if isinstance(typ, str) else "*", "link": None})
        # the API-format value list for dict-widgets (Power Lora Loader) is passed through
        if cls == "Power Lora Loader (rgthree)":
            widgets_values = [v for k, v in n["inputs"].items() if not (isinstance(v, list) and len(v) == 2 and str(v[0]) in api)]
        elif cls == "VHS_VideoCombine":
            widgets_values = {k: v for k, v in n["inputs"].items() if not (isinstance(v, list) and len(v) == 2 and str(v[0]) in api)}
        elif cls == "PrimitiveInt":
            widgets_values = [n["inputs"].get("value", 0), n["inputs"].get("control_after_generate", "fixed")]
        elif cls == "RandomNoise":
            widgets_values = [n["inputs"].get("noise_seed", 0), "fixed"]
        outputs = [{"name": on, "type": ot, "links": [], "slot_index": i} for i, (on, ot) in enumerate(zip(info.get("output_name", info["output"]), info["output"]))]
        ui_nodes[nid] = {"id": int(nid), "type": cls, "pos": pos[nid], "size": [360, 120 + 24 * (len(inputs) + len(widgets_values) if isinstance(widgets_values, list) else 8)],
                         "flags": {}, "order": depth[nid], "mode": 0, "inputs": inputs, "outputs": outputs, "properties": {"Node name for S&R": cls},
                         "widgets_values": widgets_values, "title": n.get("_meta", {}).get("title") or None}
    for nid, un in ui_nodes.items():
        for inp in un["inputs"]:
            if "_src" in inp:
                src, slot = inp.pop("_src")
                lt = inp["type"] if inp["type"] not in ("COMBO", "*") else (ui_nodes[src]["outputs"][slot]["type"] if slot < len(ui_nodes[src]["outputs"]) else "*")
                links.append([link_id, int(src), slot, int(nid), un["inputs"].index(inp), lt])
                inp["link"] = link_id
                if slot < len(ui_nodes[src]["outputs"]): ui_nodes[src]["outputs"][slot]["links"].append(link_id)
                link_id += 1
        if un["title"] is None: un.pop("title")
        if un["widgets_values"] == []: un.pop("widgets_values")
    return {"id": wid, "revision": 0, "last_node_id": max(int(x) for x in api), "last_link_id": link_id - 1,
            "nodes": [ui_nodes[k] for k in sorted(ui_nodes, key=int)], "links": links, "groups": [], "config": {}, "extra": {}, "version": 0.4}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--template", required=True); ap.add_argument("--object-info", required=True)
    a = ap.parse_args()
    os.makedirs(OUT, exist_ok=True)
    oi = load_oi(a.object_info)
    template = json.load(open(a.template))
    for name, builder in (("chain_3seg", build_api), ("chain_check", build_check_api)):
        api = builder(template)
        json.dump(api, open(os.path.join(OUT, f"{name}_api.json"), "w"), indent=1)
        ui = to_ui(api, oi, "mmx-" + name.replace("_", "-"))
        json.dump(ui, open(os.path.join(OUT, f"{name}.json"), "w"), indent=1)
        print(f"wrote {OUT}/{name}_api.json ({len(api)} nodes) and {name}.json ({len(ui['links'])} links)")


if __name__ == "__main__":
    main()
