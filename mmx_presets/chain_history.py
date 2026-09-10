"""Chain-frame history. MMX Chain Gate writes `input/<filename>` (the frame the next segment
opens on) AND a numbered copy `input/mmx_chain_history/<stem>/<label>_<segment>_<timestamp>.png`
with a small `_thumb.jpg` next to it; MMX Load Chain Frame's `use_frame` dropdown lists those
copies (newest first, `latest` = the fixed-name file) so any earlier segment can be re-opened.
`filename` keeps the chains apart: mmx_chain_last.png -> stem mmx_chain_last, label mmx_chain.
"""
from __future__ import annotations

import os, re, shutil, time

try:
    import folder_paths
except Exception:   # unit tests without ComfyUI
    folder_paths = None

HIST_SUB = "mmx_chain_history"
LATEST = "latest"
THUMB_W = 192
_ENTRY = re.compile(r"^(?P<label>.+)_(?P<segment>\d{3,})_(?P<ts>\d{8}-\d{6})\.png$")


def input_dir() -> str:
    return folder_paths.get_input_directory() if folder_paths else os.getcwd()


def stem_of(filename: str) -> str:
    name = os.path.basename((filename or "").strip()) or "mmx_chain_last.png"
    stem, ext = os.path.splitext(name)
    return stem if ext.lower() == ".png" else name


def label_of(filename: str) -> str:
    s = stem_of(filename)
    return s[:-5] if s.endswith("_last") and len(s) > 5 else s


def latest_path(filename: str) -> str:
    return os.path.join(input_dir(), stem_of(filename) + ".png")


def history_dir(filename: str) -> str:
    return os.path.join(input_dir(), HIST_SUB, stem_of(filename))


def subfolder(filename: str) -> str:
    """the `subfolder` ComfyUI's /view wants for this chain's history files"""
    return HIST_SUB + "/" + stem_of(filename)


def _entry(filename: str, name: str) -> dict | None:
    m = _ENTRY.match(name)
    if not m:
        return None
    d = history_dir(filename)
    p = os.path.join(d, name)
    thumb = name[:-4] + "_thumb.jpg"
    try:
        st = os.stat(p)
    except OSError:
        return None
    return {"name": name, "label": m["label"], "segment": int(m["segment"]), "ts": m["ts"], "path": p, "size": st.st_size, "mtime": st.st_mtime,
            "thumb": thumb if os.path.isfile(os.path.join(d, thumb)) else None, "subfolder": subfolder(filename)}


def list_history(filename: str) -> list:
    """newest first"""
    d = history_dir(filename)
    if not os.path.isdir(d):
        return []
    out = [e for e in (_entry(filename, n) for n in os.listdir(d)) if e]
    out.sort(key=lambda e: (e["segment"], e["ts"]), reverse=True)
    return out


def all_history() -> list:
    """every chain's entries (for the node's dropdown, which cannot know the filename widget)"""
    root = os.path.join(input_dir(), HIST_SUB)
    if not os.path.isdir(root):
        return []
    out = []
    for stem in sorted(os.listdir(root)):
        out += list_history(stem + ".png")
    out.sort(key=lambda e: e["mtime"], reverse=True)
    return out


def choices(filename: str | None = None) -> list:
    ents = list_history(filename) if filename else all_history()
    return [LATEST] + [e["name"] for e in ents]


def next_segment(filename: str) -> int:
    ents = list_history(filename)
    return (max(e["segment"] for e in ents) + 1) if ents else 1


def record(filename: str, src_png: str) -> dict:
    """Copy the just-written chain frame into the history with the next segment number and a
    thumbnail; returns the entry."""
    from PIL import Image
    d = history_dir(filename)
    os.makedirs(d, exist_ok=True)
    seg = next_segment(filename)
    ts = time.strftime("%Y%m%d-%H%M%S")
    name = f"{label_of(filename)}_{seg:03d}_{ts}.png"
    dst = os.path.join(d, name)
    tmp = dst + ".part"
    shutil.copy2(src_png, tmp)
    os.replace(tmp, dst)
    thumb = os.path.join(d, name[:-4] + "_thumb.jpg")
    try:
        img = Image.open(dst).convert("RGB")
        img.thumbnail((THUMB_W, THUMB_W * 2))
        ttmp = thumb + ".part.jpg"
        img.save(ttmp, "JPEG", quality=80)
        os.replace(ttmp, thumb)
    except Exception:
        pass
    return _entry(filename, name)


def find(filename: str, name: str) -> dict | None:
    if not name or name == LATEST:
        return None
    for e in list_history(filename):
        if e["name"] == name:
            return e
    for e in all_history():          # a frame of another chain (the dropdown lists them all)
        if e["name"] == name:
            return e
    return None


def resolve(filename: str, use_frame: str | None) -> tuple:
    """(path or None, description, entry or None): the frame Load Chain Frame should open on."""
    use_frame = (use_frame or LATEST).strip()
    if use_frame != LATEST:
        e = find(filename, use_frame)
        if e:
            return e["path"], f"history segment {e['segment']} ({e['name']})", e
        return None, f"history frame {use_frame!r} not found", None
    p = latest_path(filename)
    if os.path.isfile(p):
        return p, f"latest ({os.path.basename(p)})", None
    return None, f"no chain frame yet ({os.path.basename(p)} absent)", None


SLOT_PREFIX = "mmx_chain_slot_"


def is_slot_file(name) -> bool:
    return isinstance(name, str) and name.startswith(SLOT_PREFIX) and name.lower().endswith(".png")


def slot_name(filename: str) -> str:
    """the fixed input/ name the static 'Inject into Manager as slot' path writes for this chain"""
    return f"{SLOT_PREFIX}{label_of(filename)}.png"


def inject_slot(filename: str, use_frame: str | None, dest_dir: str | None = None) -> dict:
    """Copy the frame Load Chain Frame would open on (latest / a history entry) into
    input/<slot_name> for a static References Manager slot. Raises when the loader would use
    its fallback (no chain frame yet / unknown history frame)."""
    p, why, entry = resolve(filename, use_frame)
    if not p:
        raise ValueError(f"no chain frame to inject — {why}; the loader would open on its fallback (inject that Library file with its own Inject button)")
    d = dest_dir or input_dir()
    os.makedirs(d, exist_ok=True)
    name = slot_name(filename)
    dst = os.path.join(d, name)
    tmp = dst + ".part"
    shutil.copy2(p, tmp)
    os.replace(tmp, dst)
    return {"file": name, "path": dst, "source": why, "entry": entry}


def clear_history(filename: str) -> int:
    d = history_dir(filename)
    if not os.path.isdir(d):
        return 0
    n = 0
    for f in os.listdir(d):
        if f.endswith(".png") or f.endswith(".jpg"):
            try:
                os.remove(os.path.join(d, f)); n += 1
            except OSError:
                pass
    try:
        os.rmdir(d)
    except OSError:
        pass
    return n
