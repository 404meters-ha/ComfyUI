"""Submit a txt2img workflow to the ComfyUI API, wait for it, and report the output image.

Uses only the standard library so it runs under any Python. Defaults target the
SD1.5 checkpoint (v1-5-pruned-emaonly.safetensors) which fits a 10GB GPU.
"""
import argparse
import json
import os
import time
import uuid
from urllib import error, parse, request

SERVER = os.environ.get("COMFY_HOST", "http://127.0.0.1:8188")
CHECKPOINT = "v1-5-pruned-emaonly.safetensors"


def build_prompt(text, negative, seed, width, height, steps, cfg):
    return {
        "3": {"class_type": "KSampler", "inputs": {
            "seed": seed, "steps": steps, "cfg": cfg, "sampler_name": "dpmpp_2m",
            "scheduler": "karras", "denoise": 1.0,
            "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0],
            "latent_image": ["5", 0]}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": CHECKPOINT}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": text, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "ComfyUI_3080", "images": ["8", 0]}},
    }


def post_json(path, payload):
    data = json.dumps(payload).encode("utf-8")
    req = request.Request(SERVER + path, data=data, headers={"Content-Type": "application/json"})
    with request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def get_json(path):
    with request.urlopen(SERVER + path, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt", default="masterpiece, best quality, ultra-detailed, a cute corgi puppy wearing a tiny astronaut helmet, floating in space with stars, cinematic lighting")
    ap.add_argument("--negative", default="lowres, bad anatomy, bad hands, text, error, missing fingers, extra digit, fewer digits, cropped, worst quality, low quality, jpeg artifacts, signature, watermark")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--width", type=int, default=512)
    ap.add_argument("--height", type=int, default=512)
    ap.add_argument("--steps", type=int, default=25)
    ap.add_argument("--cfg", type=float, default=7.0)
    args = ap.parse_args()

    prompt = build_prompt(args.prompt, args.negative, args.seed, args.width, args.height, args.steps, args.cfg)
    client_id = str(uuid.uuid4())
    print(f"Submitting to {SERVER}/prompt ...")
    print(f"  prompt : {args.prompt}")
    print(f"  size   : {args.width}x{args.height}, steps={args.steps}, cfg={args.cfg}, seed={args.seed}")

    try:
        resp = post_json("/prompt", {"prompt": prompt, "client_id": client_id})
    except error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        print("ERROR: server rejected prompt:\n" + body)
        return 2

    if "error" in resp:
        print("ERROR: " + json.dumps(resp, ensure_ascii=False))
        return 2

    prompt_id = resp["prompt_id"]
    print(f"  queued : prompt_id={prompt_id}")

    while True:
        time.sleep(2)
        hist = get_json(f"/history/{prompt_id}")
        if prompt_id not in hist:
            continue
        entry = hist[prompt_id]
        status = entry.get("status", {})
        if status.get("completed") or status.get("status_str") == "error":
            outputs = entry.get("outputs", {})
            print("\nStatus:", status.get("status_str"))
            for m in status.get("messages", []):
                print("  msg:", m)
            for node_id, node_out in outputs.items():
                for img in node_out.get("images", []):
                    fn = img["filename"]
                    sub = img.get("subfolder", "")
                    typ = img.get("type", "output")
                    out_dir = os.path.join("output", sub) if sub else "output"
                    on_disk = os.path.join(out_dir, fn)
                    size = os.path.getsize(on_disk) if os.path.exists(on_disk) else "MISSING"
                    print(f"\nImage -> {on_disk}  ({size} bytes)")
                    view = f"{SERVER}/view?filename={parse.quote(fn)}&subfolder={parse.quote(sub)}&type={parse.quote(typ)}"
                    print(f"  url  : {view}")
            return 0 if status.get("status_str") != "error" else 1


if __name__ == "__main__":
    raise SystemExit(main())
