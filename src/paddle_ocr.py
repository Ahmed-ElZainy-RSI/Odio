


from __future__ import annotations

import sys
import json
import time
import logging
import threading
from pathlib import Path
from statistics import median
from dataclasses import dataclass, field, asdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Optional

import cv2
import numpy as np
import fitz  # PyMuPDF
import requests
from PIL import Image



@dataclass
class Config:
    
    pdf_path: str = "Publisher_Copy.pdf"
    output_dir: Path = Path("book_analysis")

    # التحويل
    render_dpi: int = 300               # 300–600 أفضل للعربي المشكّل
    min_img_area: int = 5000            # تجاهل الصور الأصغر من كده (px²)

    # OCR
    lang: str = "ar"                    # كود اللغة في PaddleOCR
    use_gpu: bool = False               # فعّلها لو عندك GPU + paddlepaddle-gpu
    conf_threshold: float = 0.60        # أقل من كده → إعادة محاولة (بند 5)
    text_det_box_thresh: float = 0.5
    text_rec_score_thresh: float = 0.30


    binarize_for_ocr: bool = False
    clahe_clip: float = 2.0
    clahe_grid: int = 8
    adaptive_block: int = 31           
    adaptive_C: int = 15


    workers: int = 4              


    use_ollama: bool = False
    ollama_url: str = "http://localhost:11434/api/generate"
    ollama_model: str = "qwen2.5"


    @property
    def zoom(self) -> float:
        return self.render_dpi / 72.0

    @property
    def pages_dir(self) -> Path:
        return self.output_dir / "pages_png"

    @property
    def images_dir(self) -> Path:
        return self.output_dir / "extracted_images"



logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ArabicBookReader")



@dataclass
class OCRBlock:
    """كتلة نص واحدة من الـ OCR مع صندوقها وثقتها."""
    text: str
    confidence: float
    bbox: list[int]            # [x0, y0, x1, y1]
    retried: bool = False


@dataclass
class PageResult:
    page_number: int
    page_type: str
    width_px: int = 0
    height_px: int = 0
    ocr_text: str = ""
    ocr_confidence: float = 0.0
    blocks: list[dict] = field(default_factory=list)
    embedded_images: list[dict] = field(default_factory=list)
    image_analysis: dict = field(default_factory=dict)
    layout: dict = field(default_factory=dict)
    processing_time: float = 0.0
    png_path: str = ""
    error: str = ""


class OCREngine:


    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._lock = threading.Lock()
        log.info("Loading OCR model (PaddleOCR)…")
        from paddleocr import PaddleOCR 
        self.ocr = PaddleOCR(
            lang=cfg.lang,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=True,    
            text_det_box_thresh=cfg.text_det_box_thresh,
            text_rec_score_thresh=cfg.text_rec_score_thresh,
        )
        log.info("OCR model ready.")

    
    @staticmethod
    def _unpack(res_item: Any) -> tuple[list[str], list[float], list]:
       
        data: Any = res_item
       
        if not isinstance(res_item, dict) and hasattr(res_item, "json"):
            j = res_item.json
            if isinstance(j, dict):
                data = j.get("res", j)
        try:
            texts = list(data["rec_texts"])
            scores = [float(s) for s in data["rec_scores"]]
            polys = data.get("rec_polys")
            if polys is None:
                polys = data.get("rec_boxes", [])
            return texts, scores, list(polys)
        except Exception:
            return [], [], []

    @staticmethod
    def _poly_to_bbox(poly: Any) -> list[int]:

        arr = np.asarray(poly).reshape(-1, 2)
        x0, y0 = arr[:, 0].min(), arr[:, 1].min()
        x1, y1 = arr[:, 0].max(), arr[:, 1].max()
        return [int(x0), int(y0), int(x1), int(y1)]

  
    @staticmethod
    def sort_reading_order(blocks: list[OCRBlock]) -> list[OCRBlock]:
        if not blocks:
            return blocks
        heights = [b.bbox[3] - b.bbox[1] for b in blocks if b.bbox[3] > b.bbox[1]]
        line_tol = (median(heights) * 0.6) if heights else 20.0
        rows: list[dict] = []
        for b in sorted(blocks, key=lambda b: b.bbox[1]):
            cy = (b.bbox[1] + b.bbox[3]) / 2.0
            placed = False
            for row in rows:
                if abs(row["cy"] - cy) <= line_tol:
                    row["items"].append(b)
                    n = len(row["items"])
                    row["cy"] = (row["cy"] * (n - 1) + cy) / n
                    placed = True
                    break
            if not placed:
                rows.append({"cy": cy, "items": [b]})
        rows.sort(key=lambda r: r["cy"])
        ordered: list[OCRBlock] = []
        for row in rows:
       
            ordered.extend(sorted(
                row["items"],
                key=lambda b: (b.bbox[0] + b.bbox[2]) / 2.0,
                reverse=True,
            ))
        return ordered

    def _run_predict(self, image: np.ndarray) -> tuple[list[str], list[float], list]:
        with self._lock:                    
            result = self.ocr.predict(image)
        if not result:
            return [], [], []
        return self._unpack(result[0])

  
    def _retry_block(self, full_img: np.ndarray, bbox: list[int]) -> Optional[OCRBlock]:
        pad = 6
        h, w = full_img.shape[:2]
        x0 = max(0, bbox[0] - pad); y0 = max(0, bbox[1] - pad)
        x1 = min(w, bbox[2] + pad); y1 = min(h, bbox[3] + pad)
        if x1 <= x0 or y1 <= y0:
            return None
        crop = full_img[y0:y1, x0:x1]
        texts, scores, _ = self._run_predict(crop)
        if not texts:
            return None
        text = " ".join(texts).strip()
        conf = float(np.mean(scores)) if scores else 0.0
        return OCRBlock(text=text, confidence=round(conf, 4), bbox=bbox, retried=True)

  
    def recognize(self, ocr_input: np.ndarray, full_img: np.ndarray
                  ) -> tuple[str, float, list[OCRBlock]]:
     
        texts, scores, polys = self._run_predict(ocr_input)
        blocks: list[OCRBlock] = []
        for i, txt in enumerate(texts):
            if not txt or not txt.strip():
                continue
            conf = scores[i] if i < len(scores) else 0.0
            bbox = self._poly_to_bbox(polys[i]) if i < len(polys) else [0, 0, 0, 0]
            block = OCRBlock(text=txt.strip(), confidence=round(float(conf), 4), bbox=bbox)
            # 5) إعادة محاولة لو الثقة أقل من الحد
            if block.confidence < self.cfg.conf_threshold and any(bbox):
                retried = self._retry_block(full_img, bbox)
                if retried and retried.confidence > block.confidence:
                    block = retried
            blocks.append(block)

        blocks = self.sort_reading_order(blocks)
        text = "\n".join(b.text for b in blocks)
        avg = float(np.mean([b.confidence for b in blocks])) if blocks else 0.0
        return text, round(avg, 4), blocks



def render_page_to_array(page: fitz.Page, cfg: Config) -> np.ndarray:
    """يحوّل صفحة PDF إلى مصفوفة BGR بدقة render_dpi."""
    mat = fitz.Matrix(cfg.zoom, cfg.zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


# ═════════════════════════════════════════════════════════════
# 2) Preprocessing pipeline
#   Gray → Deskew → Denoise → CLAHE → (Adaptive+Morph optional) → Sharpen
# ═════════════════════════════════════════════════════════════
def _deskew(gray: np.ndarray, max_angle: float = 5.0) -> np.ndarray:
    """تصحيح ميل بسيط (≤ max_angle درجة) عبر Hough."""
    try:
        edges = cv2.Canny(gray, 50, 150, apertureSize=3)
        lines = cv2.HoughLines(edges, 1, np.pi / 180, 200)
        if lines is None:
            return gray
        angles = []
        for line in lines[:30]:
            _, theta = line[0]
            angle = (theta * 180 / np.pi) - 90
            if abs(angle) <= max_angle:
                angles.append(angle)
        if not angles:
            return gray
        med = float(np.median(angles))
        if abs(med) < 0.3:
            return gray
        h, w = gray.shape
        M = cv2.getRotationMatrix2D((w / 2, h / 2), med, 1.0)
        return cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_REPLICATE)
    except Exception:
        return gray


def preprocess_for_ocr(bgr: np.ndarray, cfg: Config) -> np.ndarray:


    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)


    h, w = gray.shape
    if min(h, w) < 1000:
        s = 1000 / min(h, w)
        gray = cv2.resize(gray, None, fx=s, fy=s, interpolation=cv2.INTER_CUBIC)

    gray = _deskew(gray)
    gray = cv2.fastNlMeansDenoising(gray, h=10, templateWindowSize=7, searchWindowSize=21)

    clahe = cv2.createCLAHE(clipLimit=cfg.clahe_clip,
                            tileGridSize=(cfg.clahe_grid, cfg.clahe_grid))
    gray = clahe.apply(gray)

    if cfg.binarize_for_ocr:
        binary = cv2.adaptiveThreshold(
            gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY,
            cfg.adaptive_block, cfg.adaptive_C,
        )
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
        out = binary
    else:
  
        blur = cv2.GaussianBlur(gray, (0, 0), 3)
        out = cv2.addWeighted(gray, 1.4, blur, -0.4, 0)

    return cv2.cvtColor(out, cv2.COLOR_GRAY2BGR)


def classify_page(bgr: np.ndarray, ocr_text: str, page_num: int) -> str:
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    color_ratio = float(np.mean(hsv[:, :, 1] > 50)) * 100
    text_chars = len("".join(ocr_text.split()))

    if page_num == 1:
        return "cover"
    if page_num == 2:
        return "cover_back_or_title"
    if page_num == 3:
        return "info_page"
    if "خريطة" in ocr_text:
        return "map"
    if color_ratio > 40 and text_chars < 100:
        return "illustration"
    if color_ratio > 25 and text_chars > 200:
        return "mixed"
    if text_chars < 30 and color_ratio < 15:
        return "blank_or_decorative"
    if text_chars > 300:
        return "text"
    return "mixed"


def analyze_image_content(bgr: np.ndarray) -> dict:
    result: dict[str, Any] = {}
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    result["brightness_mean"] = round(float(np.mean(gray)), 2)
    result["brightness_std"] = round(float(np.std(gray)), 2)
    try:
        small = cv2.resize(bgr, (150, 150))
        pixels = small.reshape(-1, 3).astype(np.float32)
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
        _, labels, centers = cv2.kmeans(pixels, 5, None, criteria, 3,
                                        cv2.KMEANS_RANDOM_CENTERS)
        counts = np.bincount(labels.flatten())
        total = counts.sum()
        palette = []
        for i in np.argsort(-counts):
            b, g, r = centers[i]
            palette.append({"hex": "#{:02X}{:02X}{:02X}".format(int(r), int(g), int(b)),
                            "percent": round(float(counts[i] / total * 100), 1)})
        result["dominant_colors"] = palette
    except Exception as e:
        result["dominant_colors"] = [{"error": str(e)}]
    try:
        edges = cv2.Canny(cv2.GaussianBlur(gray, (5, 5), 0), 50, 150)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        result["contour_count"] = len(contours)
        result["complexity"] = ("high" if len(contours) > 800
                                else "medium" if len(contours) > 200 else "low")
    except Exception:
        result["contour_count"] = -1
        result["complexity"] = "unknown"
    try:
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        skin = cv2.inRange(hsv, np.array([0, 20, 70], np.uint8),
                           np.array([30, 255, 255], np.uint8))
        ratio = float(np.mean(skin > 0)) * 100
        result["skin_tone_ratio_pct"] = round(ratio, 2)
        result["has_human_figure"] = ratio > 3.0
    except Exception:
        result["has_human_figure"] = False
    return result


def extract_embedded_images(fitz_page: fitz.Page, doc: fitz.Document,
                            page_num: int, cfg: Config) -> list[dict]:
    saved: list[dict] = []
    try:
        for idx, img in enumerate(fitz_page.get_images(full=True)):
            xref = img[0]
            try:
                base = fitz.Pixmap(doc, xref)
                pix = base if base.n in (1, 3) else fitz.Pixmap(fitz.csRGB, base)
                if pix.width * pix.height < cfg.min_img_area:
                    continue
                fname = cfg.images_dir / f"page{page_num:02d}_img{idx:02d}.png"
                pix.save(str(fname))
            
                rects = fitz_page.get_image_rects(xref)
                pos = None
                if rects:
                    r = rects[0]
                    pos = {"x": round(r.x0, 1), "y": round(r.y0, 1)}
                saved.append({
                    "path": str(fname), "page": page_num,
                    "width": pix.width, "height": pix.height, "position": pos,
                })
            except Exception as e:
                log.debug(f"  skip image xref={xref}: {e}")
    except Exception as e:
        log.warning(f"  Could not extract images on page {page_num}: {e}")
    return saved


# ═════════════════════════════════════════════════════════════
# 7) Ollama post-processing — تصحيح فقط، مع الحفاظ على التشكيل
# ═════════════════════════════════════════════════════════════
_OLLAMA_SYSTEM = """أنت مدقّق OCR للنص العربي. مهمتك تصحيح أخطاء المسح الضوئي فقط.
قواعد صارمة:
- أخرج النص المصحّح فقط، بدون أي شرح أو مقدمة.
- لا تضف ولا تحذف أي كلمة أو جملة.
- حافظ على كل علامات التشكيل (الحركات) كما هي: َ ُ ِ ّ ْ ً ٌ ٍ
- حافظ على علامات الترقيم وترتيب الفقرات.
- صحّح فقط: الحروف المقطوعة، الكلمات المكسورة، الهمزات في غير موضعها،
  والحروف المتشابهة الناتجة عن OCR (ة/ه، ي/ى، ا/أ/إ/آ)."""


def _ollama_available(cfg: Config) -> bool:
    try:
        r = requests.get(cfg.ollama_url.replace("/api/generate", "/api/tags"), timeout=3)
        return r.status_code == 200
    except Exception:
        return False


def correct_with_ollama(text: str, cfg: Config) -> str:
    """تصحيح نص صفحة واحدة عبر Ollama (لو متاح)، وإلا يرجّع النص كما هو."""
    if not text.strip():
        return text
    prompt = f"{_OLLAMA_SYSTEM}\n\nالنص:\n---\n{text}\n---\nالنص المصحّح:"
    try:
        r = requests.post(cfg.ollama_url, timeout=180, json={
            "model": cfg.ollama_model, "prompt": prompt, "stream": False,
            "options": {"temperature": 0.05, "top_p": 0.9,
                        "repeat_penalty": 1.1, "num_predict": 2048},
        })
        if r.status_code == 200:
            out = r.json().get("response", "").strip()
            return out if len(out) >= max(10, len(text) * 0.3) else text
    except Exception as e:
        log.warning(f"  Ollama correction failed: {e}")
    return text


# ═════════════════════════════════════════════════════════════
# معالجة صفحة واحدة (بدون OCR — الجزء اللي بيتوازى)
# ═════════════════════════════════════════════════════════════
def process_page_visual(doc: fitz.Document, page_idx: int, cfg: Config
                        ) -> tuple[PageResult, np.ndarray, np.ndarray]:
    page_num = page_idx + 1
    result = PageResult(page_number=page_num, page_type="unknown")
    page = doc[page_idx]

    log.info(f"  [p{page_num}] Rendering page…")
    bgr = render_page_to_array(page, cfg)
    result.height_px, result.width_px = bgr.shape[:2]

    png_path = cfg.pages_dir / f"page_{page_num:02d}.png"
    cv2.imwrite(str(png_path), bgr)
    result.png_path = str(png_path)

    log.info(f"  [p{page_num}] Image analysis…")
    result.image_analysis = analyze_image_content(bgr)

    log.info(f"  [p{page_num}] Preprocessing…")
    ocr_input = preprocess_for_ocr(bgr, cfg)

    result.embedded_images = extract_embedded_images(page, doc, page_num, cfg)
    return result, ocr_input, bgr



def process_pdf(cfg: Config, engine: OCREngine) -> list[PageResult]:
    for d in (cfg.output_dir, cfg.pages_dir, cfg.images_dir):
        d.mkdir(parents=True, exist_ok=True)

    doc = fitz.open(cfg.pdf_path)
    total = len(doc)
    log.info(f"Opened PDF: {cfg.pdf_path} — {total} pages")

    results: dict[int, PageResult] = {}
    timers: dict[int, float] = {}


    def _visual(idx: int):
        t0 = time.time()
        res, ocr_input, bgr = process_page_visual(doc, idx, cfg)
        return idx, res, ocr_input, bgr, t0

    with ThreadPoolExecutor(max_workers=cfg.workers) as ex:
        futures = [ex.submit(_visual, i) for i in range(total)]
        for fut in as_completed(futures):
            try:
                idx, res, ocr_input, bgr, t0 = fut.result()
            except Exception as e:
                log.error(f"  visual stage failed: {e}")
                continue
            page_num = idx + 1
            try:
                log.info(f"  [p{page_num}] Running OCR…")
                text, conf, blocks = engine.recognize(ocr_input, bgr)

                # 7) تصحيح اختياري
                if cfg.use_ollama and text.strip():
                    log.info(f"  [p{page_num}] Post-processing (Ollama)…")
                    text = correct_with_ollama(text, cfg)

                res.ocr_text = text
                res.ocr_confidence = conf
                res.blocks = [asdict(b) for b in blocks]
                res.layout = {
                    "n_text_blocks": len(blocks),
                    "n_images": len(res.embedded_images),
                    "low_conf_blocks": sum(1 for b in blocks
                                           if b.confidence < cfg.conf_threshold),
                }
                res.page_type = classify_page(bgr, text, page_num)
            except Exception as e:
                res.error = str(e)
                log.error(f"  ERROR on page {page_num}: {e}")

            res.processing_time = round(time.time() - t0, 2)
            results[page_num] = res
            timers[page_num] = res.processing_time
            log.info(f"  [p{page_num}] Done — type={res.page_type} "
                     f"conf={res.ocr_confidence:.2f} blocks={len(res.blocks)} "
                     f"{res.processing_time:.1f}s")

    doc.close()
    return [results[k] for k in sorted(results)]



def build_reports(results: list[PageResult], cfg: Config) -> None:
    # JSON
    payload = {
        "source_pdf": cfg.pdf_path,
        "total_pages": len(results),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "config": {"render_dpi": cfg.render_dpi, "lang": cfg.lang,
                   "conf_threshold": cfg.conf_threshold, "use_ollama": cfg.use_ollama},
        "pages": [asdict(r) for r in results],
    }
    (cfg.output_dir / "full_report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    # TXT
    txt: list[str] = ["=" * 70, "  تقرير قراءة الكتاب",
                      f"  المصدر: {cfg.pdf_path}", f"  الصفحات: {len(results)}", "=" * 70, ""]
    for r in results:
        txt.append("─" * 60)
        txt.append(f"صفحة {r.page_number} | نوع: {r.page_type} | ثقة: {r.ocr_confidence:.2f}")
        if r.ocr_text.strip():
            txt.append(r.ocr_text)
        else:
            txt.append("[لا يوجد نص — صورة توضيحية]")
        txt.append("")
    (cfg.output_dir / "full_report_arabic.txt").write_text("\n".join(txt), encoding="utf-8")

    # Markdown
    md: list[str] = [f"# تقرير قراءة الكتاب", "",
                     f"- **المصدر:** `{cfg.pdf_path}`",
                     f"- **الصفحات:** {len(results)}",
                     f"- **التاريخ:** {time.strftime('%Y-%m-%d %H:%M:%S')}", ""]
    for r in results:
        md.append(f"## صفحة {r.page_number} — {r.page_type}")
        md.append("")
        md.append(f"- الأبعاد: {r.width_px}×{r.height_px} | ثقة OCR: **{r.ocr_confidence:.2f}** "
                  f"| كتل: {len(r.blocks)} | زمن: {r.processing_time}s")
        if r.embedded_images:
            md.append(f"- صور مضمّنة: {len(r.embedded_images)}")
        if r.error:
            md.append(f"- ⚠ خطأ: `{r.error}`")
        md.append("")
        if r.ocr_text.strip():
            md.append("> " + r.ocr_text.replace("\n", "\n> "))
        else:
            md.append("_[لا يوجد نص — صورة توضيحية]_")
        md.append("")
    (cfg.output_dir / "full_report.md").write_text("\n".join(md), encoding="utf-8")

    log.info(f"Reports saved in: {cfg.output_dir}/  (json, txt, md)")


def print_summary(results: list[PageResult], cfg: Config) -> None:
    print("\n" + "=" * 60)
    print(" 📊 ملخص التحليل")
    print("=" * 60)
    types: dict[str, int] = {}
    total_chars = 0
    for r in results:
        types[r.page_type] = types.get(r.page_type, 0) + 1
        total_chars += len(r.ocr_text.strip())
    print(f" الصفحات      : {len(results)}")
    print(f" إجمالي النص  : {total_chars} حرف")
    for pt, c in sorted(types.items(), key=lambda x: -x[1]):
        print(f"   {pt:<24} {c}")
    print(f" المخرجات في  : {cfg.output_dir}/")
    print("=" * 60)


def main() -> None:
    cfg = Config()
    if len(sys.argv) > 1:
        cfg.pdf_path = sys.argv[1]

    if not Path(cfg.pdf_path).exists():
        log.error(f"الملف غير موجود: {cfg.pdf_path}")
        log.error("الاستخدام: python book_reader.py input.pdf")
        sys.exit(1)

    if cfg.use_ollama and not _ollama_available(cfg):
        log.warning("Ollama غير متاح — سيتم التخطّي والحفظ بدون تصحيح.")
        cfg.use_ollama = False

    engine = OCREngine(cfg)
    results = process_pdf(cfg, engine)
    build_reports(results, cfg)
    print_summary(results, cfg)
    log.info("Completed ✅")


if __name__ == "__main__":
    main()