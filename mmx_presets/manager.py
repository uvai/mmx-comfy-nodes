"""MMX References Manager: a drop-in for ComfyUI-MiniMaxRefPack's MiniMaxH3ReferencePack
(identical inputs and 20 outputs, same web widget — web/mmx_manager.js re-uses the RefPack's
own extension for this class) with two additions:

  * the widget re-renders when references_json / direction are set from outside (the Deck's
    Send, a Library node's Inject, window.mmx.setReferences / setDirection);
  * a blank openrouter_api_key falls back to OPENROUTER_API_KEY, then LLM_KEY, then
    OPENROUTER_KEY (the Vast template's name), from this process's env or PID 1's;
  * an optional IMAGE input `first_frame`: when connected, the run writes the image into
    ComfyUI/input (mmx_ff_<…>.png) and uses it as the LAST picture slot (Picture N, N = the
    image count with it), keeping every other slot; not connected = exactly the stock node.

Registered only when the RefPack is importable (it normally loads first: custom_nodes are
loaded in directory order and "ComfyUI-…" sorts before "mmx-…"); otherwise the pack logs it
and registers everything else.
"""
from __future__ import annotations

import hashlib, importlib.util, json, os, sys

from . import store as S

FIRST_FRAME_PREFIX = "mmx_ff_"
IMAGE_CAP = 9


def is_first_frame_file(name) -> bool:
    return isinstance(name, str) and name.startswith(FIRST_FRAME_PREFIX) and name.lower().endswith(".png")


def frame_bytes(image):
    """(uint8 HxWx3 array, sha1[:12]) of the first frame of a [B,H,W,3] float tensor."""
    import numpy as np
    t = image[0] if getattr(image, "ndim", 3) == 4 else image
    a = (t.detach().cpu().numpy() * 255.0).clip(0, 255).astype("uint8")
    return a, hashlib.sha1(a.tobytes()).hexdigest()[:12]


def write_first_frame(image, input_dir: str, name: str | None = None) -> tuple[str, tuple[int, int]]:
    """Write the frame into input_dir as `name` (the frontend's per-queue mmx_ff_<node>_<queue>.png)
    or as mmx_ff_<content hash>.png; returns (filename, (w, h))."""
    from PIL import Image
    a, h = frame_bytes(image)
    name = name if is_first_frame_file(name) else f"{FIRST_FRAME_PREFIX}{h}.png"
    os.makedirs(input_dir, exist_ok=True)
    dst = os.path.join(input_dir, name)
    tmp = dst + ".part.png"
    Image.fromarray(a).save(tmp, "PNG")
    os.replace(tmp, dst)
    return name, (int(a.shape[1]), int(a.shape[0]))


def parse_references(references_json: str) -> list:
    try:
        d = json.loads(references_json or "{}")
    except Exception:
        return []
    refs = d.get("references") if isinstance(d, dict) else d
    return [r for r in (refs or []) if isinstance(r, dict) and r.get("file")]


def has_chain_slot(references_json: str) -> str | None:
    """The static chain-slot picture (mmx_chain_slot_*.png, written by Load Chain Frame's
    'Inject into Manager as slot') when the list holds one: then the run-time first_frame
    override is skipped, so the two paths can never both apply."""
    from . import chain_history as CH
    for r in parse_references(references_json):
        if r.get("kind") == "image" and CH.is_slot_file(r.get("file")):
            return r["file"]
    return None


def find_marker(references_json: str) -> str | None:
    """The mmx_ff_* entry the frontend put into the exported list (its filename is what we write)."""
    for r in parse_references(references_json):
        if r.get("kind") == "image" and is_first_frame_file(r.get("file")):
            return r["file"]
    return None


def apply_first_frame(references_json: str, name: str) -> tuple[str, int, str | None]:
    """references_json with `name` as the LAST picture: a stale mmx_ff_* entry is dropped, the
    other slots are kept, the 9th picture is replaced when the list is already full.
    Returns (json, picture slot, replaced file or None)."""
    refs = [r for r in parse_references(references_json) if not (r.get("kind") == "image" and is_first_frame_file(r.get("file")))]
    images = [r for r in refs if r.get("kind") == "image"]
    replaced = None
    if len(images) >= IMAGE_CAP:
        replaced = images[-1]
        refs.remove(replaced)
    last = max([i for i, r in enumerate(refs) if r.get("kind") == "image"], default=-1)
    refs.insert(last + 1, {"kind": "image", "file": name})
    slot = sum(1 for r in refs if r.get("kind") == "image")
    return json.dumps({"references": refs}), slot, (replaced or {}).get("file")

_BASE = None
_REASON = ""


def _find_refpack_class():
    """The loaded RefPack node class, or None (reason in _REASON)."""
    global _REASON
    for name, mod in list(sys.modules.items()):
        if name.endswith("minimax_refpack.nodes") and hasattr(mod, "MiniMaxH3ReferencePack"):
            return mod.MiniMaxH3ReferencePack
    # not loaded yet (or loaded under another name): import the sibling package directly
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cands = [os.path.join(os.path.dirname(here), "ComfyUI-MiniMaxRefPack")]
    cands += [p for p in os.environ.get("MMX_REFPACK_DIR", "").split(os.pathsep) if p]
    for d in cands:
        pkg = os.path.join(d, "minimax_refpack", "__init__.py")
        if not os.path.isfile(pkg):
            continue
        try:
            if d not in sys.path:
                sys.path.insert(0, d)
            mod = importlib.import_module("minimax_refpack.nodes")
            return mod.MiniMaxH3ReferencePack
        except Exception as e:
            _REASON = f"{d}: {e}"
    _REASON = _REASON or "ComfyUI-MiniMaxRefPack not found next to this pack"
    return None


def resolve_key(explicit: str = "") -> str:
    if explicit and explicit.strip():
        return explicit.strip()
    for name in ("OPENROUTER_API_KEY", "LLM_KEY", "OPENROUTER_KEY"):
        v = S._pid1_env(name)
        if v:
            return v
    return ""


NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}

_BASE = _find_refpack_class()
if _BASE is not None:
    _DEBUG_SLOT = list(_BASE.RETURN_NAMES).index("debug")

    class MMXReferencesManager(_BASE):
        CATEGORY = "mmx"
        DESCRIPTION = ("MiniMax References Manager with an externally writable widget (Deck Send / Library Inject / window.mmx), "
                       "OPENROUTER_KEY as a key fallback and an optional first_frame IMAGE input (written into input/ at run time "
                       "and used as the last picture). Same inputs and outputs as MiniMaxH3ReferencePack otherwise.")

        @classmethod
        def INPUT_TYPES(cls):
            t = dict(super().INPUT_TYPES())
            t["optional"] = dict(t.get("optional") or {})
            # appended LAST: the RefPack restores its widget values positionally, an IMAGE socket adds no widget
            t["optional"]["first_frame"] = ("IMAGE", {"tooltip": "Optional. When connected, this image is written into ComfyUI/input "
                                                      "(mmx_ff_<…>.png) at run time and used as the LAST picture slot (Picture N) on top "
                                                      "of the pictures in the widget; every other slot is kept. Wire MMX Load Chain Frame here."})
            return t

        @classmethod
        def IS_CHANGED(cls, *args, first_frame=None, **kw):
            key = super().IS_CHANGED(*args, **kw)
            if first_frame is not None:
                key = f"{key}|first_frame:{frame_bytes(first_frame)[1]}"
            return key

        def build(self, *args, first_frame=None, **kw):
            provider = str(kw.get("prompt_provider") or "openrouter")
            if provider == "openrouter":
                kw["openrouter_api_key"] = resolve_key(kw.get("openrouter_api_key") or "")
            note = ""
            slot = has_chain_slot(kw.get("references_json") or "")
            if first_frame is not None and slot:
                images = [r for r in parse_references(kw.get("references_json") or "") if r.get("kind") == "image"]
                n = next((i + 1 for i, r in enumerate(images) if r.get("file") == slot), len(images))
                note = f"first_frame input IGNORED: the static chain slot {slot} is <Picture {n}> (delete that tile in the Manager to re-enable the run-time override)"
            elif first_frame is not None:
                import folder_paths
                rj = kw.get("references_json") or ""
                name, (w, h) = write_first_frame(first_frame, folder_paths.get_input_directory(), find_marker(rj))
                kw["references_json"], slot, replaced = apply_first_frame(rj, name)
                note = (f"first_frame: {name} ({w}x{h}) written into input/ and used as <Picture {slot}> (the last picture)"
                        + (f", replacing {replaced} (list was full)" if replaced else "") + f"\nreferences_json (effective): {kw['references_json']}")
            out = list(super().build(*args, **kw))
            if note:
                out[_DEBUG_SLOT] = (out[_DEBUG_SLOT] or "") + "\n" + note
            return tuple(out)

    NODE_CLASS_MAPPINGS["MMXReferencesManager"] = MMXReferencesManager
    NODE_DISPLAY_NAME_MAPPINGS["MMXReferencesManager"] = "MMX References Manager"
