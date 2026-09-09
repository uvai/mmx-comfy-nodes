#!/usr/bin/env python3
"""Frontend checks for the deck build against a running ComfyUI (playwright chromium, headless).

    python3 tools/ui_check_deck.py --server http://127.0.0.1:8188 [--frame s1_first.png] [--shots DIR]

Loads examples/deck.json and drives the real widgets: tag buttons follow the Manager's slots,
Inject from the two Library nodes lands in the Manager's slot UI, Send lands in the Manager's
direction + the Stack's rows (visible in both), the exported API JSON carries the same values,
preset save / load / update / delete round-trip, phrase chips insert, the First Frame Check
skips cleanly with no reference, the MMX References Manager drop-in re-renders on an external
widget write, and the workflow serialises the pushed state.
"""
import argparse, json, os, sys, time

from playwright.sync_api import sync_playwright

HERE = os.path.dirname(os.path.abspath(__file__))
EX = os.path.join(os.path.dirname(HERE), "examples", "deck.json")
results = []


def check(name, cond, detail=""):
    results.append(bool(cond)); print(("PASS " if cond else "FAIL ") + name + (f"  — {detail}" if detail and not cond else ""), flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8188")
    ap.add_argument("--frame", default="s1_first.png", help="an image in ComfyUI/input for the check-skip run")
    ap.add_argument("--shots", default="")
    ap.add_argument("--lib-a", default="Subjects/j/identity.png"); ap.add_argument("--lib-b", default="Sets/room/first_frame.png"); ap.add_argument("--lib-video", default="VideoRef/seg1.mp4")
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
        for n in wf["nodes"]:
            if n["type"] == "MMXLibraryImage":
                n["widgets_values"][0] = a.lib_a if n["id"] == 401 else a.lib_b
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
        check("References Manager #185 (stock) has the RefPack body, 20 outputs, and the mmx write hooks", info["mgr"]["hasBody"] and info["mgr"]["outputs"] == 20 and all(h and h.startswith(n) for h, n in zip(info["mgr"]["hooked"], ["references_json", "direction"])), str(info["mgr"]))
        check("First Frame Check #301: reference optional (shape 7), enabled widget true", dict((i[0], i[2]) for i in info["chk"]["inputs"]).get("reference") == 7 and dict(info["chk"]["widgets"]).get("enabled") is True and dict(info["chk"]["widgets"]).get("threshold_db") == 24, str(info["chk"]))
        shot("deck_loaded")

        # 2. tag buttons follow the Manager's slots (empty manager: only Subject enabled)
        tags = pg.evaluate("""() => { const d = app.graph.getNodeById(400); d.mmxDeck.render(); const b = d._mmxDeck.tagButtons;
            return Object.fromEntries(Object.entries(b).map(([k, v]) => [k, !v.disabled])); }""")
        check("empty Manager: Picture/Video/Audio buttons disabled, Subject 1-3 enabled",
              all(tags[f"<Subject {i}>"] for i in (1, 2, 3)) and not any(tags[f"<Picture {i}>"] for i in range(1, 10)) and not tags["<Video 1>"] and not tags["<Audio 1>"], str(tags))

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
        check("tag buttons track the Manager: Picture 1-2 enabled, Picture 3+ disabled", tags["<Picture 1>"] and tags["<Picture 2>"] and not tags["<Picture 3>"] and not tags["<Video 1>"], str(tags))
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
        check("API JSON: Manager direction + references_json match what Send/Inject wrote (2 pictures + the video)", o185.get("direction") == prompt and [x["file"] for x in refs_api if x["kind"] == "image"] == [flat_a, flat_b] and [x["file"] for x in refs_api if x["kind"] == "video"] == [a.lib_video.replace("/", "__")], json.dumps(o185)[:300])
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

        # 9. phrases / presets survive a Refresh (pull) from the panel
        pg.evaluate("wf => app.loadGraphData(wf)", wf); time.sleep(3)
        r = pg.evaluate("""async () => { const d = app.graph.getNodeById(400), ui = d._mmxDeck; ui.btnRefresh.click(); await new Promise(r => setTimeout(r, 1500)); return {presets: ui.presetSel.options.length, chips: ui.phrases.querySelectorAll('.chip').length, loras: ui.rows[0].sel.options.length}; }""")
        check("↻ Refresh re-reads presets / phrases / LoRAs (pulling the NAS copy first)", r["chips"] >= 15 and r["loras"] >= 4, str(r))
        errs = [e for e in errors if "mmx" in e.lower() or "MiniMaxRefPack" in e]
        check("no page errors from the mmx / RefPack extensions", not errs, str(errs)[:600])
        b.close()
    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
