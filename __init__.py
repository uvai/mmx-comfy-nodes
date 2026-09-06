"""mmx-comfy-nodes — MMX preset / sequence / chain nodes for ComfyUI.

Preset store: /workspace/mmx/presets.json (MMX_PRESETS overrides), mirrored from and to the NAS
share (mmx/presets.json) over the instance's existing NAS ssh path. On load: pull the NAS copy
and merge it (survives a re-rent), then register the HTTP routes the web extension uses.
"""
import threading

from .mmx_presets import store as _store
from .mmx_presets.nodes import NODE_CLASS_MAPPINGS, NODE_DISPLAY_NAME_MAPPINGS

WEB_DIRECTORY = "./web"
__all__ = ["NODE_CLASS_MAPPINGS", "NODE_DISPLAY_NAME_MAPPINGS", "WEB_DIRECTORY"]

_st = _store.get_store()
_st.load()
print(f"[mmx-presets] store {_st.path}: {len(_st.data['presets'])} preset(s) local; NAS mirror "
      f"{'on (' + _st.cfg['userhost'] + ')' if _st.mirror_enabled and _st.nas_status()['configured'] else 'off'}")


def _initial_pull():
    try:
        res = _st.pull()
        print(f"[mmx-presets] initial NAS pull: {res}")
    except Exception as e:
        print(f"[mmx-presets] initial NAS pull failed: {e}")


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
