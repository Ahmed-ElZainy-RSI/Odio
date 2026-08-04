


import argparse
import glob
import os
import time

import torch
from PIL import Image
from transformers import AutoProcessor, Qwen2_5_VLForConditionalGeneration

MODEL_PATH = "sherif1313/Arabic-GLM-OCR-v2"
PROMPT = "Text Recognition:"


def load_model(device: str):
    processor = AutoProcessor.from_pretrained(MODEL_PATH)
    model = Qwen2_5_VLForConditionalGeneration.from_pretrained(
        MODEL_PATH,
        torch_dtype=torch.float16 if device == "cuda" else torch.float32,
        device_map="auto",
    ).eval()
    return processor, model


@torch.no_grad()
def ocr_page(processor, model, image_path: str) -> str:
    messages = [{
        "role": "user",
        "content": [
            {"type": "image", "image": image_path},
            {"type": "text", "text": PROMPT},
        ],
    }]

    text_input = processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    image = Image.open(image_path).convert("RGB")

    inputs = processor(
        text=[text_input], images=[image], padding=True, return_tensors="pt"
    ).to(model.device)

    gen = model.generate(
        **inputs,
        max_new_tokens=2048,
        do_sample=False,
        repetition_penalty=1.15,   # يمنع دخول الموديل في تكرار
        no_repeat_ngram_size=3,
    )
    trimmed = gen[0][inputs["input_ids"].shape[1]:]
    return processor.decode(trimmed, skip_special_tokens=True)


def run(pages_dir: str, out_path: str) -> str:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    image_paths = sorted(glob.glob(os.path.join(pages_dir, "*.png")))
    processor, model = load_model(device)
    print(f"موديل GLM جاهز | الجهاز: {device} | الصفحات: {len(image_paths)}")

    texts, t0 = [], time.time()
    for i, path in enumerate(image_paths, 1):
        page_text = ocr_page(processor, model, path)
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
    ap.add_argument("--out", default="results/Arabic-GLM-OCR-v2.txt")
    args = ap.parse_args()
    run(args.pages, args.out)
