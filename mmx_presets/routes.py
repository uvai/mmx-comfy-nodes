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
  GET  /mmx/library                 -> {root, items:[{path, name, folder, kind, size, mtime}], count, sync}
  POST /mmx/library/refresh[?sync=1]-> re-scan the mirror (sync=1: start the NAS mirror script first)
  GET  /mmx/library/thumb?path=<rel>[&w=320] -> image/jpeg (first frame for videos)
  GET  /mmx/library/sync            -> mirror script status + log tail
"""
from __future__ import annotations

import json

from . import store as S
from . import library as L


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

    def _library_payload():
        items = L.scan()
        return {"root": L.root(), "items": items, "count": len(items), "paths": [it["path"] for it in items], "sync": L.sync_status()}

    @routes.get("/mmx/library")
    async def library_get(request):
        return web.json_response(_library_payload())

    @routes.post("/mmx/library/refresh")
    async def library_refresh(request):
        out = {}
        if request.query.get("sync") == "1":
            out["sync_started"] = L.start_sync()
        L.scan(force=True)
        out.update(_library_payload())
        return web.json_response(out)

    @routes.get("/mmx/library/sync")
    async def library_sync(request):
        return web.json_response(L.sync_status())

    @routes.get("/mmx/library/thumb")
    async def library_thumb(request):
        try:
            w = max(64, min(1024, int(request.query.get("w", "320"))))
            data = L.thumb_jpeg(request.query.get("path", ""), w)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=404)
        return web.Response(body=data, content_type="image/jpeg", headers={"Cache-Control": "max-age=60"})

    return True
