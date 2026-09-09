"""MMX References Manager: a drop-in for ComfyUI-MiniMaxRefPack's MiniMaxH3ReferencePack
(identical inputs and 20 outputs, same web widget — web/mmx_manager.js re-uses the RefPack's
own extension for this class) with two additions:

  * the widget re-renders when references_json / direction are set from outside (the Deck's
    Send, a Library node's Inject, window.mmx.setReferences / setDirection);
  * a blank openrouter_api_key falls back to OPENROUTER_API_KEY, then LLM_KEY, then
    OPENROUTER_KEY (the Vast template's name), from this process's env or PID 1's.

Registered only when the RefPack is importable (it normally loads first: custom_nodes are
loaded in directory order and "ComfyUI-…" sorts before "mmx-…"); otherwise the pack logs it
and registers everything else.
"""
from __future__ import annotations

import importlib.util, os, sys

from . import store as S

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
    class MMXReferencesManager(_BASE):
        CATEGORY = "mmx"
        DESCRIPTION = ("MiniMax References Manager with an externally writable widget (Deck Send / Library Inject / window.mmx) "
                       "and OPENROUTER_KEY as a key fallback. Same inputs and outputs as MiniMaxH3ReferencePack.")

        def build(self, *args, **kw):
            provider = str(kw.get("prompt_provider") or "openrouter")
            if provider == "openrouter":
                kw["openrouter_api_key"] = resolve_key(kw.get("openrouter_api_key") or "")
            return super().build(*args, **kw)

    NODE_CLASS_MAPPINGS["MMXReferencesManager"] = MMXReferencesManager
    NODE_DISPLAY_NAME_MAPPINGS["MMXReferencesManager"] = "MMX References Manager"
