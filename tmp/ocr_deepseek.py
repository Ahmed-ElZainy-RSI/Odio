


import argparse
import glob
import os
import time

import torch
from transformers import AutoModel, AutoTokenizer

MODEL_NAME = "deepseek-ai/DeepSeek-OCR"
PROMPT = "<image>\n<|grounding|>Convert the document to markdown. "


def load_model(device: str):
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, trust_remote_code=True)
    model = AutoModel.from_pretrained(
        MODEL_NAME,
        _attn_implementation="eager",
        trust_remote_code=True,
        use_safetensors=True,
        low_cpu_mem_usage=True,
        torch_dtype=torch.bfloat16 if device == "cuda" else torch.float32,
    ).eval().to(device)
    return tokenizer, model


def read_result(out_dir: str, fallback) -> str:
    files = (glob.glob(f"{out_dir}/*.mmd")
             + glob.glob(f"{out_dir}/*.txt")
             + glob.glob(f"{out_dir}/*.md"))
    if files:
        return open(files[0], encoding="utf-8").read()
    return fallback if isinstance(fallback, str) else ""


def run(pages_dir: str, out_path: str, work_dir: str = "ds_out") -> str:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    image_paths = sorted(glob.glob(os.path.join(pages_dir, "*.png")))
    tokenizer, model = load_model(device)
    print(f"موديل DeepSeek جاهز | الجهاز: {device} | الصفحات: {len(image_paths)}")

    texts, t0 = [], time.time()
    for i, path in enumerate(image_paths, 1):
        page_dir = f"{work_dir}/page_{i:03d}"
        os.makedirs(page_dir, exist_ok=True)
        raw = model.infer(
            tokenizer,
            prompt=PROMPT,
            image_file=path,
            output_path=page_dir,
            base_size=1024,
            image_size=768,
            crop_mode=True,
            save_results=True,
        )
        page_text = read_result(page_dir, raw)
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
    ap.add_argument("--out", default="results/DeepSeek-OCR.txt")
    args = ap.parse_args()
    run(args.pages, args.out)
