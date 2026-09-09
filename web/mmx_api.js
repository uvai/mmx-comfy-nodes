// mmx-comfy-nodes: the small graph API the Deck, the Library nodes and scripts share, exposed as
// window.mmx. Everything here writes ordinary widget values (references_json / direction on a
// References Manager, the on/lora/strength rows on an MMX LoRA Stack) and then asks the target's
// UI to re-read them, so what the panel shows is exactly what graphToPrompt will send.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";

export const MANAGER_TYPES = ["MiniMaxH3ReferencePack", "MMXReferencesManager"];
export const STACK_TYPE = "MMXLoRAStack";
export const LIB_TYPE = "MMXLibraryImage";
export const DECK_TYPE = "MMXDeck";
export const CAPS = { image: 9, video: 3, audio: 3 };
export const ROWS = 5;
export const NONE = "(none)";

export function allNodes() { return app.graph._nodes || app.graph.nodes || []; }
export function nodeById(id) { return id == null ? null : app.graph.getNodeById(Number(id)); }
export function widget(node, name) { return (node?.widgets || []).find(w => w.name === name); }
export function findManagers() { return allNodes().filter(n => MANAGER_TYPES.includes(n.type)); }
export function findStacks() { return allNodes().filter(n => n.type === STACK_TYPE); }
export function findLibraries() { return allNodes().filter(n => n.type === LIB_TYPE); }
export function label(node) { return node ? `${node.title || node.type} #${node.id}` : "(none)"; }

// ── remembered targets (per browser) ─────────────────────────────────────────
export function remembered() { try { return JSON.parse(localStorage.getItem("mmx.targets") || "{}"); } catch (e) { return {}; } }
export function remember(kind, id) { const t = remembered(); t[kind] = id; try { localStorage.setItem("mmx.targets", JSON.stringify(t)); } catch (e) {} }
function pick(list, pref, kind) {
  if (!list.length) return null;
  const want = pref ?? remembered()[kind];
  return list.find(n => n.id === Number(want)) || list[0];
}
export function defaultManager(pref) { return pick(findManagers(), pref, "manager"); }
export function defaultStack(pref) { return pick(findStacks(), pref, "stack"); }

// ── references (the RefPack's schema: {"references":[{kind,file,use_soundtrack?,crop?,trim?}]}) ──
export function getReferences(node) {
  const w = widget(node, "references_json");
  try { const d = JSON.parse(w?.value || "{}"); return Array.isArray(d.references) ? d.references.filter(r => r && r.file) : []; }
  catch (e) { return []; }
}
export function grouped(list) {
  return { images: list.filter(r => r.kind === "image"), videos: list.filter(r => r.kind === "video"), audios: list.filter(r => r.kind === "audio") };
}
export function flatten(g) { return [...g.images, ...g.videos, ...g.audios]; }

// The tag rule (minimax_refpack/refs.py assign_tags): <Picture n> over the images in order; per
// video its soundtrack's <Audio a> BEFORE its <Video v>; standalone audio continues the <Audio> count.
export function tagsOf(nodeOrList) {
  const list = Array.isArray(nodeOrList) ? nodeOrList : getReferences(nodeOrList);
  const g = grouped(list);
  const tags = [];
  g.images.forEach((r, i) => tags.push({ tag: `<Picture ${i + 1}>`, file: r.file, kind: "image", slot: i + 1 }));
  let audio = 0;
  g.videos.forEach((r, i) => {
    if (r.use_soundtrack) { audio += 1; tags.push({ tag: `<Audio ${audio}>`, file: r.file, kind: "soundtrack", slot: audio }); }
    tags.push({ tag: `<Video ${i + 1}>`, file: r.file, kind: "video", slot: i + 1 });
  });
  g.audios.forEach(r => { audio += 1; tags.push({ tag: `<Audio ${audio}>`, file: r.file, kind: "audio", slot: audio }); });
  return { pictures: g.images.length, videos: g.videos.length, audios: audio, tags };
}

// Re-render a References Manager's slot UI from its widgets. The RefPack keeps its working
// state in node._mmrpRefs and re-reads references_json / direction only inside its onConfigure
// wrapper, so that wrapper (with no widgets_values to re-place) is the re-read entry point.
export function refreshManager(node) {
  if (!node) return false;
  node._mmxRefreshPending = false;
  if (node._mmxRefreshing) return false;
  node._mmxRefreshing = true;
  try {
    if (node._mmrpBody && typeof node.onConfigure === "function") node.onConfigure({});
    else if (node._mmrpBody?.directionInput) node._mmrpBody.directionInput.value = widget(node, "direction")?.value || "";
  } catch (e) { console.warn("[mmx] manager refresh failed", e); }
  finally { node._mmxRefreshing = false; }
  node.setDirtyCanvas?.(true, true);
  return true;
}

function notify(node, what) { try { window.dispatchEvent(new CustomEvent("mmx-graph-changed", { detail: { node: node?.id, what } })); } catch (e) {} }

export function setReferences(node, list) {
  const w = widget(node, "references_json");
  if (!w) throw new Error(`${label(node)} has no references_json widget`);
  const value = JSON.stringify({ references: flatten(grouped(list)) });
  w.value = value; w.callback?.(value);
  refreshManager(node); notify(node, "references");
  return value;
}
export function getDirection(node) { return widget(node, "direction")?.value || ""; }
export function setDirection(node, text) {
  const w = widget(node, "direction");
  if (!w) throw new Error(`${label(node)} has no direction widget`);
  w.value = text; w.callback?.(text);
  if (node._mmrpBody?.directionInput) node._mmrpBody.directionInput.value = text;
  refreshManager(node); notify(node, "direction");
  return text;
}

// "Picture 3" -> {kind:"image", index:2}; "Video 1" -> {kind:"video", index:0}; "Audio 1" -> {kind:"audio", index:0}
export function parseSlot(spec) {
  const m = /^(Picture|Video|Audio)\s+(\d+)$/i.exec(String(spec || "").trim());
  if (!m) return null;
  const kind = { picture: "image", video: "video", audio: "audio" }[m[1].toLowerCase()];
  const index = parseInt(m[2], 10) - 1;
  if (index < 0 || index >= CAPS[kind]) return null;
  return { kind, index, spec: `${m[1][0].toUpperCase()}${m[1].slice(1).toLowerCase()} ${index + 1}` };
}

// Write one reference into a slot. The RefPack list is compact (no holes), so "Picture 3" with
// one image present lands as <Picture 2>: the k-th entry is replaced when it exists, otherwise the
// file is appended. Returns what actually happened so the caller can report it honestly.
export function setReferenceSlot(node, spec, ref) {
  const s = parseSlot(spec);
  if (!s) throw new Error(`bad slot "${spec}"`);
  if (ref.kind !== s.kind) throw new Error(`${s.spec} needs ${s.kind === "image" ? "an image" : s.kind === "video" ? "a video" : "an audio file"}, got ${ref.kind} (${ref.file})`);
  const g = grouped(getReferences(node));
  const arr = g[s.kind + "s"];
  const entry = s.kind === "video" ? { kind: "video", file: ref.file, use_soundtrack: ref.use_soundtrack !== false } : { kind: s.kind, file: ref.file };
  let landed, replaced = false;
  if (s.index < arr.length) { replaced = true; arr[s.index] = entry; landed = s.index; }
  else { arr.push(entry); landed = arr.length - 1; }
  setReferences(node, flatten(g));
  const t = tagsOf(node).tags.find(x => x.kind === s.kind && x.slot === landed + 1);
  return { requested: s.spec, landed: landed + 1, tag: t ? t.tag : `<${s.spec.split(" ")[0]} ${landed + 1}>`, replaced, file: ref.file, shifted: landed !== s.index };
}
export function clearReferenceSlot(node, spec) {
  const s = parseSlot(spec);
  if (!s) throw new Error(`bad slot "${spec}"`);
  const g = grouped(getReferences(node));
  const arr = g[s.kind + "s"];
  if (s.index >= arr.length) return { requested: s.spec, removed: null };
  const [gone] = arr.splice(s.index, 1);
  setReferences(node, flatten(g));
  return { requested: s.spec, removed: gone.file };
}

// ── LoRA stack rows ──────────────────────────────────────────────────────────
export function getStack(node) {
  const rows = [];
  for (let i = 1; i <= ROWS; i++) {
    rows.push({ name: widget(node, `lora_${i}`)?.value || NONE, strength: Number(widget(node, `strength_${i}`)?.value ?? 1), on: widget(node, `on_${i}`)?.value !== false });
  }
  return rows;
}
export function normalizeRows(rows) {
  const out = [];
  for (let i = 0; i < ROWS; i++) {
    const r = (rows || [])[i] || {};
    const name = r.name || r.lora || NONE;
    out.push({ name: name || NONE, strength: Math.max(0, Math.min(2, Number(r.strength ?? 1) || 0)), on: r.on !== false && name !== NONE });
  }
  return out;
}
export function setStack(node, rows) {
  const norm = normalizeRows(rows);
  norm.forEach((r, i) => {
    const lw = widget(node, `lora_${i + 1}`), sw = widget(node, `strength_${i + 1}`), ow = widget(node, `on_${i + 1}`);
    if (lw) {
      if (lw.options?.values && !lw.options.values.includes(r.name)) lw.options.values = [...lw.options.values, r.name];   // shows the name; the server validates the file
      lw.value = r.name; lw.callback?.(r.name);
    }
    if (sw) { sw.value = r.strength; sw.callback?.(r.strength); }
    if (ow) { ow.value = r.on; ow.callback?.(r.on); }
  });
  node.mmxRefreshSummary?.();
  node.setDirtyCanvas?.(true, true); notify(node, "stack");
  return norm;
}
export function effectiveRows(rows) { return normalizeRows(rows).filter(r => r.on && r.name !== NONE && r.strength > 0); }
export function describeStack(rows) {
  const eff = effectiveRows(rows);
  return eff.length ? eff.map((r, i) => `${i + 1}. ${r.name} @ ${r.strength.toFixed(2)}`).join("\n") : "(no LoRA enabled — model/clip pass through)";
}

// ── canvas helpers ───────────────────────────────────────────────────────────
export function jumpTo(node) {
  if (!node) return;
  try { app.canvas.centerOnNode(node); } catch (e) {}
  try { app.canvas.selectNode(node); } catch (e) {}
  app.canvas.setDirty(true, true);
}
export function highlight(node, ms = 2500) {
  if (!node) return;
  if (!node._mmxHl) node._mmxHl = { color: node.color, bgcolor: node.bgcolor };
  node.color = "#6b4a00"; node.bgcolor = "#8a6a10";
  node.setDirtyCanvas(true, true);
  clearTimeout(node._mmxHlTimer);
  node._mmxHlTimer = setTimeout(() => { node.color = node._mmxHl.color; node.bgcolor = node._mmxHl.bgcolor; node._mmxHl = null; node.setDirtyCanvas(true, true); }, ms);
}

// ── library inject ───────────────────────────────────────────────────────────
export async function injectLibrary(libNode, manager, slotOverride) {
  const path = widget(libNode, "file")?.value;
  const slot = slotOverride || widget(libNode, "slot")?.value;
  if (!path || path.startsWith("(")) throw new Error("pick a library file first");
  if (!slot || slot === NONE) throw new Error("pick a target slot (Picture 1–9 / Video 1–3 / Audio 1)");
  manager = manager || defaultManager();
  if (!manager) throw new Error("no References Manager in the graph");
  const s = parseSlot(slot);
  const r = await api.fetchApi("/mmx/library/inject", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path, slot }) });
  const d = await r.json();
  if (d.error) throw new Error(d.error);
  let file = d.filename, kind = d.kind;
  if (s.kind === "image" && kind === "video" && d.frame_png) { file = d.frame_png; kind = "image"; }
  const res = setReferenceSlot(manager, slot, { kind, file, use_soundtrack: true });
  return { ...res, manager, library: libNode, path, source: d.filename, firstFrame: file !== d.filename };
}

export async function fetchJson(url, body) {
  const r = await api.fetchApi(url, body ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) } : {});
  return r.json();
}

window.mmx = {
  MANAGER_TYPES, STACK_TYPE, LIB_TYPE, DECK_TYPE, CAPS,
  nodeById, findManagers, findStacks, findLibraries, defaultManager, defaultStack, remember, remembered,
  getReferences, setReferences, tagsOf, setReferenceSlot, clearReferenceSlot, parseSlot,
  getDirection, setDirection, refreshManager,
  getStack, setStack, describeStack, effectiveRows,
  jumpTo, highlight, injectLibrary, label,
};
export default window.mmx;
