

import argparse
import glob
import os
import time

import torch
from transformers import AutoModel, AutoTokenizer

MODEL_NAME = "baidu/Unlimited-OCR"
PROMPT = "<image>Multi page parsing."


def load_model(device: str):
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME, trust_remote_code=True)
    model = AutoModel.from_pretrained(
        MODEL_NAME,
        trust_remote_code=True,
        use_safetensors=True,
        torch_dtype=torch.bfloat16 if device == "cuda" else torch.float32,
    ).eval()
    if device == "cuda":
        model = model.cuda()
    return tokenizer, model


def read_results(out_dir: str, fallback) -> str:
    files = (glob.glob(f"{out_dir}/**/*.mmd", recursive=True)
             + glob.glob(f"{out_dir}/**/*.md", recursive=True)
             + glob.glob(f"{out_dir}/**/*.txt", recursive=True))
    if files:
        return "\n".join(open(f, encoding="utf-8").read() for f in sorted(files))
    return fallback if isinstance(fallback, str) else str(fallback)


def run(pages_dir: str, out_path: str, work_dir: str = "ul_out") -> str:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    image_paths = sorted(glob.glob(os.path.join(pages_dir, "*.png")))
    tokenizer, model = load_model(device)
    os.makedirs(work_dir, exist_ok=True)
    print(f"موديل Unlimited جاهز | الجهاز: {device} | الصفحات: {len(image_paths)}")

    t0 = time.time()
    raw = model.infer_multi(
        tokenizer,
        prompt=PROMPT,
        image_files=image_paths,
        output_path=work_dir,
        image_size=1024,              # الوضع متعدد الصفحات بيستخدم base بس
        max_length=32768,
        no_repeat_ngram_size=35,      # كبح التكرار مدمج
        ngram_window=1024,
        save_results=True,
    )
    elapsed = time.time() - t0

    result = read_results(work_dir, raw)
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
    ap.add_argument("--out", default="results/Unlimited-OCR.txt")
    args = ap.parse_args()
    run(args.pages, args.out)
