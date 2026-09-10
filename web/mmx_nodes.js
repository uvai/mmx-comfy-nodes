// mmx-comfy-nodes web extension, part 2: execution results shown in the node body (First Frame
// Check numbers + PASS/FAIL/skipped, Chain Gate path, Library copy, References map, LoRA stack),
// the MMX Library Image node UI (search box filtering the dropdown, thumbnail preview, Refresh /
// Mirror from NAS, Inject / Clear slot / Inject all into the References Manager) and the MMX LoRA
// Stack body (live summary of the effective rows + Refresh LoRAs).
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { ComfyWidgets } from "../../scripts/widgets.js";
import * as M from "./mmx_api.js";

const RESULT_NODES = ["MMXFirstFrameCheck", "MMXChainGate", "MMXLibraryImage", "MMXReferencesBuilder", "MMXSaveFrame", "MMXLoRAStack", "MMXPromptAffix"];
const LIB_NONE = "(library empty — press Refresh / Mirror from NAS)";   // legacy placeholder (0.2–0.4 files): read as "unset"
const LIB_UNSET = "";                                                     // the dropdown's first entry: nothing picked yet
const isUnset = v => typeof v !== "string" || !v || v === LIB_NONE || (v.startsWith("(") && v.endsWith(")"));
const COLORS = { pass: { color: "#1f4d2b", bgcolor: "#27603a" }, fail: { color: "#5a1f1f", bgcolor: "#7a2a2a" } };
let lib = { paths: [], items: {}, root: "", loaded: false, lastLine: "" };

function resultWidget(node) {
  let w = node.widgets?.find(w => w.name === "mmx_result");
  if (w) return w;
  try {
    const r = ComfyWidgets.STRING(node, "mmx_result", ["STRING", { multiline: true }], app);
    w = r.widget; w.name = "mmx_result";
    if (w.inputEl) { w.inputEl.readOnly = true; w.inputEl.style.opacity = 0.9; w.inputEl.placeholder = "result appears here after the run"; }
  } catch (e) {
    w = node.addWidget("text", "mmx_result", "", () => {}, {}); w.disabled = true;
  }
  w.serialize = false; w.options = { ...(w.options || {}), serialize: false };
  node.setSize([Math.max(node.size[0], 360), Math.max(node.size[1], node.computeSize()[1])]);
  return w;
}

function showResult(node, text, verdict) {
  const w = resultWidget(node);
  w.value = text || "";
  if (verdict === true) { node.color = COLORS.pass.color; node.bgcolor = COLORS.pass.bgcolor; }
  else if (verdict === false) { node.color = COLORS.fail.color; node.bgcolor = COLORS.fail.bgcolor; }
  else if (verdict === "skip") { node.color = "#3a3a2a"; node.bgcolor = "#4a4a38"; }
  node.setDirtyCanvas(true, true);
}

// ── LoRA stack ───────────────────────────────────────────────────────────────
function setupStackNode(node) {
  const summary = () => showResult(node, M.describeStack(M.getStack(node)));
  node.mmxRefreshSummary = summary;
  for (const w of node.widgets || []) {
    if (/^(on|lora|strength)_\d$/.test(w.name)) { const orig = w.callback; w.callback = function (...a) { const r = orig?.apply(this, a); summary(); return r; }; }
  }
  // the five dropdowns always show the shared list (R, this button and the Deck's ↻ all feed it)
  const repopulate = () => { for (const w of node.widgets.filter(w => /^lora_\d$/.test(w.name))) w.options.values = M.loraValues(w.value); summary(); };
  node.mmxRepopulateLoras = repopulate;
  const btn = node.addWidget("button", "↻ Refresh LoRAs", null, async () => {
    btn.name = "refreshing…"; node.setDirtyCanvas(true, true);
    try { const list = await M.loadLoras(); repopulate(); btn.name = `↻ Refresh LoRAs (${list.length})`; }
    catch (e) { btn.name = "↻ Refresh LoRAs (failed: " + (e?.message || e) + ")"; }
    setTimeout(() => { btn.name = "↻ Refresh LoRAs"; node.setDirtyCanvas(true, true); }, 4000);
    node.setDirtyCanvas(true, true);
  });
  btn.serialize = false; btn.options = { ...(btn.options || {}), serialize: false };
  const onLoras = () => { if (node.graph) repopulate(); else window.removeEventListener("mmx-loras-changed", onLoras); };
  window.addEventListener("mmx-loras-changed", onLoras);
  if (M.loras.loaded) repopulate();
  const origConfigure = node.onConfigure;
  node.onConfigure = function (...a) { const r = origConfigure?.apply(this, a); summary(); return r; };
  const onReg = () => { if (node.graph) summary(); else window.removeEventListener("mmx-registry-changed", onReg); };
  window.addEventListener("mmx-registry-changed", onReg);
  if (M.registry.loaded) summary(); else M.loadRegistry(false).then(summary).catch(summary);
  node.setSize([Math.max(node.size[0], 460), node.computeSize()[1] + 60]);
}

// ── library inject ───────────────────────────────────────────────────────────
function groupOf(node) {
  const groups = app.graph._groups || app.graph.groups || [];
  const [x, y] = node.pos;
  return groups.find(g => { const b = g._bounding || g.bounding; return b && x >= b[0] && y >= b[1] && x <= b[0] + b[2] && y <= b[1] + b[3]; }) || null;
}
function librariesInGroup(node) {
  const g = groupOf(node);
  const libs = M.findLibraries();
  if (!g) return { group: null, libs };
  const b = g._bounding || g.bounding;
  return { group: g, libs: libs.filter(n => n.pos[0] >= b[0] && n.pos[1] >= b[1] && n.pos[0] <= b[0] + b[2] && n.pos[1] <= b[1] + b[3]) };
}
const slotOrder = s => { const p = M.parseSlot(s); return p ? ({ image: 0, video: 100, audio: 200 })[p.kind] + p.index : 999; };

async function injectOne(node) {
  const mgr = M.defaultManager();
  const res = await M.injectLibrary(node, mgr);
  M.highlight(mgr);
  const note = res.shifted ? ` (asked for ${res.requested}; the list had fewer entries, so it is ${res.tag})` : "";
  return `${res.replaced ? "replaced" : "added"} ${res.tag} = ${res.file}${res.firstFrame ? " (first frame of " + res.source + ")" : ""} in ${M.label(mgr)}${note}`;
}
function setupInjectButtons(node) {
  const inject = node.addWidget("button", "⇢ Inject into Manager", null, async () => {
    inject.name = "injecting…"; node.setDirtyCanvas(true, true);
    try { const t = await injectOne(node); showResult(node, t); inject.name = "⇢ Inject into Manager (done)"; }
    catch (e) { showResult(node, "inject failed: " + (e?.message || e)); inject.name = "⇢ Inject into Manager (failed)"; }
    setTimeout(() => { inject.name = "⇢ Inject into Manager"; node.setDirtyCanvas(true, true); }, 4000);
    node.setDirtyCanvas(true, true);
  });
  const clear = node.addWidget("button", "✕ Clear slot", null, () => {
    try {
      const mgr = M.defaultManager(); const slot = node.widgets.find(w => w.name === "slot")?.value;
      if (!mgr) throw new Error("no References Manager in the graph");
      const r = M.clearReferenceSlot(mgr, slot); M.highlight(mgr);
      showResult(node, r.removed ? `cleared ${r.requested} (${r.removed}) in ${M.label(mgr)}; later entries move up` : `${r.requested} is already empty in ${M.label(mgr)}`);
    } catch (e) { showResult(node, "clear failed: " + (e?.message || e)); }
  });
  const all = node.addWidget("button", "⇢ Inject all (group)", null, async () => {
    all.name = "injecting…"; node.setDirtyCanvas(true, true);
    const { group, libs } = librariesInGroup(node);
    const todo = libs.filter(n => { const s = n.widgets.find(w => w.name === "slot")?.value; return s && s !== M.NONE; }).sort((a, b) => slotOrder(a.widgets.find(w => w.name === "slot").value) - slotOrder(b.widgets.find(w => w.name === "slot").value));
    const lines = [group ? `group "${group.title}": ${todo.length} library node(s) with a slot` : `no group around this node: ${todo.length} library node(s) with a slot in the graph`];
    for (const n of todo) {
      try { lines.push("✓ " + await injectOne(n)); } catch (e) { lines.push(`✗ ${M.label(n)}: ${e?.message || e}`); }
    }
    showResult(node, lines.join("\n")); all.name = "⇢ Inject all (group)"; node.setDirtyCanvas(true, true);
  });
  for (const b of [inject, clear, all]) { b.serialize = false; b.options = { ...(b.options || {}), serialize: false }; }
  return [inject, clear, all];
}

// ── library ──────────────────────────────────────────────────────────────────
async function fetchLibrary(refresh, sync) {
  const r = refresh ? await api.fetchApi("/mmx/library/refresh" + (sync ? "?sync=1" : ""), { method: "POST" }) : await api.fetchApi("/mmx/library");
  const d = await r.json();
  lib.paths = d.paths || []; lib.root = d.root || ""; lib.items = {}; lib.loaded = true; lib.lastLine = d.sync?.last_line || "";
  for (const it of d.items || []) lib.items[it.path] = it;
  return d;
}

function fileWidget(node) { return node.widgets?.find(w => w.name === "file"); }

// Rebuild the dropdown in place from the shared library list (load, Refresh, R, a finished
// mirror, a search keystroke). The current pick is never forced: an unset value stays unset, a
// pick that is in the list stays selected, and a saved pick the mirror does not (yet) hold is
// kept visible as an extra entry — it becomes a normal entry the moment the mirror brings the
// file, and the server validates it only on queue.
function applyFilter(node) {
  const fw = fileWidget(node); if (!fw) return;
  const q = (node.widgets?.find(w => w.name === "mmx_search")?.value || "").trim().toLowerCase();
  const terms = q.split(/\s+/).filter(Boolean);
  const matches = lib.paths.filter(p => terms.every(t => p.toLowerCase().includes(t)));
  const cur = isUnset(fw.value) ? LIB_UNSET : fw.value;
  const values = [LIB_UNSET, ...matches];
  if (cur && !values.includes(cur)) values.push(cur);
  fw.options.values = values;
  if (terms.length && matches.length && !matches.includes(cur)) { fw.value = matches[0]; loadThumb(node); }   // typing a search picks the first match
  else if (fw.value !== cur) fw.value = cur;                                                                     // the legacy placeholder reads as unset
  if (!lib.paths.length) showResult(node, `library empty (${lib.root || "mirror root unknown"}) — last mirror log:\n${lib.lastLine || "(no mirror log yet)"}\nThe boot mirror retries every 60 s for 2 h while the share is locked; unlock it in vgo, or press Mirror from NAS.`);
  else if (!fw.value) showResult(node, `no file selected — ${lib.paths.length} file${lib.paths.length === 1 ? "" : "s"} in the library (pick one in the dropdown; the search box filters it). Not an error until queued.`);
  else if (!lib.paths.includes(fw.value)) showResult(node, `${fw.value} is not in the mirror (${lib.root || "?"}) — kept as your pick; it is used as soon as the mirror brings it (Refresh / Mirror from NAS).\nlast mirror log: ${lib.lastLine || "(no mirror log yet)"}`);
  node.setDirtyCanvas(true, true);
}
function refreshAllLibraryNodes() {
  for (const n of app.graph._nodes || []) if (n.comfyClass === "MMXLibraryImage") { applyFilter(n); loadThumb(n); }
}

function loadThumb(node) {
  const fw = fileWidget(node);
  const p = fw?.value;
  if (isUnset(p)) { node.imgs = null; node.setDirtyCanvas(true, true); return; }
  const img = new Image();
  img.onload = () => { if (fileWidget(node)?.value === p) { node.imgs = [img]; node.setSizeForImage?.(); node.setDirtyCanvas(true, true); } };
  img.onerror = () => { if (fileWidget(node)?.value === p) { node.imgs = null; node.setDirtyCanvas(true, true); } };
  img.src = api.apiURL(`/mmx/library/thumb?path=${encodeURIComponent(p)}&w=384&t=${Date.now() >> 16}`);
  const it = lib.items[p];
  if (it) showResult(node, `${it.kind}  ${(it.size / 1048576).toFixed(2)} MB  ->  input/${p.replace(/\//g, "__")}`);
}

async function pollSync(node, btn) {
  for (let i = 0; i < 600; i++) {
    await new Promise(r => setTimeout(r, 2000));
    let st;
    try { st = await (await api.fetchApi("/mmx/library/sync")).json(); } catch (e) { break; }
    const tail = (st.log_tail || "").trim().split("\n").pop() || "";
    btn.name = `⇣ mirroring… ${tail.slice(-48)}`; node.setDirtyCanvas(true, true);
    if (!st.running) {
      await fetchLibrary(true, false); refreshAllLibraryNodes();   // every Library node's list, in place, picks kept
      btn.name = `⇣ Mirror from NAS (done: ${lib.paths.length} files${st.last?.rc ? ", rc " + st.last.rc : ""})`;
      showResult(node, (st.log_tail || "").trim().split("\n").slice(-4).join("\n"));
      break;
    }
  }
  setTimeout(() => { btn.name = "⇣ Mirror from NAS"; node.setDirtyCanvas(true, true); }, 6000);
  node.setDirtyCanvas(true, true);
}

function setupLibraryNode(node) {
  const fw = fileWidget(node);
  // search box (client side, not serialized). It is appended AFTER the node's own widgets
  // (file, slot) so widgets_values keeps its two-entry shape on every frontend, index-mapped or
  // not: [file, slot] in the file is [file, slot] on the canvas.
  const sw = node.addWidget("text", "mmx_search", "", () => applyFilter(node), { serialize: false });
  sw.serialize = false; sw.options = { ...(sw.options || {}), serialize: false, placeholder: "search" };
  const refresh = node.addWidget("button", "↻ Refresh library", null, async () => {
    refresh.name = "refreshing…"; node.setDirtyCanvas(true, true);
    try { await fetchLibrary(true, false); refreshAllLibraryNodes(); refresh.name = `↻ Refresh library (${lib.paths.length} files)`; }
    catch (e) { refresh.name = "↻ Refresh library (failed: " + (e?.message || e) + ")"; }
    setTimeout(() => { refresh.name = "↻ Refresh library"; node.setDirtyCanvas(true, true); }, 4000);
    node.setDirtyCanvas(true, true);
  });
  refresh.serialize = false; refresh.options = { ...(refresh.options || {}), serialize: false };
  const mirror = node.addWidget("button", "⇣ Mirror from NAS", null, async () => {
    mirror.name = "⇣ starting mirror…"; node.setDirtyCanvas(true, true);
    try {
      const d = await fetchLibrary(true, true);
      const s = d.sync_started || {};
      if (s.error) { mirror.name = "⇣ Mirror from NAS (" + s.error + ")"; showResult(node, s.error); setTimeout(() => { mirror.name = "⇣ Mirror from NAS"; node.setDirtyCanvas(true, true); }, 6000); return; }
      pollSync(node, mirror);
    } catch (e) { mirror.name = "⇣ Mirror from NAS (failed: " + (e?.message || e) + ")"; }
    node.setDirtyCanvas(true, true);
  });
  mirror.serialize = false; mirror.options = { ...(mirror.options || {}), serialize: false };
  if (fw) {
    const orig = fw.callback;
    fw.callback = function (...args) { const r = orig?.apply(this, args); loadThumb(node); return r; };
  }
  const buttons = setupInjectButtons(node);
  // body order: the node's own widgets (file, slot) first and in definition order — the shape
  // widgets_values is saved and restored in — then ours: search, refresh, mirror, inject buttons, result
  const ours = [sw, refresh, mirror, ...buttons, resultWidget(node)];
  node.widgets = [...node.widgets.filter(w => !ours.includes(w)), ...ours];
  const origConfigure = node.onConfigure;
  node.onConfigure = function (...args) { const r = origConfigure?.apply(this, args); if (lib.loaded) applyFilter(node); loadThumb(node); return r; };
  const paint = () => { applyFilter(node); loadThumb(node); };
  if (lib.loaded) paint(); else fetchLibrary(false).then(paint).catch(() => {});
  node.setSize([Math.max(node.size[0], 380), Math.max(node.size[1], node.computeSize()[1] + 200)]);
}

// ── extension ────────────────────────────────────────────────────────────────
app.registerExtension({
  name: "mmx.nodes",
  async setup() {
    // the gate raises after sending this, so the node's own ui output never arrives
    api.addEventListener("mmx-gate", ev => {
      const d = ev.detail || {};
      const node = app.graph.getNodeById(Number(d.node));
      if (node) showResult(node, d.text, false);
    });
    try { await fetchLibrary(false); } catch (e) { console.warn("[mmx-nodes] library fetch failed", e); }
    try { await M.loadRegistry(false); } catch (e) { console.warn("[mmx-nodes] registry fetch failed", e); }
    try { await M.loadLoras(); } catch (e) { console.warn("[mmx-nodes] loras fetch failed", e); }
  },
  // "R" / app.refreshComboInNodes: the frontend has just rewritten every combo from the fresh
  // object_info. Take the LoRA list from that same definition (so the Stack rows, the Deck rows
  // and Preset Save agree with what the server will validate), re-read the library mirror so the
  // search filter works on the new file list, and repaint.
  async refreshComboInNodes(defs) {
    const fromDef = defs?.MMXLoRAStack?.input?.required?.lora_1?.[0];
    if (Array.isArray(fromDef)) M.setLoraList(fromDef); else { try { await M.loadLoras(); } catch (e) {} }
    try { await fetchLibrary(true, false); } catch (e) { console.warn("[mmx-nodes] library refresh failed", e); }
    refreshAllLibraryNodes();
    for (const n of app.graph._nodes || []) if (n.comfyClass === "MMXLoRAStack") n.mmxRepopulateLoras?.();
    try { await M.loadRegistry(false); } catch (e) {}
  },
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (!RESULT_NODES.includes(nodeData.name)) return;
    const origExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      origExecuted?.apply(this, arguments);
      const text = Array.isArray(message?.text) ? message.text.join("\n") : (message?.text || "");
      let verdict;
      if (nodeData.name === "MMXFirstFrameCheck" && Array.isArray(message?.passed)) verdict = message?.skipped?.[0] ? "skip" : !!message.passed[0];
      if (nodeData.name === "MMXChainGate") verdict = Array.isArray(message?.passed) ? !!message.passed[0] : true;   // strict fail raises instead (mmx-gate event)
      showResult(this, text, verdict);
    };
  },
  async nodeCreated(node) {
    if (!RESULT_NODES.includes(node.comfyClass)) return;
    resultWidget(node);
    if (node.comfyClass === "MMXLibraryImage") setupLibraryNode(node);
    if (node.comfyClass === "MMXLoRAStack") setupStackNode(node);
  },
});
