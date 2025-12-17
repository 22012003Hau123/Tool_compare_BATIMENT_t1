"""
Mode 1: So sánh kích thước ảnh sản phẩm giữa 2 PDF và annotate vào CẢ 2 PDF.
Refactor từ tool_compare_pages_2025.py, không import từ file gốc.

Features:
- Trích xuất và so sánh ảnh sản phẩm từ 2 PDF
- Blue annotation: matched products (dist <= hash_threshold)
- Red annotation: unmatched products (dist > hash_threshold)
- Annotate cả 2 PDF (reference và final)
"""

from __future__ import annotations

import os
import tempfile
from typing import Dict, List, Tuple

import fitz  # PyMuPDF
from PIL import Image
import imagehash

from pdf_optimizer import smart_preprocess

# Ngưỡng hash distance để coi là cùng sản phẩm
DEFAULT_HASH_THRESHOLD = 28


def extract_products(pdf_path: str, out_dir: str) -> List[Dict]:
    """
    Trích xuất images từ PDF blocks sử dụng get_text('rawdict').
    Extract image blocks (type 1) và form XObject blocks (type 2).
    """
    os.makedirs(out_dir, exist_ok=True)
    doc = fitz.open(pdf_path)

    products = []
    idx = 0

    for page_index, page in enumerate(doc):
        raw = page.get_text("rawdict")

        for block in raw["blocks"]:
            if block["type"] in [1, 2]:  # image block OR form XObject block
                bbox = block["bbox"]
                x0, y0, x1, y1 = bbox

                # Exclude footer zone (50px from bottom)
                page_height = page.rect.height
                footer_zone_start = page_height - 50
                if y1 > footer_zone_start:
                    continue  # Skip images in footer

                width_pt = x1 - x0
                height_pt = y1 - y0

                width_px = width_pt * 96 / 72
                height_px = height_pt * 96 / 72

                r = fitz.Rect(bbox)
                pix = page.get_pixmap(matrix=fitz.Matrix(2, 2), clip=r)

                filename = os.path.join(out_dir, f"product_{idx}.png")
                pix.save(filename)

                products.append({
                    "file": filename,
                    "page": page_index,
                    "width_pt": width_pt,
                    "height_pt": height_pt,
                    "width_px": width_px,
                    "height_px": height_px,
                    "bbox": bbox
                })
                idx += 1

    doc.close()
    return products


def compute_hash(path: str):
    img = Image.open(path).convert("RGB")
    return imagehash.phash(img)


def pair_products(list1: List[Dict], list2: List[Dict]) -> Tuple[List[Tuple[Dict, Dict, int]], List[Dict], List[Dict]]:
    """
    Gán mỗi ảnh ở PDF1 với ảnh giống nhất ở PDF2 dựa trên perceptual hash distance.
    Sử dụng greedy matching: ưu tiên cặp có hash distance nhỏ nhất trước.
    Mỗi sản phẩm chỉ được match 1 lần duy nhất (không duplicate).
    
    Returns: (pairs, list1, list2) với hash đã được tính toán.
    """
    # Compute hashes for all products
    for p in list1:
        p["hash"] = compute_hash(p["file"])
    for p in list2:
        p["hash"] = compute_hash(p["file"])

    # Generate ALL possible pairs with their distances
    all_candidates: List[Tuple[Dict, Dict, int]] = []
    for p1 in list1:
        for p2 in list2:
            dist = abs(p1["hash"] - p2["hash"])
            all_candidates.append((p1, p2, dist))
    
    # Sort by distance (ascending) - prioritize lower hash distance
    all_candidates.sort(key=lambda x: x[2])
    
    # Greedy matching: select pairs with lowest distance first
    # Each product can only be matched once
    used_p1 = set()
    used_p2 = set()
    pairs: List[Tuple[Dict, Dict, int]] = []
    
    for p1, p2, dist in all_candidates:
        p1_id = id(p1)
        p2_id = id(p2)
        
        # Skip if either product is already matched
        if p1_id in used_p1 or p2_id in used_p2:
            continue
        
        # Add this pair
        pairs.append((p1, p2, dist))
        used_p1.add(p1_id)
        used_p2.add(p2_id)
    
    return pairs, list1, list2


def compare_pairs(
    pairs: List[Tuple[Dict, Dict, int]],
    list1: List[Dict],
    list2: List[Dict],
    pdf1_path: str,
    pdf2_path: str,
    output_pdf1: str,
    output_pdf2: str,
    hash_threshold: int = DEFAULT_HASH_THRESHOLD,
) -> List[Dict]:
    """
    So sánh kích thước từng cặp và annotate vào CẢ 2 PDF.
    - Matched products (dist <= hash_threshold): Blue annotation trên CẢ 2 PDF
    - Unmatched products (dist > hash_threshold): Red annotation CHỈ trên PDF gốc
    
    Returns: danh sách kết quả comparison.
    """
    doc1 = fitz.open(pdf1_path)
    doc2 = fitz.open(pdf2_path)
    
    comparisons: List[Dict] = []
    annotations_added_pdf1 = 0
    annotations_added_pdf2 = 0

    # Track which products in list2 have been matched
    matched_p2_ids = set()
    
    # Helper function to scale bbox from one page to another
    def scale_bbox(bbox: tuple, from_page: fitz.Page, to_page: fitz.Page) -> tuple:
        """Scale bounding box coordinates from one page size to another."""
        scale_x = to_page.rect.width / from_page.rect.width
        scale_y = to_page.rect.height / from_page.rect.height
        x0, y0, x1, y1 = bbox
        return (x0 * scale_x, y0 * scale_y, x1 * scale_x, y1 * scale_y)
    
    # Process all pairs
    for p1, p2, dist in pairs:
        w1, h1 = p1["width_px"], p1["height_px"]
        w2, h2 = p2["width_px"], p2["height_px"]

        if dist <= hash_threshold:
            # MATCHED PAIR - Blue annotation on BOTH PDFs (with scaled bbox)
            scale_w = (w2 / w1) * 100 if w1 else 0
            scale_h = (h2 / h1) * 100 if h1 else 0

            comparisons.append({
                "pdf1_file": os.path.basename(p1["file"]),
                "pdf2_file": os.path.basename(p2["file"]),
                "hash_distance": dist,
                "pdf1_size_px": (w1, h1),
                "pdf2_size_px": (w2, h2),
                "scale_percent": {"width": scale_w, "height": scale_h},
                "page": p2["page"],
                "bbox": p2["bbox"],
                "status": "matched"
            })
            
            # Annotate PDF1 (Blue) at p1's position
            page1 = doc1.load_page(p1["page"])
            rect1 = fitz.Rect(p1["bbox"])
            annot1 = page1.add_rect_annot(rect1)
            annot1.set_colors(stroke=(0, 0, 1))  # Blue
            annot1.set_border(width=1.5)
            annot1.set_opacity(0.4)
            annotation_text = (
                f"Ref: {w1:.1f} × {h1:.1f}px\n"
                f"Final: {w2:.1f} × {h2:.1f}px\n"
                f"Échelle: L={scale_w:.1f}%, H={scale_h:.1f}%"
            )
            annot1.set_info(title="✓ Produit Correspondant", content=annotation_text)
            annot1.update()
            annotations_added_pdf1 += 1
            
            # Annotate PDF2 (Blue) at p2's position
            page2 = doc2.load_page(p2["page"])
            rect2 = fitz.Rect(p2["bbox"])
            annot2 = page2.add_rect_annot(rect2)
            annot2.set_colors(stroke=(0, 0, 1))  # Blue
            annot2.set_border(width=1.5)
            annot2.set_opacity(0.4)
            annot2.set_info(title="✓ Produit Correspondant", content=annotation_text)
            annot2.update()
            annotations_added_pdf2 += 1
            
            # Mark p2 as matched
            matched_p2_ids.add(id(p2))
            
        else:
            # UNMATCHED PAIR (dist > hash_threshold)
            # Annotate on BOTH PDFs (Red) - show they're paired but don't match well
            comparisons.append({
                "pdf1_file": os.path.basename(p1["file"]),
                "pdf2_file": os.path.basename(p2["file"]),
                "hash_distance": dist,
                "pdf1_size_px": (w1, h1),
                "pdf2_size_px": (w2, h2),
                "scale_percent": None,
                "page": p1["page"],
                "bbox": p1["bbox"],
                "status": "unmatched_pair"
            })
            
            # Annotate PDF1 (Red)
            page1 = doc1.load_page(p1["page"])
            rect1 = fitz.Rect(p1["bbox"])
            annot1 = page1.add_rect_annot(rect1)
            annot1.set_colors(stroke=(1, 0, 0))  # Red
            annot1.set_border(width=2.0)
            annot1.set_opacity(0.5)
            annot1.set_info(
                title="✗ Produit Non-Correspondant (Paire)",
                content=f"Pairé avec {os.path.basename(p2['file'])} mais hash distance trop grande: {dist} > {hash_threshold}"
            )
            annot1.update()
            annotations_added_pdf1 += 1
            
            # Mark p2 as used (it's in a pair even though unmatched)
            matched_p2_ids.add(id(p2))
            
            # Annotate PDF2 (Red) also
            page2 = doc2.load_page(p2["page"])
            rect2 = fitz.Rect(p2["bbox"])
            annot2 = page2.add_rect_annot(rect2)
            annot2.set_colors(stroke=(1, 0, 0))  # Red
            annot2.set_border(width=2.0)
            annot2.set_opacity(0.5)
            annot2.set_info(
                title="✗ Produit Non-Correspondant (Paire)",
                content=f"Pairé avec {os.path.basename(p1['file'])} mais hash distance trop grande: {dist} > {hash_threshold}"
            )
            annot2.update()
            annotations_added_pdf2 += 1
    
    # Find products in PDF2 that were never matched (exist only in PDF2)
    for p2 in list2:
        if id(p2) not in matched_p2_ids:
            w2, h2 = p2["width_px"], p2["height_px"]
            
            comparisons.append({
                "pdf1_file": None,
                "pdf2_file": os.path.basename(p2["file"]),
                "hash_distance": None,
                "pdf1_size_px": None,
                "pdf2_size_px": (w2, h2),
                "scale_percent": None,
                "page": p2["page"],
                "bbox": p2["bbox"],
                "status": "unmatched_in_pdf2"
            })
            
            # Annotate ONLY PDF2 (Red - product exists here but no match)
            page2 = doc2.load_page(p2["page"])
            rect2 = fitz.Rect(p2["bbox"])
            annot2 = page2.add_rect_annot(rect2)
            annot2.set_colors(stroke=(1, 0, 0))  # Red
            annot2.set_border(width=2.0)
            annot2.set_opacity(0.5)
            annot2.set_info(
                title="✗ Produit Non-Correspondant",
                content="Aucune image similaire trouvée dans le PDF de référence"
            )
            annot2.update()
            annotations_added_pdf2 += 1
    
    # Save both annotated PDFs
    doc1.save(output_pdf1, garbage=4, deflate=True)
    doc1.close()
    
    doc2.save(output_pdf2, garbage=4, deflate=True)
    doc2.close()

    return comparisons


def compare_mode1(
    ref_pdf_path: str,
    final_pdf_path: str,
    output_path: str | None = None,
    hash_threshold: int = DEFAULT_HASH_THRESHOLD,
) -> Dict:
    """
    Chạy mode 1:
    - Auto-detect và extract trang matching nếu ref > 1 trang
    - Trích xuất ảnh từ 2 PDF vào thư mục tạm
    - Pair bằng perceptual hash
    - Annotate kết quả vào CẢ 2 PDF (reference và final)
    - Blue annotation: matched products
    - Red annotation: unmatched products
    """
    # === SMART PREPROCESSING ===
    print("\n=== MODE 1: Comparaison de taille d'image ===")
    ref_pdf_path, preprocess_metadata = smart_preprocess(ref_pdf_path, final_pdf_path)
    # ===========================
    
    # Generate output paths for both PDFs
    if output_path is None:
        base_ref = os.path.splitext(ref_pdf_path)[0]
        base_final = os.path.splitext(final_pdf_path)[0]
        output_pdf1 = f"{base_ref}_mode1.pdf"
        output_pdf2 = f"{base_final}_mode1.pdf"
    else:
        # Use output_path as base for both outputs
        base = os.path.splitext(output_path)[0]
        output_pdf1 = f"{base}_ref.pdf"
        output_pdf2 = f"{base}_final.pdf"

    with tempfile.TemporaryDirectory() as tmpdir:
        pdf1_dir = os.path.join(tmpdir, "pdf1_products")
        pdf2_dir = os.path.join(tmpdir, "pdf2_products")

        list1 = extract_products(ref_pdf_path, pdf1_dir)
        list2 = extract_products(final_pdf_path, pdf2_dir)

        pairs, list1, list2 = pair_products(list1, list2)
        comparisons = compare_pairs(
            pairs=pairs,
            list1=list1,
            list2=list2,
            pdf1_path=ref_pdf_path,
            pdf2_path=final_pdf_path,
            output_pdf1=output_pdf1,
            output_pdf2=output_pdf2,
            hash_threshold=hash_threshold,
        )

    return {
        "output_pdf1": output_pdf1,
        "output_pdf2": output_pdf2,
        "num_products_ref": len(list1),
        "num_products_final": len(list2),
        "num_comparisons": len(comparisons),
        "comparisons": comparisons,
        "preprocessing": preprocess_metadata,  # NEW
    }


__all__ = [
    "compare_mode1",
    "extract_products",
    "pair_products",
    "compute_hash",
    "compare_pairs",
]

