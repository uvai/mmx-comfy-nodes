// MMX References Manager (the drop-in for MiniMaxH3ReferencePack) + the "re-read on external
// write" hook. The RefPack's extension only decorates nodes named MiniMaxH3ReferencePack, so for
// our subclass we hand its beforeRegisterNodeDef a nodeData wearing that name: the identical
// widget (upload row, tile canvas, prompt textarea, modals) is built for MMXReferencesManager.
// Then, for BOTH node types, the references_json and direction widgets get an accessor on the
// instance: any assignment from outside (window.mmx, the Deck, a Library node, a script) queues
// one re-render of the slot UI, so the canvas never shows a stale reference list.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { MANAGER_TYPES, refreshManager, widget, firstFrameLink, withFirstFrame, exportFirstFrameName, effectiveReferences, tagsOf, label, nodeById } from "./mmx_api.js";

const REFPACK_EXT = "MiniMaxRefPack.RefManager";
const STOCK = "MiniMaxH3ReferencePack";

function refpackExtension() {
  const list = app.extensions || [];
  return list.find(e => e && e.name === REFPACK_EXT) || null;
}

// Make an external write to the widget queue one re-render. On the current frontend a multiline
// STRING widget is a DOM widget whose `value` accessor is an own, NON-configurable property that
// routes through options.getValue / options.setValue — so the hook wraps setValue. On a frontend
// where `value` is a plain data property the accessor is defined on the instance instead. If
// neither is possible, a slow poll compares the string.
function schedule(node) {
  if (node._mmxRefreshing || node._mmxRefreshPending) return;
  node._mmxRefreshPending = true;
  queueMicrotask(() => { if (node._mmxRefreshPending) refreshManager(node); });
}
function hookValue(node, w, name) {
  if (!w || w._mmxHooked) return;
  const opts = w.options;
  if (opts && typeof opts.setValue === "function" && typeof opts.getValue === "function") {
    const origSet = opts.setValue, origGet = opts.getValue;
    opts.setValue = function (v, ...rest) {
      let before; try { before = origGet.call(this); } catch (e) {}
      const r = origSet.call(this, v, ...rest);
      if (v !== before) schedule(node);
      return r;
    };
    w._mmxHooked = name + ":setValue";
    return;
  }
  const own = Object.getOwnPropertyDescriptor(w, "value");
  if (!own || own.configurable) {
    let backing = own ? own.value : w.value;
    try {
      Object.defineProperty(w, "value", {
        configurable: true, enumerable: true,
        get: () => backing,
        set(v) { const before = backing; backing = v; if (v !== before) schedule(node); },
      });
      w._mmxHooked = name + ":property";
      return;
    } catch (e) { /* fall through to the poll */ }
  }
  let last = w.value;
  const iv = setInterval(() => {
    if (!node.graph) { clearInterval(iv); return; }
    const cur = w.value;
    if (cur !== last) { last = cur; schedule(node); }
  }, 700);
  w._mmxHooked = name + ":poll";
}

// The exported (API) references_json of an MMX References Manager whose `first_frame` input is
// linked carries the first-frame entry as the last picture, under the filename the run will write
// (mmx_ff_<node>_<queue>.png — unique per queue): what the server validates and executes is what
// the export shows. The widget's own value (what the workflow saves) is untouched.
function hookFirstFrameExport(node, w) {
  if (!w || w._mmxFFExport) return;
  const orig = w.serializeValue;
  w.serializeValue = async function (n, i) {
    let v = orig ? await orig.call(this, n, i) : this.value;
    if (!firstFrameLink(node) || effectiveReferences(node).chainSlot) return v;   // unlinked, or the static chain slot is present: no override
    let list = [];
    try { const d = JSON.parse(v || "{}"); list = Array.isArray(d.references) ? d.references.filter(r => r && r.file) : []; } catch (e) {}
    const r = withFirstFrame(list, exportFirstFrameName(node));
    node._mmxFirstFrameExport = { file: r.list.filter(x => x.kind === "image").pop().file, slot: r.slot, replaced: r.replaced?.file || null };
    return JSON.stringify({ references: r.list });
  };
  w._mmxFFExport = true;
}

// ── the ghost tile ───────────────────────────────────────────────────────────
// While `first_frame` is linked (and no static chain slot is in the list) the Manager shows,
// at the end of its picture row, what the run will put there: the upstream Load Chain Frame's
// currently selected frame (latest / a history segment / its fallback), labelled with the
// number the model will use. It is a DOM overlay on the RefPack's tile slab — dashed amber,
// translucent, pointer-transparent — so it can never be mistaken for, or collide with, an
// injected tile, and nothing about the run-time override changes.
const GEO = { x0: 10, tileY: 38, tile: 131, gap: 6, addBtn: 44, addGap: 24, cap: 9 };   // = the RefPack's CL (images row)
const GHOST_CSS = `
.mmx-ghost{position:absolute;box-sizing:border-box;border:2px dashed #d9a441;border-radius:6px;background:rgba(217,164,65,.10);pointer-events:none;overflow:hidden;font:10px/1.25 sans-serif;color:#f2d38a;z-index:3}
.mmx-ghost img{position:absolute;inset:0;width:100%;height:100%;object-fit:cover;opacity:.55}
.mmx-ghost .lbl{position:absolute;left:0;right:0;bottom:0;padding:3px 5px;background:rgba(40,30,8,.82);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.mmx-ghost .lbl b{display:block;color:#ffd98a;font-size:11px}
.mmx-ghost .tag{position:absolute;top:3px;left:5px;font-size:9px;letter-spacing:.06em;text-transform:uppercase;color:#ffd98a}
.mmx-ghost .none{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:#b08a3a;font-size:10px;text-align:center;padding:6px 6px 26px}
`;
function ghostStyles() { if (document.getElementById("mmx-ghost-styles")) return; const s = document.createElement("style"); s.id = "mmx-ghost-styles"; s.textContent = GHOST_CSS; document.head.appendChild(s); }
function loaderState(origin) {
  if (!origin || origin.comfyClass !== "MMXLoadChainFrame") return null;
  const g = n => widget(origin, n)?.value;
  const fbIn = (origin.inputs || []).find(i => i.name === "fallback");
  const links = origin.graph?.links || app.graph.links;
  const L = fbIn && fbIn.link != null ? (links?.get ? links.get(fbIn.link) : links?.[fbIn.link]) : null;
  const fbNode = L ? nodeById(L.origin_id) : null;
  return { filename: g("filename") || "mmx_chain_last.png", useFallback: g("use_fallback") === true, useFrame: g("use_frame") || "latest", chain: origin._mmxChain || null,
           fallbackFile: fbNode && fbNode.comfyClass === "MMXLibraryImage" ? (widget(fbNode, "file")?.value || "") : "", fallbackLabel: fbNode ? label(fbNode) : "(fallback not wired)" };
}
// {url, line} for what the loader would open on
function ghostSource(st) {
  const fb = () => ({ url: st.fallbackFile ? api.apiURL(`/mmx/library/thumb?path=${encodeURIComponent(st.fallbackFile)}&w=192`) : null, line: st.fallbackFile ? `fallback · ${st.fallbackFile}` : `fallback · ${st.fallbackLabel}` });
  if (st.useFallback) return { ...fb(), mode: "fallback" };
  const c = st.chain;
  if (st.useFrame !== "latest") {
    const e = c?.entries?.find(x => x.name === st.useFrame);
    if (e) return { url: api.apiURL(`/view?filename=${encodeURIComponent(e.thumb || e.name)}&subfolder=${encodeURIComponent(e.subfolder)}&type=input`), line: `segment ${String(e.segment).padStart(3, "0")} · ${e.ts}`, mode: "history" };
    return { url: null, line: `history frame missing · ${st.useFrame}`, mode: "missing" };
  }
  if (c?.latest?.exists) return { url: api.apiURL(`/view?filename=${encodeURIComponent(c.latest.name)}&type=input&t=${Math.floor((c.latest.mtime || 0) * 1000)}`), line: `latest · ${c.latest.name}`, mode: "latest" };
  if (c) return { ...fb(), line: "no chain frame yet → " + fb().line, mode: "fallback" };
  return { url: null, line: "reading chain history…", mode: "pending" };
}
function updateGhost(node) {
  const body = node._mmrpBody; const canvas = body?.canvas; if (!canvas || !canvas.parentElement) return;
  const eff = effectiveReferences(node);
  let el = node._mmxGhost;
  if (!eff.firstFrame) { if (el) el.hidden = true; node._mmxGhostSig = null; return; }
  const origin = eff.firstFrame.origin;
  const st = loaderState(origin);
  if (st && !st.chain && origin.mmxRefreshChain) { origin.mmxRefreshChain(true); }      // fills origin._mmxChain and fires mmx-chain-changed
  const src = st ? ghostSource(st) : { url: null, line: `first_frame ← ${label(origin)}`, mode: "other" };
  const n = eff.list.filter(r => r.kind === "image").length - 1;                          // pictures already in the list
  const slot = eff.firstFrame.slot;
  const cssW = canvas.clientWidth || 1315;
  let x = n ? n * (GEO.tile + GEO.gap) + GEO.x0 - GEO.gap + GEO.addGap + GEO.addBtn + GEO.addGap : GEO.x0 + GEO.addBtn + GEO.addGap;
  let w = Math.min(GEO.tile, cssW - GEO.x0 - x), replaces = false;
  if (w < 70) { x = GEO.x0 + (GEO.cap - 1) * (GEO.tile + GEO.gap); w = GEO.tile; replaces = true; }   // list full: the run replaces Picture 9
  ghostStyles();
  if (!el) { el = node._mmxGhost = document.createElement("div"); el.className = "mmx-ghost"; el.dataset.mmxGhost = "1"; canvas.parentElement.appendChild(el); }
  const sig = JSON.stringify([slot, src.url, src.line, x, w, canvas.offsetLeft, canvas.offsetTop, replaces]);
  if (node._mmxGhostSig === sig && !el.hidden) return;
  node._mmxGhostSig = sig; el.hidden = false;
  Object.assign(el.style, { left: (canvas.offsetLeft + x) + "px", top: (canvas.offsetTop + GEO.tileY) + "px", width: w + "px", height: GEO.tile + "px" });
  el.innerHTML = "";
  if (src.url) { const img = document.createElement("img"); img.src = src.url; img.alt = ""; el.appendChild(img); }
  else { const d = document.createElement("div"); d.className = "none"; d.textContent = src.mode === "pending" ? "…" : "no preview"; el.appendChild(d); }
  const tag = document.createElement("div"); tag.className = "tag"; tag.textContent = "wired · run time"; el.appendChild(tag);
  const lbl = document.createElement("div"); lbl.className = "lbl";
  const b = document.createElement("b"); b.textContent = `Picture ${slot} · first frame (wired)` + (replaces ? " — replaces Picture 9" : ""); lbl.appendChild(b);
  lbl.appendChild(document.createTextNode(src.line)); el.appendChild(lbl);
  el.title = `<Picture ${slot}> at run time = the first_frame input (${label(origin)}): ${src.line}. Not in references_json; delete nothing — unlink the input, or use the loader's "Inject into Manager as slot" for a static tile.`;
}
function installGhost(node) {
  const tick = () => { if (!node.graph) { clearInterval(node._mmxGhostTick); return; } try { updateGhost(node); } catch (e) {} };
  const onEvt = () => tick();
  window.addEventListener("mmx-graph-changed", onEvt); window.addEventListener("mmx-chain-changed", onEvt);
  node._mmxGhostTick = setInterval(tick, 1500);
  const origConn = node.onConnectionsChange;
  node.onConnectionsChange = function (...a) { const r = origConn?.apply(this, a); setTimeout(tick, 50); return r; };
  const origRemoved = node.onRemoved;
  node.onRemoved = function (...a) { clearInterval(node._mmxGhostTick); window.removeEventListener("mmx-graph-changed", onEvt); window.removeEventListener("mmx-chain-changed", onEvt); return origRemoved?.apply(this, a); };
  node.mmxGhost = () => { updateGhost(node); return node._mmxGhost && !node._mmxGhost.hidden ? node._mmxGhost : null; };
  setTimeout(tick, 200);
}

app.registerExtension({
  name: "mmx.manager",
  async beforeRegisterNodeDef(nodeType, nodeData) {
    if (nodeData.name !== "MMXReferencesManager") return;
    const ext = refpackExtension();
    if (!ext?.beforeRegisterNodeDef) { console.warn("[mmx] RefPack extension not found — MMX References Manager gets no slot UI"); return; }
    await ext.beforeRegisterNodeDef.call(ext, nodeType, { ...nodeData, name: STOCK });
  },
  async nodeCreated(node) {
    if (!MANAGER_TYPES.includes(node.comfyClass)) return;
    // the RefPack's onNodeCreated ran first (prototype hook); ours installs the write hooks
    hookValue(node, widget(node, "references_json"), "references_json");
    hookValue(node, widget(node, "direction"), "direction");
    node.mmxRefresh = () => refreshManager(node);
    if (node.comfyClass === "MMXReferencesManager") { hookFirstFrameExport(node, widget(node, "references_json")); installGhost(node); }
  },
});
