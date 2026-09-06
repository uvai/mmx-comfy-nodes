"""HTTP routes on ComfyUI's own server (PromptServer), used by the web extension's Refresh
button and by tooling:

  GET  /mmx/presets                 -> {presets:[...], names:[...], path, nas}
  POST /mmx/presets/refresh         -> pull from the NAS, re-read the file; same shape as GET
  POST /mmx/presets/import          -> body: studio project {pool:[...]} or a bare pool list;
                                       ?overwrite=1 replaces same-named presets
  POST /mmx/presets/save            -> body: one preset {name, prompt, loras, notes} (+ NAS push)
  POST /mmx/presets/delete          -> body: {name}
  GET  /mmx/presets/status          -> store path + mirror status
  GET  /mmx/loras                   -> current models/loras listing (for the extension)
"""
from __future__ import annotations

import json

from . import store as S


def _payload(st: S.PresetStore) -> dict:
    data = st.load()
    return {"presets": data["presets"], "names": [p["name"] for p in data["presets"]], "updated": data["updated"],
            "path": st.path, "nas": st.nas_status()}


def register(server_instance) -> bool:
    try:
        from aiohttp import web
    except Exception:
        return False
    routes = server_instance.routes
    st = S.get_store()

    @routes.get("/mmx/presets")
    async def presets_get(request):
        return web.json_response(_payload(st))

    @routes.post("/mmx/presets/refresh")
    async def presets_refresh(request):
        pulled = st.pull()
        st.load(force=True)
        out = _payload(st); out["pull"] = pulled
        return web.json_response(out)

    @routes.post("/mmx/presets/import")
    async def presets_import(request):
        try:
            body = await request.json()
            n = st.import_pool(body, overwrite=request.query.get("overwrite") == "1")
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
        if n:
            st.push_async()
        out = _payload(st); out["imported"] = n
        return web.json_response(out)

    @routes.post("/mmx/presets/save")
    async def presets_save(request):
        try:
            body = await request.json()
            p = st.save(body, overwrite=bool(body.get("overwrite", True)))
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
        st.push_async()
        out = _payload(st); out["saved"] = p
        return web.json_response(out)

    @routes.post("/mmx/presets/delete")
    async def presets_delete(request):
        try:
            body = await request.json()
            ok = st.delete(str(body.get("name", "")))
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
        if ok:
            st.push_async()
        out = _payload(st); out["deleted"] = ok
        return web.json_response(out)

    @routes.get("/mmx/presets/status")
    async def presets_status(request):
        return web.json_response({"path": st.path, "count": len(st.load()["presets"]), "nas": st.nas_status()})

    @routes.get("/mmx/loras")
    async def loras_get(request):
        try:
            import folder_paths
            names = list(folder_paths.get_filename_list("loras"))
        except Exception:
            names = []
        return web.json_response({"loras": names})

    return True
