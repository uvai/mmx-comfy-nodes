// mmx-comfy-nodes web extension: Refresh button (re-reads the preset store without a page
// reload, pulling the NAS copy first) and a LoRA-list panel in the MMX Preset / MMX Sequence
// node bodies that follows the selected preset.
import { app } from "../../scripts/app.js";
import { api } from "../../scripts/api.js";
import { ComfyWidgets } from "../../scripts/widgets.js";

const PRESET_NODES = ["MMXPreset", "MMXSequence", "MMXPresetSave"];
let cache = { names: [], presets: {}, loras: [] };

async function fetchPresets(refresh) {
  const r = refresh ? await api.fetchApi("/mmx/presets/refresh", { method: "POST" }) : await api.fetchApi("/mmx/presets");
  const d = await r.json();
  cache.names = d.names || [];
  cache.presets = {};
  for (const p of d.presets || []) cache.presets[p.name] = p;
  try { const l = await (await api.fetchApi("/mmx/loras")).json(); cache.loras = l.loras || []; } catch (e) {}
  return d;
}

function describe(p, scale) {
  if (!p) return "(preset not in store — press Refresh)";
  const s = scale == null ? 1 : scale;
  const lines = (p.loras && p.loras.length) ? p.loras.map(l => `${l.name.replace(/\.safetensors$/, "")} @ ${(l.strength * s).toFixed(2)}`) : ["(no LoRAs)"];
  if (p.notes) lines.push(p.notes);
  const prompt = (p.prompt || "").trim();
  if (prompt) lines.push("— " + (prompt.length > 160 ? prompt.slice(0, 160) + "…" : prompt));
  return lines.join("\n");
}

function comboWidgets(node) {
  return (node.widgets || []).filter(w => w.type === "combo" && /^preset(_\d+)?$/.test(w.name));
}

function refreshCombos(node) {
  const names = cache.names.length ? cache.names : ["(none)"];
  for (const w of comboWidgets(node)) {
    const withNone = node.comfyClass === "MMXSequence";
    w.options.values = withNone ? ["(none)", ...names] : names;
    if (!w.options.values.includes(w.value)) w.value = w.options.values[0];
  }
  if (node.comfyClass === "MMXPresetSave") {
    for (const w of (node.widgets || []).filter(w => w.type === "combo" && /^lora_\d$/.test(w.name))) {
      w.options.values = ["(none)", ...cache.loras];
      if (!w.options.values.includes(w.value)) w.value = "(none)";
    }
  }
}

function updateInfo(node) {
  const info = node.widgets?.find(w => w.name === "mmx_info");
  if (!info) return;
  const scale = node.widgets?.find(w => w.name === "strength_scale")?.value;
  if (node.comfyClass === "MMXPreset") {
    const name = node.widgets?.find(w => w.name === "preset")?.value;
    info.value = describe(cache.presets[name], scale);
  } else if (node.comfyClass === "MMXSequence") {
    const slots = comboWidgets(node).map(w => w.value).filter(v => v && v !== "(none)");
    const idx = node.widgets?.find(w => w.name === "index")?.value ?? 0;
    if (!slots.length) { info.value = "(no slots filled)"; }
    else {
      const cur = ((idx % slots.length) + slots.length) % slots.length;
      info.value = slots.map((n, i) => `${i === cur ? "▶" : " "} ${i + 1}. ${n}`).join("\n") + "\n\n" + describe(cache.presets[slots[cur]], scale);
    }
  }
  node.setDirtyCanvas(true, true);
}

app.registerExtension({
  name: "mmx.presets",
  async setup() {
    try { await fetchPresets(false); } catch (e) { console.warn("[mmx-presets] initial fetch failed", e); }
  },
  async nodeCreated(node) {
    if (!PRESET_NODES.includes(node.comfyClass)) return;
    // read-only info panel in the node body (a multiline STRING widget, not serialized)
    if (node.comfyClass !== "MMXPresetSave") {
      try {
        const w = ComfyWidgets.STRING(node, "mmx_info", ["STRING", { multiline: true }], app);
        w.widget.name = "mmx_info"; w.widget.serialize = false;
        if (w.widget.inputEl) { w.widget.inputEl.readOnly = true; w.widget.inputEl.style.opacity = 0.85; w.widget.inputEl.placeholder = "preset LoRAs"; }
      } catch (e) {
        const info = node.addWidget("text", "mmx_info", "", () => {}); info.serialize = false; info.disabled = true;
      }
    }
    const btn = node.addWidget("button", "↻ Refresh presets", null, async () => {
      btn.name = "refreshing…"; node.setDirtyCanvas(true, true);
      try {
        const d = await fetchPresets(true);
        refreshCombos(node); updateInfo(node);
        const pull = d.pull || {};
        btn.name = `↻ Refresh presets (${cache.names.length}${pull.ok === false ? ", NAS: " + pull.reason : ""})`;
      } catch (e) {
        btn.name = "↻ Refresh presets (failed: " + (e?.message || e) + ")";
      }
      setTimeout(() => { btn.name = "↻ Refresh presets"; node.setDirtyCanvas(true, true); }, 4000);
      node.setDirtyCanvas(true, true);
    });
    btn.serialize = false;
    // follow preset / index / scale changes
    for (const w of node.widgets || []) {
      if (w.type === "combo" || w.name === "index" || w.name === "strength_scale") {
        const orig = w.callback;
        w.callback = function (...args) { const r = orig?.apply(this, args); updateInfo(node); return r; };
      }
    }
    if (cache.names.length) refreshCombos(node);
    updateInfo(node);
    node.setSize([Math.max(node.size[0], 340), node.computeSize()[1]]);
  },
});

