"""MMX Library Image: a dropdown over the local mirror of the NAS library
(/workspace/mmx/library/{Subjects,VideoRef,Sets}/**, mirrored from /volume1/subgenula at boot by
tools/mmx_library_sync.sh via additional_params.sh; env MMX_LIBRARY overrides the root).

The node copies the chosen file into ComfyUI/input under a flat name (Subjects/j/j1.jpg ->
Subjects__j__j1.jpg) so the References Manager can reference it by filename, and outputs the
image (an mp4's first frame) plus that filename and the library path. Scanning, thumbnails and
the re-mirror trigger live here so routes.py and the node share them.
"""
from __future__ import annotations

import hashlib, os, shutil, subprocess, threading, time

IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".gif", ".tif", ".tiff"}
VIDEO_EXT = {".mp4", ".mov", ".webm", ".mkv", ".m4v"}
FOLDERS = ("Subjects", "VideoRef", "Sets")
NONE = "(library empty — press Refresh / Mirror from NAS)"
SCAN_TTL = 20.0


def root() -> str:
    p = os.environ.get("MMX_LIBRARY")
    if p:
        return p
    if os.path.isdir("/workspace"):
        return "/workspace/mmx/library"
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "library")


def thumbs_dir() -> str:
    d = os.environ.get("MMX_LIBRARY_THUMBS") or os.path.join(os.path.dirname(os.path.abspath(root())), "library_thumbs")
    os.makedirs(d, exist_ok=True)
    return d


def sync_script() -> str:
    return os.environ.get("MMX_LIBRARY_SYNC") or "/root/mmx_library_sync.sh"


def sync_log() -> str:
    return os.environ.get("MMX_LIBRARY_SYNC_LOG") or ("/workspace/mmx_library_sync.log" if os.path.isdir("/workspace") else os.path.join(thumbs_dir(), "sync.log"))


def kind_of(name: str) -> str | None:
    ext = os.path.splitext(name)[1].lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    return None


# ── scanning ─────────────────────────────────────────────────────────────────

_scan = {"t": 0.0, "root": None, "items": []}
_lock = threading.Lock()


def scan(force: bool = False) -> list:
    """[{path, name, folder, kind, size, mtime}] over the library folders, sorted; cached SCAN_TTL s."""
    r = root()
    with _lock:
        if not force and _scan["root"] == r and time.time() - _scan["t"] < SCAN_TTL:
            return _scan["items"]
    items = []
    if os.path.isdir(r):
        tops = [f for f in FOLDERS if os.path.isdir(os.path.join(r, f))]
        tops += sorted(d for d in os.listdir(r) if d not in FOLDERS and not d.startswith((".", "@", "_")) and os.path.isdir(os.path.join(r, d)))
        for top in tops:
            for dp, dn, fn in os.walk(os.path.join(r, top)):
                dn[:] = sorted(d for d in dn if not d.startswith((".", "@")))
                for f in fn:
                    if f.startswith("."):
                        continue
                    k = kind_of(f)
                    if not k:
                        continue
                    p = os.path.join(dp, f)
                    try:
                        st = os.stat(p)
                    except OSError:
                        continue
                    rel = os.path.relpath(p, r).replace(os.sep, "/")
                    items.append({"path": rel, "name": f, "folder": os.path.dirname(rel), "kind": k, "size": st.st_size, "mtime": st.st_mtime})
    items.sort(key=lambda x: (x["folder"].lower(), x["name"].lower()))
    with _lock:
        _scan.update(t=time.time(), root=r, items=items)
    return items


def paths(force: bool = False) -> list:
    return [it["path"] for it in scan(force)] or [NONE]


def check_path(rel: str) -> str:
    """Validate a library-relative path and return the absolute file path."""
    rel = (rel or "").strip().replace("\\", "/")
    if not rel or rel == NONE or rel.startswith("/") or ".." in rel.split("/"):
        raise ValueError(f"MMX Library: bad path {rel!r}")
    p = os.path.join(root(), rel)
    if not os.path.isfile(p):
        raise ValueError(f"MMX Library: {rel} is not in {root()} (press Refresh; Mirror from NAS if the share was locked at boot)")
    return p


# ── input-dir copy + first frame ─────────────────────────────────────────────

def input_name(rel: str) -> str:
    """Flat, stable filename inside ComfyUI/input for a library path."""
    return rel.replace("\\", "/").strip("/").replace("/", "__")


def copy_to_input(rel: str, input_dir: str) -> str:
    """Copy the library file into input_dir under input_name(rel) unless an identical copy is there."""
    src = check_path(rel)
    dst = os.path.join(input_dir, input_name(rel))
    try:
        s, d = os.stat(src), os.stat(dst)
        if s.st_size == d.st_size and abs(s.st_mtime - d.st_mtime) < 1.0:
            return dst
    except OSError:
        pass
    os.makedirs(input_dir, exist_ok=True)
    tmp = dst + ".part"
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)
    return dst


def _cache_key(path: str) -> str:
    st = os.stat(path)
    return hashlib.sha1(f"{path}:{st.st_size}:{int(st.st_mtime)}".encode()).hexdigest()[:16]


def first_frame_png(path: str) -> str:
    """The first frame of a video as a PNG in the thumbs dir (ffmpeg), cached per size+mtime."""
    out = os.path.join(thumbs_dir(), "ff_" + _cache_key(path) + ".png")
    if os.path.isfile(out):
        return out
    tmp = out + ".part.png"
    r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", path, "-frames:v", "1", tmp], capture_output=True, text=True, timeout=120)
    if r.returncode != 0 or not os.path.isfile(tmp):
        raise RuntimeError(f"MMX Library: ffmpeg could not read the first frame of {os.path.basename(path)}: {r.stderr.strip()[-200:]}")
    os.replace(tmp, out)
    return out


def load_image_tensor(path: str):
    """[1,H,W,3] float tensor from an image file (EXIF-rotated, RGB)."""
    import numpy as np, torch
    from PIL import Image, ImageOps
    img = ImageOps.exif_transpose(Image.open(path)).convert("RGB")
    return torch.from_numpy(np.asarray(img).astype(np.float32) / 255.0)[None, ...]


def thumb_jpeg(rel: str, width: int = 320) -> bytes:
    """JPEG thumbnail bytes (first frame for videos), cached in the thumbs dir."""
    from PIL import Image, ImageOps
    src = check_path(rel)
    out = os.path.join(thumbs_dir(), "t_" + _cache_key(src) + f"_{width}.jpg")
    if os.path.isfile(out):
        return open(out, "rb").read()
    img_path = first_frame_png(src) if kind_of(src) == "video" else src
    img = ImageOps.exif_transpose(Image.open(img_path)).convert("RGB")
    img.thumbnail((width, width * 2))
    tmp = out + ".part.jpg"
    img.save(tmp, "JPEG", quality=82)
    os.replace(tmp, out)
    return open(out, "rb").read()


# ── mirror trigger ───────────────────────────────────────────────────────────

_sync = {"proc": None, "started": 0.0, "last": None}


def sync_status() -> dict:
    p = _sync["proc"]
    running = p is not None and p.poll() is None
    if p is not None and not running and _sync["last"] is None:
        _sync["last"] = {"rc": p.returncode, "finished": time.time()}
    tail = ""
    try:
        with open(sync_log(), "rb") as f:
            f.seek(0, 2); n = f.tell(); f.seek(max(0, n - 4000)); tail = f.read().decode(errors="replace")
    except OSError:
        pass
    return {"script": sync_script(), "available": os.path.isfile(sync_script()), "running": running, "started": _sync["started"],
            "last": _sync["last"], "log": sync_log(), "log_tail": tail[-1500:], "root": root(), "exists": os.path.isdir(root())}


def start_sync() -> dict:
    """Run the mirror script detached (it logs to sync_log()); a running sync is not restarted."""
    st = sync_status()
    if st["running"]:
        return {**st, "started_now": False}
    if not st["available"]:
        return {**st, "started_now": False, "error": f"mirror script {sync_script()} not present on this host"}
    _sync["last"] = None
    _sync["proc"] = subprocess.Popen(["bash", sync_script()], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    _sync["started"] = time.time()
    return {**sync_status(), "started_now": True}


# ── node ─────────────────────────────────────────────────────────────────────

class MMXLibraryImage:
    """Pick a file from the mirrored NAS library. Outputs the image (mp4: first frame), the flat
    filename it was copied under in ComfyUI/input (for the References Manager) and the library path."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "file": (paths(), {"tooltip": "Subjects / VideoRef / Sets under " + root() + " — type in the search box of the node to filter; Refresh re-scans, Mirror from NAS re-syncs."}),
        }}

    RETURN_TYPES = ("IMAGE", "STRING", "STRING")
    RETURN_NAMES = ("image", "filename", "path")
    FUNCTION = "run"
    CATEGORY = "mmx/library"
    DESCRIPTION = "Library file -> IMAGE (first frame for videos) + the filename copied into ComfyUI/input + the library path."

    @classmethod
    def IS_CHANGED(cls, file):
        try:
            st = os.stat(check_path(file)); return f"{file}:{st.st_size}:{st.st_mtime_ns}"
        except Exception:
            return f"{file}:missing"

    @classmethod
    def VALIDATE_INPUTS(cls, file):
        # our own check instead of the combo-list check, so a file picked after a Refresh queues even
        # though the enum ComfyUI cached at load time does not list it
        try:
            check_path(file)
        except ValueError as e:
            return str(e)
        return True

    def run(self, file):
        import folder_paths
        src = check_path(file)
        dst = copy_to_input(file, folder_paths.get_input_directory())
        img = load_image_tensor(first_frame_png(src) if kind_of(src) == "video" else src)
        name = os.path.basename(dst)
        text = f"{file} -> input/{name} ({int(img.shape[2])}x{int(img.shape[1])}{', first frame' if kind_of(src) == 'video' else ''})"
        return {"ui": {"text": [text]}, "result": (img, name, src)}


NODE_CLASS_MAPPINGS = {"MMXLibraryImage": MMXLibraryImage}
NODE_DISPLAY_NAME_MAPPINGS = {"MMXLibraryImage": "MMX Library Image"}
