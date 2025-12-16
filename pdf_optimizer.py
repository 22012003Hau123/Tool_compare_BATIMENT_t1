"""
PDF Optimizer: Tự động tìm và tách trang tương đồng từ PDF lớn.
"""

from __future__ import annotations

import os
import tempfile
from typing import Tuple

import fitz  # PyMuPDF
from PIL import Image
import imagehash
from difflib import SequenceMatcher


def find_matching_page(ref_pdf_path: str, final_pdf_path: str, final_page_idx: int = 0) -> Tuple[int, float]:
    """
    Tìm trang trong ref_pdf giống nhất với trang final_page_idx của final_pdf.
    
    Args:
        ref_pdf_path: Đường dẫn đến PDF reference (có thể nhiều trang)
        final_pdf_path: Đường dẫn đến PDF final
        final_page_idx: Index trang trong final_pdf để tìm (mặc định 0)
    
    Returns:
        (matched_page_idx, confidence_score)
        - matched_page_idx: Index trang matching trong ref_pdf (0-based)
        - confidence_score: Độ tin cậy 0.0-1.0
    """
    # Load final page
    final_doc = fitz.open(final_pdf_path)
    final_page = final_doc.load_page(final_page_idx)
    
    # Get final page features (low resolution để nhanh)
    final_pix = final_page.get_pixmap(matrix=fitz.Matrix(1, 1))
    final_img = Image.frombytes("RGB", [final_pix.width, final_pix.height], final_pix.samples)
    final_hash = imagehash.phash(final_img, hash_size=8)
    
    # Get text (chỉ lấy 1000 ký tự đầu để nhanh)
    final_text = final_page.get_text()[:1000]
    
    final_doc.close()
    
    # Search in ref (lazy loading - từng trang một)
    ref_doc = fitz.open(ref_pdf_path)
    best_match = 0
    best_score = 0
    
    print(f"🔍 Recherche dans {ref_doc.page_count} pages...")
    
    for page_idx in range(ref_doc.page_count):
        # Load từng trang (lazy)
        page = ref_doc.load_page(page_idx)
        
        # Image similarity (low resolution)
        pix = page.get_pixmap(matrix=fitz.Matrix(1, 1))
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
        img_hash = imagehash.phash(img, hash_size=8)
        img_distance = abs(final_hash - img_hash)
        img_score = max(0, 1 - img_distance / 64)
        
        # Text similarity (partial)
        ref_text = page.get_text()[:1000]
        text_score = SequenceMatcher(None, final_text, ref_text).ratio()
        
        # Combined score (70% image, 30% text)
        score = 0.7 * img_score + 0.3 * text_score
        
        if score > best_score:
            best_score = score
            best_match = page_idx
            print(f"  ✓ Page {page_idx + 1}: {score:.1%}")
        
        # Early exit nếu match rất tốt
        if score > 0.95:
            print(f"  🎯 Correspondance parfaite trouvée à la page {page_idx + 1}")
            break
    
    ref_doc.close()
    return best_match, best_score


def extract_single_page(pdf_path: str, page_idx: int, output_path: str | None = None) -> str:
    """
    Tách 1 trang từ PDF.
    Memory efficient - chỉ load 1 trang.
    
    Args:
        pdf_path: Đường dẫn PDF nguồn
        page_idx: Index trang cần tách (0-based)
        output_path: Đường dẫn output (optional)
    
    Returns:
        Đường dẫn đến PDF đã tách (1 trang)
    """
    if output_path is None:
        temp_fd, output_path = tempfile.mkstemp(suffix=".pdf", prefix="extracted_page_")
        os.close(temp_fd)
    
    # Chỉ load 1 trang
    src_doc = fitz.open(pdf_path)
    dst_doc = fitz.open()
    dst_doc.insert_pdf(src_doc, from_page=page_idx, to_page=page_idx)
    dst_doc.save(output_path, garbage=4, deflate=True)
    
    src_doc.close()
    dst_doc.close()
    
    return output_path


def smart_preprocess(ref_pdf_path: str, final_pdf_path: str) -> Tuple[str, dict]:
    """
    Tiền xử lý thông minh:
    - Nếu ref = 1 trang: return nguyên
    - Nếu ref > 1 trang: tìm và extract trang matching
    
    Args:
        ref_pdf_path: Đường dẫn PDF reference
        final_pdf_path: Đường dẫn PDF final
    
    Returns:
        (processed_ref_path, metadata)
        - processed_ref_path: Đường dẫn PDF ref đã xử lý (1 trang)
        - metadata: Thông tin về quá trình xử lý
    """
    # Kiểm tra số trang ref
    ref_doc = fitz.open(ref_pdf_path)
    num_ref_pages = ref_doc.page_count
    ref_doc.close()
    
    metadata = {
        "ref_original_pages": num_ref_pages,
        "extracted": False,
        "matched_page": None,
        "confidence": None
    }
    
    # Nếu ref chỉ 1 trang → không cần xử lý
    if num_ref_pages == 1:
        print("ℹ️ PDF Référence: 1 page, pas besoin d'extraction.")
        return ref_pdf_path, metadata
    
    # Ref > 1 trang → tìm và extract
    print(f"📚 PDF Référence: {num_ref_pages} pages")
    print("🔍 Recherche de la page correspondante...")
    
    matched_page_idx, confidence = find_matching_page(ref_pdf_path, final_pdf_path)
    
    print(f"✅ Page {matched_page_idx + 1} trouvée (confiance: {confidence:.1%})")
    
    # Extract trang đó
    print(f"📄 Extraction de la page {matched_page_idx + 1}...")
    extracted_path = extract_single_page(ref_pdf_path, matched_page_idx)
    
    print(f"✅ Extraction terminée")
    
    metadata.update({
        "extracted": True,
        "matched_page": matched_page_idx + 1,  # 1-based for display
        "confidence": confidence
    })
    
    return extracted_path, metadata


__all__ = [
    "find_matching_page",
    "extract_single_page",
    "smart_preprocess",
]

