"""
Mode 2: Kiểm tra popup annotations đã được thực thi trong PDF final bằng GPT.
Refactor từ tool_compare_lasolution_2026.py, không import từ file gốc.
"""

from __future__ import annotations

import json
import os
from typing import Dict, List, Optional

import fitz  # PyMuPDF

from pdf_optimizer import smart_preprocess

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    # Không bắt buộc phải có dotenv
    pass

# Đọc model từ env, fallback mặc định
GPT_MODEL = os.getenv("GPT_MODEL", "gpt-4o-mini")


def get_openai_client(api_key: Optional[str] = None) -> Optional[OpenAI]:
    """
    Khởi tạo OpenAI client từ api_key (ưu tiên) hoặc từ env OPENAI_API_KEY.
    """
    if OpenAI is None:
        return None

    key = api_key or os.getenv("OPENAI_API_KEY", "")
    if not key or key == "your-api-key-here":
        return None

    try:
        return OpenAI(api_key=key)
    except Exception:
        return None


def extract_popup_annotations(pdf_path: str) -> List[Dict]:
    """
    Trích xuất các popup/text annotations từ PDF reference.
    """
    doc = fitz.open(pdf_path)
    annotations: List[Dict] = []

    for page_num in range(doc.page_count):
        page = doc.load_page(page_num)
        annots = page.annots()
        if not annots:
            continue

        for annot in annots:
            annot_type = annot.type[0]
            if annot_type not in (
                fitz.PDF_ANNOT_POPUP,
                fitz.PDF_ANNOT_TEXT,
                fitz.PDF_ANNOT_FREE_TEXT,
            ):
                continue

            try:
                content = annot.info.get("content", "") or annot.info.get("title", "")

                if not content and hasattr(annot, "popup"):
                    popup = getattr(annot, "popup", None)
                    if popup:
                        content = popup.info.get("content", "")

                if content and content.strip():
                    annotations.append(
                        {
                            "page": page_num,
                            "annotation": annot,
                            "content": content.strip(),
                            "rect": annot.rect,
                        }
                    )
            except Exception:
                # Bỏ qua annotation lỗi
                continue

    doc.close()
    return annotations


def get_text_around_annotation(page: fitz.Page, rect: fitz.Rect, context_size: int = 200) -> str:
    expanded_rect = fitz.Rect(
        max(0, rect.x0 - context_size),
        max(0, rect.y0 - context_size),
        min(page.rect.width, rect.x1 + context_size),
        min(page.rect.height, rect.y1 + context_size),
    )
    return page.get_text("text", clip=expanded_rect).strip()


def check_annotation_with_gpt(
    client: Optional[OpenAI],
    annotation_content: str,
    current_text: str,
    context_text: str,
    model: str = GPT_MODEL,
) -> Dict:
    """
    Gọi GPT để đánh giá annotation đã được thực hiện hay chưa.
    """
    if client is None:
        return {
            "implemented": False,
            "confidence": 0.0,
            "reasoning": "OpenAI client not available",
            "evidence": "",
            "status": "unclear",
        }

    prompt = f"""
Bạn là một chuyên gia kiểm tra tài liệu PDF. Kiểm tra yêu cầu sửa đổi từ popup annotation đã được thực hiện chưa.

YÊU CẦU SỬA ĐỔI:
{annotation_content}

TEXT HIỆN TẠI (vị trí annotation):
{current_text}

CONTEXT XUNG QUANH:
{context_text}

Trả lời JSON với các field: implemented (true/false), reasoning, evidence, status (implemented/not_implemented/partial/unclear), confidence (0-1).
"""

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {
                    "role": "system",
                    "content": "Bạn là chuyên gia kiểm tra tài liệu. Trả lời chỉ bằng JSON, không thêm text.",
                },
                {"role": "user", "content": prompt},
            ],
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        result = json.loads(response.choices[0].message.content)
        return {
            "implemented": bool(result.get("implemented", False)),
            "confidence": float(result.get("confidence", 0.0)),
            "reasoning": result.get("reasoning", ""),
            "evidence": result.get("evidence", ""),
            "status": result.get("status", "unclear"),
        }
    except Exception as e:
        return {
            "implemented": False,
            "confidence": 0.0,
            "reasoning": f"Error: {e}",
            "evidence": "",
            "status": "unclear",
        }


def _annotate_status(final_page: fitz.Page, rect: fitz.Rect, annotation_content: str, result: Dict):
    status = result.get("status", "unclear")
    if status == "implemented":
        color = (0, 1, 0)
        title = "Mode2-LaSolution ✅"
    elif status == "not_implemented":
        color = (1, 0, 0)
        title = "Mode2-LaSolution ❌"
    elif status == "partial":
        color = (1, 1, 0)
        title = "Mode2-LaSolution ⚠️"
    else:
        color = (0.5, 0.5, 0.5)
        title = "Mode2-LaSolution ❓"

    annot = final_page.add_rect_annot(rect)
    annot.set_colors(stroke=color)
    annot.set_border(width=1.2)
    annot.set_info(
        title=title,
        content=(
            f"Request: {annotation_content}\n"
            f"Status: {status}\n"
            f"Confidence: {result.get('confidence', 0.0):.2f}\n"
            f"Reason: {result.get('reasoning', '')}"
        ),
        subject=status,
    )
    annot.update()


def compare_mode2(
    ref_pdf_path: str,
    final_pdf_path: str,
    output_path: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
) -> Dict:
    """
    Mode 2 – Đọc popup annotations từ ref_pdf, kiểm tra bằng GPT, annotate vào final_pdf.
    """
    # === SMART PREPROCESSING ===
    print("\n=== MODE 2: Vérification des annotations ===")
    ref_pdf_path, preprocess_metadata = smart_preprocess(ref_pdf_path, final_pdf_path)
    # ===========================
    
    model_name = model or GPT_MODEL
    if output_path is None:
        base = os.path.splitext(final_pdf_path)[0]
        output_path = f"{base}_mode2_lasolution_diff.pdf"

    annotations = extract_popup_annotations(ref_pdf_path)

    ref_doc = fitz.open(ref_pdf_path)
    final_doc = fitz.open(final_pdf_path)

    client = get_openai_client(api_key=api_key)

    annotations_by_page: Dict[int, List[Dict]] = {}
    for ann in annotations:
        annotations_by_page.setdefault(ann["page"], []).append(ann)

    num_pages = min(ref_doc.page_count, final_doc.page_count)
    results: List[Dict] = []

    for i in range(num_pages):
        if i not in annotations_by_page:
            continue

        final_page = final_doc.load_page(i)
        for ann_data in annotations_by_page[i]:
            annotation_content = ann_data["content"]
            rect = ann_data["rect"]

            current_text = get_text_around_annotation(final_page, rect, context_size=200)
            context_text = get_text_around_annotation(final_page, rect, context_size=400)

            check_result = check_annotation_with_gpt(
                client=client,
                annotation_content=annotation_content,
                current_text=current_text,
                context_text=context_text,
                model=model_name,
            )

            result_entry = {
                "page": i + 1,
                "status": check_result.get("status"),
                "implemented": check_result.get("implemented"),
                "reasoning": check_result.get("reasoning", ""),
                "evidence": check_result.get("evidence", ""),
                "confidence": check_result.get("confidence", 0.0),
                "annotation": annotation_content,
            }
            results.append(result_entry)

            try:
                _annotate_status(final_page, rect, annotation_content, check_result)
            except Exception:
                # Không làm ngắt luồng nếu annotate lỗi
                pass

    final_doc.save(output_path, garbage=4, deflate=True)
    ref_doc.close()
    final_doc.close()

    summary = {
        "total_annotations": len(results),
        "implemented": sum(1 for r in results if r["status"] == "implemented"),
        "not_implemented": sum(1 for r in results if r["status"] == "not_implemented"),
        "partial": sum(1 for r in results if r["status"] == "partial"),
        "unclear": sum(1 for r in results if r["status"] == "unclear"),
    }

    return {
        "output_pdf": output_path,
        "results": results,
        "summary": summary,
        "preprocessing": preprocess_metadata,  # NEW
    }


__all__ = [
    "compare_mode2",
    "extract_popup_annotations",
    "get_text_around_annotation",
    "check_annotation_with_gpt",
    "get_openai_client",
]

