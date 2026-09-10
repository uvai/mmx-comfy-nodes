#!/usr/bin/env python3
"""Frontend checks for the deck build against a running ComfyUI (playwright chromium, headless).

    python3 tools/ui_check_deck.py --server http://127.0.0.1:8188 [--frame s1_first.png] [--shots DIR]
                                   [--empty-server http://127.0.0.1:8189] [--vue-too] [--loras-dir …] [--library-dir …]

Loads examples/deck.json and drives the real widgets: the Deck panel fills the node and sizes it
at 400 / 700 / 1000 px and after a reload (screenshots with --shots; --vue-too repeats that in
the frontend's Nodes 2.0 mode), tag buttons follow the Manager's slots (a linked first_frame
counts as the last picture), Inject from the two Library nodes lands in the Manager's slot UI,
Send lands in the Manager's direction + the Stack's rows (visible in both), the exported API
JSON carries the same values, preset save / load / update / delete round-trip, phrase chips
insert, the First Frame Check skips cleanly with no reference, the Manager's first_frame input
(run 1 = the Load Chain Frame fallback, run 2 = the Chain Gate's frame), the MMX References
Manager drop-in re-renders on an external widget write, the workflow serialises the pushed
state, and — against --empty-server, a ComfyUI whose library mirror is empty — deck.json and
deck_chain.json load with zero validation errors (an unset Library file is only an error on queue).
"""
import argparse, io, json, os, sys, time, urllib.parse, urllib.request

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
EX = os.path.join(os.path.dirname(HERE), "examples", "deck.json")
EX_CHAIN = os.path.join(os.path.dirname(HERE), "examples", "deck_chain.json")
results = []

WIDTHS = (400, 700, 1000)
# geometry of the Deck panel inside node 400 at the current size
JS_LAYOUT = """() => { const n = app.graph.getNodeById(400); const ui = n._mmxDeck; const rr = ui.root.getBoundingClientRect(), ir = ui.inner.getBoundingClientRect();
    const s = app.canvas.ds.scale || 1;
    const strength = ui.rows.map(r => { const b = r.str.getBoundingClientRect(); return [b.width / s, (rr.right - b.right) / s, b.height > 0]; });
    const sel = ui.rows.map(r => r.sel.getBoundingClientRect().width / s);
    const tagBtn = ui.tagButtons['<Picture 1>'].getBoundingClientRect(), preset = ui.presetSel.getBoundingClientRect();
    let titleBottom = null;
    if (LiteGraph.vueNodesMode) { const h = ui.root.closest('[data-node-id]')?.querySelector('.lg-node-header, [data-testid^="node-header"]'); if (h) titleBottom = h.getBoundingClientRect().bottom; }
    else { const cr = app.canvas.canvas.getBoundingClientRect(); titleBottom = cr.top + (n.pos[1] + app.canvas.ds.offset[1]) * s; }
    return {size: n.size.slice(), vue: !!LiteGraph.vueNodesMode, rootW: rr.width / s, rootH: rr.height / s, innerH: ir.height / s, innerOverflow: (ir.bottom - rr.bottom) / s,
            strength, sel, chips: [ui.phrases.scrollHeight, ui.phrases.clientHeight], pad: ui.root.style.paddingTop,
            toolbarTop: titleBottom == null ? null : (Math.min(tagBtn.top, preset.top) - titleBottom) / s, minNodeH: n.computeSize()[1]}; }"""
JS_CENTER = """() => { const n = app.graph.getNodeById(400); app.canvas.ds.scale = 1; app.canvas.centerOnNode(n); app.canvas.setDirty(true, true); }"""
JS_SETW = """(w) => { const n = app.graph.getNodeById(400); n.setSize([w, n.size[1]]); n.onResize?.(n.size); app.canvas.ds.scale = 1; app.canvas.centerOnNode(n); app.canvas.setDirty(true, true); }"""
JS_RECT = """(id) => { const n = app.graph.getNodeById(id); const c = app.canvas; const r = c.canvas.getBoundingClientRect(); const s = c.ds.scale, o = c.ds.offset;
    return {x: r.left + (n.pos[0] + o[0]) * s, y: r.top + (n.pos[1] + o[1] - LiteGraph.NODE_TITLE_HEIGHT) * s, w: n.size[0] * s, h: (n.size[1] + LiteGraph.NODE_TITLE_HEIGHT) * s}; }"""


def node_shot(pg, path, nid, pad=10):
    if not path:
        return
    r = pg.evaluate(JS_RECT, nid)
    vp = pg.viewport_size
    x, y = max(0, r["x"] - pad), max(0, r["y"] - pad)
    pg.screenshot(path=path, clip={"x": x, "y": y, "width": max(1, min(vp["width"] - x, r["w"] + 2 * pad)), "height": max(1, min(vp["height"] - y, r["h"] + 2 * pad))})


def layout_ok(g, w):
    """the panel fills the node, nothing is clipped, the strength inputs sit beside the dropdowns, chips do not scroll"""
    fills = g["rootW"] >= w - 30 and g["rootW"] <= w
    fits = g["innerOverflow"] <= 1 and g["size"][1] + 1 >= g["minNodeH"]
    strength = all(sw >= 50 and inside >= 0 and vis for sw, inside, vis in g["strength"]) and all(x >= 100 for x in g["sel"])
    chips = g["chips"][0] <= g["chips"][1] + 1
    toolbar = g["toolbarTop"] is None or g["toolbarTop"] >= -0.5
    return fills and fits and strength and chips and toolbar


def layout_checks(pg, shot, label):
    for w in WIDTHS:
        pg.evaluate(JS_SETW, w); time.sleep(1.2)
        g = pg.evaluate(JS_LAYOUT)
        check(f"{label} {w} px: panel fills the node (root {g['rootW']:.0f} of {w}), content fits (node {g['size'][1]:.0f} ≥ min {g['minNodeH']:.0f}, overflow {g['innerOverflow']:.0f}), "
              f"strength inputs visible beside the dropdowns, chips wrap ({g['chips'][0]}/{g['chips'][1]}), toolbar below the title (+{g['toolbarTop'] if g['toolbarTop'] is None else round(g['toolbarTop'])} px, pad {g['pad'] or '0'})",
              layout_ok(g, w), json.dumps(g))
        shot(f"deck_layout_{label}_{w}")


def fetch_png(server, name, typ="input"):
    from PIL import Image
    with urllib.request.urlopen(f"{server}/view?filename={urllib.parse.quote(name)}&type={typ}") as r:
        return Image.open(io.BytesIO(r.read())).convert("RGB")


def same_image(a, b):
    from PIL import ImageChops
    return a.size == b.size and ImageChops.difference(a, b).getbbox() is None


def check(name, cond, detail=""):
    results.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f"  — {detail}" if detail and not cond else ""), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8188")
    ap.add_argument("--frame", default="s1_first.png", help="an image in ComfyUI/input for the check-skip run")
    ap.add_argument("--shots", default="")
    ap.add_argument("--loras-dir", default="", help="ComfyUI/models/loras on this host: a file is dropped there to test the R refresh")
    ap.add_argument("--library-dir", default="", help="the library mirror root on this host (MMX_LIBRARY): a file is dropped there for the same test")
    ap.add_argument("--lib-a", default="Subjects/j/identity.png"); ap.add_argument("--lib-b", default="Sets/room/first_frame.png"); ap.add_argument("--lib-video", default="VideoRef/seg1.mp4")
    ap.add_argument("--empty-server", default="", help="a ComfyUI whose library mirror is EMPTY: both examples must load there with zero validation errors")
    ap.add_argument("--vue-too", action="store_true", help="repeat the layout checks with the frontend's Nodes 2.0 (Vue) mode on")
    ap.add_argument("--input-dir", default="", help="ComfyUI/input on this host (only to delete the first_frame test's chain file afterwards)")
    a = ap.parse_args()
    shot = (lambda n: None) if not a.shots else (lambda n: pg.screenshot(path=os.path.join(a.shots, n + ".png")))
    with sync_playwright() as p:
        b = p.chromium.launch()
        global pg
        pg = b.new_page(viewport={"width": 1900, "height": 1150})
        errors = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
        pg.goto(a.server)
        pg.wait_for_function("() => window.app && window.app.graph && Object.keys(LiteGraph.registered_node_types).length > 100", timeout=180000)
        time.sleep(2)
        have = pg.evaluate("() => ['MMXDeck','MMXLoRAStack','MMXReferencesManager','MMXLibraryImage','MMXFirstFrameCheck'].map(n => !!LiteGraph.registered_node_types[n])")
        check("frontend registered Deck / LoRA Stack / References Manager / Library Image / First Frame Check", all(have), str(have))
        check("window.mmx API present", pg.evaluate("() => !!(window.mmx && window.mmx.setReferences && window.mmx.setDirection && window.mmx.setStack && window.mmx.injectLibrary)"))

        # 1. load deck.json
        wf = json.load(open(EX))
        wf_nodes = {n["id"]: n for n in wf["nodes"]}
        shipped = {n["id"]: list(n["widgets_values"]) for n in wf["nodes"] if n["type"] == "MMXLibraryImage"}
        check("shipped deck.json: both Library nodes carry file = '' (nothing picked) and a slot from the node's list", shipped == {401: ["", "Picture 1"], 402: ["", "(none)"]}, str(shipped))
        for n in wf["nodes"]:
            if n["type"] == "MMXLibraryImage":
                n["widgets_values"] = [a.lib_a, "Picture 1"] if n["id"] == 401 else [a.lib_b, "Picture 9"]   # this host's files; #402 injects into Picture 9 below
        pg.evaluate("wf => app.loadGraphData(wf)", wf)
        time.sleep(4)
        info = pg.evaluate("""() => {
            const nodes = app.graph._nodes || app.graph.nodes;
            const missing = nodes.filter(n => !LiteGraph.registered_node_types[n.type]).map(n => n.type);
            const by = id => app.graph.getNodeById(id);
            const stack = by(137), deck = by(400), mgr = by(185), chk = by(301);
            return {count: nodes.length, missing,
                    stack: stack && {type: stack.type, inputs: stack.inputs.map(i => [i.name, i.link]), out0: stack.outputs[0].links, widgets: stack.widgets.map(w => [w.name, w.value])},
                    turbo: by(158) && {type: by(158).type, inLink: by(158).inputs[0].link, wv: by(158).widgets.map(w => w.value)},
                    deck: deck && {hasPanel: !!deck._mmxDeck, hidden: deck.widgets.filter(w => ['prompt','preset','loras_json','targets_json'].includes(w.name)).map(w => [w.name, w.hidden === true, w.computeSize()[1]]), size: deck.size},
                    mgr: mgr && {type: mgr.type, hasBody: !!mgr._mmrpBody, hooked: [mgr.widgets.find(w => w.name === 'references_json')?._mmxHooked, mgr.widgets.find(w => w.name === 'direction')?._mmxHooked], outputs: mgr.outputs.length},
                    chk: chk && {inputs: chk.inputs.map(i => [i.name, i.link, i.shape]), widgets: chk.widgets.map(w => [w.name, w.value])}};
        }""")
        print("   loaded:", json.dumps(info)[:1200])
        check("deck.json loads with no missing node types (33 nodes)", info["count"] == 33 and not info["missing"], str(info["missing"]))
        aff = pg.evaluate("""() => { const a = app.graph.getNodeById(403), s = app.graph.getNodeById(137), m = app.graph.getNodeById(185), d = app.graph.getNodeById(186), r = app.graph.getNodeById(184);
            const L = id => app.graph.links.get ? app.graph.links.get(id) : app.graph.links[id];
            const src = (n, name) => { const i = n.inputs.find(x => x.name === name); const l = i && i.link != null && L(i.link); return l ? [Number(l.origin_id), Number(l.origin_slot)] : null; };
            return {stackOutputs: s.outputs.map(o => o.name), affixPrompt: src(a, 'prompt'), affixTrig: src(a, 'auto_triggers'), dispSrc: src(d, 'source'), r2vPrompt: src(r, 'prompt'),
                    widgets: a.widgets.map(w => [w.name, w.value]), mgrPromptLinks: m.outputs[18].links.length}; }""")
        check("Stack has triggers/phrases outputs; Affix #403 wired: Manager prompt -> Affix -> Display + R2V prompt; Stack triggers -> auto_triggers",
              aff["stackOutputs"] == ["model", "clip", "stack", "triggers", "phrases"] and aff["affixPrompt"] == [185, 18] and aff["affixTrig"] == [137, 3] and aff["dispSrc"] == [403, 0] and aff["r2vPrompt"] == [403, 0] and aff["mgrPromptLinks"] == 1
              and dict(aff["widgets"]).get("mode") == "prepend", str(aff))
        st = info["stack"]
        check("MMX LoRA Stack #137 replaced the Power Lora Loader: model+clip links kept, MODEL out -> turbo LoRA #158 (untouched)",
              st and st["type"] == "MMXLoRAStack" and dict(st["inputs"])["model"] == 278 and dict(st["inputs"])["clip"] == 258 and st["out0"] == [334]
              and info["turbo"]["inLink"] == 334 and info["turbo"]["wv"][0].startswith("minimax_h3_fl2v_turbo") and abs(info["turbo"]["wv"][1] - 0.85) < 1e-9, str(st) + str(info["turbo"]))
        check("stack rows restored from widgets_values (row 1 on, H3_Motion_BoosterV2 @ 0.7)", dict(st["widgets"]).get("on_1") is True and dict(st["widgets"]).get("lora_1") == "H3_Motion_BoosterV2.safetensors" and abs(dict(st["widgets"]).get("strength_1") - 0.7) < 1e-9, str(st["widgets"]))
        check("Deck panel built; its 4 state widgets hidden (0 height)", info["deck"]["hasPanel"] and len(info["deck"]["hidden"]) == 4 and all(h[1] and h[2] == 0 for h in info["deck"]["hidden"]), str(info["deck"]))
        check("MMX References Manager #185 (drop-in) has the RefPack body, 20 outputs, and the mmx write hooks", info["mgr"]["type"] == "MMXReferencesManager" and info["mgr"]["hasBody"] and info["mgr"]["outputs"] == 20 and all(h and h.startswith(n) for h, n in zip(info["mgr"]["hooked"], ["references_json", "direction"])), str(info["mgr"]))
        ff = pg.evaluate("""() => { const m = app.graph.getNodeById(185), lc = app.graph.getNodeById(404), lib = app.graph.getNodeById(402), chk = app.graph.getNodeById(301);
            const L = id => app.graph.links.get ? app.graph.links.get(id) : app.graph.links[id];
            const src = (n, name) => { const i = n.inputs.find(x => x.name === name); const l = i && i.link != null && L(i.link); return l ? [Number(l.origin_id), Number(l.origin_slot)] : null; };
            return {ff: src(m, 'first_frame'), fallback: src(lc, 'fallback'), ref: src(chk, 'reference'), lcWidgets: lc.widgets.map(w => [w.name, w.value]), gone300: !app.graph.getNodeById(300), tags: window.mmx.tagsOf(m).tags.map(t => [t.tag, t.file, !!t.firstFrame])}; }""")
        check("Load Chain Frame #404: fallback <- Library #402, image -> Manager first_frame AND -> First Frame Check reference; LoadImage #300 gone; use_fallback on (one segment)",
              ff["ff"] == [404, 0] and ff["fallback"] == [402, 0] and ff["ref"] == [404, 0] and ff["gone300"] and dict(ff["lcWidgets"]).get("use_fallback") is True, str(ff))
        check("with first_frame linked and no pictures in the widget, the Deck counts the first frame as <Picture 1>", ff["tags"] == [["<Picture 1>", "(first_frame input)", True]], str(ff["tags"]))
        check("First Frame Check #301: reference optional (shape 7), enabled widget true", dict((i[0], i[2]) for i in info["chk"]["inputs"]).get("reference") == 7 and dict(info["chk"]["widgets"]).get("enabled") is True and dict(info["chk"]["widgets"]).get("threshold_db") == 24, str(info["chk"]))
        shot("deck_loaded")

        # 1b. layout: the panel fills the node at 400 / 700 / 1000 px, nothing clipped, then after a reload
        layout_checks(pg, shot, "classic")
        pg.evaluate("wf => app.loadGraphData(wf)", wf); time.sleep(3)
        pg.evaluate(JS_CENTER); time.sleep(1.5)
        g = pg.evaluate(JS_LAYOUT)
        check(f"after a workflow reload (saved size {g['size'][0]:.0f}x{g['size'][1]:.0f}): panel fills the node, content fits, strength inputs + toolbar visible, chips wrap", layout_ok(g, g["size"][0]), json.dumps(g))
        shot("deck_layout_classic_reload")
        if a.vue_too:
            urllib.request.urlopen(urllib.request.Request(a.server + "/settings/Comfy.VueNodes.Enabled", data=b"true", method="POST")).read()
            try:
                pg.goto(a.server); pg.wait_for_function("() => window.app && window.app.graph && Object.keys(LiteGraph.registered_node_types).length > 100", timeout=180000); time.sleep(2)
                pg.evaluate("wf => app.loadGraphData(wf)", wf); time.sleep(4)
                check("Nodes 2.0 (Vue) mode is on for this pass", pg.evaluate("() => !!LiteGraph.vueNodesMode"))
                layout_checks(pg, shot, "vue")
                pg.evaluate("wf => app.loadGraphData(wf)", wf); time.sleep(3)
                pg.evaluate(JS_CENTER); time.sleep(1.5)
                g = pg.evaluate(JS_LAYOUT)
                check("Vue mode, after a reload: panel fills the node, content fits, strength inputs + toolbar visible", layout_ok(g, g["size"][0]), json.dumps(g))
                shot("deck_layout_vue_reload")
            finally:
                urllib.request.urlopen(urllib.request.Request(a.server + "/settings/Comfy.VueNodes.Enabled", data=b"false", method="POST")).read()
            pg.goto(a.server); pg.wait_for_function("() => window.app && window.app.graph && Object.keys(LiteGraph.registered_node_types).length > 100", timeout=180000); time.sleep(2)
            pg.evaluate("wf => app.loadGraphData(wf)", wf); time.sleep(4)
        else:
            pg.evaluate("wf => app.loadGraphData(wf)", wf); time.sleep(3)

        # 2. tag buttons follow the Manager's slots (no pictures in the widget: the linked first_frame is Picture 1)
        tags = pg.evaluate("""() => { const d = app.graph.getNodeById(400); d.mmxDeck.render(); const b = d._mmxDeck.tagButtons;
            return Object.fromEntries(Object.entries(b).map(([k, v]) => [k, [!v.disabled, v.classList.contains('ff'), v.title]])); }""")
        check("Manager with no pictures + first_frame linked: Picture 1 enabled and marked as the first frame, Picture 2+ / Video / Audio disabled, Subject 1-3 enabled",
              all(tags[f"<Subject {i}>"][0] for i in (1, 2, 3)) and tags["<Picture 1>"][0] and tags["<Picture 1>"][1] and "first_frame" in tags["<Picture 1>"][2] and not any(tags[f"<Picture {i}>"][0] for i in range(2, 10)) and not tags["<Video 1>"][0] and not tags["<Audio 1>"][0], str(tags)[:400])
        tags = pg.evaluate("""() => { const d = app.graph.getNodeById(400); const m = app.graph.getNodeById(185); const lc = app.graph.getNodeById(404);
            const lk = m.inputs.find(i => i.name === 'first_frame').link; m.disconnectInput(m.inputs.findIndex(i => i.name === 'first_frame')); d.mmxDeck.render();
            const off = Object.fromEntries(Object.entries(d._mmxDeck.tagButtons).map(([k, v]) => [k, !v.disabled]));
            lc.connect(0, m, m.inputs.findIndex(i => i.name === 'first_frame')); d.mmxDeck.render();
            const on = Object.fromEntries(Object.entries(d._mmxDeck.tagButtons).map(([k, v]) => [k, !v.disabled]));
            return {off, on, relinked: m.inputs.find(i => i.name === 'first_frame').link != null}; }""")
        check("unlinking first_frame drops that slot (no Picture enabled), relinking counts it again", not any(tags["off"][f"<Picture {i}>"] for i in range(1, 10)) and tags["on"]["<Picture 1>"] and not tags["on"]["<Picture 2>"] and tags["relinked"], str(tags)[:300])

        # 3. inject from the two library nodes
        r = pg.evaluate("""async () => { const lib = app.graph.getNodeById(401); await lib.widgets.find(w => w.name.startsWith('⇢ Inject into')).callback();
            const mgr = app.graph.getNodeById(185);
            return {result: lib.widgets.find(w => w.name === 'mmx_result').value, refs: mgr._mmrpRefs, json: mgr.widgets.find(w => w.name === 'references_json').value, color: mgr.color}; }""")
        print("   inject 1:", json.dumps(r)[:400])
        flat_a = a.lib_a.replace("/", "__")
        check("Inject (Picture 1): file copied to input/ and written into the Manager's references_json", flat_a in r["json"] and json.loads(r["json"])["references"][0] == {"kind": "image", "file": flat_a}, r["json"])
        check("the Manager's slot UI shows it (_mmrpRefs.images has the file) and the node is highlighted", len(r["refs"]["images"]) == 1 and r["refs"]["images"][0]["file"] == flat_a and r["color"] == "#6b4a00", str(r["refs"]) + r["color"])
        check("library node reports where it landed", "<Picture 1>" in r["result"] and "#185" in r["result"], r["result"])
        r = pg.evaluate("""async () => { const lib = app.graph.getNodeById(402); await lib.widgets.find(w => w.name.startsWith('⇢ Inject into')).callback();
            const mgr = app.graph.getNodeById(185);
            return {result: lib.widgets.find(w => w.name === 'mmx_result').value, images: mgr._mmrpRefs.images.map(i => i.file), n: mgr.widgets.find(w => w.name === 'references_json').value}; }""")
        flat_b = a.lib_b.replace("/", "__")
        check("Inject (Picture 9) into a 1-image list lands as <Picture 2>, reported honestly, earlier slot kept", r["images"] == [flat_a, flat_b] and "<Picture 2>" in r["result"] and "Picture 9" in r["result"], str(r))
        time.sleep(2.2)
        tags = pg.evaluate("() => { const b = app.graph.getNodeById(400)._mmxDeck.tagButtons; return Object.fromEntries(Object.entries(b).map(([k, v]) => [k, !v.disabled])); }")
        check("tag buttons track the Manager: Picture 1-2 (injected) + Picture 3 (the linked first_frame) enabled, Picture 4+ disabled", tags["<Picture 1>"] and tags["<Picture 2>"] and tags["<Picture 3>"] and not tags["<Picture 4>"] and not tags["<Video 1>"], str(tags))
        thumbs = pg.evaluate("() => { const m = app.graph.getNodeById(185); return {canvas: !!m._mmrpBody.canvas, w: m._mmrpBody.canvas.width, tiles: m._mmrpRefs.images.length}; }")
        check("Manager tile canvas present with 2 image tiles", thumbs["canvas"] and thumbs["tiles"] == 2 and thumbs["w"] > 0, str(thumbs))
        shot("manager_injected")
        # video into Video 1 and clear slot
        r = pg.evaluate("""async (v) => { const lib = app.graph.getNodeById(402); const fw = lib.widgets.find(w => w.name === 'file'); fw.value = v; lib.widgets.find(w => w.name === 'slot').value = 'Video 1';
            await lib.widgets.find(w => w.name.startsWith('⇢ Inject into')).callback(); const mgr = app.graph.getNodeById(185);
            const t = window.mmx.tagsOf(mgr); return {videos: mgr._mmrpRefs.videos, tags: t.tags.map(x => x.tag), result: lib.widgets.find(w => w.name === 'mmx_result').value}; }""", a.lib_video)
        check("Inject a library mp4 into Video 1: the mp4 itself lands (soundtrack on) -> <Audio 1> + <Video 1>", len(r["videos"]) == 1 and r["videos"][0]["file"] == a.lib_video.replace("/", "__") and "<Video 1>" in r["tags"], str(r))
        r = pg.evaluate("""() => { const lib = app.graph.getNodeById(402); lib.widgets.find(w => w.name === 'slot').value = 'Picture 2'; lib.widgets.find(w => w.name === '✕ Clear slot').callback();
            const mgr = app.graph.getNodeById(185); return {images: mgr._mmrpRefs.images.map(i => i.file), result: lib.widgets.find(w => w.name === 'mmx_result').value}; }""")
        check("Clear slot (Picture 2) removes only that entry; Picture 1 kept", r["images"] == [flat_a] and "cleared Picture 2" in r["result"], str(r))
        r = pg.evaluate("""async () => { const lib = app.graph.getNodeById(402); lib.widgets.find(w => w.name === 'slot').value = 'Picture 9'; lib.widgets.find(w => w.name === 'file').value = %s;
            await lib.widgets.find(w => w.name.startsWith('⇢ Inject all')).callback(); const mgr = app.graph.getNodeById(185);
            return {images: mgr._mmrpRefs.images.map(i => i.file), videos: mgr._mmrpRefs.videos.length, result: lib.widgets.find(w => w.name === 'mmx_result').value}; }""" % json.dumps(a.lib_b))
        check("Inject all (group): both library nodes of the Deck/Library group injected in slot order", r["images"] == [flat_a, flat_b] and 'group "Deck / Library' in r["result"] and r["result"].count("✓") == 2, str(r))

        # 4. Deck: type a prompt, set rows, Send
        prompt = "<Picture 1> walks toward <Picture 2>. Wide shot, slow push in, natural light."
        r = pg.evaluate("""async (prompt) => { const d = app.graph.getNodeById(400); const ui = d._mmxDeck;
            ui.editor.value = ''; ui.editor.focus(); ui.tagButtons['<Picture 1>'].click(); ui.editor.value = prompt; ui.editor.dispatchEvent(new Event('input'));
            ui.rows[0].sel.value = 'MiniMax-H3-Ref2VA-Acc-8Step.safetensors'; ui.rows[0].str.value = '0.5'; ui.rows[0].on.checked = true; ui.rows[0].sel.dispatchEvent(new Event('change'));
            ui.rows[1].sel.value = 'side_fuck_h3_000000750.safetensors'; ui.rows[1].str.value = '1.25'; ui.rows[1].on.checked = true; ui.rows[1].sel.dispatchEvent(new Event('change'));
            ui.rows[2].sel.value = 'H3_Motion_BoosterV2.safetensors'; ui.rows[2].sel.dispatchEvent(new Event('change')); ui.rows[2].str.value = '0.8'; ui.rows[2].str.dispatchEvent(new Event('change')); ui.rows[2].on.checked = false; ui.rows[2].on.dispatchEvent(new Event('change'));
            const before = app.graph.getNodeById(185).widgets.find(w => w.name === 'direction').value;
            ui.btnSend.click();
            const mgr = app.graph.getNodeById(185), stk = app.graph.getNodeById(137);
            return {before, state: d.mmxDeck.state(), direction: mgr.widgets.find(w => w.name === 'direction').value, textarea: mgr._mmrpBody.directionInput.value,
                    stack: Object.fromEntries(stk.widgets.filter(w => /^(on|lora|strength)_\\d$/.test(w.name)).map(w => [w.name, w.value])), summary: stk.widgets.find(w => w.name === 'mmx_result').value,
                    report: ui.report.innerText, jumps: ui.report.querySelectorAll('a').length, undo: !ui.btnUndo.disabled, colors: [mgr.color, stk.color]}; }""", prompt)
        print("   send:", json.dumps(r)[:700])
        check("Deck state widgets follow the panel (prompt + 3 rows)", r["state"]["prompt"] == prompt and r["state"]["rows"][0]["name"] == "MiniMax-H3-Ref2VA-Acc-8Step.safetensors" and r["state"]["rows"][1]["strength"] == 1.25 and r["state"]["rows"][2]["on"] is False, str(r["state"]))
        check("Send: Manager direction widget AND its textarea show the editor text", r["direction"] == prompt and r["textarea"] == prompt and r["before"] != prompt, f"{r['direction']!r} / {r['textarea']!r}")
        check("Send: Stack rows written as plain widgets (rows 1-2 on, row 3 off, rows 4-5 none)", r["stack"]["lora_1"] == "MiniMax-H3-Ref2VA-Acc-8Step.safetensors" and r["stack"]["strength_1"] == 0.5 and r["stack"]["on_1"] is True
              and r["stack"]["lora_2"] == "side_fuck_h3_000000750.safetensors" and r["stack"]["strength_2"] == 1.25 and r["stack"]["on_3"] is False and r["stack"]["lora_4"] == "(none)" and r["stack"]["on_4"] is False, str(r["stack"]))
        check("Stack body shows the effective stack (2 rows)", r["summary"].startswith("1. MiniMax-H3-Ref2VA-Acc-8Step.safetensors @ 0.50") and "2. side_fuck_h3_000000750.safetensors @ 1.25" in r["summary"] and "H3_Motion" not in r["summary"], r["summary"])
        check("landed report with two jump links, Undo enabled, both nodes highlighted", "#185" in r["report"] and "#137" in r["report"] and r["jumps"] == 2 and r["undo"] and r["colors"] == ["#6b4a00", "#6b4a00"], r["report"])
        shot("deck_sent")
        # export API JSON
        api_json = pg.evaluate("async () => (await app.graphToPrompt()).output")
        o185, o137 = api_json.get("185", {}).get("inputs", {}), api_json.get("137", {}).get("inputs", {})
        refs_api = json.loads(o185.get("references_json", "{}")).get("references", [])
        imgs_api = [x["file"] for x in refs_api if x["kind"] == "image"]
        check("API JSON: Manager direction + references_json match what Send/Inject wrote (2 pictures + the video) and carry the first_frame override as the LAST picture (mmx_ff_185_….png)",
              o185.get("direction") == prompt and imgs_api[:2] == [flat_a, flat_b] and len(imgs_api) == 3 and imgs_api[2].startswith("mmx_ff_185_") and [x["file"] for x in refs_api if x["kind"] == "video"] == [a.lib_video.replace("/", "__")], json.dumps(o185)[:300])
        ser_refs = pg.evaluate("() => app.graph.getNodeById(185).widgets.find(w => w.name === 'references_json').value")
        check("the widget's own references_json (what the workflow saves) has NO first_frame entry — the override lives in the export only", "mmx_ff_" not in ser_refs and flat_b in ser_refs, ser_refs[:200])
        check("API JSON: Stack rows match", o137.get("class_type") is None and o137.get("lora_1") == "MiniMax-H3-Ref2VA-Acc-8Step.safetensors" and o137.get("strength_1") == 0.5 and o137.get("on_1") is True and o137.get("on_3") is False and api_json["137"]["class_type"] == "MMXLoRAStack", json.dumps(api_json.get("137"))[:300])
        check("API JSON: turbo LoRA #158 takes the stack's model, Deck not in the prompt path", api_json["158"]["inputs"]["model"] == ["137", 0] and api_json["400"]["class_type"] == "MMXDeck")
        a403 = api_json["403"]["inputs"]
        check("API JSON: Affix takes the Manager prompt + the Stack's triggers output; R2V prompt and Display read the Affix", a403["prompt"] == ["185", 18] and a403["auto_triggers"] == ["137", 3] and a403["mode"] == "prepend"
              and api_json["184"]["inputs"]["prompt"] == ["403", 0] and api_json["186"]["inputs"]["source"] == ["403", 0], json.dumps(a403))
        # workflow JSON round trip
        ser = pg.evaluate("() => app.graph.serialize()")
        n185 = next(n for n in ser["nodes"] if n["id"] == 185); n137 = next(n for n in ser["nodes"] if n["id"] == 137); n400 = next(n for n in ser["nodes"] if n["id"] == 400)
        check("workflow JSON: Manager widgets_values[0]=direction, [1]=references_json; Stack rows; Deck state", n185["widgets_values"][0] == prompt and flat_b in n185["widgets_values"][1] and n137["widgets_values"][1] == "MiniMax-H3-Ref2VA-Acc-8Step.safetensors" and n137["widgets_values"][2] == 0.5
              and n400["widgets_values"][0] == prompt and json.loads(n400["widgets_values"][2])[1]["strength"] == 1.25 and json.loads(n400["widgets_values"][3]) == {"manager": 185, "stack": 137}, str(n400["widgets_values"])[:300])
        # pull + undo
        r = pg.evaluate("""() => { const d = app.graph.getNodeById(400), ui = d._mmxDeck; const mgr = app.graph.getNodeById(185);
            window.mmx.setDirection(mgr, 'edited on the manager'); ui.btnPull.click(); const pulled = d.mmxDeck.state().prompt;
            ui.btnUndo.click(); return {pulled, editor: ui.editor.value, direction: mgr.widgets.find(w => w.name === 'direction').value, textarea: mgr._mmrpBody.directionInput.value,
                stack1: app.graph.getNodeById(137).widgets.find(w => w.name === 'lora_1').value}; }""")
        check("Pull from graph reads the Manager's direction back into the editor", r["pulled"] == "edited on the manager" and r["editor"] == "edited on the manager", str(r))
        check("Undo restores the pre-Send direction (textarea too) and the Stack rows", r["direction"] == "<Subject 1> hugs <Subject 2>" and r["textarea"] == "<Subject 1> hugs <Subject 2>" and r["stack1"] == "H3_Motion_BoosterV2.safetensors", str(r))

        # 4b. registry: edit a row's triggers from the Deck, see them in the Stack body and the Send report; disabling removes them
        r = pg.evaluate("""async () => { const d = app.graph.getNodeById(400), ui = d._mmxDeck;
            d.mmxDeck.editRegistry(0); const box = ui.regEditor; const inputs = box.querySelectorAll('input');
            inputs[0].value = 'accel8, turbo mode'; inputs[1].value = 'fast pan'; inputs[2].value = '0.5';
            box.querySelector('button.primary').click(); await new Promise(r => setTimeout(r, 1200));
            const reg = await (await fetch('/mmx/registry')).json();
            ui.btnSend.click();
            const stk = app.graph.getNodeById(137);
            return {entry: reg.loras['MiniMax-H3-Ref2VA-Acc-8Step.safetensors'], boxHidden: box.hidden, trigLine: ui.rows[0].trig.textContent, prefixLine: ui.prefixLine.textContent,
                    report: ui.report.innerText, stackBody: stk.widgets.find(w => w.name === 'mmx_result').value}; }""")
        print("   registry:", json.dumps(r)[:600])
        check("Deck ✎ writes the registry (triggers, phrases, strength) through /mmx/registry/set", r["entry"] and r["entry"]["triggers"] == ["accel8", "turbo mode"] and r["entry"]["phrases"] == ["fast pan"] and r["entry"]["default_strength"] == 0.5 and r["entry"]["auto"] is False and r["boxHidden"], str(r["entry"]))
        check("row shows its triggers; Send report shows the trigger prefix; Stack body lists triggers under the row", r["trigLine"] == "accel8, turbo mode" and "trigger prefix that will be applied" in r["report"] and "accel8, turbo mode" in r["report"]
              and "triggers: accel8, turbo mode" in r["stackBody"] and "→ triggers out: accel8, turbo mode" in r["stackBody"], r["report"] + r["stackBody"])
        r = pg.evaluate("""() => { const d = app.graph.getNodeById(400), ui = d._mmxDeck; ui.rows[0].on.checked = false; ui.rows[0].on.dispatchEvent(new Event('change')); ui.btnSend.click();
            return {report: ui.report.innerText, prefixLine: ui.prefixLine.textContent, stackBody: app.graph.getNodeById(137).widgets.find(w => w.name === 'mmx_result').value, on1: app.graph.getNodeById(137).widgets.find(w => w.name === 'on_1').value}; }""")
        check("disabling the row removes its triggers from the prefix (report + Stack body)", "accel8" not in r["report"] and "(none" in r["prefixLine"] and "accel8" not in r["stackBody"] and r["on1"] is False, r["report"])
        pg.evaluate("""() => { const d = app.graph.getNodeById(400), ui = d._mmxDeck; ui.rows[0].on.checked = true; ui.rows[0].on.dispatchEvent(new Event('change')); ui.btnSend.click(); }""")
        registry_after = pg.evaluate("async () => (await (await fetch('/mmx/registry/refresh', {method: 'POST'})).json()).loras['MiniMax-H3-Ref2VA-Acc-8Step.safetensors']")
        check("registry refresh (pull + pre-fill) keeps the user entry", registry_after and registry_after["triggers"] == ["accel8", "turbo mode"] and registry_after["auto"] is False, str(registry_after))

        # 4c. "R" (refresh node definitions): a file dropped into models/loras / the library mirror shows up in the Stack rows,
        #     the Deck rows and the Library dropdown, and the node's own Refresh + the Deck's ↻ give the same list
        if a.loras_dir:
            new_lora, new_lib = "ZZ_dropped_after_load.safetensors", "Subjects/j/zz_dropped_after_load.png"
            open(os.path.join(a.loras_dir, new_lora), "wb").write(b"\x00" * 64)
            if a.library_dir:
                import shutil
                shutil.copy(os.path.join(a.library_dir, a.lib_a), os.path.join(a.library_dir, new_lib))
            try:
                before = pg.evaluate("""() => ({stack: app.graph.getNodeById(137).widgets.find(w => w.name === 'lora_1').options.values.slice(), lib: app.graph.getNodeById(401).widgets.find(w => w.name === 'file').options.values.slice()})""")
                check("before R: the dropped files are not listed yet", new_lora not in before["stack"] and new_lib not in before["lib"], str(before)[:300])
                r = pg.evaluate("""async ([lora, lib]) => { await app.refreshComboInNodes(); await new Promise(r => setTimeout(r, 800));
                    const stk = app.graph.getNodeById(137), d = app.graph.getNodeById(400), l = app.graph.getNodeById(401);
                    const oi = await (await fetch('/object_info/MMXLoRAStack')).json();
                    const oiLib = await (await fetch('/object_info/MMXLibraryImage')).json();
                    return {oi: oi.MMXLoRAStack.input.required.lora_1[0].includes(lora), oiLib: oiLib.MMXLibraryImage.input.required.file[0].includes(lib),
                            stackRows: stk.widgets.filter(w => /^lora_\\d$/.test(w.name)).map(w => w.options.values.includes(lora)),
                            deckRows: d._mmxDeck.rows.map(r => [...r.sel.options].some(o => o.value === lora)),
                            libValues: l.widgets.find(w => w.name === 'file').options.values.includes(lib), libSearchOk: l.widgets.find(w => w.name === 'mmx_search') != null,
                            shared: window.mmx.loras.list.includes(lora)}; }""", [new_lora, new_lib])
                check("after R: /object_info lists the new LoRA and the new library file (call-time scans)", r["oi"] and r["oiLib"], str(r))
                check("after R: all 5 Stack rows, all 5 Deck rows and the shared list carry the new LoRA; the Library dropdown lists the new file", all(r["stackRows"]) and all(r["deckRows"]) and r["shared"] and r["libValues"], str(r))
                r = pg.evaluate("""async (lora) => { const stk = app.graph.getNodeById(137), d = app.graph.getNodeById(400);
                    await stk.widgets.find(w => w.name.startsWith('↻ Refresh LoRAs')).callback(); const fromStack = stk.widgets.find(w => w.name === 'lora_1').options.values.slice();
                    d._mmxDeck.btnRefresh.click(); await new Promise(r => setTimeout(r, 1500)); const fromDeck = [...d._mmxDeck.rows[0].sel.options].map(o => o.value);
                    const oi = await (await fetch('/object_info/MMXLoRAStack')).json(); const fromR = oi.MMXLoRAStack.input.required.lora_1[0];
                    return {same: JSON.stringify(fromStack) === JSON.stringify(fromDeck) && JSON.stringify(fromStack) === JSON.stringify(fromR), fromStack, fromDeck, fromR}; }""", new_lora)
                check("the node's Refresh, the Deck's ↻ and R yield the identical list", r["same"], json.dumps(r)[:400])
            finally:
                os.remove(os.path.join(a.loras_dir, new_lora))
                if a.library_dir:
                    try: os.remove(os.path.join(a.library_dir, new_lib))
                    except OSError: pass
            pg.evaluate("async () => { await app.refreshComboInNodes(); }"); time.sleep(0.8)
            r = pg.evaluate("""(lora) => app.graph.getNodeById(137).widgets.find(w => w.name === 'lora_1').options.values.includes(lora)""", new_lora)
            check("after removing the file + R it is gone again", r is False)
        else:
            print("skip R-refresh checks (pass --loras-dir / --library-dir for this host)")

        # 5. presets round trip (save -> reload page state -> load -> update -> delete)
        r = pg.evaluate("""async (prompt) => { const d = app.graph.getNodeById(400), ui = d._mmxDeck;
            ui.editor.value = prompt; ui.editor.dispatchEvent(new Event('input')); ui.presetName.value = 'ui deck test';
            await d.mmxDeck.save(false); const saved = ui.report.innerText;
            const list = await (await fetch('/mmx/presets')).json(); const p = list.presets.find(x => x.name === 'ui deck test');
            ui.editor.value = ''; ui.editor.dispatchEvent(new Event('input')); ui.rows[0].sel.value = '(none)'; ui.rows[0].sel.dispatchEvent(new Event('change'));
            d.mmxDeck.loadPreset('ui deck test');
            const st = d.mmxDeck.state();
            await d.mmxDeck.save(false); const dup = ui.report.innerText;
            ui.editor.value = prompt + ' UPDATED'; ui.editor.dispatchEvent(new Event('input')); await d.mmxDeck.save(true);
            const list2 = await (await fetch('/mmx/presets')).json(); const p2 = list2.presets.find(x => x.name === 'ui deck test');
            return {saved, stored: p, st, dup, updated: p2 && p2.prompt, editor: ui.editor.value, rows: st.rows.slice(0, 3), sel: ui.presetSel.value}; }""", prompt)
        print("   preset:", json.dumps(r)[:600])
        check("Save writes the preset (prompt + 5-row loras with on flags) through /mmx/presets/save", r["stored"] and r["stored"]["prompt"] == prompt and [l["name"] for l in r["stored"]["loras"]] == ["MiniMax-H3-Ref2VA-Acc-8Step.safetensors", "side_fuck_h3_000000750.safetensors", "H3_Motion_BoosterV2.safetensors"] and r["stored"]["loras"][2].get("on") is False, str(r["stored"]))
        check("Load fills the editor AND the LoRA rows back", r["st"]["prompt"] == prompt and r["rows"][0]["name"] == "MiniMax-H3-Ref2VA-Acc-8Step.safetensors" and r["rows"][1]["strength"] == 1.25 and r["rows"][2]["on"] is False and r["sel"] == "ui deck test", str(r["st"])[:300])
        check("Save refuses an existing name (Update overwrites)", "exists" in r["dup"] and r["updated"] == prompt + " UPDATED", r["dup"])
        r = pg.evaluate("""async () => { const d = app.graph.getNodeById(400), ui = d._mmxDeck; window.confirm = () => true; ui.presetSel.value = 'ui deck test'; ui.btnDelete.click();
            await new Promise(r => setTimeout(r, 800)); const list = await (await fetch('/mmx/presets')).json(); return {names: list.names, report: ui.report.innerText, deleted: list.deleted}; }""")
        check("Delete removes the preset", "ui deck test" not in r["names"], str(r))

        # 6. phrases: chip insert (ellipsis rule), add, delete
        r = pg.evaluate("""async () => { const d = app.graph.getNodeById(400), ui = d._mmxDeck;
            ui.editor.value = '<Picture 2>'; ui.editor.setSelectionRange(11, 11); ui.editor.dispatchEvent(new Event('input'));
            const chip = [...ui.phrases.querySelectorAll('.chip')].find(c => c.textContent.startsWith('…is the absolute')); chip.click();
            const after1 = ui.editor.value;
            ui.phraseGroup.value = 'Camera'; ui.phraseText.value = 'crash zoom'; ui.btnPhraseAdd.click(); await new Promise(r => setTimeout(r, 900));
            const chips = [...ui.phrases.querySelectorAll('.chip')].map(c => c.textContent);
            ui.btnPhraseEdit.click(); const c2 = [...ui.phrases.querySelectorAll('.chip')].find(c => c.textContent.startsWith('crash zoom')); c2.querySelector('.x').click(); await new Promise(r => setTimeout(r, 700)); ui.btnPhraseEdit.click();
            const chips2 = [...ui.phrases.querySelectorAll('.chip')].map(c => c.textContent);
            const srv = await (await fetch('/mmx/phrases')).json();
            return {after1, hasNew: chips.includes('crash zoom'), gone: !chips2.some(c => c.startsWith('crash zoom')), srvHas: srv.groups.some(g => g.phrases.some(p => p.text === 'crash zoom')), groups: srv.groups.map(g => g.name)}; }""")
        check("phrase chip with a leading ellipsis inserts after the tag without the ellipsis", r["after1"] == "<Picture 2> is the absolute first frame of the video.", repr(r["after1"]))
        check("phrase add / delete round-trip through /mmx/phrases (seed groups present)", r["hasNew"] and r["gone"] and not r["srvHas"] and r["groups"][:4] == ["First frame", "Camera", "Lighting", "Pacing"], str(r))

        # 7. MMX References Manager drop-in: same UI, 20 outputs, external widget write re-renders
        r = pg.evaluate("""async () => { const n = LiteGraph.createNode('MMXReferencesManager'); n.pos = [100, 100]; app.graph.add(n); await new Promise(r => setTimeout(r, 300));
            const rw = n.widgets.find(w => w.name === 'references_json');
            rw.value = JSON.stringify({references: [{kind: 'image', file: %s}, {kind: 'image', file: %s}]});
            await new Promise(r => setTimeout(r, 50));
            const tiles = n._mmrpRefs.images.map(i => i.file);
            window.mmx.setDirection(n, 'hello from mmx'); const dir = n._mmrpBody.directionInput.value;
            const tags = window.mmx.tagsOf(n);
            return {outputs: n.outputs.length, names: n.outputs.map(o => o.name).slice(-2), hasBody: !!n._mmrpBody, hooked: rw._mmxHooked, tiles, dir, tags: tags.tags.map(t => t.tag), size: n.size, id: n.id}; }""" % (json.dumps(flat_a), json.dumps(flat_b)))
        print("   manager:", json.dumps(r)[:400])
        check("MMX References Manager: RefPack UI + 20 outputs (…, prompt, debug)", r["hasBody"] and r["outputs"] == 20 and r["names"] == ["prompt", "debug"], str(r))
        check("a plain widget write from outside re-renders its slot UI (2 tiles) and setDirection fills its textarea", r["tiles"] == [flat_a, flat_b] and r["dir"] == "hello from mmx" and r["tags"] == ["<Picture 1>", "<Picture 2>"], str(r))
        pg.evaluate("id => app.graph.remove(app.graph.getNodeById(id))", r["id"])

        # 8. First Frame Check skips cleanly with no reference / when disabled (queued through the UI)
        def run_check(with_ref, enabled):
            return pg.evaluate("""async ([frame, withRef, enabled]) => {
                app.graph.clear();
                const mk = (t, x, y) => { const n = LiteGraph.createNode(t); n.pos = [x, y]; app.graph.add(n); return n; };
                const a = mk('LoadImage', 50, 50), c = mk('MMXFirstFrameCheck', 500, 50), p = mk('PreviewImage', 900, 50);
                a.widgets.find(w => w.name === 'image').value = frame;
                a.connect(0, c, 0); c.connect(3, p, 0);
                if (withRef) { const b = mk('LoadImage', 50, 400); b.widgets.find(w => w.name === 'image').value = frame; b.connect(0, c, c.inputs.findIndex(i => i.name === 'reference')); }
                c.widgets.find(w => w.name === 'enabled').value = enabled;
                const q = await app.queuePrompt(0);
                for (let i = 0; i < 120; i++) { await new Promise(r => setTimeout(r, 1000)); const s = await (await fetch('/queue')).json(); if (!s.queue_running.length && !s.queue_pending.length) break; }
                await new Promise(r => setTimeout(r, 1500));
                const h = await (await fetch('/history')).json(); const last = Object.values(h).slice(-1)[0];
                const out = last && last.outputs && last.outputs[String(c.id)];
                const pout = last && last.outputs && last.outputs[String(p.id)];
                return {text: c.widgets.find(w => w.name === 'mmx_result')?.value, color: c.bgcolor, status: last && last.status && last.status.status_str, out, previewImgs: (pout && pout.images || []).length};
            }""", [a.frame, with_ref, enabled])
        r = run_check(False, True)
        print("   check no-ref:", json.dumps(r)[:400])
        check("no reference connected: run succeeds, body says skipped (first segment), passed=True, psnr=-1, comparison = frame 0 previewed", r["status"] == "success" and (r["text"] or "").startswith("skipped (first segment)") and r["out"]["passed"] == [True] and r["out"]["psnr"] == [-1.0] and r["out"]["skipped"] == [True] and r["previewImgs"] >= 1 and r["color"] == "#4a4a38", str(r))
        r = run_check(True, False)
        check("enabled=false with a reference: skipped too (check disabled)", r["status"] == "success" and "check disabled" in (r["text"] or "") and r["out"]["passed"] == [True], str(r))
        r = run_check(True, True)
        check("enabled with the same image as reference: real comparison, PASS", r["status"] == "success" and (r["text"] or "").startswith("PASS") and r["out"]["passed"] == [True] and r["out"]["psnr"][0] > 30, str(r))

        # 8b. Prompt Affix executes: triggers prepended, mode append, none
        def run_affix(trig, mode):
            return pg.evaluate("""async ([trig, mode]) => {
                app.graph.clear();
                const mk = (t, x, y) => { const n = LiteGraph.createNode(t); n.pos = [x, y]; app.graph.add(n); return n; };
                const p = mk('PrimitiveString', 50, 50), t = mk('PrimitiveString', 50, 200), a = mk('MMXPromptAffix', 500, 50), s = mk('PreviewAny', 900, 50);
                p.widgets[0].value = '<Subject 1> walks in.'; t.widgets[0].value = trig;
                p.connect(0, a, 0); t.connect(0, a, a.inputs.findIndex(i => i.name === 'auto_triggers')); a.connect(0, s, 0);
                a.widgets.find(w => w.name === 'mode').value = mode;
                await app.queuePrompt(0);
                for (let i = 0; i < 60; i++) { await new Promise(r => setTimeout(r, 1000)); const q = await (await fetch('/queue')).json(); if (!q.queue_running.length && !q.queue_pending.length) break; }
                await new Promise(r => setTimeout(r, 1000));
                const h = await (await fetch('/history')).json(); const last = Object.values(h).slice(-1)[0];
                return {status: last && last.status && last.status.status_str, text: (last && last.outputs && last.outputs[String(a.id)] || {}).text, body: a.widgets.find(w => w.name === 'mmx_result')?.value};
            }""", [trig, mode])
        r = run_affix("accel8, turbo mode", "prepend")
        check("Affix run (prepend): final prompt = triggers, prompt; shown in the node body", r["status"] == "success" and r["text"][0].endswith("accel8, turbo mode, <Subject 1> walks in.") and (r["body"] or "").startswith("triggers (prepend): accel8, turbo mode"), str(r))
        r = run_affix("accel8", "append")
        check("Affix run (append)", r["status"] == "success" and r["text"][0].endswith("<Subject 1> walks in., accel8"), str(r))
        r = run_affix("", "prepend")
        check("Affix run with no triggers: prompt unchanged", r["status"] == "success" and r["text"][0].endswith("\n<Subject 1> walks in.") and "no triggers" in r["text"][0], str(r))

        # 8c. first_frame: run 1 = the Load Chain Frame fallback (library first frame) as the last picture,
        #     run 2 = the Chain Gate's frame; the export carries the override filename; the check compares against the same image
        chain_name = f"mmx_uitest_ff_{int(time.time())}.png"
        g = pg.evaluate("""async ([chain, lib, frames]) => {
            app.graph.clear();
            const mk = (t, x, y) => { const n = LiteGraph.createNode(t); n.pos = [x, y]; app.graph.add(n); return n; };
            const W = (n, name, v) => { n.widgets.find(w => w.name === name).value = v; };
            const L = mk('MMXLibraryImage', 50, 50), C = mk('MMXLoadChainFrame', 450, 50), M = mk('MMXReferencesManager', 850, 50), F = mk('LoadImage', 50, 500), K = mk('MMXFirstFrameCheck', 450, 500), G = mk('MMXChainGate', 850, 700), P = mk('PreviewImage', 1400, 50), D = mk('PreviewAny', 1400, 400), B = mk('PreviewAny', 1400, 600);
            await new Promise(r => setTimeout(r, 300));
            W(L, 'file', lib); W(C, 'filename', chain); W(C, 'use_fallback', false);
            W(M, 'direction', 'first frame test'); W(M, 'prompt_provider', 'none');
            const rw = M.widgets.find(w => w.name === 'references_json'); rw.value = JSON.stringify({references: [{kind: 'image', file: %s}]}); rw.callback?.(rw.value);
            W(F, 'image', frames); W(K, 'threshold_db', 0); W(K, 'enabled', true); W(G, 'filename', chain); W(G, 'stop_queue', true);
            L.connect(0, C, 0); C.connect(0, M, M.inputs.findIndex(i => i.name === 'first_frame')); C.connect(0, K, K.inputs.findIndex(i => i.name === 'reference'));
            F.connect(0, K, 0); F.connect(0, G, 0); K.connect(2, G, 1);
            M.connect(1, P, 0); M.connect(19, D, 0); C.connect(1, B, 0);
            return {ids: {M: M.id, K: K.id, G: G.id, D: D.id, B: B.id}, tags: window.mmx.tagsOf(M).tags.map(t => [t.tag, t.file])}; }""" % json.dumps(flat_a), [chain_name, a.lib_b, a.frame])
        check("first_frame graph: identity in the widget + first_frame linked -> <Picture 1> identity, <Picture 2> first_frame", g["tags"] == [["<Picture 1>", flat_a], ["<Picture 2>", "(first_frame input)"]], str(g))
        RUN = """async (ids) => {
            const p = await app.graphToPrompt(); const exported = p.output[String(ids.M)].inputs.references_json;
            const res = await fetch('/prompt', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({prompt: p.output, client_id: 'ui-check'})});
            const d = await res.json(); if (!res.ok) return {error: JSON.stringify(d).slice(0, 600)};
            for (let i = 0; i < 180; i++) { await new Promise(r => setTimeout(r, 1000)); const q = await (await fetch('/queue')).json(); if (!q.queue_running.length && !q.queue_pending.length) break; }
            await new Promise(r => setTimeout(r, 800));
            const h = await (await fetch('/history/' + d.prompt_id)).json(); const e = h[d.prompt_id]; const o = e.outputs || {};
            return {exported, status: e.status && e.status.status_str, promptRefs: e.prompt[2][String(ids.M)].inputs.references_json,
                    debug: ((o[String(ids.D)] || {}).text || [''])[0], fromFile: ((o[String(ids.B)] || {}).text || [''])[0], chk: o[String(ids.K)], gate: o[String(ids.G)]}; }"""
        lib_first = fetch_png(a.server, a.lib_b.replace("/", "__"))     # the library file, as copied into input/ by the Library node
        frame_src = fetch_png(a.server, a.frame)
        for run in (1, 2):
            r = pg.evaluate(RUN, g["ids"])
            print(f"   first_frame run {run}:", json.dumps(r)[:500])
            if "error" in r:
                check(f"first_frame run {run} queued", False, r["error"]); break
            exp = json.loads(r["exported"])["references"]
            ffs = [x["file"] for x in exp if x["kind"] == "image" and x["file"].startswith("mmx_ff_")]
            written = fetch_png(a.server, ffs[0]) if ffs else None
            check(f"run {run}: exported API references_json = identity + the override filename as the last picture, and the executed prompt carries the same", len(ffs) == 1 and exp[-1]["file"] == ffs[0] and exp[0]["file"] == flat_a and r["promptRefs"] == r["exported"] and r["status"] == "success", json.dumps(r)[:300])
            check(f"run {run}: Manager debug reports the override as <Picture 2>", f"first_frame: {ffs[0] if ffs else '?'}" in r["debug"] and "used as <Picture 2>" in r["debug"], r["debug"][-300:])
            if run == 1:
                check("run 1: Load Chain Frame used the FALLBACK (from_file False); the written mmx_ff file IS the library first frame; the gate wrote the chain file", r["fromFile"] == "False" and written is not None and same_image(written, lib_first) and not same_image(written, frame_src) and r["gate"]["written"] == [True], str(r["fromFile"]) + str(r["gate"]))
            else:
                check("run 2: Load Chain Frame read the gate's frame (from_file True); the written mmx_ff file IS that frame (not the fallback)", r["fromFile"] == "True" and written is not None and same_image(written, frame_src) and not same_image(written, lib_first), str(r["fromFile"]))
                check("run 2: the First Frame Check compared the frames against the SAME image the Manager anchored (PSNR 100, identical)", r["chk"]["psnr"] == [100] and r["chk"]["passed"] == [True], str(r["chk"]["text"]))
        if a.input_dir:
            for f in (chain_name, chain_name.replace(".png", "_REJECTED.png")):
                try: os.remove(os.path.join(a.input_dir, f))
                except OSError: pass

        # 9. phrases / presets survive a Refresh (pull) from the panel
        pg.evaluate("wf => app.loadGraphData(wf)", wf); time.sleep(3)
        r = pg.evaluate("""async () => { const d = app.graph.getNodeById(400), ui = d._mmxDeck; ui.btnRefresh.click(); await new Promise(r => setTimeout(r, 1500)); return {presets: ui.presetSel.options.length, chips: ui.phrases.querySelectorAll('.chip').length, loras: ui.rows[0].sel.options.length}; }""")
        check("↻ Refresh re-reads presets / phrases / LoRAs (pulling the NAS copy first)", r["chips"] >= 15 and r["loras"] >= 4, str(r))
        errs = [e for e in errors if "mmx" in e.lower() or "MiniMaxRefPack" in e]
        check("no page errors from the mmx / RefPack extensions", not errs, str(errs)[:600])

        # 10. a clean ComfyUI with an EMPTY library: both examples load as shipped with zero validation errors
        if a.empty_server:
            pg2 = b.new_page(viewport={"width": 1900, "height": 1150})
            errs2 = []
            pg2.on("pageerror", lambda e: errs2.append("PAGE " + str(e)))
            pg2.on("console", lambda m: errs2.append(m.text) if m.type == "error" else None)
            pg2.goto(a.empty_server)
            pg2.wait_for_function("() => window.app && window.app.graph && Object.keys(LiteGraph.registered_node_types).length > 100", timeout=180000); time.sleep(2)
            lib_enum = json.loads(urllib.request.urlopen(a.empty_server + "/object_info/MMXLibraryImage").read())["MMXLibraryImage"]["input"]
            check("empty-library server: the file enum is [''] (unset first, no placeholder text) and the slot enum is the SLOTS list", lib_enum["required"]["file"][0] == [""] and lib_enum["optional"]["slot"][0][:2] == ["(none)", "Picture 1"] and "Picture 9" in lib_enum["optional"]["slot"][0], str(lib_enum["required"]["file"][0]))
            for path, label, n_nodes in ((EX, "deck.json", 33), (EX_CHAIN, "deck_chain.json", 34)):
                wfe = json.load(open(path))
                n_err = len(errs2)
                pg2.evaluate("wf => app.loadGraphData(wf)", wfe); time.sleep(4)
                st = pg2.evaluate("""(n) => { const nodes = app.graph._nodes || []; const missing = nodes.filter(x => !LiteGraph.registered_node_types[x.type]).map(x => x.type);
                    const toasts = [...document.querySelectorAll('.p-toast-message, .p-dialog')].map(e => e.innerText.slice(0, 200));
                    const libs = nodes.filter(x => x.type === 'MMXLibraryImage').map(x => { const f = x.widgets.find(w => w.name === 'file'), s = x.widgets.find(w => w.name === 'slot'); return {id: x.id, file: f.value, fileOk: f.options.values.includes(f.value), slot: s.value, slotOk: s.options.values.includes(s.value), order: x.widgets.slice(0, 2).map(w => w.name), body: (x.widgets.find(w => w.name === 'mmx_result') || {}).value}; });
                    const m = app.graph.getNodeById(185); const ffl = m.inputs.find(i => i.name === 'first_frame');
                    return {count: nodes.length, missing, toasts, libs, mgr: {type: m.type, body: !!m._mmrpBody, outputs: m.outputs.length, ff: ffl && ffl.link != null}, gate: !!app.graph.getNodeById(406), deckPanel: !!app.graph.getNodeById(400)._mmxDeck}; }""", n_nodes)
                print(f"   clean load {label}:", json.dumps(st)[:700])
                # a toast about model files this host lacks, or a 404 on the base graph's VideoCombine preview, is not the pack's:
                # only messages naming our nodes / widgets / validation count
                ours = lambda t: any(k in t for k in ("MMX", "mmx", "Library", "slot", "first_frame", "RefPack", "valid", "widget"))
                bad_toasts = [t for t in st["toasts"] if ours(t)]; bad_errs = [e for e in errs2[n_err:] if ours(e)]
                check(f"{label} on the empty-library server: {n_nodes} nodes, no missing types, no validation toast / dialog, no console error about the pack's nodes or widgets",
                      st["count"] == n_nodes and not st["missing"] and not bad_toasts and not bad_errs, str(st["missing"]) + str(st["toasts"]) + str(errs2[n_err:])[:300])
                check(f"{label}: Library nodes load file = '' and slot = shipped value, BOTH inside their dropdown lists; widgets are [file, slot] first",
                      [(x["file"], x["slot"]) for x in st["libs"]] == [("", "Picture 1"), ("", "(none)")] and all(x["fileOk"] and x["slotOk"] and x["order"] == ["file", "slot"] for x in st["libs"]), str(st["libs"])[:400])
                check(f"{label}: the node body shows the empty library + the last mirror log line", all("library empty" in (x["body"] or "") and "last mirror log" in (x["body"] or "") for x in st["libs"]), str([x["body"] for x in st["libs"]])[:300])
                check(f"{label}: MMX References Manager #185 with the RefPack body, 20 outputs, first_frame linked; Deck panel built" + ("; Chain Gate #406 present" if label.startswith("deck_chain") else ""),
                      st["mgr"] == {"type": "MMXReferencesManager", "body": True, "outputs": 20, "ff": True} and st["deckPanel"] and st["gate"] == label.startswith("deck_chain"), str(st["mgr"]))
                q = pg2.evaluate("""async () => { const p = await app.graphToPrompt(); const res = await fetch('/prompt', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({prompt: p.output, client_id: 'ui-check'})});
                    const d = await res.json(); const ne = d.node_errors || {}; return Object.fromEntries(Object.entries(ne).map(([k, v]) => [k, [v.class_type, v.errors.map(e => e.type + ': ' + e.message + ' / ' + e.details)]])); }""")
                mmx_errs = {k: v for k, v in q.items() if v[0].startswith("MMX")}
                check(f"{label}: queueing reports the unset file ONLY as a lazy custom validation on #402 (wired into the chain) — no value_not_in_list on any MMX node, nothing on the unwired #401",
                      set(mmx_errs) == {"402"} and all("no file selected" in e for e in mmx_errs["402"][1]) and not any("value_not_in_list" in e for v in q.values() for e in v[1] if v[0].startswith("MMX")), json.dumps(mmx_errs)[:400])
                if label == "deck.json":
                    pg2.evaluate("""() => { const n = app.graph.getNodeById(402); app.canvas.ds.scale = 1; app.canvas.centerOnNode(n); app.canvas.setDirty(true, true); }"""); time.sleep(1)
                    node_shot(pg2, os.path.join(a.shots, "library_empty_402.png") if a.shots else "", 402)
            pg2.close()
        else:
            print("skip clean-load checks (pass --empty-server for a ComfyUI with an empty library mirror)")
        b.close()
    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
