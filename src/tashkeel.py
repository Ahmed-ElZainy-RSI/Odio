

import argparse
import os
import sys

import torch
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from text_utils import split_sentences  # noqa: E402

MODEL_NAME = "basharalrfooh/Fine-Tashkeel"


def load_model(device: str):
    tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
    model = AutoModelForSeq2SeqLM.from_pretrained(MODEL_NAME).to(device).eval()
    return tokenizer, model


@torch.no_grad()
def add_tashkeel(text: str, tokenizer, model, device: str) -> str:
    out_lines = []
    for line in text.split("\n"):
        line = line.strip()
        if not line:
            out_lines.append("")
            continue
        parts = []
        for chunk in split_sentences(line):
            ids = tokenizer(chunk, return_tensors="pt").input_ids.to(device)
            out = model.generate(ids, max_new_tokens=min(512, ids.shape[1] * 3 + 20))
            parts.append(tokenizer.decode(
                out[0], skip_special_tokens=True, clean_up_tokenization_spaces=False
            ))
        out_lines.append(" ".join(parts))
    return "\n".join(out_lines)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("-o", "--out", default="output_tashkeel.txt")
    args = ap.parse_args()

    device = "cuda" if torch.cuda.is_available() else "cpu"
    tokenizer, model = load_model(device)
    print(f"موديل التشكيل جاهز | الجهاز: {device}")

    text = open(args.input, encoding="utf-8").read()
    result = add_tashkeel(text, tokenizer, model, device)

    with open(args.out, "w", encoding="utf-8") as f:
        f.write(result)
    print(f"اتحفظ النص المشكّل في {args.out}")
