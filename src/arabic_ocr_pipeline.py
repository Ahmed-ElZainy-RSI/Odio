"""
╔══════════════════════════════════════════════════════════════╗
║         Arabic OCR Pipeline — قراءة PDF عربي بالتشكيل       ║
╠══════════════════════════════════════════════════════════════╣
║  الموديلات:                                                   ║
║  • NAMAA-Space/Qari-OCR-0.2.2.1-Arabic-2B-Instruct           ║
║    أفضل موديل OCR عربي مفتوح المصدر (CER=5.9%)              ║
║    يقرأ التشكيل مباشرةً من الصورة                            ║
║  • CAMeL-Lab/bert-base-arabic-camelbert-msa-diacritization   ║
║    يُضيف تشكيلاً على أي كلمة فاتت الموديل الأول              ║
╠══════════════════════════════════════════════════════════════╣
║  التثبيت (مرة واحدة):                                        ║
║  pip install transformers qwen_vl_utils accelerate           ║
║  pip install bitsandbytes peft pdf2image pillow torch        ║
╚══════════════════════════════════════════════════════════════╝

الاستخدام:
  python arabic_ocr_pipeline.py --pdf "Publisher Copy.pdf"
  python arabic_ocr_pipeline.py --pdf "book.pdf" --pages 3-10
  python arabic_ocr_pipeline.py --pdf "book.pdf" --pages 3-24 --output out.txt
  python arabic_ocr_pipeline.py --pdf "book.pdf" --no-diac
"""

import argparse
import gc
import json
import os
import re
import sys
import unicodedata
import warnings
from pathlib import Path
from typing import Optional

warnings.filterwarnings("ignore")
os.environ["TOKENIZERS_PARALLELISM"] = "false"


# ── صور وPDF ─────────────────────────────────────────────────
from pdf2image import convert_from_path
from PIL import Image
import torch

# ── Transformers ─────────────────────────────────────────────
from transformers import (
    Qwen2VLForConditionalGeneration,
    AutoProcessor,
    AutoTokenizer,
    AutoModelForTokenClassification,
    pipeline as hf_pipeline,
)

try:
    from qwen_vl_utils import process_vision_info
    QWEN_VL_UTILS = True
except ImportError:
    QWEN_VL_UTILS = False
    print("[تحذير] qwen_vl_utils غير مثبَّت — شغّل: pip install qwen_vl_utils")


# ═══════════════════════════════════════════════════════════════
# ثوابت
# ═══════════════════════════════════════════════════════════════
QARI_MODEL  = "NAMAA-Space/Qari-OCR-0.2.2.1-VL-2B-Instruct"
CAMEL_MODEL = "CAMeL-Lab/bert-base-arabic-camelbert-msa-diacritization"
ARABIC_RE   = re.compile(r"[\u0600-\u06FF\u0750-\u077F\uFB50-\uFDFF\uFE70-\uFEFF]")
MAX_IMG_DIM = 1600
MAX_TOKENS  = 2000

# الـ prompt الرسمي المُوصى به من ورقة QARI
QARI_PROMPT = (
    "Below is the image of one page of a document, "
    "as well as some raw textual content that was previously extracted for it. "
    "Just return the plain text representation of this document as if you were "
    "reading it naturally. Do not hallucinate."
)


# ═══════════════════════════════════════════════════════════════
# أدوات مساعدة
# ═══════════════════════════════════════════════════════════════
def get_device() -> str:
    if torch.cuda.is_available():
        vram = torch.cuda.get_device_properties(0).total_memory / 1e9
        print(f"[جهاز] GPU ✓  VRAM: {vram:.1f} GB")
        return "cuda"
    print("[جهاز] CPU — الموديل سيعمل أبطأ لكن بشكل صحيح")
    return "cpu"


def resize_image(img: Image.Image, max_dim: int = MAX_IMG_DIM) -> Image.Image:
    w, h = img.size
    if max(w, h) <= max_dim:
        return img
    scale = max_dim / max(w, h)
    return img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)


def parse_page_range(s: str) -> Optional[tuple]:
    if not s:
        return None
    parts = s.strip().split("-")
    first = int(parts[0])
    last  = int(parts[1]) if len(parts) > 1 else first
    return (first, last)


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# ═══════════════════════════════════════════════════════════════
# 1. تحويل PDF إلى صور
# ═══════════════════════════════════════════════════════════════
def pdf_to_images(pdf_path: str, dpi: int = 200,
                  page_range: Optional[tuple] = None) -> list:
    kwargs = {"dpi": dpi, "fmt": "RGB"}
    if page_range:
        kwargs["first_page"] = page_range[0]
        kwargs["last_page"]  = page_range[1]

    print(f"\n[PDF] تحويل '{Path(pdf_path).name}' بدقة {dpi} DPI …")
    images = convert_from_path(pdf_path, **kwargs)
    print(f"[PDF] ✓ {len(images)} صفحة محوَّلة")
    return images


# ═══════════════════════════════════════════════════════════════
# 2. QARI-OCR — المحرك الرئيسي
# ═══════════════════════════════════════════════════════════════
class QARIEngine:
    """
    QARI-OCR-0.2.2.1 — أفضل موديل OCR عربي مفتوح المصدر
    -------------------------------------------------------
    - مبني على Qwen2-VL-2B
    - مُدرَّب على 50,000 صورة عربية بتشكيل كامل
    - CER=5.9%  WER=22.1%  BLEU=0.597
    - يدعم 12 خط عربي مختلف، أحجام 14-40pt
    - يقرأ الصفحة كاملة (full-page OCR)
    """

    def __init__(self, device: str):
        self.device = device
        print(f"\n[QARI] تحميل: {QARI_MODEL}")
        print("[QARI] أول تشغيل: التحميل يستغرق 5-15 دقيقة حسب الإنترنت")
        print("[QARI] بعد أول تشغيل: يعمل من الكاش المحلي فوراً …")

        token = os.environ.get("HF_TOKEN")

        # الموديل ده PEFT adapter فوق unsloth/Qwen2-VL-2B
        # لازم نحمّل الـ base model الأول ثم نضيف الـ adapter
        BASE_MODEL = "unsloth/Qwen2-VL-2B-Instruct-unsloth-bnb-4bit"

        print(f"[QARI] تحميل base model: {BASE_MODEL} …")
        from peft import PeftModel

        if device == "cuda":
            base = Qwen2VLForConditionalGeneration.from_pretrained(
                BASE_MODEL,
                torch_dtype=torch.float16,
                device_map="auto",
                token=token,
            )
        else:
            base = Qwen2VLForConditionalGeneration.from_pretrained(
                BASE_MODEL,
                torch_dtype=torch.float32,
                device_map="cpu",
                token=token,
            )

        print(f"[QARI] تحميل adapter: {QARI_MODEL} …")
        self.model = PeftModel.from_pretrained(base, QARI_MODEL, token=token)
        self.model.eval()

        self.processor = AutoProcessor.from_pretrained(BASE_MODEL, token=token)
        print("[QARI] ✓ الموديل جاهز للقراءة")

    def read_page(self, image: Image.Image, page_num: int) -> str:
        """يقرأ صفحة ويُرجع النص العربي مع التشكيل."""
        img      = resize_image(image)
        tmp_path = os.path.abspath(f"_tmp_page_{page_num}.png")
        img.save(tmp_path, format="PNG")

        try:
            result = self._run_model(tmp_path)
        except Exception as e:
            result = ""
            print(f"  [QARI] تحذير صفحة {page_num}: {str(e)[:80]}")
        finally:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)

        return result

    def _run_model(self, image_path: str) -> str:
        if not QWEN_VL_UTILS:
            raise RuntimeError("pip install qwen_vl_utils")

        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "image", "image": f"file://{image_path}"},
                    {"type": "text",  "text":  QARI_PROMPT},
                ],
            }
        ]

        text_input = self.processor.apply_chat_template(
            messages, tokenize=False, add_generation_prompt=True
        )
        image_inputs, video_inputs = process_vision_info(messages)
        inputs = self.processor(
            text=[text_input],
            images=image_inputs,
            videos=video_inputs,
            padding=True,
            return_tensors="pt",
        ).to(self.device)

        with torch.no_grad():
            generated_ids = self.model.generate(
                **inputs,
                max_new_tokens=MAX_TOKENS,
                do_sample=False,
                temperature=None,
                top_p=None,
            )

        trimmed = [
            out[len(inp):]
            for inp, out in zip(inputs.input_ids, generated_ids)
        ]
        output = self.processor.batch_decode(
            trimmed,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )[0]

        # تنظيف الذاكرة
        del inputs, generated_ids, trimmed
        if self.device == "cuda":
            torch.cuda.empty_cache()
        gc.collect()

        return output.strip()


# ═══════════════════════════════════════════════════════════════
# 3. CAMeL-Lab — تشكيل تكميلي
# ═══════════════════════════════════════════════════════════════
class DiacritizationEngine:
    """
    CAMeL-Lab Arabic Diacritization (BERT-based)
    ---------------------------------------------
    يُكمّل التشكيل على الكلمات التي لم يُشكّلها QARI.
    يعمل على CPU بسرعة معقولة.
    """

    def __init__(self):
        print(f"\n[تشكيل] تحميل: {CAMEL_MODEL} …")
        try:
            token = os.environ.get("HF_TOKEN")
            self.tokenizer = AutoTokenizer.from_pretrained(CAMEL_MODEL, token=token)
            self.model = AutoModelForTokenClassification.from_pretrained(CAMEL_MODEL, token=token)
            self.pipe = hf_pipeline(
                "token-classification",
                model=self.model,
                tokenizer=self.tokenizer,
                aggregation_strategy="none",
                device=-1,
            )
            self.available = True
            print("[تشكيل] ✓ جاهز")
        except Exception as e:
            print(f"[تشكيل] ✗ {str(e)[:100]}")
            self.available = False

    def diacritize(self, text: str) -> str:
        if not self.available or not text.strip():
            return text

        result = []
        for line in text.splitlines():
            if not line.strip() or not ARABIC_RE.search(line):
                result.append(line)
                continue
            try:
                result.append(self._process_line(line))
            except Exception:
                result.append(line)
        return "\n".join(result)

    def _process_line(self, line: str, chunk_words: int = 150) -> str:
        words = line.split()
        if not words:
            return line
        chunks = []
        for i in range(0, len(words), chunk_words):
            chunk = " ".join(words[i : i + chunk_words])
            try:
                chunks.append(self._run_chunk(chunk))
            except Exception:
                chunks.append(chunk)
        return " ".join(chunks)

    def _run_chunk(self, text: str) -> str:
        items = self.pipe(text)
        insertions: dict = {}
        for item in items:
            label = item.get("entity", "O")
            end   = item.get("end", -1)
            if label in ("O", "PAD") or end < 0:
                continue
            diac = self._to_diac(label)
            if diac:
                insertions[end] = diac
        if not insertions:
            return text
        result = []
        for i, ch in enumerate(text):
            result.append(ch)
            if (i + 1) in insertions:
                result.append(insertions[i + 1])
        return "".join(result)

    @staticmethod
    def _to_diac(label: str) -> str:
        TABLE = {
            "fatha":    "\u064E",
            "kasra":    "\u0650",
            "damma":    "\u064F",
            "sukun":    "\u0652",
            "shadda":   "\u0651",
            "fathatan": "\u064B",
            "kasratan": "\u064D",
            "dammatan": "\u064C",
            "tanwin_f": "\u064B",
            "tanwin_k": "\u064D",
            "tanwin_d": "\u064C",
        }
        ll = label.lower().replace("-", "_")
        for k, v in TABLE.items():
            if k in ll:
                return v
        if len(label) == 1 and label in "\u064B\u064C\u064D\u064E\u064F\u0650\u0651\u0652":
            return label
        return ""


# ═══════════════════════════════════════════════════════════════
# 4. Pipeline الرئيسي
# ═══════════════════════════════════════════════════════════════
def run_pipeline(
    pdf_path: str,
    page_range_str: Optional[str] = None,
    output_path: Optional[str]    = None,
    dpi: int                      = 200,
    skip_diacritization: bool     = False,
) -> list:

    print("\n" + "═" * 60)
    print("  Arabic OCR Pipeline — QARI + CAMeL")
    print("═" * 60)

    device = get_device()

    # تحميل الموديلات
    qari  = QARIEngine(device)
    diac  = None if skip_diacritization else DiacritizationEngine()

    # تحويل PDF
    page_range = parse_page_range(page_range_str) if page_range_str else None
    images     = pdf_to_images(pdf_path, dpi=dpi, page_range=page_range)
    total      = len(images)

    # معالجة الصفحات
    all_pages: list = []
    for idx, img in enumerate(images, start=1):
        page_num = (page_range[0] + idx - 1) if page_range else idx
        end_page = (page_range[1] if page_range else total)

        print(f"\n{'─'*50}")
        print(f"  الصفحة {page_num} / {end_page}  [{idx}/{total}]")
        print(f"{'─'*50}")

        # QARI-OCR
        print("  QARI يقرأ …")
        ocr_text = qari.read_page(img, page_num)
        cleaned  = clean_text(ocr_text)
        print(f"  ✓ {len(cleaned.splitlines())} سطر")

        # تشكيل تكميلي
        if diac and diac.available and cleaned:
            print("  تشكيل تكميلي …")
            try:
                final = diac.diacritize(cleaned)
            except Exception:
                final = cleaned
        else:
            final = cleaned

        all_pages.append({"page": page_num, "text": final})

        preview = final[:200].replace("\n", " ↵ ")
        print(f"\n  {preview}{'…' if len(final) > 200 else ''}")

    # حفظ
    if not output_path:
        output_path = f"{Path(pdf_path).stem}_ocr_result.txt"

    diac_status = (
        "لا (--no-diac)" if skip_diacritization
        else ("نعم" if diac and diac.available else "لا (فشل التحميل)")
    )

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# Arabic OCR Result\n")
        f.write(f"# الملف   : {pdf_path}\n")
        f.write(f"# الموديل : {QARI_MODEL}\n")
        f.write(f"# التشكيل : {diac_status}\n")
        f.write(f"# الصفحات : {all_pages[0]['page']} → {all_pages[-1]['page']}\n")
        f.write("═" * 60 + "\n")
        for p in all_pages:
            f.write(f"\n{'═'*50}\n")
            f.write(f"الصفحة {p['page']}\n")
            f.write(f"{'═'*50}\n\n")
            f.write(p["text"])
            f.write("\n")

    json_path = str(Path(output_path).with_suffix(".json"))
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(
            {"source": pdf_path, "model": QARI_MODEL,
             "diacritization": diac_status, "pages": all_pages},
            f, ensure_ascii=False, indent=2,
        )

    print(f"\n{'═'*60}")
    print(f"  ✓ النص  : {output_path}")
    print(f"  ✓ JSON  : {json_path}")
    print("═" * 60 + "\n")
    return all_pages


# ═══════════════════════════════════════════════════════════════
# 5. نقطة الدخول
# ═══════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description="Arabic OCR — QARI-OCR + CAMeL Diacritization",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
أمثلة:
  python arabic_ocr_pipeline.py --pdf "Publisher Copy.pdf"
  python arabic_ocr_pipeline.py --pdf "book.pdf" --pages 3-10
  python arabic_ocr_pipeline.py --pdf "book.pdf" --pages 3-24 --output out.txt
  python arabic_ocr_pipeline.py --pdf "book.pdf" --no-diac
        """,
    )
    parser.add_argument("--pdf",     required=True,
                        help="مسار ملف PDF")
    parser.add_argument("--pages",   default=None,
                        help="نطاق الصفحات مثل '3-10' أو '5'")
    parser.add_argument("--output",  default=None,
                        help="مسار ملف الإخراج .txt")
    parser.add_argument("--dpi",     type=int, default=200,
                        help="دقة التحويل — افتراضي: 200")
    parser.add_argument("--no-diac", action="store_true",
                        help="تخطّى التشكيل التكميلي")
    args = parser.parse_args()

    if not os.path.isfile(args.pdf):
        print(f"\n✗ الملف غير موجود: {args.pdf}")
        print("  ضع الملف في نفس مجلد السكريبت أو أعطِ المسار الكامل.")
        sys.exit(1)

    run_pipeline(
        pdf_path=args.pdf,
        page_range_str=args.pages,
        output_path=args.output,
        dpi=args.dpi,
        skip_diacritization=args.no_diac,
    )


if __name__ == "__main__":
    main()