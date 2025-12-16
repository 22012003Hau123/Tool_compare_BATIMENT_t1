"""
Mode 3: So sánh word-by-word giữa 2 PDF và annotate highlight.
Refactor từ tool_compare_assemblage.py với enhanced logic:

Features:
- Word-by-word comparison với difflib SequenceMatcher
- 3 loại thay đổi với màu sắc rõ ràng:
  
  🔴 ĐỎ (REPLACED): Text bị THAY ĐỔI
     - Text ở cùng vị trí nhưng nội dung khác nhau
     - Tô đỏ trên CẢ 2 PDF (Ref và Final)
     - Hiển thị text cũ và text mới trong annotation
  
  🟡 VÀNG (MISSING): Text bị XÓA
     - Text có trong Reference nhưng KHÔNG có trong Final
     - Tô vàng chỉ trên PDF Reference
  
  🟢 XANH (EXTRA): Text được THÊM
     - Text có trong Final nhưng KHÔNG có trong Reference
     - Tô xanh chỉ trên PDF Final
"""

from __future__ import annotations

from typing import Dict, List, Tuple

import fitz  # PyMuPDF

from pdf_optimizer import smart_preprocess

CASE_INSENSITIVE = True
IGNORE_QUOTES = True


def _normalize_word(word: str) -> str:
    import unicodedata
    
    if CASE_INSENSITIVE:
        word = word.lower()
    
    if IGNORE_QUOTES:
        # XÓA quotes/apostrophes TRƯỚC normalize để tránh tạo combining chars
        pre_normalize_chars = ["'", "'", "'", "`", "´"]
        for char in pre_normalize_chars:
            word = word.replace(char, "")
        
        # SAU ĐÓ mới normalize Unicode
        word = unicodedata.normalize('NFKC', word)
        # XÓA HẾT TẤT CẢ các loại apostrophe, quotes, accents
        # Không replace về ' mà XÓA LUÔN để: d'emploi → demploi
        chars_to_remove = [
            "'",  # Normal apostrophe
            "'",  # U+2019 Right single quotation mark
            "'",  # U+2018 Left single quotation mark  
            "ʼ",  # U+02BC Modifier letter apostrophe
            "`",  # U+0060 Grave accent / Backtick
            "´",  # U+00B4 Acute accent
            "ˊ",  # U+02CA Modifier letter acute accent
            "ˋ",  # U+02CB Modifier letter grave accent
            "ʹ",  # U+02B9 Modifier letter prime
            "′",  # U+2032 Prime
            "‵",  # U+2035 Reversed prime
            "＇", # U+FF07 Fullwidth apostrophe
            "՚",  # U+055A Armenian apostrophe
            "Ꞌ",  # U+A78B Latin capital letter saltillo
            "ꞌ",  # U+A78C Latin small letter saltillo
            "ʻ",  # U+02BB Modifier letter turned comma
            "ʽ",  # U+02BD Modifier letter reversed comma
            "\u0301",  # Combining acute accent
            "\u0300",  # Combining grave accent
            '"',  # Normal double quote
            """,  # U+201C Left double quotation mark
            """,  # U+201D Right double quotation mark
            "«",  # Left-pointing double angle quotation mark
            "»",  # Right-pointing double angle quotation mark
            "„",  # Double low-9 quotation mark
            "‟",  # Double high-reversed-9 quotation mark
            "〝", # U+301D Reversed double prime quotation mark
            "〞", # U+301E Double prime quotation mark
            "＂", # U+FF02 Fullwidth quotation mark
        ]
        
        # XÓA tất cả
        for char in chars_to_remove:
            word = word.replace(char, "")
        
        # NORMALIZE SUPERSCRIPT/SUBSCRIPT về dạng thường
        # VD: "PLUS⁽¹⁾" → "PLUS(1)"
        superscript_map = {
            '⁰': '0', '¹': '1', '²': '2', '³': '3', '⁴': '4',
            '⁵': '5', '⁶': '6', '⁷': '7', '⁸': '8', '⁹': '9',
            '⁽': '(', '⁾': ')', '⁺': '+', '⁻': '-', '⁼': '=',
        }
        subscript_map = {
            '₀': '0', '₁': '1', '₂': '2', '₃': '3', '₄': '4',
            '₅': '5', '₆': '6', '₇': '7', '₈': '8', '₉': '9',
            '₍': '(', '₎': ')', '₊': '+', '₋': '-', '₌': '=',
        }
        
        for sup, normal in superscript_map.items():
            word = word.replace(sup, normal)
        for sub, normal in subscript_map.items():
            word = word.replace(sub, normal)
        
        # XÓA HOÀN TOÀN patterns (số nhỏ) - VD: (1), (2), (12) để ignore trong comparison
        # Nhưng GIỮ numbers lớn như 32859, 61545
        # Dùng regex để tìm và xóa: (1-2 chữ số)
        import re
        word = re.sub(r'\([0-9]{1,2}\)', '', word)  # Xóa (1), (2), (12), etc.
        word = re.sub(r'\[[0-9]{1,2}\]', '', word)  # Xóa [1], [2], etc.
        word = re.sub(r'\{[0-9]{1,2}\}', '', word)  # Xóa {1}, {2}, etc.
        
        # XÓA TẤT CẢ PUNCTUATION còn lại (dấu chấm, dấu phẩy, v.v...)
        # Category 'P' = Punctuation: . , ; : ! ? - ...
        word = ''.join(c for c in word if not unicodedata.category(c).startswith('P'))
        
        # Remove zero-width characters
        word = word.replace("\u200b", "")  # Zero-width space
        word = word.replace("\u200c", "")  # Zero-width non-joiner
        word = word.replace("\u200d", "")  # Zero-width joiner
        word = word.replace("\ufeff", "")  # Zero-width no-break space
        
        # Remove bất kỳ combining marks còn lại
        word = ''.join(c for c in word if unicodedata.category(c) != 'Mn')
        
        # XÓA HẾT SPACES
        # VD: "PLUS(1)" → "PLUS 1" → "PLUS1"
        #     "PLUS⁽¹⁾" → "PLUS(1)" → "PLUS 1" → "PLUS1"
        word = word.replace(' ', '').strip()
    
    return word


def extract_page_words_with_boxes(pdf_path: str) -> List[Dict]:
    doc = fitz.open(pdf_path)
    pages: List[Dict] = []
    for page_index in range(doc.page_count):
        page = doc.load_page(page_index)
        words_raw = page.get_text("words")
        words = []
        for x0, y0, x1, y1, text, *_ in words_raw:
            words.append(
                {"text": text, "rect": fitz.Rect(x0, y0, x1, y1), "highlight_color": None}
            )
        pages.append({"page": page_index, "words": words})
    doc.close()
    return pages


def preprocess_merge_parentheses(words_data: List[Dict]) -> List[Dict]:
    """
    Pre-process: Merge patterns như "PLUS" + "(1)" thành "PLUS(1)" TRƯỚC KHI normalize.
    
    VD: ["PLUS", "(1)"] → ["PLUS(1)"]
        ["PLUS", "⁽¹⁾"] → ["PLUS⁽¹⁾"]
    """
    import re
    
    if not words_data:
        return words_data
    
    merged = []
    i = 0
    
    while i < len(words_data):
        current = words_data[i]
        
        # Check nếu word tiếp theo là pattern: (số) hoặc ⁽số⁾
        if i + 1 < len(words_data):
            next_word = words_data[i + 1]
            next_text = next_word["text"]
            
            # Pattern: (1), (2), ⁽¹⁾, ⁽²⁾, etc. (chỉ có số 1-2 chữ số trong ngoặc)
            if re.match(r'^[\(⁽][0-9⁰¹²³⁴⁵⁶⁷⁸⁹]{1,2}[\)⁾]$', next_text):
                # MERGE: "PLUS" + "(1)" → "PLUS(1)"
                merged_text = current["text"] + next_text
                merged_rect = fitz.Rect(current["rect"]) | fitz.Rect(next_word["rect"])
                
                merged.append({
                    "text": merged_text,
                    "rect": merged_rect,
                    "highlight_color": None
                })
                i += 2  # Skip cả 2 words
                continue
        
        # Không merge, giữ nguyên
        merged.append(current)
        i += 1
    
    return merged


def align_words_assemblage(ref_words_data: List[Dict], final_words_data: List[Dict]):
    """
    So sánh word-by-word với 3 loại thay đổi:
    
    1. REPLACED (ĐỎ): Text bị THAY ĐỔI (cùng vị trí nhưng khác nội dung)
       - Tô ĐỎ trên CẢ 2 PDF (Ref và Final)
    
    2. MISSING (VÀNG): Text có trong Reference nhưng KHÔNG có trong Final
       - Tô VÀNG trên PDF Reference
    
    3. EXTRA (XANH): Text có trong Final nhưng KHÔNG có trong Reference
       - Tô XANH trên PDF Final
       
    POST-PROCESSING: Loại bỏ highlight nếu text giống nhau ở cả 2 PDFs
    """
    from difflib import SequenceMatcher

    # PRE-PROCESS: Merge "PLUS" + "(1)" → "PLUS(1)"
    ref_words_data = preprocess_merge_parentheses(ref_words_data)
    final_words_data = preprocess_merge_parentheses(final_words_data)

    # Normalize for comparison
    ref_norm = [_normalize_word(w["text"]) for w in ref_words_data]
    final_norm = [_normalize_word(w["text"]) for w in final_words_data]

    s = SequenceMatcher(None, ref_norm, final_norm)
    opcodes = list(s.get_opcodes())
    
    # DISABLE REPLACE MERGE
    # Chỉ giữ DELETE (MISSING - màu vàng) và INSERT (EXTRA - màu xanh)
    # Không merge thành REPLACE (màu đỏ) vì gây nhiều false positives
    merged_opcodes = opcodes
    
    # Process opcodes
    idx1_current = 0
    idx2_current = 0

    for tag, i1, i2, j1, j2 in merged_opcodes:
        if tag == "equal":
            # Skip - text giống nhau, không cần highlight
            idx1_current += i2 - i1
            idx2_current += j2 - j1

        elif tag == "delete":
            # MISSING TEXT: Text chỉ có trong Reference
            # Tô VÀNG trên Reference
            for k in range(i2 - i1):
                ref_words_data[idx1_current + k]["highlight_color"] = "yellow"
                ref_words_data[idx1_current + k]["change_type"] = "MISSING"
            idx1_current += i2 - i1

        elif tag == "insert":
            # EXTRA TEXT: Text chỉ có trong Final
            # Tô XANH trên Final
            for k in range(j2 - j1):
                final_words_data[idx2_current + k]["highlight_color"] = "green"
                final_words_data[idx2_current + k]["change_type"] = "EXTRA"
            idx2_current += j2 - j1

        elif tag == "replace":
            # TREAT REPLACE AS DELETE + INSERT
            # Phần bị xóa: Tô VÀNG trên Ref
            for k in range(i2 - i1):
                ref_words_data[idx1_current + k]["highlight_color"] = "yellow"
                ref_words_data[idx1_current + k]["change_type"] = "MISSING"
            
            # Phần được thêm: Tô XANH trên Final
            for k in range(j2 - j1):
                final_words_data[idx2_current + k]["highlight_color"] = "green"
                final_words_data[idx2_current + k]["change_type"] = "EXTRA"

            idx1_current += i2 - i1
            idx2_current += j2 - j1

    # POST-PROCESSING: Loại bỏ highlights cho words có text GIỐNG NHAU
    # Mục đích: Tránh tô màu cho '32859' khi nó có ở cả 2 PDF
    remove_same_text_highlights(ref_words_data, final_words_data)

    return ref_words_data, final_words_data


def remove_same_text_highlights(ref_words_data: List[Dict], final_words_data: List[Dict]):
    """
    Loại bỏ highlights cho các words có text giống nhau trong cả 2 PDFs.
    
    Logic:
    - Thu thập TẤT CẢ normalized texts từ cả 2 PDFs (ALL words, không chỉ highlighted)
    - Tìm common texts (texts xuất hiện ở CẢ 2 PDFs)
    - Nếu 1 highlighted word nằm trong common texts → XÓA highlight
    
    Ví dụ: '0,00' xuất hiện nhiều lần ở cả 2 PDF → không tô màu
            '32859' có ở cả Ref và Final → không tô màu
    """
    # Thu thập TẤT CẢ normalized texts từ CẢ 2 PDFs (không phân biệt highlighted hay không)
    all_ref_norm_set = set()
    all_final_norm_set = set()
    
    for w in ref_words_data:
        norm_text = _normalize_word(w["text"])
        if norm_text:  # Chỉ add nếu không rỗng
            all_ref_norm_set.add(norm_text)
    
    for w in final_words_data:
        norm_text = _normalize_word(w["text"])
        if norm_text:
            all_final_norm_set.add(norm_text)
    
    # Thu thập concatenated versions của HIGHLIGHTED consecutive words
    # VD: ["PLUS", "(1)"] highlighted → cũng add "plus" vào check
    for i in range(len(ref_words_data) - 1):
        if ref_words_data[i].get("highlight_color") and ref_words_data[i+1].get("highlight_color"):
            concat = _normalize_word(ref_words_data[i]["text"]) + _normalize_word(ref_words_data[i+1]["text"])
            if concat:
                all_ref_norm_set.add(concat)
    
    for i in range(len(final_words_data) - 1):
        if final_words_data[i].get("highlight_color") and final_words_data[i+1].get("highlight_color"):
            concat = _normalize_word(final_words_data[i]["text"]) + _normalize_word(final_words_data[i+1]["text"])
            if concat:
                all_final_norm_set.add(concat)
    
    # Tìm COMMON texts: texts xuất hiện ở CẢ 2 PDFs
    common_texts = all_ref_norm_set & all_final_norm_set
    
    if not common_texts:
        return
    
    # Loại bỏ highlight cho các words có normalized text nằm trong common_texts
    for w in ref_words_data:
        if w.get("highlight_color"):
            norm_text = _normalize_word(w["text"])
            if norm_text and norm_text in common_texts:
                w["highlight_color"] = None
                w["change_type"] = None
    
    for w in final_words_data:
        if w.get("highlight_color"):
            norm_text = _normalize_word(w["text"])
            if norm_text and norm_text in common_texts:
                w["highlight_color"] = None
                w["change_type"] = None
    
    # Check consecutive pairs: nếu concat của 2 words liên tiếp match với common_texts
    for i in range(len(ref_words_data) - 1):
        w1, w2 = ref_words_data[i], ref_words_data[i+1]
        if w1.get("highlight_color") and w2.get("highlight_color"):
            concat = _normalize_word(w1["text"]) + _normalize_word(w2["text"])
            if concat in common_texts:
                w1["highlight_color"] = None
                w1["change_type"] = None
                w2["highlight_color"] = None
                w2["change_type"] = None
    
    for i in range(len(final_words_data) - 1):
        w1, w2 = final_words_data[i], final_words_data[i+1]
        if w1.get("highlight_color") and w2.get("highlight_color"):
            concat = _normalize_word(w1["text"]) + _normalize_word(w2["text"])
            if concat in common_texts:
                w1["highlight_color"] = None
                w1["change_type"] = None
                w2["highlight_color"] = None
                w2["change_type"] = None


def merge_adjacent_words(words_data: List[Dict]) -> List[Dict]:
    """
    Gộp các words liền kề cùng hàng và cùng màu thành một annotation dài ngang.
    
    Args:
        words_data: Danh sách words với rect, highlight_color, change_type
    
    Returns:
        Danh sách merged annotations (mỗi item là một group gộp)
    """
    # Chỉ lấy các words có highlight
    highlighted_words = [w for w in words_data if w.get("highlight_color")]
    
    if not highlighted_words:
        return []
    
    # Sort theo y (top), rồi x (left) để xử lý theo thứ tự đọc
    highlighted_words.sort(key=lambda w: (w["rect"].y0, w["rect"].x0))
    
    merged_groups = []
    current_group = None
    
    VERTICAL_THRESHOLD = 5    # pixels - cùng hàng nếu y chênh lệch < 5px
    HORIZONTAL_GAP = 20       # pixels - merge nếu khoảng cách ngang < 20px
    
    for word in highlighted_words:
        if current_group is None:
            # Bắt đầu group mới
            current_group = {
                "rect": fitz.Rect(word["rect"]),
                "highlight_color": word["highlight_color"],
                "change_type": word.get("change_type"),
                "texts": [word["text"]],
                "replaced_with": word.get("replaced_with"),
                "replaced_from": word.get("replaced_from"),
            }
        else:
            # Kiểm tra xem có thể merge với group hiện tại không
            same_row = abs(word["rect"].y0 - current_group["rect"].y0) < VERTICAL_THRESHOLD
            same_color = word["highlight_color"] == current_group["highlight_color"]
            same_type = word.get("change_type") == current_group.get("change_type")
            horizontal_gap = word["rect"].x0 - current_group["rect"].x1
            close_enough = horizontal_gap < HORIZONTAL_GAP
            
            if same_row and same_color and same_type and close_enough:
                # Merge vào group hiện tại
                current_group["rect"] = current_group["rect"] | word["rect"]  # Union của 2 rects
                current_group["texts"].append(word["text"])
                # Cập nhật replaced info nếu có
                if word.get("replaced_with"):
                    current_group["replaced_with"] = word.get("replaced_with")
                if word.get("replaced_from"):
                    current_group["replaced_from"] = word.get("replaced_from")
            else:
                # Lưu group hiện tại và bắt đầu group mới
                merged_groups.append(current_group)
                current_group = {
                    "rect": fitz.Rect(word["rect"]),
                    "highlight_color": word["highlight_color"],
                    "change_type": word.get("change_type"),
                    "texts": [word["text"]],
                    "replaced_with": word.get("replaced_with"),
                    "replaced_from": word.get("replaced_from"),
                }
    
    # Đừng quên group cuối cùng
    if current_group:
        merged_groups.append(current_group)
    
    return merged_groups


def apply_highlights_to_page(page: fitz.Page, words_data: List[Dict], page_num: int) -> int:
    """
    Apply highlights to a PDF page with detailed change type information.
    Gộp các annotations liền kề cùng hàng thành một annotation dài ngang.
    
    Màu sắc:
    - ĐỎ: Text REPLACED (Ref và Final khác nhau)
    - VÀNG: Text MISSING (Ref có, Final không có)
    - XANH: Text EXTRA (Final có, Ref không có)
    
    Note: Logic thông minh - không tô màu nếu text giống nhau ở cả 2 PDFs
    """
    # Color map
    color_map = {
        "red": (1.0, 0.4, 0.4),      # Đỏ - Text bị thay đổi (REPLACED)
        "yellow": (1.0, 1.0, 0.4),   # Vàng - Text bị xóa (MISSING)
        "green": (0.5, 1.0, 0.5),    # Xanh lá - Text được thêm (EXTRA)
    }

    highlights_added = 0
    
    # MERGE các words liền kề cùng hàng trước khi apply annotation
    merged_groups = merge_adjacent_words(words_data)

    for group in merged_groups:
        color = color_map.get(group["highlight_color"])
        if not color:
            continue

        try:
            # Apply highlight cho toàn bộ merged rect
            annot = page.add_highlight_annot(group["rect"])
            annot.set_colors(stroke=color)
            annot.set_opacity(0.5)

            # Add detailed message based on change type
            change_type = group.get("change_type", "CHANGED")
            text_content = " ".join(group["texts"])  # Gộp tất cả texts trong group

            # Generate descriptive message
            if change_type == "REPLACED":
                title = "Mode3-MODIFIÉ"
                # Kiểm tra xem có thông tin replaced_with hoặc replaced_from không
                if "replaced_with" in group and group["replaced_with"]:
                    # Đây là text trong Reference đã bị thay đổi
                    content = (
                        f"🔴 TEXTE MODIFIÉ\n"
                        f"Ancien texte (Référence): '{text_content}'\n"
                        f"Nouveau texte (Final): '{group['replaced_with']}'\n"
                        f"Statut: Texte a été MODIFIÉ"
                    )
                elif "replaced_from" in group and group["replaced_from"]:
                    # Đây là text trong Final (text mới)
                    content = (
                        f"🔴 TEXTE MODIFIÉ\n"
                        f"Ancien texte (Référence): '{group['replaced_from']}'\n"
                        f"Nouveau texte (Final): '{text_content}'\n"
                        f"Statut: Texte a été MODIFIÉ"
                    )
                else:
                    content = (
                        f"🔴 TEXTE MODIFIÉ\n"
                        f"Texte: '{text_content}'\n"
                        f"Statut: Texte a été MODIFIÉ"
                    )
            elif change_type == "MISSING":
                title = "Mode3-MANQUANT"
                content = (
                    f"🟡 TEXTE MANQUANT\n"
                    f"Texte: '{text_content}'\n"
                    f"Statut: Présent dans Référence mais PAS dans Final\n"
                    f"Action: Texte a été SUPPRIMÉ"
                )
            elif change_type == "EXTRA":
                title = "Mode3-SUPPLÉMENTAIRE"
                content = (
                    f"🟢 TEXTE SUPPLÉMENTAIRE\n"
                    f"Texte: '{text_content}'\n"
                    f"Statut: Présent dans Final mais PAS dans Référence\n"
                    f"Action: Texte a été AJOUTÉ"
                )
            else:
                title = f"Mode3-{change_type}"
                content = f"Change: {change_type}\nText: '{text_content}'"

            annot.set_info(title=title, content=content)
            annot.update()
            highlights_added += 1
        except Exception as e:
            # Silent fail for individual highlights
            continue

    return highlights_added


def compare_pages_assemblage(
    ref_page: fitz.Page,
    ref_page_dict: Dict,
    final_page: fitz.Page,
    page_index: int,
) -> Tuple[int, int]:
    """
    So khớp word diff và annotate cho cả ref_page và final_page. Trả về số highlight đã thêm.
    """
    ref_words_data = ref_page_dict["words"]

    final_words_raw = final_page.get_text("words")
    final_words_data = [
        {"text": t, "rect": fitz.Rect(x0, y0, x1, y1), "highlight_color": None}
        for x0, y0, x1, y1, t, *_ in final_words_raw
    ]

    align_words_assemblage(ref_words_data, final_words_data)

    ref_count = apply_highlights_to_page(ref_page, ref_words_data, page_index)
    final_count = apply_highlights_to_page(final_page, final_words_data, page_index)

    return ref_count, final_count


def compare_mode3(
    ref_pdf_path: str,
    final_pdf_path: str,
    output_ref: str | None = None,
    output_final: str | None = None,
) -> Dict:
    """
    Mode 3 – Annotate cả reference và final PDF với highlight diff.
    
    3 loại thay đổi:
    - 🔴 ĐỎ (REPLACED): Text bị thay đổi (tô đỏ trên cả 2 PDF)
    - 🟡 VÀNG (MISSING): Text có trong Ref nhưng không có trong Final (tô vàng trên Ref)
    - 🟢 XANH (EXTRA): Text có trong Final nhưng không có trong Ref (tô xanh trên Final)
    
    Logic thông minh: Text giống nhau ở cả 2 PDFs sẽ KHÔNG được tô màu
    (Ví dụ: '32859' có ở cả 2 → không highlight)
    
    Returns:
        Dict with output_ref, output_final, stats, and preprocessing metadata
    """
    # === SMART PREPROCESSING ===
    print("\n=== MODE 3: Comparaison mot-à-mot ===")
    ref_pdf_path, preprocess_metadata = smart_preprocess(ref_pdf_path, final_pdf_path)
    # ===========================
    
    ref_doc = fitz.open(ref_pdf_path)
    ref_pages_data = extract_page_words_with_boxes(ref_pdf_path)
    final_doc = fitz.open(final_pdf_path)

    num_pages = min(len(ref_pages_data), final_doc.page_count, ref_doc.page_count)

    if output_ref is None:
        output_ref = ref_pdf_path.rsplit(".", 1)[0] + "_mode3_ref.pdf"
    if output_final is None:
        output_final = final_pdf_path.rsplit(".", 1)[0] + "_mode3_final.pdf"

    ref_highlights = 0
    final_highlights = 0

    for i in range(num_pages):
        ref_page = ref_doc.load_page(i)
        ref_page_dict = ref_pages_data[i]
        final_page = final_doc.load_page(i)

        r_count, f_count = compare_pages_assemblage(ref_page, ref_page_dict, final_page, i)
        ref_highlights += r_count
        final_highlights += f_count

    ref_doc.save(output_ref, garbage=4, deflate=True)
    ref_doc.close()

    final_doc.save(output_final, garbage=4, deflate=True)
    final_doc.close()

    stats = {
        "total_pages": num_pages,
        "ref_highlights": ref_highlights,
        "final_highlights": final_highlights,
    }

    return {
        "output_ref": output_ref,
        "output_final": output_final,
        "stats": stats,
        "preprocessing": preprocess_metadata,  # NEW
    }


__all__ = [
    "compare_mode3",
    "extract_page_words_with_boxes",
    "align_words_assemblage",
    "apply_highlights_to_page",
    "compare_pages_assemblage",
]

