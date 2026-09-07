"""MMX References Builder: up to 9 image + 3 video + 1 audio filenames (ComfyUI/input names,
typed or wired from MMX Library Image) -> `references_json` in the MiniMaxH3ReferencePack schema

    {"references": [{"kind": "image", "file": "a.png"},
                    {"kind": "video", "file": "b.mp4", "use_soundtrack": true},
                    {"kind": "audio", "file": "c.wav"}]}

compacted in slot order (empty slots dropped), plus a `picture_map` that says which MiniMax tag
each filled slot ends up with. The tag rule is the RefPack's (refs.assign_tags, read out of
comfy_extras/nodes_minimax_h3.py): <Picture n> over the compacted images, then per video its
soundtrack's <Audio a> before its <Video n>, then standalone audio continues the <Audio a> count.
"""
from __future__ import annotations

import json, os

MAX_IMAGES, MAX_VIDEOS, MAX_AUDIOS = 9, 3, 1


def build(images: list, videos: list, audios: list, use_soundtrack: bool = True) -> tuple:
    """(references_json, picture_map, refs_list). Slot lists may hold '' for empty slots."""
    refs, lines = [], []
    n = 0
    for slot, f in enumerate(images, start=1):
        f = (f or "").strip()
        if not f:
            continue
        n += 1
        refs.append({"kind": "image", "file": f})
        lines.append(f"slot {slot} -> <Picture {n}>  {f}")
    audio_n = 0
    for slot, f in enumerate(videos, start=1):
        f = (f or "").strip()
        if not f:
            continue
        vn = sum(1 for r in refs if r["kind"] == "video") + 1
        refs.append({"kind": "video", "file": f, "use_soundtrack": bool(use_soundtrack)})
        tag = f"<Video {vn}>"
        if use_soundtrack:
            audio_n += 1
            tag += f" (+ soundtrack <Audio {audio_n}>)"
        lines.append(f"video {slot} -> {tag}  {f}")
    for slot, f in enumerate(audios, start=1):
        f = (f or "").strip()
        if not f:
            continue
        audio_n += 1
        refs.append({"kind": "audio", "file": f})
        lines.append(f"audio {slot} -> <Audio {audio_n}>  {f}")
    return json.dumps({"references": refs}), "\n".join(lines), refs


class MMXReferencesBuilder:
    """Filenames in ComfyUI/input -> references_json for the References Manager + a slot -> tag map."""

    @classmethod
    def INPUT_TYPES(cls):
        opt = {}
        for i in range(1, MAX_IMAGES + 1):
            opt[f"image_{i}"] = ("STRING", {"default": "", "tooltip": f"image slot {i}: a filename in ComfyUI/input (wire MMX Library Image's filename)"})
        for i in range(1, MAX_VIDEOS + 1):
            opt[f"video_{i}"] = ("STRING", {"default": "", "tooltip": f"video slot {i}: a filename in ComfyUI/input"})
        opt["audio_1"] = ("STRING", {"default": "", "tooltip": "audio slot 1: a filename in ComfyUI/input"})
        opt["use_soundtrack"] = ("BOOLEAN", {"default": True, "tooltip": "videos: also emit their soundtrack on video_audio_N (the References Manager's default)"})
        return {"required": {}, "optional": opt}

    RETURN_TYPES = ("STRING", "STRING")
    RETURN_NAMES = ("references_json", "picture_map")
    FUNCTION = "run"
    CATEGORY = "mmx/library"
    DESCRIPTION = "Builds the References Manager's references_json from filenames; picture_map tells which <Picture n> each slot became."

    def run(self, use_soundtrack=True, **kw):
        images = [kw.get(f"image_{i}", "") for i in range(1, MAX_IMAGES + 1)]
        videos = [kw.get(f"video_{i}", "") for i in range(1, MAX_VIDEOS + 1)]
        audios = [kw.get("audio_1", "")]
        rj, pmap, refs = build(images, videos, audios, use_soundtrack)
        missing = []
        try:
            import folder_paths
            d = folder_paths.get_input_directory()
            missing = [r["file"] for r in refs if not os.path.isfile(os.path.join(d, r["file"]))]
        except Exception:
            pass
        text = pmap or "(no references)"
        if missing:
            text += "\nNOT in ComfyUI/input yet: " + ", ".join(missing)
        return {"ui": {"text": [text]}, "result": (rj, pmap)}


NODE_CLASS_MAPPINGS = {"MMXReferencesBuilder": MMXReferencesBuilder}
NODE_DISPLAY_NAME_MAPPINGS = {"MMXReferencesBuilder": "MMX References Builder"}
