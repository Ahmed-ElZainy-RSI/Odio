

import os
import sys
import json
import time
import logging
import re
from pathlib import Path
from dataclasses import dataclass, field, asdict

import cv2
import numpy as np
import fitz
import pytesseract
from PIL import Image

PDF_PATH      = sys.argv[1] if len(sys.argv) > 1 else "Publisher_Copy.pdf"
OUTPUT_DIR    = "book_analysis"                
PAGES_DIR     = os.path.join(OUTPUT_DIR, "pages_png")
IMAGES_DIR    = os.path.join(OUTPUT_DIR, "extracted_images")
REPORT_JSON   = os.path.join(OUTPUT_DIR, "full_report.json")
REPORT_TXT    = os.path.join(OUTPUT_DIR, "full_report_arabic.txt")

RENDER_ZOOM   = 3
OCR_LANG      = "ara"
OCR_CONFIG    = "--psm 3 --oem 1"
MIN_IMG_AREA  = 5000

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
log = logging.getLogger("ArabicBookReader")


@dataclass
class PageResult:
    page_number: int
    page_type: str
    width_px: int  = 0
    height_px: int = 0
    ocr_text: str  = ""
    ocr_confidence: float = 0.0
    embedded_images: list = field(default_factory=list)
    image_analysis: dict  = field(default_factory=dict)
    png_path: str  = ""
    error: str     = ""


def ensure_dirs(*dirs):
    for d in dirs:
        os.makedirs(d, exist_ok=True)


def render_page_to_array(page, zoom=RENDER_ZOOM):
    mat = fitz.Matrix(zoom, zoom)
    pix = page.get_pixmap(matrix=mat, alpha=False)
    arr = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, 3)
    return cv2.cvtColor(arr, cv2.COLOR_RGB2BGR)


def _deskew(gray, max_angle=5.0):
    try:
        edges = cv2.Canny(gray, 50, 150, apertureSize=3)
        lines = cv2.HoughLines(edges, 1, np.pi / 180, 200)
        if lines is None:
            return gray
        angles = []
        for line in lines[:30]:
            rho, theta = line[0]
            angle = (theta * 180 / np.pi) - 90
            if abs(angle) <= max_angle:
                angles.append(angle)
        if not angles:
            return gray
        median_angle = float(np.median(angles))
        if abs(median_angle) < 0.3:
            return gray
        h, w = gray.shape
        M = cv2.getRotationMatrix2D((w / 2, h / 2), median_angle, 1.0)
        return cv2.warpAffine(gray, M, (w, h), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_REPLICATE)
    except Exception:
        return gray


def preprocess_for_ocr(bgr):
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    if min(h, w) < 800:
        scale = 800 / min(h, w)
        gray = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    denoised = cv2.fastNlMeansDenoising(gray, h=12, templateWindowSize=7, searchWindowSize=21)
    denoised = _deskew(denoised)
    _, binary = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (1, 1))
    binary = cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)
    return binary


def ocr_image(binary):
    pil = Image.fromarray(binary)
    try:
        data = pytesseract.image_to_data(pil, lang=OCR_LANG, config=OCR_CONFIG,
                                          output_type=pytesseract.Output.DICT)
        confs = [int(c) for c, w in zip(data["conf"], data["text"])
                 if w.strip() and str(c).lstrip("-").isdigit() and int(c) >= 0]
        avg_conf = float(np.mean(confs)) if confs else 0.0
        full_text = pytesseract.image_to_string(pil, lang=OCR_LANG, config=OCR_CONFIG)
        return full_text.strip(), avg_conf
    except Exception as e:
        return f"[OCR ERROR: {e}]", 0.0


def classify_page(bgr, ocr_text, page_num):
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    color_ratio = float(np.mean(hsv[:, :, 1] > 50)) * 100
    text_chars = len(re.sub(r"\s", "", ocr_text))

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


def analyze_image_content(bgr):
    result = {}
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    result["brightness_mean"] = round(float(np.mean(gray)), 2)
    result["brightness_std"]  = round(float(np.std(gray)), 2)
    try:
        small = cv2.resize(bgr, (150, 150))
        pixels = small.reshape(-1, 3).astype(np.float32)
        criteria = (cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER, 10, 1.0)
        _, labels, centers = cv2.kmeans(pixels, 5, None, criteria, 3, cv2.KMEANS_RANDOM_CENTERS)
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
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.Canny(blurred, 50, 150)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        result["contour_count"] = len(contours)
        result["complexity"] = ("high" if len(contours) > 800
                                else "medium" if len(contours) > 200 else "low")
    except Exception:
        result["contour_count"] = -1
        result["complexity"] = "unknown"
    try:
        hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
        skin_mask = cv2.inRange(hsv, np.array([0, 20, 70], np.uint8),
                                np.array([30, 255, 255], np.uint8))
        skin_ratio = float(np.mean(skin_mask > 0)) * 100
        result["skin_tone_ratio_pct"] = round(skin_ratio, 2)
        result["has_human_figure"] = skin_ratio > 3.0
    except Exception:
        result["has_human_figure"] = False
    return result


def extract_embedded_images(fitz_page, fitz_doc, page_num, out_dir):
    saved = []
    try:
        for img_index, img in enumerate(fitz_page.get_images(full=True)):
            xref = img[0]
            try:
                base = fitz.Pixmap(fitz_doc, xref)
                pix = base if base.n in (1, 3) else fitz.Pixmap(fitz.csRGB, base)
                if pix.width * pix.height < MIN_IMG_AREA:
                    continue
                fname = os.path.join(out_dir, f"page{page_num:02d}_img{img_index:02d}.png")
                pix.save(fname)
                saved.append(fname)
            except Exception as e:
                log.debug(f"  skip image xref={xref}: {e}")
    except Exception as e:
        log.warning(f"  Could not extract images on page {page_num}: {e}")
    return saved


def process_pdf(pdf_path):
    ensure_dirs(OUTPUT_DIR, PAGES_DIR, IMAGES_DIR)
    doc = fitz.open(pdf_path)
    total_pages = len(doc)
    log.info(f"Opened PDF: {pdf_path} - {total_pages} pages")
    results = []
    for page_idx in range(total_pages):
        page_num = page_idx + 1
        t0 = time.time()
        result = PageResult(page_number=page_num, page_type="unknown")
        try:
            fitz_page = doc[page_idx]
            bgr = render_page_to_array(fitz_page, zoom=RENDER_ZOOM)
            result.height_px, result.width_px = bgr.shape[:2]
            png_path = os.path.join(PAGES_DIR, f"page_{page_num:02d}.png")
            cv2.imwrite(png_path, bgr)
            result.png_path = png_path
            result.image_analysis = analyze_image_content(bgr)
            binary = preprocess_for_ocr(bgr)
            text, conf = ocr_image(binary)
            result.ocr_text = text
            result.ocr_confidence = round(conf, 2)
            result.page_type = classify_page(bgr, text, page_num)
            result.embedded_images = extract_embedded_images(fitz_page, doc, page_num, IMAGES_DIR)
        except Exception as e:
            result.error = str(e)
            log.error(f"ERROR on page {page_num}: {e}")
        log.info(f"Page {page_num}/{total_pages}: type={result.page_type} "
                 f"conf={result.ocr_confidence} chars={len(result.ocr_text.strip())} "
                 f"imgs={len(result.embedded_images)} {time.time()-t0:.1f}s")
        results.append(result)
    doc.close()
    return results


def build_reports(results):
    json_data = {
        "source_pdf": PDF_PATH,
        "total_pages": len(results),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "pages": [asdict(r) for r in results],
    }
    with open(REPORT_JSON, "w", encoding="utf-8") as f:
        json.dump(json_data, f, ensure_ascii=False, indent=2)
    log.info(f"JSON report saved: {REPORT_JSON}")

    lines = ["=" * 70, "  تقرير قراءة كتاب: ابن بطوطة في مصر",
             f"  المصدر: {PDF_PATH}", f"  عدد الصفحات: {len(results)}",
             f"  تاريخ التحليل: {time.strftime('%Y-%m-%d %H:%M:%S')}", "=" * 70, ""]
    for r in results:
        lines.append("─" * 60)
        lines.append(f"صفحة {r.page_number}  |  نوع: {r.page_type}")
        lines.append(f"   الأبعاد: {r.width_px} × {r.height_px} بكسل  |  ثقة OCR: {r.ocr_confidence:.1f}%")
        if r.embedded_images:
            lines.append(f"   صور مضمّنة: {len(r.embedded_images)}")
        if r.error:
            lines.append(f"   ⚠ خطأ: {r.error}")
        if r.ocr_text.strip():
            lines.append("   ── النص المستخرج ──")
            for ln in r.ocr_text.splitlines():
                if ln.strip():
                    lines.append(f"   {ln}")
        else:
            lines.append("   [لا يوجد نص — صورة توضيحية]")
        lines.append("")
    with open(REPORT_TXT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    log.info(f"Arabic text report saved: {REPORT_TXT}")


def print_summary(results):
    print("\n" + "=" * 60)
    print(" 📊 ملخص التحليل")
    print("=" * 60)
    type_counts = {}
    total_chars = 0
    for r in results:
        type_counts[r.page_type] = type_counts.get(r.page_type, 0) + 1
        total_chars += len(r.ocr_text.strip())
    print(f" إجمالي الصفحات : {len(results)}")
    print(f" إجمالي النص    : {total_chars} حرف")
    for pt, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
        print(f"   {pt:<24} {cnt}")
    print(f"\n المخرجات في مجلد: {OUTPUT_DIR}/")
    print("=" * 60)


if __name__ == "__main__":
    if not Path(PDF_PATH).exists():
        log.error(f"الملف غير موجود: {PDF_PATH}")
        log.error("الاستخدام: python3 book_reader_final.py path/to/file.pdf")
        sys.exit(1)
    results = process_pdf(PDF_PATH)
    build_reports(results)
    print_summary(results)
    log.info("تم بنجاح ✅")