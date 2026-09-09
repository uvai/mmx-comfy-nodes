"""LoRA registry: `/workspace/mmx/loras.json` (env MMX_LORAS_REGISTRY), mirrored to the NAS as
`mmx/loras.json` with the preset store's rules (union by filename, newer `updated` wins,
tombstones for deletions). One entry per LoRA filename:

    {"version": 1, "updated": t,
     "loras": {"H3_Motion_BoosterV2.safetensors": {"triggers": ["h3motion"], "phrases": ["fast pan"],
                                                   "default_strength": 0.85, "notes": "", "updated": t,
                                                   "auto": true}},
     "deleted": [{"name": "...", "at": t}]}

Entries the user never touched are pre-filled from the safetensors header
(`modelspec.trigger_phrase`, else the top `ss_tag_frequency` tags) with `auto: true` and
`updated: 0`, so an edit on any box, or the NAS copy, always wins over a fresh pre-fill and a
tombstone keeps a deleted entry deleted. The Stack's `triggers` / `phrases` outputs and the
Deck's Send report read this file; presets store only (name, strength, on).
"""
from __future__ import annotations

import json, os, struct, tempfile, threading, time

from . import store as S

VERSION = 1
NAS_REL = "mmx/loras.json"
MAX_AUTO_TRIGGERS = 5
HEADER_CAP = 64 * 1024 * 1024


def default_path() -> str:
    p = os.environ.get("MMX_LORAS_REGISTRY")
    if p:
        return p
    return os.path.join(os.path.dirname(S.default_path()), "loras.json")


# ── safetensors metadata ─────────────────────────────────────────────────────

def read_metadata(path: str) -> dict:
    """The `__metadata__` dict of a safetensors file (header only; never the tensors)."""
    try:
        with open(path, "rb") as f:
            n = struct.unpack("<Q", f.read(8))[0]
            if n <= 0 or n > HEADER_CAP:
                return {}
            hdr = json.loads(f.read(n).decode("utf-8", errors="replace"))
        meta = hdr.get("__metadata__") if isinstance(hdr, dict) else None
        return meta if isinstance(meta, dict) else {}
    except Exception:
        return {}


def _split(s: str) -> list:
    return [t.strip() for t in str(s).replace("\n", ",").split(",") if t.strip()]


def triggers_from_metadata(meta: dict) -> tuple:
    """(triggers, source): modelspec.trigger_phrase first; else the tags that appear in (nearly)
    every training image per ss_tag_frequency, capped at MAX_AUTO_TRIGGERS."""
    if not meta:
        return [], ""
    tp = meta.get("modelspec.trigger_phrase") or meta.get("trigger_phrase") or meta.get("ss_trigger_phrase")
    if tp:
        return _dedupe(_split(tp)), "modelspec.trigger_phrase"
    tf = meta.get("ss_tag_frequency")
    if tf:
        try:
            data = json.loads(tf) if isinstance(tf, str) else tf
            counts: dict = {}
            for _ds, tags in (data or {}).items():
                for tag, c in (tags or {}).items():
                    counts[tag.strip()] = counts.get(tag.strip(), 0) + int(c)
            if counts:
                top = max(counts.values())
                tags = sorted((t for t, c in counts.items() if t and c >= 0.9 * top), key=lambda t: (-counts[t], t))
                return _dedupe(tags[:MAX_AUTO_TRIGGERS]), "ss_tag_frequency"
        except Exception:
            pass
    return [], ""


def _dedupe(items) -> list:
    out, seen = [], set()
    for t in items:
        t = " ".join(str(t).split())
        if t and t.lower() not in seen:
            seen.add(t.lower()); out.append(t)
    return out


# ── schema ───────────────────────────────────────────────────────────────────

def empty() -> dict:
    return {"version": VERSION, "updated": 0.0, "loras": {}, "deleted": []}


def normalize_entry(e: dict, now: float | None = None) -> dict:
    e = e or {}
    try:
        ds = float(e.get("default_strength", S.DEFAULT_STRENGTH))
    except (TypeError, ValueError):
        ds = S.DEFAULT_STRENGTH
    upd = e.get("updated")
    return {"triggers": _dedupe(e.get("triggers") or []), "phrases": _dedupe(e.get("phrases") or []),
            "default_strength": max(0.0, min(2.0, ds)), "notes": str(e.get("notes") or ""),
            "updated": float((now if now is not None else time.time()) if upd is None else upd), "auto": bool(e.get("auto", False))}


def parse(raw: str) -> dict:
    data = json.loads(raw) if raw.strip() else empty()
    out = empty()
    out["updated"] = float(data.get("updated") or 0)
    for name, e in (data.get("loras") or {}).items():
        name = str(name).strip()
        if name and isinstance(e, dict):
            out["loras"][name] = normalize_entry(e, now=out["updated"])
    out["deleted"] = S._tombstones(data)
    return out


def merge(a: dict, b: dict) -> dict:
    out = empty()
    by, tomb = {}, {}
    for src in (a, b):
        for name, e in (src.get("loras") or {}).items():
            cur = by.get(name)
            if cur is None or e.get("updated", 0) > cur.get("updated", 0):
                by[name] = e
        for t in S._tombstones(src):
            if t["name"] not in tomb or t["at"] > tomb[t["name"]]["at"]:
                tomb[t["name"]] = t
    for name, t in list(tomb.items()):
        e = by.get(name)
        if e is not None:
            if e.get("updated", 0) > t["at"]:
                del tomb[name]
            else:
                del by[name]
    out["loras"] = dict(sorted(by.items(), key=lambda kv: kv[0].lower()))
    out["deleted"] = sorted(tomb.values(), key=lambda t: t["name"].lower())
    out["updated"] = max(float(a.get("updated") or 0), float(b.get("updated") or 0))
    return out


# ── the store ────────────────────────────────────────────────────────────────

class LoraRegistry:
    def __init__(self, path: str | None = None, mirror: bool | None = None, log=print, lora_path=None):
        self.path = path or default_path()
        self.cfg = S.nas_config()
        self.mirror_enabled = self.cfg["enabled"] if mirror is None else mirror
        self.log = log
        self.lock = threading.RLock()
        self.data = empty()
        self.last_mirror = {"pull": None, "push": None}
        self._mtime = None
        self._loaded = False
        self._lora_path = lora_path   # name -> absolute path (folder_paths by default)

    def lora_path(self, name: str) -> str | None:
        if self._lora_path:
            return self._lora_path(name)
        try:
            import folder_paths
            return folder_paths.get_full_path("loras", name)
        except Exception:
            return None

    def load(self, force: bool = False) -> dict:
        with self.lock:
            try:
                m = os.path.getmtime(self.path)
            except OSError:
                m = None
            if force or not self._loaded or m != self._mtime:
                self._loaded = True
                if m is None:
                    self.data = empty()
                else:
                    try:
                        self.data = parse(open(self.path, encoding="utf-8").read())
                    except Exception as e:
                        self.log(f"[mmx-registry] {self.path} unreadable ({e}); treating as empty")
                        self.data = empty()
                self._mtime = m
            return self.data

    def write(self, data: dict | None = None) -> None:
        with self.lock:
            data = data if data is not None else self.data
            data["version"] = VERSION
            cutoff = time.time() - S.TOMBSTONE_TTL
            data["deleted"] = [t for t in S._tombstones(data) if t["at"] > cutoff]
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".loras.", suffix=".json", dir=os.path.dirname(os.path.abspath(self.path)))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({k: v for k, v in data.items() if not k.startswith("_")}, f, indent=1, ensure_ascii=False)
            os.replace(tmp, self.path)
            self.data = data
            self._mtime = os.path.getmtime(self.path)

    # -- queries / edits --
    def all(self) -> dict:
        return self.load()["loras"]

    def get(self, name: str) -> dict | None:
        return self.load()["loras"].get(name)

    def set(self, name: str, entry: dict) -> dict:
        name = str(name or "").strip()
        if not name:
            raise ValueError("registry entry needs a LoRA filename")
        with self.lock:
            self.load()
            e = normalize_entry({**entry, "updated": None, "auto": False})
            e["updated"] = time.time()
            self.data["loras"][name] = e
            self.data["loras"] = dict(sorted(self.data["loras"].items(), key=lambda kv: kv[0].lower()))
            self.data["deleted"] = [t for t in self.data["deleted"] if t["name"] != name]
            self.data["updated"] = e["updated"]
            self.write()
            return e

    def delete(self, name: str) -> bool:
        with self.lock:
            self.load()
            if name not in self.data["loras"]:
                return False
            now = time.time()
            del self.data["loras"][name]
            self.data["deleted"] = [t for t in self.data["deleted"] if t["name"] != name] + [{"name": name, "at": now}]
            self.data["updated"] = now
            self.write()
            return True

    def prefill(self, names) -> int:
        """Add auto entries (updated=0) for LoRA files without one and not tombstoned. Returns the
        number added (entries with nothing found in the metadata are still added, empty, so the
        Deck lists every file)."""
        with self.lock:
            self.load()
            dead = {t["name"] for t in self.data["deleted"]}
            n = 0
            for name in names:
                if name in self.data["loras"] or name in dead:
                    continue
                path = self.lora_path(name)
                meta = read_metadata(path) if path else {}
                trig, src = triggers_from_metadata(meta)
                self.data["loras"][name] = normalize_entry({"triggers": trig, "phrases": [], "default_strength": S.DEFAULT_STRENGTH,
                                                            "notes": f"auto: triggers from {src}" if src else "", "updated": 0.0, "auto": True})
                n += 1
            if n:
                self.data["loras"] = dict(sorted(self.data["loras"].items(), key=lambda kv: kv[0].lower()))
                self.write()
            return n

    def for_rows(self, rows) -> dict:
        """{triggers: [...], phrases: [...], per_row: [{name, triggers, phrases}]} for enabled rows in
        order, each list deduped (case-insensitive, first occurrence kept)."""
        reg = self.all()
        per, trig, phr = [], [], []
        for r in rows:
            e = reg.get(r["name"]) or {}
            per.append({"name": r["name"], "triggers": list(e.get("triggers") or []), "phrases": list(e.get("phrases") or [])})
            trig += e.get("triggers") or []
            phr += e.get("phrases") or []
        return {"triggers": _dedupe(trig), "phrases": _dedupe(phr), "per_row": per}

    # -- NAS mirror --
    def nas_status(self) -> dict:
        return {"enabled": self.mirror_enabled, "configured": os.path.exists(self.cfg["key"]), "userhost": self.cfg["userhost"],
                "remote": os.path.join(self.cfg["share"], NAS_REL), "last": self.last_mirror}

    def _mirror_blocked(self):
        if not self.mirror_enabled:
            return "mirror disabled"
        if not os.path.exists(self.cfg["key"]):
            return f"NAS key {self.cfg['key']} not present"
        return None

    def pull(self) -> dict:
        res = {"ok": False, "merged": 0, "reason": self._mirror_blocked(), "at": time.time()}
        if res["reason"]:
            self.last_mirror["pull"] = res; return res
        status, body, reason = S.nas_pull_file(self.cfg, NAS_REL)
        if status == "nofile":
            res.update(ok=True, reason="no registry on the NAS yet"); self.last_mirror["pull"] = res; return res
        if status != "file":
            res["reason"] = reason; self.last_mirror["pull"] = res; return res
        try:
            remote = parse(body.decode("utf-8"))
        except Exception as e:
            res["reason"] = f"NAS loras.json unparsable: {e}"; self.last_mirror["pull"] = res; return res
        with self.lock:
            local = self.load(force=True)
            merged = merge(local, remote)
            before = {k: v["updated"] for k, v in local["loras"].items()}
            after = {k: v["updated"] for k, v in merged["loras"].items()}
            changed = sum(1 for k in set(before) | set(after) if before.get(k) != after.get(k))
            if changed or merged["deleted"] != local.get("deleted", []):
                self.write(merged)
            res.update(ok=True, merged=changed)
        self.last_mirror["pull"] = res
        return res

    def push(self) -> dict:
        res = {"ok": False, "reason": self._mirror_blocked(), "at": time.time()}
        if res["reason"]:
            self.last_mirror["push"] = res; return res
        pulled = self.pull()
        if not pulled["ok"]:
            res["reason"] = "pull before push failed: " + str(pulled["reason"]); self.last_mirror["push"] = res; return res
        payload = json.dumps({k: v for k, v in self.load().items() if not k.startswith("_")}, indent=1, ensure_ascii=False).encode("utf-8")
        ok, reason = S.nas_push_file(self.cfg, NAS_REL, payload)
        if not ok:
            res["reason"] = reason; self.last_mirror["push"] = res; return res
        res.update(ok=True, bytes=len(payload))
        self.last_mirror["push"] = res
        return res

    def push_async(self) -> None:
        threading.Thread(target=lambda: self.log(f"[mmx-registry] mirror push: {self.push()}"), daemon=True).start()


_STORE = None


def get_store() -> LoraRegistry:
    global _STORE
    if _STORE is None:
        _STORE = LoraRegistry()
    return _STORE
