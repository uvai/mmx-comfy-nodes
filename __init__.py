"""mmx-comfy-nodes — MMX preset / sequence / chain / library / deck nodes for ComfyUI.

Preset store: /workspace/mmx/presets.json (MMX_PRESETS overrides), mirrored from and to the NAS
share (mmx/presets.json) over the instance's existing NAS ssh path. On load: pull the NAS copy
and merge it (survives a re-rent), then register the HTTP routes the web extension uses.
"""
import os, threading

from .mmx_presets import store as _store
from .mmx_presets.nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS
from .mmx_presets import check as _check, library as _library, references as _references
from .mmx_presets import lora_stack as _lora_stack, deck as _deck, manager as _manager, phrases as _phrases
from .mmx_presets import affix as _affix, registry as _registry

for _m in (_check, _library, _references, _lora_stack, _deck, _manager, _affix):
    NODE_CLASS_MAPPINGS.update(_m.NODE_CLASS_MAPPINGS)
    NODE_DISPLAY_NAME_MAPPINGS.update(_m.NODE_DISPLAY_NAME_MAPPINGS)

WEB_DIRECTORY = "./web"
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

# The RefPack node reads OPENROUTER_API_KEY / LLM_KEY from ComfyUI's environment; the Vast
# template carries the key as OPENROUTER_KEY (and a ComfyUI restarted from an ssh session has
# none of the template env). Bridge it once, inside this process, from the env or PID 1's env.
if not os.environ.get("OPENROUTER_API_KEY"):
    _k = _store._pid1_env("OPENROUTER_API_KEY") or _store._pid1_env("OPENROUTER_KEY") or _store._pid1_env("LLM_KEY")
    if _k:
        os.environ["OPENROUTER_API_KEY"] = _k
        print("[mmx-presets] OPENROUTER_API_KEY set for this ComfyUI process from the template env")

_st = _store.get_store()
_st.load()
_ph = _phrases.get_store()
_ph.load()
_reg = _registry.get_store()
_reg.load()
if _manager._BASE is None:
    print(f"[mmx-presets] MMX References Manager not registered: {_manager._REASON}")
print(f"[mmx-presets] library root {_library.root()} ({len(_library.scan())} file(s))")
print(f"[mmx-presets] store {_st.path}: {len(_st.data['presets'])} preset(s) local; phrases {_ph.path}: "
      f"{sum(len(g['phrases']) for g in _ph.data['groups'])} in {len(_ph.data['groups'])} group(s); NAS mirror "
      f"{'on (' + _st.cfg['userhost'] + ')' if _st.mirror_enabled and _st.nas_status()['configured'] else 'off'}")


def _initial_pull():
    for name, store in (("presets", _st), ("phrases", _ph), ("loras registry", _reg)):
        try:
            print(f"[mmx-presets] initial NAS pull ({name}): {store.pull()}")
        except Exception as e:
            print(f"[mmx-presets] initial NAS pull ({name}) failed: {e}")
    # after the pull, so an entry edited on another box is never shadowed by a fresh pre-fill
    try:
        from .mmx_presets import lora_stack as _ls
        n = _reg.prefill(_ls.lora_names(refresh=True)[1:])
        print(f"[mmx-presets] LoRA registry: {len(_reg.all())} entr{'y' if len(_reg.all()) == 1 else 'ies'}, {n} pre-filled from safetensors metadata")
        if n:
            _reg.push_async()
    except Exception as e:
        print(f"[mmx-presets] LoRA registry pre-fill failed: {e}")


# ComfyUI imports custom nodes before the server listens; the pull can take seconds, so it
# runs in the background and the Refresh button re-pulls on demand
threading.Thread(target=_initial_pull, daemon=True).start()

try:
    from server import PromptServer
    from .mmx_presets import routes as _routes
    if _routes.register(PromptServer.instance):
        print("[mmx-presets] routes registered under /mmx/")
except Exception as e:  # e.g. imported outside ComfyUI (tests, runner)
    print(f"[mmx-presets] routes not registered: {e}")
