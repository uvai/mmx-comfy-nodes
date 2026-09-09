"""MMX nodes: preset (prompt + LoRA stack) selection on the canvas, preset save, sequence
selection for chained runs, and two small chain helpers (fixed-name frame save, chain-frame load
with fallback)."""
from __future__ import annotations

import hashlib, json, os, time

import folder_paths
import nodes as core_nodes

from . import store as S

NONE = "(none)"


def _preset_names() -> list:
    try:
        names = S.get_store().names()
    except Exception:
        names = []
    return names or [NONE]


def _lora_names() -> list:
    from . import lora_stack as LS   # one call-time scan shared by every node that lists LoRAs
    return LS.lora_names(refresh=True)


def _sig(preset: dict | None) -> str:
    if not preset:
        return "none"
    return hashlib.sha1(json.dumps({k: preset.get(k) for k in ("name", "prompt", "loras", "updated")}, sort_keys=True).encode()).hexdigest()


def apply_loras(model, clip, loras: list, scale: float, log=print):
    """Apply a preset's LoRAs in order with core LoraLoader (strength_model == strength_clip ==
    strength * scale). Unknown LoRA files raise with the exact name."""
    available = set(folder_paths.get_filename_list("loras"))
    loader = core_nodes.LoraLoader()
    for l in loras:
        if not S.lora_enabled(l):
            continue
        name = l["name"]
        if name not in available:
            raise ValueError(f"MMX preset LoRA not found in models/loras: {name}")
        s = float(l.get("strength", S.DEFAULT_STRENGTH)) * float(scale)
        if s == 0:
            continue
        model, clip = loader.load_lora(model, clip, name, s, s)
        log(f"[mmx-presets] applied {name} @ {s:.3f}")
    return model, clip


class MMXPreset:
    """Pick a preset: its LoRAs go onto model/clip, its prompt comes out as a STRING."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "model": ("MODEL",),
            "clip": ("CLIP",),
            "preset": (_preset_names(), {"tooltip": "Preset from /workspace/mmx/presets.json (mirrored from the NAS). Use the Refresh button after saving new ones."}),
            "strength_scale": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 3.0, "step": 0.05,
                                         "tooltip": "Multiplies every LoRA strength in the preset."}),
        }}

    RETURN_TYPES = ("MODEL", "CLIP", "STRING", "STRING")
    RETURN_NAMES = ("model", "clip", "prompt", "preset_name")
    FUNCTION = "run"
    CATEGORY = "mmx"
    DESCRIPTION = "Prompt + LoRA stack preset. Selecting a preset changes the prompt and the LoRAs together."

    @classmethod
    def IS_CHANGED(cls, model, clip, preset, strength_scale, **kw):
        return _sig(S.get_store().get(preset)) + f":{strength_scale}"

    @classmethod
    def VALIDATE_INPUTS(cls, preset, **kw):
        if preset == NONE:
            return "no preset selected (store empty? save one with MMX Preset Save or import the studio pool)"
        if S.get_store().get(preset) is None:
            return f"preset '{preset}' is not in the store (press Refresh)"
        return True

    def run(self, model, clip, preset, strength_scale):
        p = S.get_store().get(preset)
        if p is None:
            raise ValueError(f"MMX preset '{preset}' not found")
        model, clip = apply_loras(model, clip, p["loras"], strength_scale)
        return {"ui": {"text": [_describe(p, strength_scale)]}, "result": (model, clip, p["prompt"], p["name"])}


def _describe(p: dict, scale: float = 1.0) -> str:
    lines = [f"{p['name']}"]
    lines += [f"  {l['name']} @ {float(l.get('strength', S.DEFAULT_STRENGTH)) * scale:.2f}{'' if S.lora_enabled(l) else '  (off)'}" for l in p["loras"]] or ["  (no LoRAs)"]
    if p.get("notes"):
        lines.append("  " + p["notes"])
    return "\n".join(lines)


class MMXPresetSave:
    """Write a preset (name, prompt, up to 5 LoRAs) to the store and mirror it to the NAS."""

    @classmethod
    def INPUT_TYPES(cls):
        loras = _lora_names()
        req = {
            "name": ("STRING", {"default": "", "tooltip": "Preset name (unique; overwrite decides what happens when it exists)"}),
            "prompt": ("STRING", {"default": "", "multiline": True, "tooltip": "Prompt text; connect a STRING or type it"}),
            "overwrite": ("BOOLEAN", {"default": True}),
            "notes": ("STRING", {"default": ""}),
        }
        for i in range(1, S.MAX_LORAS + 1):
            req[f"lora_{i}"] = (loras, {"default": NONE})
            req[f"strength_{i}"] = ("FLOAT", {"default": S.DEFAULT_STRENGTH, "min": -2.0, "max": 3.0, "step": 0.05})
        return {"required": req}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("preset_name",)
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = "mmx"

    @classmethod
    def IS_CHANGED(cls, **kw):
        return float("nan")   # always re-run when queued

    def run(self, name, prompt, overwrite, notes, **kw):
        pairs = [(kw.get(f"lora_{i}"), kw.get(f"strength_{i}", S.DEFAULT_STRENGTH)) for i in range(1, S.MAX_LORAS + 1)]
        loras = [{"name": n, "strength": s} for n, s in pairs if n and n != NONE]
        st = S.get_store()
        p = st.save({"name": name, "prompt": prompt, "loras": loras, "notes": notes}, overwrite=overwrite)
        st.push_async()
        msg = f"saved preset '{p['name']}' ({len(loras)} LoRA{'s' if len(loras) != 1 else ''}) -> {st.path}; NAS mirror {'queued' if st.mirror_enabled else 'disabled'}"
        print("[mmx-presets] " + msg)
        return {"ui": {"text": [msg]}, "result": (p["name"],)}


class MMXSequence:
    """Up to 8 preset slots + an index: outputs the selected preset's prompt and applies its LoRAs.
    Empty slots are skipped; the index wraps over the filled slots, so an INT primitive with
    control_after_generate = increment queued N times walks the sequence."""

    SLOTS = 8

    @classmethod
    def INPUT_TYPES(cls):
        names = [NONE] + [n for n in _preset_names() if n != NONE]
        req = {"model": ("MODEL",), "clip": ("CLIP",),
               "index": ("INT", {"default": 0, "min": 0, "max": 100000, "control_after_generate": "increment",
                                 "tooltip": "Which filled slot to use (0-based, wraps). Set control_after_generate to increment and queue N runs."}),
               "strength_scale": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 3.0, "step": 0.05})}
        for i in range(1, cls.SLOTS + 1):
            req[f"preset_{i}"] = (names, {"default": NONE})
        return {"required": req}

    RETURN_TYPES = ("MODEL", "CLIP", "STRING", "STRING", "INT", "INT")
    RETURN_NAMES = ("model", "clip", "prompt", "preset_name", "slot", "count")
    FUNCTION = "run"
    CATEGORY = "mmx"

    @classmethod
    def _filled(cls, kw):
        return [kw.get(f"preset_{i}") for i in range(1, cls.SLOTS + 1) if kw.get(f"preset_{i}") and kw.get(f"preset_{i}") != NONE]

    @classmethod
    def IS_CHANGED(cls, model, clip, index, strength_scale, **kw):
        filled = cls._filled(kw)
        if not filled:
            return "empty"
        name = filled[int(index) % len(filled)]
        return _sig(S.get_store().get(name)) + f":{int(index) % len(filled)}:{strength_scale}"

    @classmethod
    def VALIDATE_INPUTS(cls, **kw):
        filled = cls._filled(kw)
        if not filled:
            return "MMX Sequence: no preset slot filled"
        st = S.get_store()
        missing = [n for n in filled if st.get(n) is None]
        if missing:
            return f"MMX Sequence: preset(s) not in the store: {', '.join(missing)} (press Refresh)"
        return True

    def run(self, model, clip, index, strength_scale, **kw):
        filled = self._filled(kw)
        if not filled:
            raise ValueError("MMX Sequence: no preset slot filled")
        slot = int(index) % len(filled)
        p = S.get_store().get(filled[slot])
        if p is None:
            raise ValueError(f"MMX preset '{filled[slot]}' not found")
        model, clip = apply_loras(model, clip, p["loras"], strength_scale)
        txt = f"index {index} -> slot {slot + 1}/{len(filled)}: " + _describe(p, strength_scale)
        return {"ui": {"text": [txt]}, "result": (model, clip, p["prompt"], p["name"], slot + 1, len(filled))}


# ── chain helpers ────────────────────────────────────────────────────────────

class MMXSaveFrame:
    """Save one image under a FIXED filename in ComfyUI's input directory (overwriting), so the
    next queued run can pick it up by name. Pair with MMX Load Chain Frame (or LoadImage)."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"image": ("IMAGE",),
                             "filename": ("STRING", {"default": "mmx_chain_last.png", "tooltip": "written into the ComfyUI input folder, overwritten every run"})}}

    RETURN_TYPES = ("STRING",)
    RETURN_NAMES = ("path",)
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = "mmx/chain"

    @classmethod
    def IS_CHANGED(cls, **kw):
        return float("nan")

    def run(self, image, filename):
        import numpy as np
        from PIL import Image
        name = os.path.basename(filename.strip()) or "mmx_chain_last.png"
        if not name.lower().endswith(".png"):
            name += ".png"
        path = os.path.join(folder_paths.get_input_directory(), name)
        arr = (image[-1].cpu().numpy() * 255.0).clip(0, 255).astype(np.uint8)
        tmp = path + ".tmp.png"
        Image.fromarray(arr).save(tmp, compress_level=1)
        os.replace(tmp, path)
        return {"ui": {"text": [f"saved {path}"]}, "result": (path,)}


class MMXLoadChainFrame:
    """Load the chain frame saved by MMX Save Frame; when it does not exist yet (first segment) or
    `use_fallback` is on, pass the fallback image through instead. Re-executes whenever the file
    changes (mtime + size in IS_CHANGED), which plain LoadImage does too but LoadImage refuses to
    queue when the file is absent."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {"fallback": ("IMAGE",),
                             "filename": ("STRING", {"default": "mmx_chain_last.png"}),
                             "use_fallback": ("BOOLEAN", {"default": False, "tooltip": "force the fallback (start a new chain)"})}}

    RETURN_TYPES = ("IMAGE", "BOOLEAN")
    RETURN_NAMES = ("image", "from_file")
    FUNCTION = "run"
    CATEGORY = "mmx/chain"

    @classmethod
    def _path(cls, filename):
        name = os.path.basename(filename.strip()) or "mmx_chain_last.png"
        if not name.lower().endswith(".png"):
            name += ".png"
        return os.path.join(folder_paths.get_input_directory(), name)

    @classmethod
    def IS_CHANGED(cls, fallback, filename, use_fallback):
        p = cls._path(filename)
        try:
            st = os.stat(p); return f"{st.st_mtime_ns}:{st.st_size}:{use_fallback}"
        except OSError:
            return f"absent:{use_fallback}"

    def run(self, fallback, filename, use_fallback):
        import numpy as np, torch
        from PIL import Image, ImageOps
        p = self._path(filename)
        if use_fallback or not os.path.isfile(p):
            return (fallback, False)
        img = ImageOps.exif_transpose(Image.open(p)).convert("RGB")
        arr = np.asarray(img).astype(np.float32) / 255.0
        return (torch.from_numpy(arr)[None, ...], True)


NODE_CLASS_MAPPINGS = {
    "MMXPreset": MMXPreset,
    "MMXPresetSave": MMXPresetSave,
    "MMXSequence": MMXSequence,
    "MMXSaveFrame": MMXSaveFrame,
    "MMXLoadChainFrame": MMXLoadChainFrame,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "MMXPreset": "MMX Preset",
    "MMXPresetSave": "MMX Preset Save",
    "MMXSequence": "MMX Sequence",
    "MMXSaveFrame": "MMX Save Frame (fixed name)",
    "MMXLoadChainFrame": "MMX Load Chain Frame",
}
