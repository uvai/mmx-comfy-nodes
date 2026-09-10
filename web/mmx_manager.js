// MMX References Manager (the drop-in for MiniMaxH3ReferencePack) + the "re-read on external
// write" hook. The RefPack's extension only decorates nodes named MiniMaxH3ReferencePack, so for
// our subclass we hand its beforeRegisterNodeDef a nodeData wearing that name: the identical
// widget (upload row, tile canvas, prompt textarea, modals) is built for MMXReferencesManager.
// Then, for BOTH node types, the references_json and direction widgets get an accessor on the
// instance: any assignment from outside (window.mmx, the Deck, a Library node, a script) queues
// one re-render of the slot UI, so the canvas never shows a stale reference list.
import { app } from "../../scripts/app.js";
import { MANAGER_TYPES, refreshManager, widget, firstFrameLink, withFirstFrame, exportFirstFrameName } from "./mmx_api.js";

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
    if (!firstFrameLink(node)) return v;
    let list = [];
    try { const d = JSON.parse(v || "{}"); list = Array.isArray(d.references) ? d.references.filter(r => r && r.file) : []; } catch (e) {}
    const r = withFirstFrame(list, exportFirstFrameName(node));
    node._mmxFirstFrameExport = { file: r.list.filter(x => x.kind === "image").pop().file, slot: r.slot, replaced: r.replaced?.file || null };
    return JSON.stringify({ references: r.list });
  };
  w._mmxFFExport = true;
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
    if (node.comfyClass === "MMXReferencesManager") hookFirstFrameExport(node, widget(node, "references_json"));
  },
});
