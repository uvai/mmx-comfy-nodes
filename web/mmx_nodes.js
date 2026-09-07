// mmx-comfy-nodes web extension, part 2: execution results shown in the node body (First Frame
// Check numbers + PASS/FAIL, Chain Gate path, Library copy, References map) and the MMX Library
// Image node UI (search box filtering the dropdown, thumbnail preview, Refresh / Mirror from NAS).
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { ComfyWidgets } from "../../scripts/widgets.js";

const RESULT_NODES = ["MMXFirstFrameCheck", "MMXChainGate", "MMXLibraryImage", "MMXReferencesBuilder", "MMXSaveFrame"];
const LIB_NONE = "(library empty — press Refresh / Mirror from NAS)";
const COLORS = { pass: { color: "#1f4d2b", bgcolor: "#27603a" }, fail: { color: "#5a1f1f", bgcolor: "#7a2a2a" } };
let lib = { paths: [], items: {}, root: "", loaded: false };

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
  node.setDirtyCanvas(true, true);
}

// ── library ──────────────────────────────────────────────────────────────────
async function fetchLibrary(refresh, sync) {
  const r = refresh ? await api.fetchApi("/mmx/library/refresh" + (sync ? "?sync=1" : ""), { method: "POST" }) : await api.fetchApi("/mmx/library");
  const d = await r.json();
  lib.paths = d.paths || []; lib.root = d.root || ""; lib.items = {}; lib.loaded = true;
  for (const it of d.items || []) lib.items[it.path] = it;
  return d;
}

function fileWidget(node) { return node.widgets?.find(w => w.name === "file"); }

function applyFilter(node) {
  const fw = fileWidget(node); if (!fw) return;
  const q = (node.widgets?.find(w => w.name === "mmx_search")?.value || "").trim().toLowerCase();
  const terms = q.split(/\s+/).filter(Boolean);
  let values = lib.paths.filter(p => terms.every(t => p.toLowerCase().includes(t)));
  if (!values.length) values = lib.paths.length ? [] : [LIB_NONE];
  fw.options.values = values.length ? values : [terms.length ? `(no match for "${q}")` : LIB_NONE];
  if (!fw.options.values.includes(fw.value)) {
    // keep a valid selection: the first match when filtering, else leave the stored value alone
    if (values.length && terms.length) { fw.value = values[0]; loadThumb(node); }
  }
  node.setDirtyCanvas(true, true);
}

function loadThumb(node) {
  const fw = fileWidget(node);
  const p = fw?.value;
  if (!p || p.startsWith("(")) { node.imgs = null; node.setDirtyCanvas(true, true); return; }
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
      await fetchLibrary(true, false); applyFilter(node);
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
  // search box (client side, not serialized)
  const sw = node.addWidget("text", "mmx_search", "", () => applyFilter(node), { serialize: false });
  sw.serialize = false; sw.options = { ...(sw.options || {}), serialize: false, placeholder: "search" };
  // keep the search box right under the dropdown
  const i = node.widgets.indexOf(sw); node.widgets.splice(i, 1); node.widgets.splice(node.widgets.indexOf(fw) + 1, 0, sw);
  const refresh = node.addWidget("button", "↻ Refresh library", null, async () => {
    refresh.name = "refreshing…"; node.setDirtyCanvas(true, true);
    try { await fetchLibrary(true, false); applyFilter(node); loadThumb(node); refresh.name = `↻ Refresh library (${lib.paths.length} files)`; }
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
  },
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (!RESULT_NODES.includes(nodeData.name)) return;
    const origExecuted = nodeType.prototype.onExecuted;
    nodeType.prototype.onExecuted = function (message) {
      origExecuted?.apply(this, arguments);
      const text = Array.isArray(message?.text) ? message.text.join("\n") : (message?.text || "");
      let verdict;
      if (nodeData.name === "MMXFirstFrameCheck" && Array.isArray(message?.passed)) verdict = !!message.passed[0];
      if (nodeData.name === "MMXChainGate") verdict = true;   // a failed gate raises instead of reporting
      showResult(this, text, verdict);
    };
  },
  async nodeCreated(node) {
    if (!RESULT_NODES.includes(node.comfyClass)) return;
    resultWidget(node);
    if (node.comfyClass === "MMXLibraryImage") setupLibraryNode(node);
  },
});
