"""Local printed-document extraction; no model calls or business authority."""
import io
import hashlib
import threading
import zipfile
from importlib.metadata import version
from pathlib import Path

MAX_PIXELS = 20_000_000
_engine = None
_ocr_lock = threading.Lock()
MODEL_HASHES = {
    "ch_ppocr_mobile_v2.0_cls_mobile.onnx": "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c",
    "PP-OCRv6_det_small.onnx": "090f04abcd9d9a7498bc4ebf677e4cb9bdce1fe4197ddb7e529f1ef44e1ff94f",
    "PP-OCRv6_rec_small.onnx": "6f327246b50388f3c176ae304bd95767ea6dc0c9ae92153ef8cbe210b3c14884",
}


def _image(raw):
    from PIL import Image, ImageOps
    image = Image.open(io.BytesIO(raw))
    if image.width * image.height > MAX_PIXELS or getattr(image, "n_frames", 1) != 1:
        raise ValueError("图片最多 2000 万像素，且必须为单页静态图片")
    image.load()
    return ImageOps.exif_transpose(image).convert("RGB")


def _ocr(image, orient=True):
    global _engine
    import numpy as np
    import rapidocr
    from rapidocr import RapidOCR
    with _ocr_lock:
        if _engine is None:
            models = Path(rapidocr.__file__).parent / "models"
            for name, expected in MODEL_HASHES.items():
                if not (models/name).is_file() or hashlib.sha256((models/name).read_bytes()).hexdigest() != expected:
                    raise ValueError("本地 OCR 模型缺失或版本不符，请修复安装")
            _engine = RapidOCR(params={"EngineConfig.onnxruntime.intra_op_num_threads": 2,
                                      "EngineConfig.onnxruntime.inter_op_num_threads": 1,
                                      "Global.log_level": "warning",
                                      "Det.model_path": str(models/"PP-OCRv6_det_small.onnx"),
                                      "Rec.model_path": str(models/"PP-OCRv6_rec_small.onnx"),
                                      "Cls.model_path": str(models/"ch_ppocr_mobile_v2.0_cls_mobile.onnx")})
        output = _engine(np.asarray(image))
    lines = [{"text": text, "confidence": float(score), "box": box.tolist()}
             for text, score, box in zip(output.txts or [], output.scores or [], output.boxes if output.boxes is not None else [])]
    if not lines:
        raise ValueError("未能识别文字，请上传清晰的印刷文档")
    vertical = sum(max(p[1] for p in r["box"])-min(p[1] for p in r["box"]) > 2*(max(p[0] for p in r["box"])-min(p[0] for p in r["box"])) for r in lines)
    if orient and vertical > len(lines)/2:
        text, rotated = _ocr(image.rotate(90, expand=True), orient=False)
        for region in rotated:
            region["page_rotation"] = 90
        return text, rotated
    # Keep table cells on their visual row; retain boxes for reviewing extraction.
    grouped = []
    for line in sorted(lines, key=lambda r: (min(p[1] for p in r["box"]), min(p[0] for p in r["box"]))):
        y = sum(p[1] for p in line["box"])/4
        height = max(p[1] for p in line["box"]) - min(p[1] for p in line["box"])
        if grouped and abs(y - grouped[-1][0]) < height * .5:
            grouped[-1][1].append(line)
        else:
            grouped.append((y, [line]))
    return "\n".join("\t".join(r["text"] for r in sorted(row, key=lambda r: min(p[0] for p in r["box"]))) for _, row in grouped), lines


def extract(suffix, raw):
    pages, warnings = [], []
    if suffix == ".xlsx":
        from openpyxl import load_workbook
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            if sum(f.file_size for f in archive.infolist()) > 40 * 1024 * 1024:
                raise ValueError("工作簿解压后过大，请拆分")
            if any("vbaproject" in f.filename.lower() or "externallinks/" in f.filename.lower() for f in archive.infolist()):
                raise ValueError("不支持带宏或外部链接的工作簿")
        book = load_workbook(io.BytesIO(raw), read_only=True, data_only=True, keep_links=False)
        formulas = load_workbook(io.BytesIO(raw), read_only=True, data_only=False, keep_links=False)
        try:
            for sheet in book:
                if sheet.max_row and sheet.max_row > 1000 or sheet.max_column and sheet.max_column > 100:
                    raise ValueError("工作表过大，请拆分为最多 1000 行、100 列")
                lines = []
                for cells, originals in zip(sheet.iter_rows(), formulas[sheet.title].iter_rows()):
                    values = []
                    for cell, original in zip(cells, originals):
                        if original.data_type == "f" and cell.value is None:
                            warnings.append(f"{sheet.title}!{cell.coordinate} 公式无缓存结果，未执行公式")
                            values.append("[公式结果不可用]")
                        else:
                            values.append(str(cell.value) if cell.value is not None else "")
                    if any(values):
                        lines.append("\t".join(values))
                pages.append({"page": sheet.title, "text": "\n".join(lines), "method": "xlsx"})
        finally:
            book.close(); formulas.close()
        parser = {"openpyxl": version("openpyxl")}
    elif suffix == ".pdf":
        import pypdfium2 as pdfium
        document = pdfium.PdfDocument(raw)
        try:
            if not 1 <= len(document) <= 20:
                raise ValueError("PDF 必须为 1 到 20 页")
            for index in range(len(document)):
                page = document[index]
                try:
                    textpage = page.get_textpage()
                    try:
                        text = textpage.get_text_range()
                    finally:
                        textpage.close()
                    has_images = any(obj.type == pdfium.raw.FPDF_PAGEOBJ_IMAGE for obj in page.get_objects())
                    entry = {"page": index+1, "text": text, "method": "pdf_text"}
                    if has_images or not text.strip():
                        width, height = page.get_size()
                        if width * height * 4 > MAX_PIXELS:
                            raise ValueError("PDF 页面渲染超过 2000 万像素，请缩小页面")
                        bitmap = page.render(scale=2)
                        try:
                            ocr_text, lines = _ocr(bitmap.to_pil().convert("RGB"))
                        except ValueError as exc:
                            if not text.strip():
                                raise
                            warnings.append(f"第 {index+1} 页图片文字未完整识别：{exc}；请核对原页")
                            ocr_text, lines = "", []
                        finally:
                            bitmap.close()
                        existing = {"".join(s.split()) for s in text.splitlines()}
                        added = [s for s in ocr_text.splitlines() if "".join(s.split()) not in existing]
                        entry.update(text="\n".join([text, *added]).strip(), method="pdf_text_and_ocr", regions=lines)
                    pages.append(entry)
                finally:
                    page.close()
        finally:
            document.close()
        parser = {"pypdfium2": version("pypdfium2"), "rapidocr": version("rapidocr")}
    else:
        text, lines = _ocr(_image(raw))
        pages.append({"page": 1, "text": text, "method": "ocr", "regions": lines})
        parser = {"rapidocr": version("rapidocr")}
    if any(region["confidence"] < .8 for page in pages for region in page.get("regions", [])):
        warnings.append("部分文字识别置信度较低，请核对原件中的单号、数量和金额")
    text = "\n\n".join(f"[页/表 {page['page']}]\n{page['text']}" for page in pages if page["text"].strip())
    if not text.strip():
        raise ValueError("文件中没有可提取的文字")
    if "rapidocr" in parser:
        parser["models_sha256"] = MODEL_HASHES
    return text, {"pages": pages, "parser": parser, "warnings": warnings}
