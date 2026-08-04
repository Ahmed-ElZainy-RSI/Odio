"""Run dots.ocr over a directory of page images and write the joined text plus
a structured per-block JSON, following the same shape as ocr_glm.py
(--pages / --out, a .time sidecar).

Converted from src/dotocr/dot.ipynb. That notebook grew three overlapping
versions of the same loop (cells 5, 9 and 10); this consolidates the last one,
which is the only one that wrote both book.json and book.txt.

Two behaviours from the notebook are kept because they earn their keep on long
books:
  - per-page JSON caching, so an interrupted run resumes instead of re-OCRing
    pages that already succeeded (--results holds the cache).
  - a repetition guard: dots.ocr sometimes falls into a loop and emits the same
    fragment forever, so a stuck page is retried once with the simpler
    text-only prompt.

Colab-only steps from the notebook (pip installs, !zip, files.download, and an
`assert len(pages) == 49`) are dropped.
"""

import argparse
import json
import os
import re
import time
from pathlib import Path

import torch
from huggingface_hub import snapshot_download
from qwen_vl_utils import process_vision_info
from transformers import AutoModelForCausalLM, AutoProcessor

MODEL_PATH = "dots-studio/dots.ocr"
WEIGHTS_DIR = "./weights/DotsOCR"
MAX_PIXELS = 1000000
SKIP = {"Picture", "Page-header", "Page-footer"}

LAYOUT = """Please output the layout information from the PDF image, including each layout element's bbox, its category, and the corresponding text content within the bbox.

1. Bbox format: [x1, y1, x2, y2]

2. Layout Categories: The possible categories are ['Caption', 'Footnote', 'Formula', 'List-item', 'Page-footer', 'Page-header', 'Picture', 'Section-header', 'Table', 'Text', 'Title'].

3. Text Extraction & Formatting Rules:
    - Picture: For the 'Picture' category, the text field should be omitted.
    - Formula: Format its text as LaTeX.
    - Table: Format its text as HTML.
    - All Others (Text, Title, etc.): Format their text as Markdown.

4. Constraints:
    - The output text must be the original text from the image, with no translation.
    - All layout elements must be sorted according to human reading order.

5. Final Output: The entire output must be a single JSON object.
"""

OCR_ONLY = "Extract the text content from this image."


def load_model(device: str):
    model_path = snapshot_download(MODEL_PATH, local_dir=WEIGHTS_DIR)
    # bfloat16 needs compute capability 8.0+ (Ampere and newer); older cards
    # fall back to fp16. The notebook also computed a flash_attention_2 option
    # here but then passed sdpa anyway, so sdpa is what it actually ran with.
    use_bf16 = device == "cuda" and torch.cuda.get_device_capability()[0] >= 8
    model = AutoModelForCausalLM.from_pretrained(
        model_path,
        attn_implementation="sdpa",
        torch_dtype=torch.bfloat16 if use_bf16 else torch.float16,
        device_map="auto",
        trust_remote_code=True,
    ).eval()
    processor = AutoProcessor.from_pretrained(model_path, trust_remote_code=True)
    return processor, model


def looks_stuck(text: str) -> bool:
    """True when the model has fallen into a repetition loop."""
    if not text:
        return True
    if re.search(r"(.{6,60}?)\1{4,}", text):
        return True
    return len(set(text[-400:])) < 8 and len(text) > 2000


@torch.no_grad()
def ocr_page(processor, model, image_path, prompt: str, max_tokens: int = 6000) -> str:
    messages = [{
        "role": "user",
        "content": [
            {"type": "image", "image": str(image_path), "max_pixels": MAX_PIXELS},
            {"type": "text", "text": prompt},
        ],
    }]

    text_input = processor.apply_chat_template(
        messages, tokenize=False, add_generation_prompt=True
    )
    images, videos = process_vision_info(messages)

    inputs = processor(
        text=[text_input], images=images, videos=videos,
        padding=True, return_tensors="pt",
    ).to(model.device)

    gen = model.generate(**inputs, max_new_tokens=max_tokens, do_sample=False)
    trimmed = gen[0][inputs.input_ids.shape[1]:]
    return processor.decode(trimmed, skip_special_tokens=True)


def parse(raw: str) -> list:
    """Model output as a list of layout blocks. Falls back to one text block
    when the response isn't the JSON array it was asked for."""
    try:
        return json.loads(raw[raw.index("["):raw.rindex("]") + 1])
    except Exception:
        return [{"bbox": None, "category": "Text", "text": raw.strip()}]


def blocks_to_text(blocks: list) -> str:
    return "\n\n".join(
        b["text"].strip() for b in blocks
        if b.get("category") not in SKIP and (b.get("text") or "").strip()
    )


def page_number(path: Path) -> int:
    """page_007.png -> 7, falling back to 0 when the name doesn't carry one."""
    match = re.search(r"(\d+)", path.stem)
    return int(match.group(1)) if match else 0


def run(pages_dir: str, out_path: str, results_dir: str) -> str:
    device = "cuda" if torch.cuda.is_available() else "cpu"
    image_paths = sorted(Path(pages_dir).glob("*.png"))
    cache_dir = Path(results_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    processor, model = load_model(device)
    print(f"موديل dots.ocr جاهز | الجهاز: {device} | الصفحات: {len(image_paths)}")

    book, t0 = [], time.time()
    for i, path in enumerate(image_paths, 1):
        cache = cache_dir / f"{path.stem}.json"

        if cache.exists():
            page = json.loads(cache.read_text(encoding="utf-8"))
            print(f"صفحة {i}/{len(image_paths)} — من الكاش ({len(page['text'])} حرف)")
        else:
            raw = ocr_page(processor, model, path, LAYOUT)
            if looks_stuck(raw):
                print(f"  ↻ {path.name}: تكرار — إعادة")
                raw = ocr_page(processor, model, path, OCR_ONLY, 3000)
            blocks = parse(raw)
            page = {
                "page": page_number(path),
                "image": path.name,
                "blocks": blocks,
                "text": blocks_to_text(blocks),
            }
            cache.write_text(
                json.dumps(page, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            print(f"صفحة {i}/{len(image_paths)} — {len(page['text'])} حرف")

        book.append(page)
    elapsed = time.time() - t0

    result = "\n\n---\n\n".join(f"[صفحة {pg['page']}]\n\n{pg['text']}" for pg in book)
    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(result)
    # The structured blocks (bboxes + categories) are the reason to run this
    # model over a plain OCR pass, so they're saved next to the flat text.
    with open(os.path.splitext(out_path)[0] + ".json", "w", encoding="utf-8") as f:
        json.dump(book, f, ensure_ascii=False, indent=2)
    with open(os.path.splitext(out_path)[0] + ".time", "w") as f:
        f.write(f"{elapsed}\n{len(image_paths)}")

    print(f"خلص | {elapsed:.1f}ث ({elapsed / max(1, len(image_paths)):.1f}ث/صفحة)")
    return result


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--pages", default="pages")
    ap.add_argument("--out", default="results/dots.ocr.txt")
    ap.add_argument("--results", default="results", help="per-page cache for resuming")
    args = ap.parse_args()
    run(args.pages, args.out, args.results)
