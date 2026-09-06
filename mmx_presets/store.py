"""Preset store for the MMX nodes and the mmx runner.

One JSON file, `/workspace/mmx/presets.json` by default (env MMX_PRESETS overrides; when
/workspace does not exist the pack's own directory is used), mirrored from and to the NAS share
(`/volume1/subgenula/mmx/presets.json`) over the same ssh path the instance's NAS sync uses, so
presets survive a re-rent.

File schema:
    {"version": 1, "updated": <unix float>,
     "presets": [{"name", "prompt", "loras": [{"name", "strength"}], "notes", "created", "updated"}]}

Merge rule between the local file and the NAS copy: union by name, the entry with the newer
`updated` wins. Pure Python, no ComfyUI imports, so it is unit-testable and importable by the
runner.
"""
from __future__ import annotations

import json, os, subprocess, tempfile, threading, time

VERSION = 1
MAX_LORAS = 3
DEFAULT_STRENGTH = 0.85
NAS_REL = "mmx/presets.json"


def default_path() -> str:
    p = os.environ.get("MMX_PRESETS")
    if p:
        return p
    if os.path.isdir("/workspace"):
        return "/workspace/mmx/presets.json"
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "presets.json")


def _pid1_env(name: str) -> str:
    v = os.environ.get(name, "")
    if v:
        return v
    try:
        for kv in open("/proc/1/environ", "rb").read().split(b"\0"):
            if kv.startswith(name.encode() + b"="):
                return kv.split(b"=", 1)[1].decode(errors="replace")
    except Exception:
        pass
    return ""


def nas_config() -> dict:
    userhost = os.environ.get("MMX_NAS") or ""
    if not userhost:
        dest = _pid1_env("NAS_DEST")
        if "@" in dest:
            userhost = dest.split(":")[0]
    userhost = userhost or "alchera@100.81.253.103"
    key = os.environ.get("MMX_NAS_KEY") or "/root/.ssh/mmx_nas_key"
    proxy = os.environ.get("MMX_NAS_PROXY")
    if proxy is None:
        proxy = "nc -X 5 -x 127.0.0.1:1055 %h %p" if os.path.exists("/root/.ssh/mmx_nas_key") else "none"
    return {"userhost": userhost, "key": key, "proxy": proxy,
            "share": os.environ.get("MMX_NAS_SHARE", "/volume1/subgenula"),
            "enabled": os.environ.get("MMX_PRESETS_MIRROR", "1") != "0"}


def _ssh_cmd(cfg: dict) -> list:
    cmd = ["ssh", "-i", cfg["key"], "-o", "IdentitiesOnly=yes", "-o", "BatchMode=yes", "-o", "ConnectTimeout=12",
           "-o", "StrictHostKeyChecking=accept-new"]
    if cfg["proxy"] and cfg["proxy"] != "none":
        cmd += ["-o", f"ProxyCommand={cfg['proxy']}"]
    return cmd


def _q(s: str) -> str:
    return "'" + s.replace("'", "'\\''") + "'"


# ── schema ───────────────────────────────────────────────────────────────────

class PresetError(ValueError):
    pass


def normalize_lora(l) -> dict:
    if isinstance(l, str):
        l = {"name": l}
    name = str(l.get("name") or l.get("lora") or "").strip()
    if not name:
        raise PresetError("lora entry has no name")
    try:
        strength = float(l.get("strength", DEFAULT_STRENGTH))
    except (TypeError, ValueError):
        raise PresetError(f"lora {name}: strength must be a number")
    return {"name": name, "strength": strength}


def normalize_preset(p: dict, now: float | None = None) -> dict:
    now = now or time.time()
    name = str(p.get("name") or "").strip()
    if not name:
        raise PresetError("preset has no name")
    loras = [normalize_lora(l) for l in (p.get("loras") or []) if l]
    if len(loras) > MAX_LORAS:
        raise PresetError(f"preset {name}: at most {MAX_LORAS} LoRAs")
    return {"name": name, "prompt": str(p.get("prompt") or p.get("text") or ""), "loras": loras,
            "notes": str(p.get("notes") or ""), "created": float(p.get("created") or now),
            "updated": float(p.get("updated") or p.get("created") or now)}


def empty() -> dict:
    return {"version": VERSION, "updated": 0.0, "presets": []}


def parse(raw: str) -> dict:
    data = json.loads(raw) if raw.strip() else empty()
    if isinstance(data, list):                      # bare preset list
        data = {"presets": data}
    if "presets" not in data and "pool" in data:    # studio project / pool export
        return import_studio_pool(data, empty())
    out = empty()
    out["updated"] = float(data.get("updated") or 0)
    for p in data.get("presets") or []:
        out["presets"].append(normalize_preset(p))
    return out


def import_studio_pool(pool, base: dict | None = None, overwrite: bool = False) -> dict:
    """Import mmx_studio's prompt pool: the `mmx.pool` localStorage array
    [{id, name, text, loras:[{name, strength}]}] or a project export {v, pool:[...]}.
    Returns the merged store; existing names are kept unless overwrite=True."""
    store = base if base is not None else empty()
    items = pool.get("pool") if isinstance(pool, dict) else pool
    if not isinstance(items, list):
        raise PresetError("studio pool import: expected a list or a {pool:[...]} project")
    have = {p["name"]: i for i, p in enumerate(store["presets"])}
    n = 0
    for it in items:
        if not isinstance(it, dict) or not (it.get("name") or "").strip():
            continue
        p = normalize_preset({"name": it.get("name"), "prompt": it.get("text") or it.get("prompt") or "",
                              "loras": it.get("loras") or [], "notes": f"imported from studio pool (id {it.get('id', '?')})"})
        if p["name"] in have and not overwrite:
            continue
        if p["name"] in have:
            p["created"] = store["presets"][have[p["name"]]]["created"]
            store["presets"][have[p["name"]]] = p
        else:
            have[p["name"]] = len(store["presets"]); store["presets"].append(p)
        n += 1
    store["updated"] = time.time() if n else store["updated"]
    store["_imported"] = n
    return store


def merge(a: dict, b: dict) -> dict:
    """Union by name; the newer `updated` wins per preset."""
    out = empty()
    by = {}
    for src in (a, b):
        for p in src.get("presets") or []:
            cur = by.get(p["name"])
            if cur is None or p.get("updated", 0) > cur.get("updated", 0):
                by[p["name"]] = p
    out["presets"] = sorted(by.values(), key=lambda p: p["name"].lower())
    out["updated"] = max(float(a.get("updated") or 0), float(b.get("updated") or 0))
    return out


# ── the store ────────────────────────────────────────────────────────────────

class PresetStore:
    def __init__(self, path: str | None = None, mirror: bool | None = None, log=print):
        self.path = path or default_path()
        self.cfg = nas_config()
        self.mirror_enabled = self.cfg["enabled"] if mirror is None else mirror
        self.log = log
        self.lock = threading.RLock()
        self.data = empty()
        self.loaded_at = 0.0
        self.last_mirror = {"pull": None, "push": None}
        self._mtime = None

    # -- local file --
    def load(self, force: bool = False) -> dict:
        with self.lock:
            try:
                m = os.path.getmtime(self.path)
            except OSError:
                m = None
            if force or m != self._mtime:
                if m is None:
                    self.data = empty()
                else:
                    try:
                        self.data = parse(open(self.path, encoding="utf-8").read())
                    except Exception as e:
                        self.log(f"[mmx-presets] {self.path} unreadable ({e}); treating as empty")
                        self.data = empty()
                self._mtime = m
                self.loaded_at = time.time()
            return self.data

    def write(self, data: dict | None = None) -> None:
        with self.lock:
            data = data if data is not None else self.data
            data["version"] = VERSION
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".presets.", suffix=".json", dir=os.path.dirname(os.path.abspath(self.path)))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({k: v for k, v in data.items() if not k.startswith("_")}, f, indent=1, ensure_ascii=False)
            os.replace(tmp, self.path)
            self.data = data
            self._mtime = os.path.getmtime(self.path)

    # -- queries --
    def names(self) -> list:
        return [p["name"] for p in self.load()["presets"]]

    def get(self, name: str) -> dict | None:
        for p in self.load()["presets"]:
            if p["name"] == name:
                return p
        return None

    def save(self, preset: dict, overwrite: bool = True) -> dict:
        with self.lock:
            self.load()
            p = normalize_preset(preset)
            for i, cur in enumerate(self.data["presets"]):
                if cur["name"] == p["name"]:
                    if not overwrite:
                        raise PresetError(f"preset '{p['name']}' exists (overwrite is off)")
                    p["created"] = cur["created"]; p["updated"] = time.time()
                    self.data["presets"][i] = p
                    break
            else:
                self.data["presets"].append(p)
            self.data["presets"].sort(key=lambda x: x["name"].lower())
            self.data["updated"] = time.time()
            self.write()
            return p

    def delete(self, name: str) -> bool:
        with self.lock:
            self.load()
            n = len(self.data["presets"])
            self.data["presets"] = [p for p in self.data["presets"] if p["name"] != name]
            if len(self.data["presets"]) != n:
                self.data["updated"] = time.time(); self.write(); return True
            return False

    def import_pool(self, pool, overwrite: bool = False) -> int:
        with self.lock:
            self.load()
            merged = import_studio_pool(pool, self.data, overwrite)
            n = merged.pop("_imported", 0)
            if n:
                self.write(merged)
            return n

    # -- NAS mirror --
    def nas_status(self) -> dict:
        cfg = self.cfg
        return {"enabled": self.mirror_enabled, "configured": os.path.exists(cfg["key"]), "userhost": cfg["userhost"],
                "remote": os.path.join(cfg["share"], NAS_REL), "last": self.last_mirror}

    def _nas(self, cmd: str, stdin: bytes | None = None, timeout: int = 40):
        cfg = self.cfg
        r = subprocess.run(_ssh_cmd(cfg) + [cfg["userhost"], cmd], input=stdin, capture_output=True, timeout=timeout)
        return r.returncode, r.stdout, r.stderr.decode(errors="replace").strip()

    def pull(self) -> dict:
        """Merge the NAS copy into the local file. Locked share / unreachable NAS = no-op with a reason."""
        res = {"ok": False, "merged": 0, "reason": None, "at": time.time()}
        if not self.mirror_enabled or not os.path.exists(self.cfg["key"]):
            res["reason"] = "mirror disabled" if not self.mirror_enabled else f"NAS key {self.cfg['key']} not present"
            self.last_mirror["pull"] = res; return res
        remote = os.path.join(self.cfg["share"], NAS_REL)
        share = self.cfg["share"]
        cmd = (f"if ! mount | grep -q {_q(' ' + share + ' type ecryptfs')} && [ ! -d {_q(share)} ]; then echo MMX_LOCKED; exit 0; fi; "
               f"if [ -f {_q(remote)} ]; then echo MMX_FILE; cat {_q(remote)}; else echo MMX_NOFILE; fi")
        try:
            rc, out, err = self._nas(cmd)
        except subprocess.TimeoutExpired:
            res["reason"] = "NAS unreachable (timeout)"; self.last_mirror["pull"] = res; return res
        if rc != 0:
            res["reason"] = "NAS ssh failed: " + (err.splitlines()[-1] if err else f"rc={rc}"); self.last_mirror["pull"] = res; return res
        head, _, body = out.partition(b"\n")
        tag = head.strip().decode(errors="replace")
        if tag == "MMX_LOCKED":
            res["reason"] = "share is locked"; self.last_mirror["pull"] = res; return res
        if tag == "MMX_NOFILE":
            res.update(ok=True, reason="no presets on the NAS yet"); self.last_mirror["pull"] = res; return res
        try:
            remote_data = parse(body.decode("utf-8"))
        except Exception as e:
            res["reason"] = f"NAS presets.json unparsable: {e}"; self.last_mirror["pull"] = res; return res
        with self.lock:
            local = self.load(force=True)
            before = {p["name"]: p.get("updated") for p in local["presets"]}
            merged = merge(local, remote_data)
            changed = sum(1 for p in merged["presets"] if before.get(p["name"]) != p.get("updated"))
            if changed or not os.path.exists(self.path):
                self.write(merged)
            res.update(ok=True, merged=changed)
        self.last_mirror["pull"] = res
        return res

    def push(self) -> dict:
        """Write the merged local file to the NAS (merging with whatever is there first)."""
        res = {"ok": False, "reason": None, "at": time.time()}
        if not self.mirror_enabled or not os.path.exists(self.cfg["key"]):
            res["reason"] = "mirror disabled" if not self.mirror_enabled else f"NAS key {self.cfg['key']} not present"
            self.last_mirror["push"] = res; return res
        pulled = self.pull()
        if not pulled["ok"]:
            res["reason"] = "pull before push failed: " + str(pulled["reason"]); self.last_mirror["push"] = res; return res
        remote = os.path.join(self.cfg["share"], NAS_REL)
        payload = json.dumps({k: v for k, v in self.load().items() if not k.startswith("_")}, indent=1, ensure_ascii=False).encode("utf-8")
        cmd = (f"mkdir -p {_q(os.path.dirname(remote))} && cat > {_q(remote + '.tmp')} && mv -f {_q(remote + '.tmp')} {_q(remote)} && echo MMX_PUSHED")
        try:
            rc, out, err = self._nas(cmd, stdin=payload)
        except subprocess.TimeoutExpired:
            res["reason"] = "NAS unreachable (timeout)"; self.last_mirror["push"] = res; return res
        if rc != 0 or b"MMX_PUSHED" not in out:
            res["reason"] = "NAS write failed: " + (err.splitlines()[-1] if err else f"rc={rc}"); self.last_mirror["push"] = res; return res
        res.update(ok=True, bytes=len(payload))
        self.last_mirror["push"] = res
        return res

    def push_async(self) -> None:
        threading.Thread(target=lambda: self.log(f"[mmx-presets] mirror push: {self.push()}"), daemon=True).start()


_STORE = None


def get_store() -> PresetStore:
    global _STORE
    if _STORE is None:
        _STORE = PresetStore()
    return _STORE
