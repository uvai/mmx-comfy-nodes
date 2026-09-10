// MMX Deck: prompt constructor + preset manager as one DOM panel on the node. State lives in the
// node's four (hidden) widgets — prompt, preset, loras_json, targets_json — so it rides in
// widgets_values; the panel is a view over them. Nothing here runs on queue: Save / Update /
// Delete are buttons, Send writes the References Manager's `direction` and the MMX LoRA Stack's
// rows through window.mmx and reports where it landed.
import { app } from "../../scripts/app.js";
import * as M from "./mmx_api.js";

// The panel is content-sized and fluid: no pixel widths anywhere, the node's minimum size is
// measured from the content (on load, on every resize, whenever the content reflows).
const MIN_W = 380, DEFAULT_W = 880, FALLBACK_H = 720, DOM_MARGIN = 24;
const TAG_GROUPS = [["Picture", 9], ["Subject", 3], ["Video", 3], ["Audio", 1]];
const cache = { names: [], presets: {}, loras: [], groups: [], loaded: false };

// ── data ─────────────────────────────────────────────────────────────────────
async function loadPresets(refresh) {
  const d = await M.fetchJson(refresh ? "/mmx/presets/refresh" : "/mmx/presets", refresh ? {} : null);
  cache.names = d.names || []; cache.presets = {};
  for (const p of d.presets || []) cache.presets[p.name] = p;
  return d;
}
async function loadLoras(refresh) { return M.loadLoras(); }   // the shared list (fires mmx-loras-changed)
async function loadPhrases(refresh) {
  const d = await M.fetchJson(refresh ? "/mmx/phrases/refresh" : "/mmx/phrases", refresh ? {} : null);
  cache.groups = d.groups || [];
  return d;
}
async function loadAll(refresh) {
  await Promise.all([loadPresets(refresh).catch(e => console.warn("[mmx-deck] presets", e)), loadLoras(refresh).catch(() => {}), loadPhrases(refresh).catch(e => console.warn("[mmx-deck] phrases", e)),
                     M.loadRegistry(refresh).catch(e => console.warn("[mmx-deck] registry", e))]);
  cache.loaded = true;
}

// ── widget state ─────────────────────────────────────────────────────────────
function readState(node) {
  const g = n => M.widget(node, n)?.value;
  let rows = [], targets = {};
  try { rows = JSON.parse(g("loras_json") || "[]"); } catch (e) {}
  try { targets = JSON.parse(g("targets_json") || "{}") || {}; } catch (e) {}
  return { prompt: g("prompt") || "", preset: g("preset") || "", rows: M.normalizeRows(rows), targets };
}
function writeState(node, patch) {
  const set = (n, v) => { const w = M.widget(node, n); if (w && w.value !== v) { w.value = v; w.callback?.(v); } };
  if (patch.prompt !== undefined) set("prompt", patch.prompt);
  if (patch.preset !== undefined) set("preset", patch.preset);
  if (patch.rows !== undefined) set("loras_json", JSON.stringify(M.normalizeRows(patch.rows)));
  if (patch.targets !== undefined) set("targets_json", JSON.stringify(patch.targets));
}

// ── hidden state widgets (same recipe as the RefPack: lock hidden/type, zero size, hide the element) ──
function hideWidget(w) {
  if (!w) return;
  try { Object.defineProperty(w, "hidden", { get: () => true, set: () => {} }); } catch (e) { try { w.hidden = true; } catch (_) {} }
  try { Object.defineProperty(w, "type", { get: () => "hidden", set: () => {} }); } catch (e) {}
  try { w.options = { ...(w.options || {}), hidden: true }; } catch (e) {}
  try { w.computeSize = () => [0, 0]; } catch (e) {}
  if (!window.LiteGraph?.vueNodesMode) { try { w.draw = () => {}; } catch (e) {} }
  let n = 0;
  const iv = setInterval(() => { try { if (w.element) { w.element.style.display = "none"; clearInterval(iv); } } catch (e) {} if (++n > 20) clearInterval(iv); }, 50);
}

// ── styles ───────────────────────────────────────────────────────────────────
const CSS = `
.mmx-deck{display:flex;flex-direction:column;width:100%;min-width:0;font:12px/1.35 system-ui,sans-serif;color:#ddd;box-sizing:border-box;padding:2px 4px 6px;overflow:visible}
.mmx-deck *{box-sizing:border-box}
.mmx-deck .inner{display:flex;flex-direction:column;gap:6px;width:100%;min-width:0}
.mmx-deck .row{display:flex;gap:6px;align-items:center;flex-wrap:wrap;flex:0 0 auto;min-width:0}
.mmx-deck .lbl{color:#9a9a9a;min-width:52px;text-transform:uppercase;font-size:10px;letter-spacing:.06em}
.mmx-deck select,.mmx-deck input[type=text],.mmx-deck input[type=number],.mmx-deck textarea{background:#1c1c1c;color:#eee;border:1px solid #444;border-radius:4px;padding:3px 6px;font:inherit;max-width:100%;min-width:0}
.mmx-deck input[type=number]{width:64px}
.mmx-deck button{background:#2e2e2e;color:#eee;border:1px solid #555;border-radius:4px;padding:3px 8px;cursor:pointer;font:inherit}
.mmx-deck button:hover:not(:disabled){background:#3d3d3d}
.mmx-deck button:disabled{opacity:.35;cursor:default}
.mmx-deck button.tag{padding:2px 7px;border-color:#3f5f8a;background:#22334d}
.mmx-deck button.tag.sub{border-color:#5f8a3f;background:#2a3d22}
.mmx-deck button.tag.ff{border-color:#8a6a1f;background:#4d3d22}
.mmx-deck button.primary{background:#2d5a2d;border-color:#4a8a4a;font-weight:600}
.mmx-deck button.danger{border-color:#8a3a3a}
.mmx-deck textarea.editor{width:100%;min-height:150px;resize:vertical;font-family:ui-monospace,Menlo,monospace;font-size:12.5px;flex:0 0 auto}
.mmx-deck .chips{display:flex;flex-wrap:wrap;gap:4px;align-items:center;min-width:0}
.mmx-deck .chip{background:#333;border:1px solid #555;border-radius:12px;padding:1px 9px;cursor:pointer;max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.mmx-deck .chip:hover{background:#444}
.mmx-deck .chip .x{margin-left:6px;color:#e88;cursor:pointer}
.mmx-deck .grp{color:#8fb0d8;font-size:10px;text-transform:uppercase;letter-spacing:.06em;margin-right:2px}
.mmx-deck .phrases{display:flex;flex-direction:column;gap:3px;padding:4px;border:1px solid #333;border-radius:4px;flex:0 0 auto;min-width:0}
.mmx-deck .lrows{display:flex;flex-direction:column;gap:3px;flex:1 1 auto;min-width:0}
.mmx-deck .lrow{display:grid;grid-template-columns:auto auto minmax(0,1fr) 68px auto;gap:2px 6px;align-items:center;min-width:0}
.mmx-deck .lrow .n{color:#9a9a9a;white-space:nowrap}
.mmx-deck .lrow select{width:100%}
.mmx-deck .lrow input[type=number]{width:64px}
.mmx-deck .trig{color:#9fc9ff;font-size:11px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;min-width:0}
.mmx-deck .lrow .trig{grid-column:3 / -1}
.mmx-deck .lrow .trig:empty{display:none}
.mmx-deck .trig .auto{color:#888;font-style:italic}
.mmx-deck .regedit{background:#181818;border:1px solid #3f5f8a;border-radius:4px;padding:6px;display:flex;flex-direction:column;gap:4px;margin:2px 0 4px}
.mmx-deck .regedit input[type=text]{flex:1 1 160px}
.mmx-deck .regedit .hint{color:#999;font-size:11px}
.mmx-deck .report{white-space:pre-wrap;overflow-wrap:anywhere;font-family:ui-monospace,Menlo,monospace;font-size:11.5px;background:#161616;border:1px solid #333;border-radius:4px;padding:5px 7px;min-height:56px;max-height:160px;overflow:auto;flex:0 0 auto}
.mmx-deck .report a{color:#8fc1ff;cursor:pointer;text-decoration:underline}
.mmx-deck .ok{color:#8fd98f}.mmx-deck .warn{color:#f0c060}.mmx-deck .err{color:#f08080}
.mmx-deck hr{border:0;border-top:1px solid #333;margin:2px 0}
`;
function injectStyles() {
  if (document.getElementById("mmx-deck-styles")) return;
  const s = document.createElement("style"); s.id = "mmx-deck-styles"; s.textContent = CSS; document.head.appendChild(s);
}

// ── DOM helpers ──────────────────────────────────────────────────────────────
const el = (tag, attrs = {}, ...kids) => {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") e.className = v; else if (k === "text") e.textContent = v; else if (k.startsWith("on")) e.addEventListener(k.slice(2), v); else if (v !== undefined) e.setAttribute(k, v);
  }
  for (const k of kids) if (k != null) e.appendChild(typeof k === "string" ? document.createTextNode(k) : k);
  return e;
};
const stop = e => { e.stopPropagation(); };

function insertAtCursor(ta, text, opts = {}) {
  const v = ta.value, s = ta.selectionStart ?? v.length, e = ta.selectionEnd ?? s;
  let ins = text;
  const before = v.slice(0, s), after = v.slice(e);
  if (opts.leadSpace !== false && before && !/[\s(\[]$/.test(before)) ins = " " + ins;
  if (opts.trailSpace !== false && after && !/^[\s.,;:!?)\]]/.test(after)) ins = ins + " ";
  ta.value = before + ins + after;
  const caret = s + ins.length - (opts.caretBack || 0);
  ta.focus(); ta.setSelectionRange(caret, caret);
  ta.dispatchEvent(new Event("input", { bubbles: true }));
}
function insertPhrase(ta, phrase) {
  let t = phrase.trim();
  if (/^…|^\.\.\./.test(t)) { t = t.replace(/^(…|\.\.\.)\s*/, ""); insertAtCursor(ta, t, { leadSpace: true }); return; }
  if (/…$|\.\.\.$/.test(t)) { t = t.replace(/\s*(…|\.\.\.)$/, "") + " "; insertAtCursor(ta, t, { trailSpace: false }); return; }
  insertAtCursor(ta, t);
}

// ── the panel ────────────────────────────────────────────────────────────────
function buildPanel(node) {
  const outer = el("div", { class: "mmx-deck" });
  outer.addEventListener("pointerdown", stop); outer.addEventListener("mousedown", stop); outer.addEventListener("wheel", stop); outer.addEventListener("keydown", stop);
  const root = el("div", { class: "inner" });
  outer.appendChild(root);
  const ui = node._mmxDeck = { root: outer, inner: root, tagButtons: {}, rows: [], phrasesEdit: false };

  // presets
  ui.presetSel = el("select", { onchange: () => { ui.presetName.value = ui.presetSel.value; } });
  ui.presetName = el("input", { type: "text", placeholder: "preset name", style: "flex:1 1 140px;max-width:260px" });
  ui.btnLoad = el("button", { text: "Load", title: "fill the editor and the LoRA rows from the selected preset", onclick: () => loadPreset(node, ui.presetSel.value) });
  ui.btnSave = el("button", { text: "Save", title: "save the editor + rows as a NEW preset under this name", onclick: () => savePreset(node, false) });
  ui.btnUpdate = el("button", { text: "Update", title: "overwrite the preset of this name", onclick: () => savePreset(node, true) });
  ui.btnDelete = el("button", { text: "Delete", class: "danger", onclick: () => deletePreset(node) });
  ui.btnRefresh = el("button", { text: "↻", title: "re-read presets / phrases / LoRAs (pulls the NAS copy first)", onclick: async () => { ui.btnRefresh.textContent = "…"; try { await loadAll(true); renderAll(node); } finally { ui.btnRefresh.textContent = "↻"; } } });
  root.appendChild(el("div", { class: "row" }, el("span", { class: "lbl", text: "preset" }), ui.presetSel, ui.presetName, ui.btnLoad, ui.btnSave, ui.btnUpdate, ui.btnDelete, ui.btnRefresh));

  // tag buttons
  const tagRow = el("div", { class: "row" }, el("span", { class: "lbl", text: "tags" }));
  for (const [name, count] of TAG_GROUPS) {
    for (let i = 1; i <= count; i++) {
      const tag = `<${name} ${i}>`;
      const b = el("button", { class: "tag" + (name === "Subject" ? " sub" : ""), text: `${name[0]}${i}`, title: tag, onclick: () => insertAtCursor(ui.editor, tag) });
      ui.tagButtons[tag] = b; tagRow.appendChild(b);
    }
    tagRow.appendChild(el("span", { text: " " }));
  }
  ui.tagInfo = el("span", { class: "lbl", text: "" });
  tagRow.appendChild(ui.tagInfo);
  root.appendChild(tagRow);

  // editor
  ui.editor = el("textarea", { class: "editor", placeholder: "prompt — tag buttons and phrase chips insert at the caret", spellcheck: "false" });
  ui.editor.addEventListener("input", () => writeState(node, { prompt: ui.editor.value }));
  root.appendChild(ui.editor);

  // phrases
  ui.phrases = el("div", { class: "phrases" });
  ui.phraseGroup = el("input", { type: "text", placeholder: "group", style: "flex:0 1 110px", list: `mmx-deck-groups-${node.id}` });
  ui.phraseGroupList = el("datalist", { id: `mmx-deck-groups-${node.id}` });
  ui.phraseText = el("input", { type: "text", placeholder: "new phrase (… at either end = the tag goes there)", style: "flex:1 1 200px" });
  ui.btnPhraseAdd = el("button", { text: "+ phrase", onclick: () => addPhrase(node) });
  ui.btnPhraseEdit = el("button", { text: "✎ edit", onclick: () => { ui.phrasesEdit = !ui.phrasesEdit; ui.btnPhraseEdit.textContent = ui.phrasesEdit ? "✓ done" : "✎ edit"; renderPhrases(node); } });
  root.appendChild(el("div", { class: "row" }, el("span", { class: "lbl", text: "phrases" }), ui.phraseGroup, ui.phraseGroupList, ui.phraseText, ui.btnPhraseAdd, ui.btnPhraseEdit));
  root.appendChild(ui.phrases);

  // LoRA rows
  const tbl = el("div", { class: "lrows" });
  for (let i = 0; i < M.ROWS; i++) {
    const on = el("input", { type: "checkbox", title: "on / off" });
    const sel = el("select");
    const str = el("input", { type: "number", min: "0", max: "2", step: "0.05", title: "strength" });
    const sync = () => { writeState(node, { rows: ui.rows.map(r => ({ name: r.sel.value, strength: parseFloat(r.str.value) || 0, on: r.on.checked })) }); renderTriggerLines(node); };
    on.addEventListener("change", sync); str.addEventListener("change", sync); str.addEventListener("input", sync);
    sel.addEventListener("change", () => {
      // picking a LoRA switches the row on and takes the registry's default strength while the
      // strength is still at the widget default (1); a value the user typed is kept
      const e = M.registry.loras[sel.value];
      if (e && e.default_strength != null && parseFloat(str.value) === 1) str.value = e.default_strength;
      if (sel.value !== M.NONE) on.checked = true;
      sync();
    });
    const trig = el("div", { class: "trig" });
    const edit = el("button", { text: "✎", title: "edit this LoRA's trigger words / phrases in the registry", onclick: () => openRegistryEditor(node, i) });
    ui.rows.push({ on, sel, str, trig, edit });
    tbl.appendChild(el("div", { class: "lrow" }, el("span", { class: "n", text: `LoRA ${i + 1}` }), on, sel, str, edit, trig));
  }
  root.appendChild(el("div", { class: "row", style: "align-items:flex-start;flex-wrap:nowrap" }, el("span", { class: "lbl", text: "loras" }), tbl));
  ui.regEditor = el("div", { class: "regedit", hidden: "" });
  root.appendChild(ui.regEditor);
  ui.prefixLine = el("div", { class: "trig", style: "white-space:normal" });
  root.appendChild(ui.prefixLine);

  // targets + actions
  ui.mgrSel = el("select", { onchange: () => { const t = readState(node).targets; t.manager = Number(ui.mgrSel.value) || null; writeState(node, { targets: t }); M.remember("manager", t.manager); renderTags(node); } });
  ui.stkSel = el("select", { onchange: () => { const t = readState(node).targets; t.stack = Number(ui.stkSel.value) || null; writeState(node, { targets: t }); M.remember("stack", t.stack); } });
  root.appendChild(el("div", { class: "row" }, el("span", { class: "lbl", text: "targets" }), el("span", { text: "Manager" }), ui.mgrSel, el("span", { text: "Stack" }), ui.stkSel));
  ui.btnSend = el("button", { class: "primary", text: "⇢ Send to graph", onclick: () => send(node) });
  ui.btnPull = el("button", { text: "⇠ Pull from graph", onclick: () => pull(node) });
  ui.btnUndo = el("button", { text: "↶ Undo", disabled: "", onclick: () => undo(node) });
  root.appendChild(el("div", { class: "row" }, ui.btnSend, ui.btnPull, ui.btnUndo));
  ui.report = el("div", { class: "report", text: "Send writes the editor into the Manager's direction and the rows into the LoRA Stack." });
  root.appendChild(ui.report);
  return outer;
}

// ── sizing ───────────────────────────────────────────────────────────────────
// The node's minimum size follows the panel: the inner wrapper is content-sized, a
// ResizeObserver reports every reflow (load, a width change, chips loading, the editor being
// dragged taller) and the node grows to fit; the DOM widget's min height (getMinHeight for the
// current frontend, computeSize for older ones) is the same number, so the resize handle cannot
// shrink the node below its content. The top padding equals whatever part of the title bar
// covers the panel (title height when it cannot be measured), so the toolbar is never clipped.
const TITLE_H = () => Number(window.LiteGraph?.NODE_TITLE_HEIGHT) || 30;
function titleOverlap(node) {
  const ui = node._mmxDeck; if (!ui) return null;
  const r = ui.root.getBoundingClientRect(); if (!r.height) return null;
  const c = app.canvas; const s = (c?.ds?.scale) || 1;
  let titleBottom = null;
  if (window.LiteGraph?.vueNodesMode) {
    const host = ui.root.closest("[data-node-id]");
    const hdr = host?.querySelector(".lg-node-header, [data-testid^='node-header'], header");
    if (hdr) titleBottom = hdr.getBoundingClientRect().bottom;
  } else if (c?.canvas && c.ds) {
    const cr = c.canvas.getBoundingClientRect();
    titleBottom = cr.top + (node.pos[1] + c.ds.offset[1]) * s;   // pos.y is the title bar's bottom edge
  }
  if (titleBottom == null) return null;
  return Math.max(0, Math.min(TITLE_H(), (titleBottom - r.top) / s));
}
// content height, or 0 while it cannot be trusted: hidden / off-screen (no layout), or the
// element laid out at a bogus width (the frontend parks it at 0 px before the first draw and
// everything wraps into a tall column)
function measure(node) {
  const ui = node._mmxDeck; if (!ui) return 0;
  const h = ui.inner.offsetHeight, w = ui.root.offsetWidth;
  return h > 0 && w >= MIN_W - 40 ? h : 0;
}
function minHeight(node) {
  const h = measure(node);
  if (h) node._mmxContentH = h + (node._mmxTopPad || 0) + 8;
  return (node._mmxContentH || FALLBACK_H) + DOM_MARGIN;
}
function fitNode(node) {
  const ui = node._mmxDeck; if (!ui || !node.graph) return;
  if (!measure(node)) return;
  const pad = titleOverlap(node); const want = pad == null ? TITLE_H() : Math.round(pad);
  if (want !== node._mmxTopPad) { node._mmxTopPad = want; ui.root.style.paddingTop = want + "px"; }
  const above = ui.dw && ui.dw.y > 0 ? ui.dw.y : 60;                          // outputs + hidden widgets above the panel
  const need = minHeight(node) + above;
  const minNode = node.computeSize ? node.computeSize()[1] : 0;             // the frontend's own minimum (includes the DOM widget's min height)
  const target = Math.max(minNode, need);
  const cur = node.size[1], auto = node._mmxAutoH != null && Math.abs(cur - node._mmxAutoH) < 1;
  // grow to fit; shrink back to the content only while the height is still the one this code set
  // (a height the user dragged taller is theirs)
  const w = Math.max(node.size[0], MIN_W), h = (cur < target || auto) ? target : cur;
  if (Math.abs(w - node.size[0]) > 0.5 || Math.abs(h - node.size[1]) > 0.5) { node.setSize([w, h]); node._mmxAutoH = h; node.setDirtyCanvas?.(true, true); }
  else if (auto) node._mmxAutoH = h;
}
function installSizing(node, dw) {
  const ui = node._mmxDeck; ui.dw = dw;
  dw.computeSize = w => [Math.max(MIN_W, (w || node.size?.[0] || DEFAULT_W) - 20), minHeight(node)];
  dw.options = { ...(dw.options || {}), serialize: false, getMinHeight: () => minHeight(node), getMinWidth: () => MIN_W };
  ui.root.style.setProperty("--comfy-widget-min-height", FALLBACK_H + "px");
  let raf = 0;
  const schedule = () => { if (raf) return; raf = requestAnimationFrame(() => { raf = 0; fitNode(node); }); };
  node.mmxFit = () => { raf && cancelAnimationFrame(raf); raf = 0; fitNode(node); };
  if (window.ResizeObserver) { const ro = new ResizeObserver(schedule); ro.observe(ui.inner); node._mmxRO = ro; }
  const origResize = node.onResize;
  node.onResize = function (...a) { const r = origResize?.apply(this, a); schedule(); return r; };
  return schedule;
}

// ── rendering ────────────────────────────────────────────────────────────────
function targets(node) {
  const t = readState(node).targets;
  return { manager: M.defaultManager(t.manager), stack: M.defaultStack(t.stack) };
}
function renderTargets(node) {
  const ui = node._mmxDeck; if (!ui) return;
  const fill = (sel, list, cur) => {
    const want = String(cur?.id ?? "");
    const sig = list.map(n => `${n.id}:${M.label(n)}`).join("|") + "@" + want;
    if (sel._sig === sig) return; sel._sig = sig;
    sel.innerHTML = "";
    if (!list.length) sel.appendChild(el("option", { value: "", text: "(none in graph)" }));
    for (const n of list) sel.appendChild(el("option", { value: String(n.id), text: M.label(n) }));
    sel.value = want;
  };
  const t = targets(node);
  fill(ui.mgrSel, M.findManagers(), t.manager); fill(ui.stkSel, M.findStacks(), t.stack);
}
function renderTags(node) {
  const ui = node._mmxDeck; if (!ui) return;
  const mgr = targets(node).manager;
  const info = mgr ? M.tagsOf(mgr) : { pictures: 0, videos: 0, audios: 0, tags: [] };
  const sig = JSON.stringify([mgr?.id, info.pictures, info.videos, info.audios, info.firstFrame?.slot, info.firstFrame?.origin?.id]);
  if (ui._tagSig === sig) return; ui._tagSig = sig;
  for (const [tag, b] of Object.entries(ui.tagButtons)) {
    const m = /^<(\w+) (\d+)>$/.exec(tag); const n = Number(m[2]);
    const on = m[1] === "Subject" ? true : m[1] === "Picture" ? n <= info.pictures : m[1] === "Video" ? n <= info.videos : n <= info.audios;
    b.disabled = !on;
    const f = info.tags.find(x => x.tag === tag);
    b.classList.toggle("ff", !!(f && f.firstFrame));
    b.title = f ? (f.firstFrame ? `${tag} = the first_frame input (${M.label(info.firstFrame?.origin)}) — written into input/ at run time as the last picture` : `${tag} = ${f.file}`)
                : (on ? tag : `${tag} — no such slot in ${mgr ? M.label(mgr) : "the Manager"}`);
  }
  ui.tagInfo.textContent = mgr ? `${M.label(mgr)}: ${info.pictures} picture${info.pictures === 1 ? "" : "s"}${info.firstFrame ? ` (Picture ${info.firstFrame.slot} = first_frame ← ${M.label(info.firstFrame.origin)})` : ""}, ${info.videos} video${info.videos === 1 ? "" : "s"}, ${info.audios} audio` : "no References Manager in the graph";
}
function renderPresets(node) {
  const ui = node._mmxDeck; if (!ui) return;
  const st = readState(node);
  const sig = cache.names.join("|") + "@" + st.preset;
  if (ui.presetSel._sig !== sig) {
    ui.presetSel._sig = sig; ui.presetSel.innerHTML = "";
    if (!cache.names.length) ui.presetSel.appendChild(el("option", { value: "", text: "(no presets yet)" }));
    for (const n of cache.names) ui.presetSel.appendChild(el("option", { value: n, text: n }));
    if (cache.names.includes(st.preset)) ui.presetSel.value = st.preset;
  }
  if (document.activeElement !== ui.presetName) ui.presetName.value = st.preset || ui.presetName.value;
}
function renderPhrases(node) {
  const ui = node._mmxDeck; if (!ui) return;
  ui.phrases.innerHTML = ""; ui.phraseGroupList.innerHTML = "";
  if (!cache.groups.length) ui.phrases.appendChild(el("span", { class: "lbl", text: "(no phrases — add one above)" }));
  for (const g of cache.groups) {
    ui.phraseGroupList.appendChild(el("option", { value: g.name }));
    const line = el("div", { class: "chips" }, el("span", { class: "grp", text: g.name }));
    for (const p of g.phrases) {
      const chip = el("span", { class: "chip", text: p.text, title: "insert at the caret", onclick: () => insertPhrase(ui.editor, p.text) });
      if (ui.phrasesEdit) chip.appendChild(el("span", { class: "x", text: "✕", title: "delete this phrase", onclick: async e => { e.stopPropagation(); await M.fetchJson("/mmx/phrases/delete", { group: g.name, text: p.text }); await loadPhrases(false); renderPhrases(node); } }));
      line.appendChild(chip);
    }
    ui.phrases.appendChild(line);
  }
}
function renderRows(node) {
  const ui = node._mmxDeck; if (!ui) return;
  const st = readState(node);
  ui.rows.forEach((r, i) => {
    const row = st.rows[i];
    const opts = M.loraValues(row.name);
    const sig = opts.join("|");
    if (r.sel._sig !== sig) { r.sel._sig = sig; r.sel.innerHTML = ""; for (const v of opts) r.sel.appendChild(el("option", { value: v, text: v === M.NONE ? v : v.replace(/\.safetensors$/, "") })); }
    r.sel.value = row.name; r.str.value = row.strength; r.on.checked = row.on;
  });
  renderTriggerLines(node);
}
function renderTriggerLines(node) {
  const ui = node._mmxDeck; if (!ui) return;
  const st = readState(node);
  ui.rows.forEach((r, i) => {
    const row = st.rows[i]; const e = M.registry.loras[row.name];
    r.edit.disabled = !row.name || row.name === M.NONE;
    if (!e || row.name === M.NONE) { r.trig.textContent = ""; r.trig.title = ""; return; }
    const t = e.triggers?.length ? e.triggers.join(", ") : "(no triggers)";
    r.trig.innerHTML = ""; r.trig.appendChild(document.createTextNode(t));
    if (e.auto) r.trig.appendChild(el("span", { class: "auto", text: " auto" }));
    r.trig.title = `${row.name}\ntriggers: ${t}\nphrases: ${(e.phrases || []).join(", ") || "(none)"}${e.notes ? "\n" + e.notes : ""}`;
  });
  const info = M.triggersFor(st.rows);
  ui.prefixLine.textContent = info.triggers.length ? `trigger prefix for the enabled rows: ${info.triggers.join(", ")}` : "trigger prefix for the enabled rows: (none)";
}
function openRegistryEditor(node, i) {
  const ui = node._mmxDeck; const name = ui.rows[i].sel.value;
  if (!name || name === M.NONE) return;
  const e = M.registry.loras[name] || { triggers: [], phrases: [], default_strength: 0.85, notes: "" };
  const box = ui.regEditor; box.innerHTML = ""; box.hidden = false;
  const trig = el("input", { type: "text", value: (e.triggers || []).join(", "), placeholder: "trigger words, comma-separated" });
  const phr = el("input", { type: "text", value: (e.phrases || []).join(", "), placeholder: "associated phrases, comma-separated" });
  const str = el("input", { type: "number", min: "0", max: "2", step: "0.05", value: e.default_strength ?? 0.85 });
  const notes = el("input", { type: "text", value: e.notes || "", placeholder: "notes" });
  const hint = el("div", { class: "hint", text: e.auto ? "pre-filled from the file's metadata — saving makes it yours" : "" });
  const meta = el("button", { text: "from metadata", title: "read the safetensors header again", onclick: async () => {
    const d = await M.fetchJson("/mmx/registry/metadata?name=" + encodeURIComponent(name));
    hint.textContent = d.found ? (d.triggers.length ? `metadata (${d.source}): ${d.triggers.join(", ")}` : "the file carries no trigger metadata (keys: " + Object.keys(d.metadata || {}).slice(0, 8).join(", ") + ")") : "file not found in models/loras";
    if (d.triggers.length) trig.value = d.triggers.join(", ");
  } });
  const save = el("button", { class: "primary", text: "Save to registry", onclick: async () => {
    const body = { name, triggers: trig.value.split(",").map(x => x.trim()).filter(Boolean), phrases: phr.value.split(",").map(x => x.trim()).filter(Boolean), default_strength: parseFloat(str.value) || 0, notes: notes.value };
    const d = await M.fetchJson("/mmx/registry/set", body);
    if (d.error) { hint.textContent = "✗ " + d.error; return; }
    await M.loadRegistry(false); box.hidden = true; renderTriggerLines(node);
    report(node, [{ cls: "ok", text: `✓ registry: ${name} — triggers: ${body.triggers.join(", ") || "(none)"}; phrases: ${body.phrases.join(", ") || "(none)"} → ${d.path}; NAS mirror ${d.nas?.enabled && d.nas?.configured ? "queued" : "off"}` }]);
  } });
  const cancel = el("button", { text: "Cancel", onclick: () => { box.hidden = true; } });
  box.appendChild(el("div", { class: "row" }, el("span", { class: "lbl", text: "registry" }), el("b", { text: name })));
  box.appendChild(el("div", { class: "row" }, el("span", { class: "lbl", text: "triggers" }), trig));
  box.appendChild(el("div", { class: "row" }, el("span", { class: "lbl", text: "phrases" }), phr));
  box.appendChild(el("div", { class: "row" }, el("span", { class: "lbl", text: "strength" }), str, el("span", { class: "lbl", text: "notes" }), notes));
  box.appendChild(el("div", { class: "row" }, save, cancel, meta, hint));
  ui._regRow = i;
}
function renderEditor(node) {
  const ui = node._mmxDeck; if (!ui) return;
  const st = readState(node);
  if (ui.editor.value !== st.prompt && document.activeElement !== ui.editor) ui.editor.value = st.prompt;
}
function renderAll(node) { renderEditor(node); renderPresets(node); renderPhrases(node); renderRows(node); renderTargets(node); renderTags(node); }

function report(node, lines) {
  const ui = node._mmxDeck; if (!ui) return;
  ui.report.innerHTML = "";
  for (const l of lines) {
    const div = el("div", { class: l.cls || "" }, l.text);
    if (l.node) div.appendChild(el("a", { text: " [jump]", onclick: () => M.jumpTo(l.node) }));
    ui.report.appendChild(div);
  }
}

// ── actions ──────────────────────────────────────────────────────────────────
function currentPreset(node) {
  const st = readState(node);
  return { name: (node._mmxDeck.presetName.value || "").trim(), prompt: st.prompt, loras: st.rows.filter(r => r.name && r.name !== M.NONE).map(r => ({ name: r.name, strength: r.strength, on: r.on })) };
}
async function savePreset(node, overwrite) {
  const p = currentPreset(node);
  if (!p.name) { report(node, [{ cls: "err", text: "✗ type a preset name first" }]); return; }
  if (!overwrite && cache.names.includes(p.name)) { report(node, [{ cls: "warn", text: `✗ preset '${p.name}' exists — use Update to overwrite it` }]); return; }
  const d = await M.fetchJson("/mmx/presets/save", { ...p, overwrite });
  if (d.error) { report(node, [{ cls: "err", text: "✗ " + d.error }]); return; }
  await loadPresets(false); writeState(node, { preset: p.name }); renderPresets(node);
  report(node, [{ cls: "ok", text: `✓ ${overwrite ? "updated" : "saved"} preset '${p.name}' (${p.loras.length} LoRA row${p.loras.length === 1 ? "" : "s"}) → ${d.path}; NAS mirror ${d.nas?.enabled && d.nas?.configured ? "queued" : "off"}` }]);
}
async function deletePreset(node) {
  const name = node._mmxDeck.presetSel.value;
  if (!name) return;
  if (!confirm(`Delete preset '${name}'?`)) return;
  const d = await M.fetchJson("/mmx/presets/delete", { name });
  await loadPresets(false);
  if (readState(node).preset === name) writeState(node, { preset: "" });
  node._mmxDeck.presetName.value = ""; renderPresets(node);
  report(node, [{ cls: d.deleted ? "ok" : "warn", text: d.deleted ? `✓ deleted preset '${name}'` : `preset '${name}' was not in the store` }]);
}
function loadPreset(node, name) {
  const p = cache.presets[name];
  if (!p) { report(node, [{ cls: "err", text: `✗ preset '${name}' not in the store (press ↻)` }]); return; }
  writeState(node, { prompt: p.prompt || "", preset: p.name, rows: (p.loras || []).map(l => ({ name: l.name, strength: l.strength, on: l.on !== false })) });
  node._mmxDeck.presetName.value = p.name;
  renderEditor(node); renderRows(node); renderPresets(node);
  report(node, [{ cls: "ok", text: `✓ loaded preset '${p.name}': ${(p.prompt || "").length} chars, ${(p.loras || []).length} LoRA row${(p.loras || []).length === 1 ? "" : "s"} — press Send to push it into the graph` }]);
}
async function addPhrase(node) {
  const ui = node._mmxDeck;
  const group = ui.phraseGroup.value.trim(), text = ui.phraseText.value.trim();
  if (!group || !text) { report(node, [{ cls: "err", text: "✗ a phrase needs a group and a text" }]); return; }
  const d = await M.fetchJson("/mmx/phrases/add", { group, text });
  if (d.error) { report(node, [{ cls: "err", text: "✗ " + d.error }]); return; }
  ui.phraseText.value = ""; await loadPhrases(false); renderPhrases(node);
  report(node, [{ cls: "ok", text: `✓ phrase added to '${group}' (${d.path}; NAS mirror ${d.nas?.enabled && d.nas?.configured ? "queued" : "off"})` }]);
}

function send(node) {
  const st = readState(node), t = targets(node), lines = [];
  node._mmxUndo = { manager: t.manager ? { node: t.manager, direction: M.getDirection(t.manager) } : null, stack: t.stack ? { node: t.stack, rows: M.getStack(t.stack) } : null };
  if (t.manager) {
    M.setDirection(t.manager, st.prompt); M.highlight(t.manager);
    lines.push({ cls: "ok", text: `✓ direction (${st.prompt.length} chars) → ${M.label(t.manager)}`, node: t.manager });
  } else lines.push({ cls: "err", text: "✗ no References Manager in the graph — direction not sent" });
  if (t.stack) {
    const rows = M.setStack(t.stack, st.rows); M.highlight(t.stack);
    const eff = M.effectiveRows(rows);
    lines.push({ cls: "ok", text: `✓ ${eff.length} LoRA row${eff.length === 1 ? "" : "s"} on (${eff.map(r => r.name.replace(/\.safetensors$/, "") + "@" + r.strength.toFixed(2)).join(", ") || "none"}) → ${M.label(t.stack)}`, node: t.stack });
    const info = M.triggersFor(rows);
    lines.push({ cls: info.triggers.length ? "ok" : "warn", text: info.triggers.length ? `  trigger prefix that will be applied (Stack → Prompt Affix): ${info.triggers.join(", ")}` : "  trigger prefix: (none — no enabled row has trigger words in the registry)" });
  } else lines.push({ cls: "err", text: "✗ no MMX LoRA Stack in the graph — rows not sent" });
  node._mmxDeck.btnUndo.disabled = !(t.manager || t.stack);
  report(node, lines); renderTags(node);
}
function pull(node) {
  const t = targets(node), lines = [];
  if (t.manager) { writeState(node, { prompt: M.getDirection(t.manager) }); lines.push({ cls: "ok", text: `✓ editor ← direction of ${M.label(t.manager)} (${M.getDirection(t.manager).length} chars)`, node: t.manager }); }
  else lines.push({ cls: "err", text: "✗ no References Manager in the graph" });
  if (t.stack) { writeState(node, { rows: M.getStack(t.stack) }); lines.push({ cls: "ok", text: `✓ rows ← ${M.label(t.stack)}`, node: t.stack }); }
  else lines.push({ cls: "err", text: "✗ no MMX LoRA Stack in the graph" });
  renderEditor(node); renderRows(node); report(node, lines);
}
function undo(node) {
  const u = node._mmxUndo; if (!u) return;
  const lines = [];
  if (u.manager && u.manager.node.graph) { M.setDirection(u.manager.node, u.manager.direction); M.highlight(u.manager.node); lines.push({ cls: "ok", text: `↶ direction restored on ${M.label(u.manager.node)}`, node: u.manager.node }); }
  if (u.stack && u.stack.node.graph) { M.setStack(u.stack.node, u.stack.rows); M.highlight(u.stack.node); lines.push({ cls: "ok", text: `↶ rows restored on ${M.label(u.stack.node)}`, node: u.stack.node }); }
  node._mmxUndo = null; node._mmxDeck.btnUndo.disabled = true;
  report(node, lines.length ? lines : [{ cls: "warn", text: "nothing to undo" }]); renderTags(node);
}

// ── extension ────────────────────────────────────────────────────────────────
app.registerExtension({
  name: "mmx.deck",
  async setup() { try { await loadAll(false); } catch (e) {} },
  async nodeCreated(node) {
    if (node.comfyClass !== M.DECK_TYPE) return;
    injectStyles();
    for (const n of ["prompt", "preset", "loras_json", "targets_json"]) hideWidget(M.widget(node, n));
    const root = buildPanel(node);
    const dw = node.addDOMWidget("mmx_deck", "custom", root, { serialize: false });
    const refit = installSizing(node, dw);
    node.setSize([DEFAULT_W, FALLBACK_H + DOM_MARGIN + 60]);
    const paint = () => { renderAll(node); refit(); };
    if (cache.loaded) paint(); else loadAll(false).then(paint).catch(paint);
    const origConfigure = node.onConfigure;
    node.onConfigure = function (...a) { const r = origConfigure?.apply(this, a); setTimeout(() => { renderAll(node); refit(); }, 0); return r; };
    const onGraph = () => { renderTargets(node); renderTags(node); refit(); };
    window.addEventListener("mmx-graph-changed", onGraph);
    const onReg = () => { if (node.graph) { renderRows(node); } };
    window.addEventListener("mmx-registry-changed", onReg);
    window.addEventListener("mmx-loras-changed", onReg);
    root.addEventListener("mouseenter", onGraph);
    node._mmxTick = setInterval(() => { if (!node.graph) { clearInterval(node._mmxTick); return; } onGraph(); }, 1500);
    const origRemoved = node.onRemoved;
    node.onRemoved = function (...a) { clearInterval(node._mmxTick); try { node._mmxRO?.disconnect(); } catch (e) {} window.removeEventListener("mmx-graph-changed", onGraph); window.removeEventListener("mmx-registry-changed", onReg); window.removeEventListener("mmx-loras-changed", onReg); return origRemoved?.apply(this, a); };
    node.mmxDeck = { send: () => send(node), pull: () => pull(node), undo: () => undo(node), loadPreset: n => loadPreset(node, n), save: o => savePreset(node, o), state: () => readState(node), render: () => renderAll(node), editRegistry: i => openRegistryEditor(node, i), fit: () => node.mmxFit(), ui: node._mmxDeck };
  },
});
