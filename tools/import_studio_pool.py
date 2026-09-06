#!/usr/bin/env python3
"""Import mmx_studio's prompt pool into the preset store.

Accepts either shape the studio produces:
  - a "Save project" export  ({"v": 2, "pool": [...], "lanes": [...], ...})
  - the raw localStorage value of `mmx.pool`  ([{"id","name","text","loras"}, ...])
    (DevTools console: copy(localStorage.getItem('mmx.pool')) → paste into a file)

    python3 tools/import_studio_pool.py pool.json [--overwrite] [--store /workspace/mmx/presets.json] [--push]
    python3 tools/import_studio_pool.py pool.json --server http://127.0.0.1:8188   # via the running ComfyUI
"""
import argparse, json, os, sys, urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file"); ap.add_argument("--overwrite", action="store_true"); ap.add_argument("--store")
    ap.add_argument("--push", action="store_true", help="mirror to the NAS after importing (local mode only)")
    ap.add_argument("--server", help="post to a running ComfyUI's /mmx/presets/import instead of writing the file")
    a = ap.parse_args()
    body = json.load(open(a.file, encoding="utf-8"))
    if a.server:
        req = urllib.request.Request(a.server.rstrip("/") + "/mmx/presets/import" + ("?overwrite=1" if a.overwrite else ""),
                                     data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        res = json.load(urllib.request.urlopen(req, timeout=60))
        print(f"imported {res.get('imported')} preset(s); store now has {len(res.get('names', []))}: {', '.join(res.get('names', []))}")
        return
    if a.store:
        os.environ["MMX_PRESETS"] = a.store
    from mmx_presets import store as S
    st = S.PresetStore(mirror=a.push)
    n = st.import_pool(body, overwrite=a.overwrite)
    print(f"imported {n} preset(s) into {st.path}; store now has {len(st.names())}: {', '.join(st.names())}")
    if a.push and n:
        print("NAS push:", st.push())


if __name__ == "__main__":
    main()
