import os
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, TesseractCliOcrOptions
from docling.document_converter import DocumentConverter, PdfFormatOption

source = "assets/Ibn_Battuta_in_Egypt.pdf"  # file path or URL

# The system tesseract install ships with no language packs, so point it at a
# local tessdata dir (ara/osd traineddata + the configs/tessconfigs dirs
# copied from the system install, which docling's tsv/hocr output needs).
TESSDATA_DIR = Path.home() / ".local" / "share" / "tessdata"
# docling's orientation-detection step shells out to tesseract without
# --tessdata-dir, so it only picks up TESSDATA_PREFIX from the environment.
os.environ["TESSDATA_PREFIX"] = str(TESSDATA_DIR)

pipeline_options = PdfPipelineOptions()
pipeline_options.do_ocr = True
# EasyOCR (the default-ish engine) recognizes Arabic characters but assembles
# words left-to-right, scrambling RTL reading order within a line. Tesseract's
# 'ara' model is trained for cursive Arabic and preserves correct RTL order.
pipeline_options.ocr_options = TesseractCliOcrOptions(lang=["ara"], path=str(TESSDATA_DIR))
# Scanned Arabic pages often have a broken/empty text layer, so force OCR
# instead of trusting whatever text layer the PDF already has.
pipeline_options.ocr_options.force_full_page_ocr = True

converter = DocumentConverter(
    format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options),
    }
)
doc = converter.convert(source).document

print(doc.export_to_markdown())  # output: "### Docling Technical Report[...]"
