"""Run Falcon-OCR over a directory of page images and write the joined text,
following the same shape as ocr_glm.py (--pages / --out, a .time sidecar).

Converted from src/falcon/03_falcon_ocr.ipynb, which OCR'd one hardcoded
/content/ image and printed the result instead of saving it.
"""

import argparse
import glob
import os
import time

import torch
from PIL import Image
from transformers import AutoModelForCausalLM

MODEL_PATH = "tiiuae/Falcon-OCR"
CATEGORY = "plain"  # Falcon-OCR's own default category


def load_model(device: str):
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_PATH,
        trust_remote_code=True,
        torch_dtype=torch.bfloat16 if device == "cuda" else torch.float32,
        device_map="auto",
    ).eval()
    return model


@torch.no_grad()
def ocr_page(model, image_path: str) -> str:
    image = Image.open(image_path).convert("RGB")
    # Falcon-OCR exposes its own generate(), which takes the PIL image
    # directly and returns one string per requested category.
    texts = model.generate(image, category=CATEGORY)
    return texts[0] if texts else ""


def run(pages_dir: str, out_path: str) -> str:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    image_paths = sorted(glob.glob(os.path.join(pages_dir, "*.png")))
    model = load_model(device)
    print(f"موديل Falcon جاهز | الجهاز: {device} | الصفحات: {len(image_paths)}")

    texts, t0 = [], time.time()
    for i, path in enumerate(image_paths, 1):
        page_text = ocr_page(model, path)
        texts.append(page_text)
        print(f"صفحة {i}/{len(image_paths)} — {len(page_text)} حرف")
    elapsed = time.time() - t0

    result = "\n".join(texts)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(result)
    with open(os.path.splitext(out_path)[0] + ".time", "w") as f:
        f.write(f"{elapsed}\n{len(image_paths)}")

    print(f"خلص | {elapsed:.1f}ث ({elapsed / max(1, len(image_paths)):.1f}ث/صفحة)")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", default="pages")
    ap.add_argument("--out", default="results/Falcon-OCR.txt")
    args = ap.parse_args()
    run(args.pages, args.out)
