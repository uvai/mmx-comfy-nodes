"""HTTP routes on ComfyUI's own server (PromptServer), used by the web extension's Refresh
button and by tooling:

  GET  /mmx/presets                 -> {presets:[...], names:[...], path, nas}
  POST /mmx/presets/refresh         -> pull from the NAS, re-read the file; same shape as GET
  POST /mmx/presets/import          -> body: studio project {pool:[...]} or a bare pool list;
                                       ?overwrite=1 replaces same-named presets
  POST /mmx/presets/save            -> body: one preset {name, prompt, loras, notes} (+ NAS push)
  POST /mmx/presets/delete          -> body: {name}
  GET  /mmx/presets/status          -> store path + mirror status
  GET  /mmx/loras[?refresh=1]       -> current models/loras listing (refresh=1: drop the folder cache first)
  GET  /mmx/phrases                 -> {groups:[{name, phrases:[{text, updated}]}], path, nas}
  POST /mmx/phrases/refresh         -> pull from the NAS, re-read; same shape as GET
  POST /mmx/phrases/add             -> body {group, text}         (+ NAS push)
  POST /mmx/phrases/delete          -> body {group, text}         (+ NAS push)
  POST /mmx/phrases/replace         -> body {groups:[...]}: bulk edit from the Deck (+ NAS push)
  POST /mmx/library/inject          -> body {path, slot}: copy the library file into ComfyUI/input
                                       -> {filename, kind, frame_png} for the References Manager
  GET  /mmx/status                  -> {openrouter_key: bool, refpack: bool, manager: bool, presets, phrases}
  GET  /mmx/registry                -> {loras:{file:{triggers, phrases, default_strength, notes, auto}}, path, nas}
  POST /mmx/registry/refresh        -> pull from the NAS, pre-fill new files from their metadata; same shape
  POST /mmx/registry/set            -> body {name, triggers, phrases, default_strength, notes} (+ NAS push)
  POST /mmx/registry/delete         -> body {name}                                            (+ NAS push)
  GET  /mmx/registry/metadata?name= -> {metadata: {...}, triggers: [...], source} straight from the file
  POST /mmx/registry/for_rows       -> body {rows:[{name, on}]} -> {triggers, phrases, per_row}
  GET  /mmx/library                 -> {root, items:[{path, name, folder, kind, size, mtime}], count, sync}
  POST /mmx/library/refresh[?sync=1]-> re-scan the mirror (sync=1: start the NAS mirror script first)
  GET  /mmx/library/thumb?path=<rel>[&w=320] -> image/jpeg (first frame for videos)
  GET  /mmx/library/sync            -> mirror script status + log tail
"""
from __future__ import annotations

import json

from . import store as S
from . import library as L
from . import phrases as P
from . import registry as REG


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
            from . import lora_stack as LS
            names = LS.lora_names(refresh=request.query.get("refresh") == "1")[1:]
        except Exception:
            names = []
        return web.json_response({"loras": names})

    # ── phrases ──
    ph = P.get_store()

    def _phrases_payload():
        return {"groups": ph.load()["groups"], "updated": ph.data["updated"], "path": ph.path, "nas": ph.nas_status()}

    @routes.get("/mmx/phrases")
    async def phrases_get(request):
        return web.json_response(_phrases_payload())

    @routes.post("/mmx/phrases/refresh")
    async def phrases_refresh(request):
        pulled = ph.pull()
        ph.load(force=True)
        out = _phrases_payload(); out["pull"] = pulled
        return web.json_response(out)

    @routes.post("/mmx/phrases/add")
    async def phrases_add(request):
        try:
            body = await request.json()
            added = ph.add(str(body.get("group", "")), str(body.get("text", "")))
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
        ph.push_async()
        out = _phrases_payload(); out["added"] = added
        return web.json_response(out)

    @routes.post("/mmx/phrases/delete")
    async def phrases_delete(request):
        try:
            body = await request.json()
            ok = ph.remove(str(body.get("group", "")), str(body.get("text", "")))
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
        if ok:
            ph.push_async()
        out = _phrases_payload(); out["deleted"] = ok
        return web.json_response(out)

    @routes.post("/mmx/phrases/replace")
    async def phrases_replace(request):
        try:
            body = await request.json()
            ph.replace_all(body.get("groups") or [])
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
        ph.push_async()
        return web.json_response(_phrases_payload())

    # ── LoRA registry ──
    reg = REG.get_store()

    def _registry_payload():
        return {"loras": reg.all(), "updated": reg.data["updated"], "path": reg.path, "nas": reg.nas_status()}

    def _lora_files():
        try:
            from . import lora_stack as LS
            return LS.lora_names(refresh=True)[1:]
        except Exception:
            return []

    @routes.get("/mmx/registry")
    async def registry_get(request):
        return web.json_response(_registry_payload())

    @routes.post("/mmx/registry/refresh")
    async def registry_refresh(request):
        pulled = reg.pull()
        added = reg.prefill(_lora_files())
        if added:
            reg.push_async()
        out = _registry_payload(); out["pull"] = pulled; out["prefilled"] = added
        return web.json_response(out)

    @routes.post("/mmx/registry/set")
    async def registry_set(request):
        try:
            body = await request.json()
            e = reg.set(str(body.get("name", "")), body)
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
        reg.push_async()
        out = _registry_payload(); out["saved"] = e
        return web.json_response(out)

    @routes.post("/mmx/registry/delete")
    async def registry_delete(request):
        try:
            body = await request.json()
            ok = reg.delete(str(body.get("name", "")))
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
        if ok:
            reg.push_async()
        out = _registry_payload(); out["deleted"] = ok
        return web.json_response(out)

    @routes.get("/mmx/registry/metadata")
    async def registry_metadata(request):
        name = request.query.get("name", "")
        path = reg.lora_path(name)
        meta = REG.read_metadata(path) if path else {}
        trig, src = REG.triggers_from_metadata(meta)
        keep = {k: (v if len(str(v)) < 4000 else str(v)[:4000] + "…") for k, v in meta.items()}
        return web.json_response({"name": name, "found": bool(path), "metadata": keep, "triggers": trig, "source": src})

    @routes.post("/mmx/registry/for_rows")
    async def registry_for_rows(request):
        try:
            body = await request.json()
            rows = [r for r in (body.get("rows") or []) if r.get("name") and r.get("name") != "(none)" and r.get("on", True) is not False]
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response(reg.for_rows(rows))

    @routes.post("/mmx/library/inject")
    async def library_inject(request):
        try:
            import folder_paths
            body = await request.json()
            out = L.inject_file(str(body.get("path", "")), folder_paths.get_input_directory(), str(body.get("slot", "")))
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)
        return web.json_response(out)

    @routes.get("/mmx/status")
    async def status_get(request):
        from . import manager as M
        return web.json_response({"openrouter_key": bool(M.resolve_key("")), "refpack": M._BASE is not None,
                                  "manager": "MMXReferencesManager" in M.NODE_CLASS_MAPPINGS, "refpack_reason": M._REASON,
                                  "presets": len(st.load()["presets"]), "phrases": sum(len(g["phrases"]) for g in ph.load()["groups"]), "registry": len(reg.all()),
                                  "presets_path": st.path, "phrases_path": ph.path})

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
