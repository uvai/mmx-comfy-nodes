"""Phrase chips for the MMX Deck: `/workspace/mmx/phrases.json` (env MMX_PHRASES overrides),
mirrored to the NAS as `mmx/phrases.json` exactly like presets.json (same transport, same
merge idea: union by (group, text), newer wins, tombstones for deletions).

    {"version": 1, "updated": <unix float>,
     "groups": [{"name": "Camera", "phrases": [{"text": "Wide shot, slow push in", "updated": t}]}],
     "deleted": [{"group": "Camera", "text": "...", "at": t}]}

A phrase whose text starts or ends with an ellipsis is inserted without it (the Deck puts the
caret where the ellipsis was), so "…is the absolute first frame of the video." reads as a chip
but lands as " is the absolute first frame of the video." after the tag the user placed.
"""
from __future__ import annotations

import json, os, tempfile, threading, time

from . import store as S

VERSION = 1
NAS_REL = "mmx/phrases.json"

# Seed: the two studio constraints plus the camera / lighting fragments of the studio's default
# prompt pool (`establishing`, `close up`); the pool has no pacing phrases, so those three are ours.
SEED = [
    ("First frame", ["…is the absolute first frame of the video.",
                     "Construct the character reference using …"]),
    ("Camera", ["Wide shot, slow push in", "turns to camera", "shallow depth of field", "close up",
                "static locked-off frame", "handheld, slight sway"]),
    ("Lighting", ["natural light", "soft key light from the left", "film grain", "golden hour backlight"]),
    ("Pacing", ["slow and deliberate", "single continuous take, no cuts", "a beat of stillness, then movement"]),
]


def default_path() -> str:
    p = os.environ.get("MMX_PHRASES")
    if p:
        return p
    return os.path.join(os.path.dirname(S.default_path()), "phrases.json")


def empty() -> dict:
    return {"version": VERSION, "updated": 0.0, "groups": [], "deleted": []}


def seeded(now: float | None = None) -> dict:
    """The seed set. Seed phrases carry updated=0 so a tombstone from the NAS (a seed phrase the
    user deleted on another rent) always beats a fresh re-seed, and a NAS copy of the same phrase
    (real timestamp) wins over the seed."""
    d = empty()
    d["groups"] = [{"name": g, "phrases": [{"text": t, "updated": 0.0} for t in items]} for g, items in SEED]
    d["updated"] = now or time.time()
    return d


def _norm_text(t) -> str:
    return " ".join(str(t or "").split())


def parse(raw: str) -> dict:
    data = json.loads(raw) if raw.strip() else empty()
    out = empty()
    out["updated"] = float(data.get("updated") or 0)
    seen = set()
    for g in data.get("groups") or []:
        name = _norm_text(g.get("name") if isinstance(g, dict) else g)
        if not name:
            continue
        items = []
        for p in (g.get("phrases") or []) if isinstance(g, dict) else []:
            if isinstance(p, str):
                p = {"text": p}
            text = _norm_text(p.get("text"))
            if not text or (name, text) in seen:
                continue
            seen.add((name, text))
            items.append({"text": text, "updated": float(out["updated"] if p.get("updated") is None else p["updated"])})
        grp = next((x for x in out["groups"] if x["name"] == name), None)
        if grp is None:
            out["groups"].append({"name": name, "phrases": items})
        else:
            grp["phrases"] += items
    for t in data.get("deleted") or []:
        try:
            if t.get("text") and t.get("group"):
                out["deleted"].append({"group": _norm_text(t["group"]), "text": _norm_text(t["text"]), "at": float(t.get("at") or 0)})
        except (AttributeError, TypeError, ValueError):
            pass
    return out


def merge(a: dict, b: dict) -> dict:
    """Union by (group, text); newer `updated` wins; tombstones newer than the phrase remove it.
    Group order: a's groups first (in a's order), then b's new groups; phrases likewise."""
    out = empty()
    order, by, tomb = [], {}, {}
    for src in (a, b):
        for g in src.get("groups") or []:
            if g["name"] not in order:
                order.append(g["name"])
            for p in g["phrases"]:
                k = (g["name"], p["text"])
                if k not in by:
                    by[k] = dict(p); by[k]["_order"] = len(by)
                elif p.get("updated", 0) > by[k].get("updated", 0):
                    by[k] = {**by[k], **p}
        for t in src.get("deleted") or []:
            k = (t["group"], t["text"])
            if k not in tomb or t["at"] > tomb[k]["at"]:
                tomb[k] = t
    for k, t in list(tomb.items()):
        p = by.get(k)
        if p is not None:
            if p.get("updated", 0) > t["at"]:
                del tomb[k]
            else:
                del by[k]
    for name in order:
        items = sorted([p for (g, _), p in by.items() if g == name], key=lambda p: p["_order"])
        out["groups"].append({"name": name, "phrases": [{"text": p["text"], "updated": p["updated"]} for p in items]})
    out["groups"] = [g for g in out["groups"] if g["phrases"]]
    out["deleted"] = sorted(tomb.values(), key=lambda t: (t["group"].lower(), t["text"].lower()))
    out["updated"] = max(float(a.get("updated") or 0), float(b.get("updated") or 0))
    return out


class PhraseStore:
    def __init__(self, path: str | None = None, mirror: bool | None = None, log=print):
        self.path = path or default_path()
        self.cfg = S.nas_config()
        self.mirror_enabled = self.cfg["enabled"] if mirror is None else mirror
        self.log = log
        self.lock = threading.RLock()
        self.data = empty()
        self.last_mirror = {"pull": None, "push": None}
        self._mtime = None
        self._loaded = False

    def load(self, force: bool = False) -> dict:
        with self.lock:
            try:
                m = os.path.getmtime(self.path)
            except OSError:
                m = None
            if force or not self._loaded or m != self._mtime:
                self._loaded = True
                if m is None:
                    self.data = seeded()          # first run: the seed; a NAS pull merges on top
                    self.write()
                else:
                    try:
                        self.data = parse(open(self.path, encoding="utf-8").read())
                    except Exception as e:
                        self.log(f"[mmx-phrases] {self.path} unreadable ({e}); treating as empty")
                        self.data = empty()
                    self._mtime = m
            return self.data

    def write(self, data: dict | None = None) -> None:
        with self.lock:
            data = data if data is not None else self.data
            data["version"] = VERSION
            cutoff = time.time() - S.TOMBSTONE_TTL
            data["deleted"] = [t for t in data.get("deleted") or [] if t["at"] > cutoff]
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            fd, tmp = tempfile.mkstemp(prefix=".phrases.", suffix=".json", dir=os.path.dirname(os.path.abspath(self.path)))
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({k: v for k, v in data.items() if not k.startswith("_")}, f, indent=1, ensure_ascii=False)
            os.replace(tmp, self.path)
            self.data = data
            self._mtime = os.path.getmtime(self.path)

    # -- edits --
    def groups(self) -> list:
        return self.load()["groups"]

    def add(self, group: str, text: str) -> dict:
        group, text = _norm_text(group), _norm_text(text)
        if not group or not text:
            raise ValueError("phrase needs a group and a text")
        with self.lock:
            self.load()
            now = time.time()
            grp = next((g for g in self.data["groups"] if g["name"] == group), None)
            if grp is None:
                grp = {"name": group, "phrases": []}; self.data["groups"].append(grp)
            cur = next((p for p in grp["phrases"] if p["text"] == text), None)
            if cur is None:
                grp["phrases"].append({"text": text, "updated": now})
            else:
                cur["updated"] = now
            self.data["deleted"] = [t for t in self.data["deleted"] if (t["group"], t["text"]) != (group, text)]
            self.data["updated"] = now
            self.write()
            return {"group": group, "text": text}

    def remove(self, group: str, text: str) -> bool:
        group, text = _norm_text(group), _norm_text(text)
        with self.lock:
            self.load()
            now = time.time()
            removed = False
            for g in self.data["groups"]:
                if g["name"] == group:
                    n = len(g["phrases"]); g["phrases"] = [p for p in g["phrases"] if p["text"] != text]
                    removed = removed or len(g["phrases"]) != n
            self.data["groups"] = [g for g in self.data["groups"] if g["phrases"]]
            if removed:
                self.data["deleted"] = [t for t in self.data["deleted"] if (t["group"], t["text"]) != (group, text)] + [{"group": group, "text": text, "at": now}]
                self.data["updated"] = now
                self.write()
            return removed

    def replace_all(self, groups: list) -> dict:
        """Bulk edit from the Deck's editor: new (group, text) pairs get `updated` now, removed ones a tombstone."""
        with self.lock:
            self.load()
            now = time.time()
            new = parse(json.dumps({"groups": groups}))
            old_keys = {(g["name"], p["text"]): p for g in self.data["groups"] for p in g["phrases"]}
            new_keys = {(g["name"], p["text"]) for g in new["groups"] for p in g["phrases"]}
            for g in new["groups"]:
                for p in g["phrases"]:
                    k = (g["name"], p["text"])
                    p["updated"] = old_keys[k]["updated"] if k in old_keys else now
            gone = [k for k in old_keys if k not in new_keys]
            new["deleted"] = [t for t in self.data["deleted"] if (t["group"], t["text"]) not in new_keys] + \
                             [{"group": g, "text": t, "at": now} for g, t in gone]
            new["updated"] = now
            self.write(new)
            return self.data

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
            res.update(ok=True, reason="no phrases on the NAS yet"); self.last_mirror["pull"] = res; return res
        if status != "file":
            res["reason"] = reason; self.last_mirror["pull"] = res; return res
        try:
            remote = parse(body.decode("utf-8"))
        except Exception as e:
            res["reason"] = f"NAS phrases.json unparsable: {e}"; self.last_mirror["pull"] = res; return res
        with self.lock:
            local = self.load(force=True)
            merged = merge(local, remote)
            before = {(g["name"], p["text"]): p["updated"] for g in local["groups"] for p in g["phrases"]}
            after = {(g["name"], p["text"]): p["updated"] for g in merged["groups"] for p in g["phrases"]}
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
        threading.Thread(target=lambda: self.log(f"[mmx-phrases] mirror push: {self.push()}"), daemon=True).start()


_STORE = None


def get_store() -> PhraseStore:
    global _STORE
    if _STORE is None:
        _STORE = PhraseStore()
    return _STORE
