#!/usr/bin/env python3
"""End-to-end check of the chain-verification nodes against a running ComfyUI (stdlib only).

    python3 tools/live_check.py --server http://127.0.0.1:8188 \
        --frame s1_first.png --good-ref first_frame.png --bad-ref identity.png \
        --lib-image Subjects/j/identity.png --lib-video VideoRef/seg1.mp4

Runs, in order, and prints PASS/FAIL per step:
  1. First Frame Check + Chain Gate with the CORRECT reference: passes, input/<chain>.png written
  2. the same with a WRONG reference, with two filler prompts queued behind it: the check fails,
     the gate writes <chain>_REJECTED.png, leaves <chain>.png untouched (same bytes as step 1),
     raises, and the queue is emptied (the fillers never run)
  3. MMX Library Image x2 -> MMX References Builder -> MiniMax References Manager
     (prompt_provider none): the library files land in input/ under their flat names, the
     Manager resolves them (PreviewImage on image_1 / video_1 produce images) and the prompt
     output carries the picture_map text
Filenames are ComfyUI/input names (put a decoded first frame + its reference there first);
library paths are relative to the mirror root.
"""
import argparse, json, sys, time, urllib.request, urllib.error, urllib.parse, uuid

results = []


def check(name, cond, detail=""):
    results.append(bool(cond))
    print(("PASS " if cond else "FAIL ") + name + (f"  — {detail}" if detail and not cond else ""), flush=True)


class Comfy:
    def __init__(self, server):
        self.server = server.rstrip("/")
        self.client_id = uuid.uuid4().hex

    def _req(self, path, data=None, method=None):
        req = urllib.request.Request(self.server + path, data=json.dumps(data).encode() if data is not None else None,
                                     headers={"Content-Type": "application/json"} if data is not None else {}, method=method)
        with urllib.request.urlopen(req, timeout=120) as r:
            body = r.read()
            return json.loads(body) if body.strip().startswith(b"{") or body.strip().startswith(b"[") else body

    def queue(self, prompt):
        return self._req("/prompt", {"prompt": prompt, "client_id": self.client_id})

    def wait(self, pid, timeout=900):
        t0 = time.time()
        while time.time() - t0 < timeout:
            h = self._req(f"/history/{pid}")
            if pid in h:
                return h[pid]
            time.sleep(1.0)
        raise TimeoutError(pid)

    def pending(self):
        q = self._req("/queue")
        return len(q.get("queue_pending", [])), len(q.get("queue_running", []))

    def input_file(self, name):
        try:
            with urllib.request.urlopen(self.server + "/view?" + urllib.parse.urlencode({"filename": name, "type": "input"}), timeout=60) as r:
                return r.read()
        except urllib.error.HTTPError:
            return None


def node_out(hist, nid):
    return (hist.get("outputs") or {}).get(str(nid), {})


def check_gate_graph(frame, ref, chain, threshold):
    return {
        "1": {"class_type": "LoadImage", "inputs": {"image": frame}},
        "2": {"class_type": "LoadImage", "inputs": {"image": ref}},
        "3": {"class_type": "MMXFirstFrameCheck", "inputs": {"images": ["1", 0], "reference": ["2", 0], "threshold_db": threshold}},
        "4": {"class_type": "MMXChainGate", "inputs": {"images": ["1", 0], "passed": ["3", 2], "filename": chain, "stop_queue": True}},
        "5": {"class_type": "PreviewImage", "inputs": {"images": ["3", 3]}},
    }


def filler_graph(frame):
    return {"1": {"class_type": "LoadImage", "inputs": {"image": frame}},
            "2": {"class_type": "PreviewImage", "inputs": {"images": ["1", 0]}}}


def refs_graph(lib_image, lib_video):
    g = {
        "10": {"class_type": "MMXLibraryImage", "inputs": {"file": lib_image}},
        "12": {"class_type": "MMXReferencesBuilder", "inputs": {"image_1": ["10", 1], "image_9": ["10", 1], "use_soundtrack": True}},
        "13": {"class_type": "MiniMaxH3ReferencePack", "inputs": {"direction": ["12", 1], "references_json": ["12", 0], "prompt_provider": "none",
                                                                   "openrouter_api_key": "", "width": 768, "height": 448, "length_seconds": 2.0}},
        "14": {"class_type": "PreviewImage", "inputs": {"images": ["13", 0]}},   # image_1
        "15": {"class_type": "PreviewImage", "inputs": {"images": ["13", 1]}},   # image_2 (the slot-9 image, compacted)
        "16": {"class_type": "PreviewImage", "inputs": {"images": ["10", 0]}},   # the library node's own image
    }
    if lib_video:
        g["11"] = {"class_type": "MMXLibraryImage", "inputs": {"file": lib_video}}
        g["12"]["inputs"]["video_1"] = ["11", 1]
        g["17"] = {"class_type": "PreviewImage", "inputs": {"images": ["13", 9]}}   # video_1 frames
        g["18"] = {"class_type": "PreviewImage", "inputs": {"images": ["11", 0]}}   # first frame from the library node
    return g


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--server", default="http://127.0.0.1:8188")
    ap.add_argument("--frame", default="s1_first.png", help="input/: a decoded first frame")
    ap.add_argument("--good-ref", default="first_frame.png", help="input/: the reference that frame was guided on")
    ap.add_argument("--bad-ref", default="identity.png", help="input/: an unrelated image")
    ap.add_argument("--chain", default="mmx_livecheck_chain.png")
    ap.add_argument("--threshold", type=float, default=24.0)
    ap.add_argument("--lib-image", default="", help="library path of an image (Subjects/...)")
    ap.add_argument("--lib-video", default="", help="library path of an mp4 (VideoRef/...)")
    a = ap.parse_args()
    c = Comfy(a.server)
    stem = a.chain[:-4] if a.chain.lower().endswith(".png") else a.chain
    rejected = stem + "_REJECTED.png"

    # 1. correct reference
    r = c.queue(check_gate_graph(a.frame, a.good_ref, a.chain, a.threshold))
    h = c.wait(r["prompt_id"])
    st = h.get("status", {})
    o3, o4 = node_out(h, 3), node_out(h, 4)
    psnr_ok = (o3.get("psnr") or [0])[0]
    print(f"   correct reference: PSNR {psnr_ok:.2f} dB  SSIM {(o3.get('ssim') or [0])[0]:.3f}  text={o3.get('text')}")
    check("1a check PASSES with the correct reference", st.get("status_str") == "success" and (o3.get("passed") or [False])[0], json.dumps(st)[:300])
    check("1b check shows the comparison strip", bool(o3.get("images")))
    good_bytes = c.input_file(a.chain)
    check("1c gate wrote input/" + a.chain, good_bytes is not None and (o4.get("written") or [False])[0] and a.chain in (o4.get("text") or [""])[0], str(o4)[:200])

    # 2. wrong reference + fillers behind it
    time.sleep(1.1)   # distinct mtime for the REJECTED file
    r = c.queue(check_gate_graph(a.frame, a.bad_ref, a.chain, a.threshold))
    f1 = c.queue(filler_graph(a.frame)); f2 = c.queue(filler_graph(a.frame))
    h = c.wait(r["prompt_id"])
    st = h.get("status", {})
    o3 = node_out(h, 3)
    msgs = [m for m in st.get("messages", []) if m[0] == "execution_error"]
    err = msgs[0][1] if msgs else {}
    psnr_bad = (o3.get("psnr") or [0])[0]
    print(f"   wrong reference: PSNR {psnr_bad:.2f} dB  text={o3.get('text')}")
    check("2a check FAILS with a wrong reference", st.get("status_str") == "error" and o3.get("passed") == [False] and psnr_bad < a.threshold, json.dumps(st)[:300])
    check("2b the error names the gate and the rejected file", err.get("node_type") == "MMXChainGate" and rejected in (err.get("exception_message") or ""), json.dumps(err)[:300])
    check("2c gate wrote " + rejected, c.input_file(rejected) is not None)
    check("2d input/" + a.chain + " untouched (same bytes as step 1)", c.input_file(a.chain) == good_bytes)
    time.sleep(2)
    pend, run = c.pending()
    ran = []
    for f in (f1, f2):
        hh = c._req(f"/history/{f['prompt_id']}")
        ran.append(f["prompt_id"] in hh)
    check("2e the queue was stopped (fillers dropped, none ran)", pend == 0 and not any(ran), f"pending={pend} running={run} fillers ran={ran}")

    # 3. library -> references builder -> References Manager
    if a.lib_image:
        r = c.queue(refs_graph(a.lib_image, a.lib_video))
        h = c.wait(r["prompt_id"])
        st = h.get("status", {})
        flat = a.lib_image.replace("/", "__")
        check("3a library graph ran (Library Image -> References Builder -> References Manager, provider none)", st.get("status_str") == "success", json.dumps(st)[:400])
        check("3b library image copied into input/ as " + flat, c.input_file(flat) is not None and flat in (node_out(h, 10).get("text") or [""])[0], str(node_out(h, 10))[:200])
        pm = (node_out(h, 12).get("text") or [""])[0]
        check("3c picture_map: slot 1 -> <Picture 1>, slot 9 -> <Picture 2> (compacted)", "slot 1 -> <Picture 1>" in pm and "slot 9 -> <Picture 2>" in pm, pm)
        check("3d References Manager resolved image_1 and image_2 from the builder's JSON", bool(node_out(h, 14).get("images")) and bool(node_out(h, 15).get("images")))
        check("3e Library Image outputs the image", bool(node_out(h, 16).get("images")))
        if a.lib_video:
            vflat = a.lib_video.replace("/", "__")
            check("3f video copied into input/ as " + vflat + " and first frame output", c.input_file(vflat) is not None and bool(node_out(h, 18).get("images")) and "first frame" in (node_out(h, 11).get("text") or [""])[0], str(node_out(h, 11))[:200])
            check("3g References Manager resolved video_1", bool(node_out(h, 17).get("images")))
            check("3h picture_map lists video 1 -> <Video 1>", "video 1 -> <Video 1>" in pm, pm)

    failed = results.count(False)
    print(f"\n{len(results) - failed}/{len(results)} passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
