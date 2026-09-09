"""MMX First Frame Check + MMX Chain Gate.

First Frame Check compares frame 0 of a decoded batch against the intended first frame in the
guide's geometry: the reference is cover-cropped to the frame's aspect and lanczos-resized to
the frame's size exactly as MiniMaxH3AddGuide does (`_resize(image, width, height, "center")`
-> `comfy.utils.common_upscale(..., "lanczos", "center")`), so the number measures what the
guide actually anchored, not the raw reference.

Chain Gate writes the last frame under a fixed name in ComfyUI/input only when the check
passed; otherwise it writes <stem>_REJECTED.png next to it, leaves the previous good frame
alone, clears the pending queue and raises.
"""
from __future__ import annotations

import math, os, time

import numpy as np
import torch

try:
    import folder_paths
except ImportError:  # tests
    folder_paths = None

try:
    from comfy.utils import common_upscale as _comfy_upscale
except Exception:  # outside ComfyUI: PIL replica below (same crop arithmetic, PIL LANCZOS like comfy.utils.lanczos)
    _comfy_upscale = None


# ── guide geometry ────────────────────────────────────────────────────────────

def _pil_center_cover(samples: torch.Tensor, width: int, height: int) -> torch.Tensor:
    """comfy.utils.common_upscale(samples[B,C,H,W], width, height, 'lanczos', 'center') without comfy."""
    from PIL import Image
    old_width, old_height = samples.shape[-1], samples.shape[-2]
    old_aspect, new_aspect = old_width / old_height, width / height
    x = y = 0
    if old_aspect > new_aspect:
        x = round((old_width - old_width * (new_aspect / old_aspect)) / 2)
    elif old_aspect < new_aspect:
        y = round((old_height - old_height * (old_aspect / new_aspect)) / 2)
    s = samples.narrow(-2, y, old_height - y * 2).narrow(-1, x, old_width - x * 2)
    out = []
    for img in s.movedim(1, -1):
        pil = Image.fromarray(np.clip(255.0 * img.cpu().numpy(), 0, 255).astype(np.uint8))
        pil = pil.resize((width, height), resample=Image.Resampling.LANCZOS)
        out.append(torch.from_numpy(np.array(pil).astype(np.float32) / 255.0).movedim(-1, 0))
    return torch.stack(out).to(samples.device, samples.dtype)


def guide_geometry(image: torch.Tensor, width: int, height: int) -> torch.Tensor:
    """[B,H,W,C] -> [B,height,width,3]: the exact transform MiniMaxH3AddGuide applies to its image."""
    samples = image[..., :3].movedim(-1, 1)
    if _comfy_upscale is not None:
        samples = _comfy_upscale(samples, width, height, "lanczos", "center")
    else:
        samples = _pil_center_cover(samples, width, height)
    return samples.movedim(1, -1)


def cover_crop_box(src_w: int, src_h: int, dst_w: int, dst_h: int) -> tuple:
    """(x, y, w, h) of the source region the guide keeps (for the report)."""
    old_aspect, new_aspect = src_w / src_h, dst_w / dst_h
    x = y = 0
    if old_aspect > new_aspect:
        x = round((src_w - src_w * (new_aspect / old_aspect)) / 2)
    elif old_aspect < new_aspect:
        y = round((src_h - src_h * (old_aspect / new_aspect)) / 2)
    return x, y, src_w - 2 * x, src_h - 2 * y


# ── metrics ──────────────────────────────────────────────────────────────────

PSNR_CAP = 100.0   # identical images: report 100 dB instead of inf (JSON-safe)


def psnr(a: torch.Tensor, b: torch.Tensor) -> float:
    mse = float(torch.mean((a.float() - b.float()) ** 2))
    if mse <= 1e-12:
        return PSNR_CAP
    return min(PSNR_CAP, 10.0 * math.log10(1.0 / mse))


def _gaussian_window(size: int = 11, sigma: float = 1.5) -> torch.Tensor:
    ax = torch.arange(size, dtype=torch.float32) - (size - 1) / 2
    g = torch.exp(-(ax ** 2) / (2 * sigma ** 2))
    g = g / g.sum()
    return (g[:, None] * g[None, :])


def ssim(a: torch.Tensor, b: torch.Tensor) -> float:
    """Structural similarity (Wang et al. 2004) on luma, 11x11 gaussian window, sigma 1.5, valid conv."""
    def luma(x):
        x = x.float()
        return (0.299 * x[..., 0] + 0.587 * x[..., 1] + 0.114 * x[..., 2])[None, None]   # [1,1,H,W]
    ya, yb = luma(a), luma(b)
    h, w = ya.shape[-2:]
    size = min(11, h, w)
    if size < 3:
        return 1.0 if float(torch.mean(torch.abs(ya - yb))) < 1e-6 else 0.0
    win = _gaussian_window(size, 1.5)[None, None].to(ya.device)
    conv = lambda x: torch.nn.functional.conv2d(x, win)
    mu_a, mu_b = conv(ya), conv(yb)
    s_aa = conv(ya * ya) - mu_a ** 2
    s_bb = conv(yb * yb) - mu_b ** 2
    s_ab = conv(ya * yb) - mu_a * mu_b
    c1, c2 = 0.01 ** 2, 0.03 ** 2
    m = ((2 * mu_a * mu_b + c1) * (2 * s_ab + c2)) / ((mu_a ** 2 + mu_b ** 2 + c1) * (s_aa + s_bb + c2))
    return float(m.mean().clamp(-1, 1))


# ── comparison strip ─────────────────────────────────────────────────────────

# 6-stop colormap for the abs-diff heat-map (black -> blue -> cyan -> yellow -> red -> white)
_STOPS = np.array([[0, 0, 0], [0, 0, 160], [0, 200, 255], [255, 240, 0], [255, 40, 0], [255, 255, 255]], dtype=np.float32) / 255.0
HEAT_FULL_SCALE = 0.25   # a per-pixel mean abs diff of 0.25 (64/255) and above is white


def heatmap(diff: np.ndarray) -> np.ndarray:
    """[H,W] 0..1 -> [H,W,3] float RGB through the colormap."""
    t = np.clip(diff / HEAT_FULL_SCALE, 0, 1) * (len(_STOPS) - 1)
    i = np.clip(np.floor(t).astype(int), 0, len(_STOPS) - 2)
    f = (t - i)[..., None]
    return _STOPS[i] * (1 - f) + _STOPS[i + 1] * f


def _label(img: np.ndarray, text: str, ok: bool | None = None) -> np.ndarray:
    """Draw a label bar on top of a [H,W,3] float image (PIL default font, works everywhere)."""
    from PIL import Image, ImageDraw
    pil = Image.fromarray((np.clip(img, 0, 1) * 255).astype(np.uint8))
    d = ImageDraw.Draw(pil, "RGBA")
    bar_h = max(18, pil.height // 18)
    bg = (0, 0, 0, 170) if ok is None else ((0, 110, 40, 190) if ok else (150, 20, 20, 190))
    d.rectangle([0, 0, pil.width, bar_h], fill=bg)
    try:
        from PIL import ImageFont
        font = ImageFont.load_default(size=max(11, bar_h - 6))
    except Exception:
        font = None
    d.text((6, 2), text, fill=(255, 255, 255, 255), font=font)
    return np.asarray(pil).astype(np.float32) / 255.0


def comparison_strip(ref: torch.Tensor, frame: torch.Tensor, psnr_db: float, ssim_v: float, passed: bool, threshold: float) -> torch.Tensor:
    """reference | frame 0 | abs-diff heat-map, labelled, as one [1,H,3W+gaps,3] IMAGE."""
    r = ref.float().cpu().numpy(); f = frame.float().cpu().numpy()
    diff = np.abs(r - f).mean(axis=-1)
    panels = [_label(r, "reference (guide geometry)"),
              _label(f, "frame 0"),
              _label(heatmap(diff), f"abs diff  PSNR {psnr_db:.1f} dB  SSIM {ssim_v:.3f}  {'PASS' if passed else 'FAIL'} (>= {threshold:g} dB)", passed)]
    gap = np.full((r.shape[0], 6, 3), 0.15, dtype=np.float32)
    strip = np.concatenate([panels[0], gap, panels[1], gap, panels[2]], axis=1)
    return torch.from_numpy(np.ascontiguousarray(strip))[None, ...]


def _save_preview(image: torch.Tensor, prefix: str) -> list:
    """Write an IMAGE to ComfyUI's temp dir so it shows up in the node like a PreviewImage."""
    if folder_paths is None:
        return []
    from PIL import Image
    d = folder_paths.get_temp_directory()
    os.makedirs(d, exist_ok=True)
    name = f"{prefix}_{int(time.time() * 1000) % 100000000:08d}.png"
    arr = (image[0].cpu().numpy() * 255.0).clip(0, 255).astype(np.uint8)
    Image.fromarray(arr).save(os.path.join(d, name), compress_level=1)
    return [{"filename": name, "subfolder": "", "type": "temp"}]


# ── nodes ────────────────────────────────────────────────────────────────────

class MMXFirstFrameCheck:
    """Compare frame 0 of `images` with `reference` in the guide's geometry (cover-crop + lanczos to the
    frame's size, exactly like MiniMaxH3AddGuide). Outputs PSNR, SSIM, passed and a labelled
    reference | frame 0 | abs-diff strip; shows the numbers and PASS/FAIL in the node. With `enabled`
    off or no reference connected it skips: passed=True, psnr=ssim=-1, comparison=frame 0."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE", {"tooltip": "decoded frames (VAE Decode); frame 0 is checked"}),
            "threshold_db": ("FLOAT", {"default": 24.0, "min": 0.0, "max": 100.0, "step": 0.5,
                                       "tooltip": "PSNR at or above this passes. Guide continuations measure ~29-33 dB, a wrong reference < 20 dB."}),
        }, "optional": {
            "reference": ("IMAGE", {"tooltip": "the intended first frame: the slot-9 image or the previous segment's last frame. Leave unconnected on a first segment: the check is skipped."}),
            "enabled": ("BOOLEAN", {"default": True, "label_on": "enabled", "label_off": "skipped",
                                    "tooltip": "off: no comparison, passed=True, psnr=-1, comparison=frame 0 (first segment of a chain)"}),
        }}

    RETURN_TYPES = ("FLOAT", "FLOAT", "BOOLEAN", "IMAGE")
    RETURN_NAMES = ("psnr", "ssim", "passed", "comparison")
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = "mmx/chain"
    DESCRIPTION = "PSNR / SSIM of frame 0 against the intended first frame, measured in MiniMaxH3AddGuide's cover-crop geometry."

    SKIP_TEXT = "skipped (first segment)"

    def run(self, images, threshold_db, reference=None, enabled=True):
        frame = images[0:1]
        h, w = int(frame.shape[1]), int(frame.shape[2])
        if not enabled or reference is None:
            why = "check disabled" if not enabled else "no reference connected"
            text = f"{self.SKIP_TEXT}: {why}\nframe 0 {w}x{h} of {int(images.shape[0])} passed through; passed=True psnr=-1"
            print("[mmx-check] " + text.replace("\n", " | "))
            return {"ui": {"text": [text], "images": _save_preview(frame, "mmx_check"), "psnr": [-1.0], "ssim": [-1.0], "passed": [True], "skipped": [True]},
                    "result": (-1.0, -1.0, True, frame)}
        rh, rw = int(reference.shape[1]), int(reference.shape[2])
        ref = guide_geometry(reference[0:1], w, h)
        p = psnr(ref[0], frame[0]); s = ssim(ref[0], frame[0]); ok = p >= float(threshold_db)
        strip = comparison_strip(ref[0], frame[0], p, s, ok, float(threshold_db))
        cx, cy, cw, ch = cover_crop_box(rw, rh, w, h)
        crop_txt = f"cover-crop {cw}x{ch}@({cx},{cy}) -> {w}x{h}" if (rw, rh) != (w, h) else f"{w}x{h} (same size, no crop)"
        text = (f"{'PASS' if ok else 'FAIL'}  PSNR {p:.2f} dB  (threshold {float(threshold_db):g})  SSIM {s:.3f}\n"
                f"frame 0 {w}x{h} of {int(images.shape[0])} | reference {rw}x{rh}: {crop_txt}")
        print("[mmx-check] " + text.replace("\n", " | "))
        return {"ui": {"text": [text], "images": _save_preview(strip, "mmx_check"), "psnr": [p], "ssim": [s], "passed": [ok], "skipped": [False]},
                "result": (p, s, ok, strip)}


def gate_paths(filename: str) -> tuple:
    """(good_path, rejected_path) under ComfyUI/input for a fixed-name chain frame."""
    name = os.path.basename((filename or "").strip()) or "mmx_chain_last.png"
    stem, ext = os.path.splitext(name)
    if ext.lower() != ".png":
        stem, ext = name, ".png"
    d = folder_paths.get_input_directory() if folder_paths else os.getcwd()
    return os.path.join(d, stem + ext), os.path.join(d, stem + "_REJECTED" + ext)


def _write_png(image: torch.Tensor, path: str) -> None:
    from PIL import Image
    arr = (image.cpu().numpy() * 255.0).clip(0, 255).astype(np.uint8)
    tmp = path + ".tmp.png"
    Image.fromarray(arr).save(tmp, compress_level=1)
    os.replace(tmp, path)


def _wipe_pending_queue() -> int:
    """Drop every pending (not running) prompt so a failed segment does not let the rest of the
    chain run from a stale frame. Returns the number dropped (0 outside ComfyUI)."""
    try:
        from server import PromptServer
        q = PromptServer.instance.prompt_queue
        with q.mutex:
            n = len(q.queue)
        q.wipe_queue()
        return n
    except Exception:
        return 0


class MMXChainGate:
    """When `passed`: write the LAST frame to ComfyUI/input/<filename> (overwrite) for the next
    segment. When not: write <stem>_REJECTED.png instead, keep the previous good frame, clear the
    pending queue and raise so the chain stops here."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE",),
            "passed": ("BOOLEAN", {"default": True, "forceInput": True, "tooltip": "from MMX First Frame Check"}),
            "filename": ("STRING", {"default": "mmx_chain_last.png", "tooltip": "written into ComfyUI/input; the next segment's LoadImage / MMX Load Chain Frame reads it"}),
        }, "optional": {
            "stop_queue": ("BOOLEAN", {"default": True, "tooltip": "on failure also clear the pending queue (the remaining segments)"}),
        }, "hidden": {"unique_id": "UNIQUE_ID"}}

    RETURN_TYPES = ("STRING", "BOOLEAN")
    RETURN_NAMES = ("path", "written")
    FUNCTION = "run"
    OUTPUT_NODE = True
    CATEGORY = "mmx/chain"
    DESCRIPTION = "Writes the last frame under a fixed name only when the first-frame check passed; a failure writes _REJECTED.png, keeps the old frame and stops the queue."

    @classmethod
    def IS_CHANGED(cls, **kw):
        return float("nan")

    def run(self, images, passed, filename, stop_queue=True, unique_id=None):
        good, rejected = gate_paths(filename)
        last = images[-1]
        if passed:
            _write_png(last, good)
            text = f"PASS -> wrote {good}"
            print("[mmx-gate] " + text)
            return {"ui": {"text": [text], "path": [good], "written": [True]}, "result": (good, True)}
        _write_png(last, rejected)
        kept = f"{good} kept from the previous segment" if os.path.isfile(good) else f"{good} not written (no previous frame)"
        dropped = _wipe_pending_queue() if stop_queue else 0
        text = (f"FAIL -> wrote {rejected}; {kept}"
                + (f"; cleared {dropped} pending prompt(s)" if dropped else "; queue empty" if stop_queue else ""))
        print("[mmx-gate] " + text)
        _notify(unique_id, text)
        raise RuntimeError("MMX Chain Gate: first-frame check FAILED. " + text)


def _notify(unique_id, text: str) -> None:
    """Push the gate's verdict to the frontend before raising (an error aborts the node's ui output)."""
    try:
        from server import PromptServer
        PromptServer.instance.send_sync("mmx-gate", {"node": str(unique_id), "text": text})
    except Exception:
        pass


NODE_CLASS_MAPPINGS = {"MMXFirstFrameCheck": MMXFirstFrameCheck, "MMXChainGate": MMXChainGate}
NODE_DISPLAY_NAME_MAPPINGS = {"MMXFirstFrameCheck": "MMX First Frame Check", "MMXChainGate": "MMX Chain Gate"}
