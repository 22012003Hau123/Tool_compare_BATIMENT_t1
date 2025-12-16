"""
Flask backend: cung cấp 3 endpoint cho 3 mode so sánh PDF.
"""

from __future__ import annotations

import os
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Tuple, Optional

from flask import Flask, jsonify, request, send_file

from mode1 import compare_mode1
from mode2 import compare_mode2
from mode3 import compare_mode3

BASE_DIR = Path(__file__).resolve().parent
TEMP_DIR = BASE_DIR / "temp_pdf"  # dùng cho output/result theo session
TEMP_DIR.mkdir(parents=True, exist_ok=True)

# Thư mục lưu ref/final upload lâu dài (dọn bằng crontab)
PERSIST_DIR = BASE_DIR / "temp_folder"
REF_DIR = PERSIST_DIR / "ref"
FINAL_DIR = PERSIST_DIR / "final"
for d in (REF_DIR, FINAL_DIR):
    d.mkdir(parents=True, exist_ok=True)

# Track sessions đã sẵn sàng để cleanup (sau khi PDFs load xong)
_sessions_ready_for_cleanup = {}
# Track last access time cho mỗi session để auto-cleanup session cũ
_session_last_access = {}

# Load .env file nếu có (cùng cấp với backend_flask.py)
env_file = BASE_DIR / ".env"
if env_file.exists():
    try:
        with open(env_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    key = key.strip()
                    value = value.strip().strip('"').strip("'")
                    os.environ[key] = value
    except Exception:
        pass  # Ignore errors when reading .env

app = Flask(__name__)


def _convert_to_json_serializable(obj):
    """
    Convert các kiểu dữ liệu không JSON serializable (int64, float64, etc.) 
    thành kiểu Python chuẩn.
    """
    import numpy as np
    import pandas as pd
    
    if isinstance(obj, dict):
        return {key: _convert_to_json_serializable(value) for key, value in obj.items()}
    elif isinstance(obj, (list, tuple)):
        return [_convert_to_json_serializable(item) for item in obj]
    elif isinstance(obj, (np.integer, np.int64, np.int32, np.int16, np.int8)):
        return int(obj)
    elif isinstance(obj, (np.floating, np.float64, np.float32, np.float16)):
        return float(obj)
    elif isinstance(obj, np.bool_):
        return bool(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif pd.isna(obj):
        return None
    else:
        return obj


def _cleanup_worker():
    """Background worker để cleanup sessions định kỳ."""
    while True:
        try:
            _check_and_cleanup_sessions()
            time.sleep(2)  # Check mỗi 2 giây
        except Exception:
            pass


# Khởi động background cleanup worker
cleanup_thread = threading.Thread(target=_cleanup_worker, daemon=True)
cleanup_thread.start()


def _get_session_dir(session_id: Optional[str] = None) -> Path:
    """Lấy thư mục session. Nếu không có session_id, tạo session mới."""
    if not session_id:
        session_id = str(uuid.uuid4())
    session_dir = TEMP_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def _save_upload(file_storage, prefix: str, session_id: Optional[str] = None) -> Tuple[Path, str]:
    """
    Lưu file upload vào thư mục cố định:
    - ref_pdf -> temp_folder/ref
    - final_pdf -> temp_folder/final
    Không đổi tên (giữ nguyên filename). Nếu trùng tên thì xóa file cũ rồi lưu mới.
    Vẫn trả về session_id để dùng cho kết quả (result) theo session.
    """
    if not session_id:
        session_id = str(uuid.uuid4())

    target_dir = REF_DIR if prefix == "ref" else FINAL_DIR
    safe_name = os.path.basename(file_storage.filename) or f"{prefix}.pdf"
    dest = target_dir / safe_name

    if dest.exists():
        dest.unlink()

    file_storage.save(dest)
    return dest, session_id


def _require_files() -> Tuple[Path, Path, str]:
    """
    Lấy file ref và final:
    - Nếu có ref_filename, final_filename và session_id trong form → dùng file đã upload
    - Nếu không → upload mới từ request.files
    
    Trả về (ref_path, final_path, session_id).
    """
    # Lấy session_id từ form
    session_id = request.form.get("session_id")
    
    # Kiểm tra xem có filename đã upload không
    ref_filename = request.form.get("ref_filename")
    final_filename = request.form.get("final_filename")
    
    # Nếu có cả 2 filename và session_id → dùng file đã upload trong thư mục cố định
    if ref_filename and final_filename and session_id:
        ref_path = REF_DIR / os.path.basename(ref_filename)
        final_path = FINAL_DIR / os.path.basename(final_filename)

        # Kiểm tra file có tồn tại không
        if not ref_path.exists():
            raise ValueError(f"File reference không tồn tại: {ref_filename}. Kiểm tra trong: {REF_DIR}")
        if not final_path.exists():
            raise ValueError(f"File final không tồn tại: {final_filename}. Kiểm tra trong: {FINAL_DIR}")

        return ref_path, final_path, session_id
    
    # Upload mới nếu không có filename
    if "ref_pdf" not in request.files:
        raise ValueError("Thiếu file ref_pdf. Cần upload file hoặc cung cấp ref_filename trong form data.")
    if "final_pdf" not in request.files:
        raise ValueError("Thiếu file final_pdf. Cần upload file hoặc cung cấp final_filename trong form data.")
    
    # Tạo session mới nếu chưa có
    if not session_id:
        session_id = str(uuid.uuid4())
    
    ref_path, _ = _save_upload(request.files["ref_pdf"], "ref", session_id)
    final_path, _ = _save_upload(request.files["final_pdf"], "final", session_id)
    return ref_path, final_path, session_id


@app.get("/")
def index():
    """Trang chủ - hiển thị thông tin về API."""
    return jsonify({
        "service": "Compare Batiment Flask Backend",
        "version": "1.0.0",
        "endpoints": {
            "health": "GET /api/health",
            "upload_ref": "POST /api/upload/ref",
            "upload_final": "POST /api/upload/final",
            "compare_mode1": "POST /api/compare/mode1",
            "compare_mode2": "POST /api/compare/mode2",
            "compare_mode3": "POST /api/compare/mode3",
            "download": "GET /api/download/<filename>"
        },
        "status": "running"
    })


@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


@app.post("/api/compare/mode1")
def compare_mode1_endpoint():
    try:
        ref_path, final_path, session_id = _require_files()
        session_dir = _get_session_dir(session_id)
        output_path = session_dir / f"mode1_{uuid.uuid4().hex}.pdf"

        result = compare_mode1(
            ref_pdf_path=str(ref_path),
            final_pdf_path=str(final_path),
            output_path=str(output_path),
        )
        # Trả về tên file và session_id để frontend có thể download
        # Mode1 now returns output_pdf1 and output_pdf2 (both annotated PDFs)
        if "output_pdf1" in result:
            result["output_pdf1"] = os.path.basename(result["output_pdf1"])
        if "output_pdf2" in result:
            result["output_pdf2"] = os.path.basename(result["output_pdf2"])
        
        # Tự động schedule cleanup sau 30 giây (fallback nếu JavaScript không chạy)
        import time
        cleanup_time = time.time() + 30
        _sessions_ready_for_cleanup[session_id] = cleanup_time
        _session_last_access[session_id] = time.time()
        
        # Convert result để đảm bảo JSON serializable (xử lý int64, float64, etc.)
        result_serializable = _convert_to_json_serializable(result)
        
        return jsonify({"success": True, "data": result_serializable, "session_id": session_id})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e), "type": "ValueError"}), 400
    except Exception as e:
        import traceback
        error_detail = traceback.format_exc()
        return jsonify({
            "success": False,
            "error": str(e),
            "type": type(e).__name__,
            "detail": error_detail
        }), 400


@app.post("/api/compare/mode2")
def compare_mode2_endpoint():
    try:
        ref_path, final_path, session_id = _require_files()
        session_dir = _get_session_dir(session_id)
        output_path = session_dir / f"mode2_{uuid.uuid4().hex}.pdf"
        # Ưu tiên: env variable > form data > header
        api_key = os.environ.get("OPENAI_API_KEY") or request.form.get("api_key") or request.headers.get("X-API-Key")

        result = compare_mode2(
            ref_pdf_path=str(ref_path),
            final_pdf_path=str(final_path),
            output_path=str(output_path),
            api_key=api_key,
        )
        # Trả về tên file và session_id
        if "output_pdf" in result:
            result["output_pdf"] = os.path.basename(result["output_pdf"])
        
        # Tự động schedule cleanup sau 30 giây (fallback nếu JavaScript không chạy)
        import time
        cleanup_time = time.time() + 30
        _sessions_ready_for_cleanup[session_id] = cleanup_time
        _session_last_access[session_id] = time.time()
        
        # Convert result để đảm bảo JSON serializable (xử lý int64, float64, etc.)
        result_serializable = _convert_to_json_serializable(result)
        
        return jsonify({"success": True, "data": result_serializable, "session_id": session_id})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e), "type": "ValueError"}), 400
    except Exception as e:
        import traceback
        return jsonify({
            "success": False,
            "error": str(e),
            "type": type(e).__name__,
            "detail": traceback.format_exc()
        }), 400


@app.post("/api/compare/mode3")
def compare_mode3_endpoint():
    try:
        ref_path, final_path, session_id = _require_files()
        session_dir = _get_session_dir(session_id)
        output_ref = session_dir / f"mode3_ref_{uuid.uuid4().hex}.pdf"
        output_final = session_dir / f"mode3_final_{uuid.uuid4().hex}.pdf"

        result = compare_mode3(
            ref_pdf_path=str(ref_path),
            final_pdf_path=str(final_path),
            output_ref=str(output_ref),
            output_final=str(output_final),
        )
        # Trả về tên file và session_id
        if "output_ref" in result:
            result["output_ref"] = os.path.basename(result["output_ref"])
        if "output_final" in result:
            result["output_final"] = os.path.basename(result["output_final"])
        
        # Tự động schedule cleanup sau 30 giây (fallback nếu JavaScript không chạy)
        import time
        cleanup_time = time.time() + 30
        _sessions_ready_for_cleanup[session_id] = cleanup_time
        _session_last_access[session_id] = time.time()
        
        # Convert result để đảm bảo JSON serializable (xử lý int64, float64, etc.)
        result_serializable = _convert_to_json_serializable(result)
        
        return jsonify({"success": True, "data": result_serializable, "session_id": session_id})
    except ValueError as e:
        return jsonify({"success": False, "error": str(e), "type": "ValueError"}), 400
    except Exception as e:
        import traceback
        return jsonify({
            "success": False,
            "error": str(e),
            "type": type(e).__name__,
            "detail": traceback.format_exc()
        }), 400


@app.post("/api/upload/ref")
def upload_ref_pdf():
    """Upload ref PDF vào temp_folder/ref (giữ tên file, ghi đè nếu trùng)."""
    try:
        if "ref_pdf" not in request.files:
            return jsonify({"success": False, "error": "Thiếu file ref_pdf"}), 400
        
        file_storage = request.files["ref_pdf"]
        if file_storage.filename == "":
            return jsonify({"success": False, "error": "File rỗng"}), 400
        
        # Lấy session_id từ form, tạo mới nếu chưa có
        session_id = request.form.get("session_id")
        if not session_id:
            session_id = str(uuid.uuid4())
        
        # Lưu file vào thư mục cố định
        dest, session_id = _save_upload(file_storage, "ref", session_id)
        filename = dest.name
        
        return jsonify({"success": True, "filename": filename, "session_id": session_id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.post("/api/upload/final")
def upload_final_pdf():
    """Upload final PDF vào temp_folder/final (giữ tên file, ghi đè nếu trùng)."""
    try:
        if "final_pdf" not in request.files:
            return jsonify({"success": False, "error": "Thiếu file final_pdf"}), 400
        
        file_storage = request.files["final_pdf"]
        if file_storage.filename == "":
            return jsonify({"success": False, "error": "File rỗng"}), 400
        
        # Lấy session_id từ form, tạo mới nếu chưa có
        session_id = request.form.get("session_id")
        if not session_id:
            session_id = str(uuid.uuid4())
        
        # Lưu file vào thư mục cố định
        dest, session_id = _save_upload(file_storage, "final", session_id)
        filename = dest.name
        
        return jsonify({"success": True, "filename": filename, "session_id": session_id})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 400


@app.route("/api/download/<path:filename>", methods=['GET', 'OPTIONS'])
def download_pdf(filename: str):
    """Endpoint để tải PDF từ server. Tìm file trong thư mục result theo session và thư mục ref/final cố định."""
    # Handle OPTIONS request for CORS preflight
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Methods', 'GET, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Content-Security-Policy', "frame-ancestors *;")
        return response
    # Lấy session_id và filename từ query params hoặc path
    session_id = request.args.get("session_id")
    safe_filename = os.path.basename(filename)
    
    file_path = None
    search_log = []
    
    # 1. Nếu có session_id → tìm trực tiếp trong session directory (kết quả)
    if session_id:
        session_dir = TEMP_DIR / session_id
        potential_path = session_dir / safe_filename
        search_log.append(f"Searching in session_dir: {potential_path} (exists: {potential_path.exists()})")
        if potential_path.exists() and potential_path.is_file():
            file_path = potential_path
    
    # 2. Nếu chưa tìm thấy → tìm trong thư mục ref/final cố định
    if not file_path or not file_path.exists():
        for fixed_dir in (REF_DIR, FINAL_DIR):
            potential_path = fixed_dir / safe_filename
            search_log.append(f"Searching in fixed_dir: {potential_path} (exists: {potential_path.exists()})")
            if potential_path.exists() and potential_path.is_file():
                file_path = potential_path
                break

    # 3. Nếu chưa tìm thấy → tìm trong tất cả session directories
    if not file_path or not file_path.exists():
        try:
            if TEMP_DIR.exists():
                # Extract prefix từ filename (ví dụ: "ref_" từ "ref_1a92729bd7664c34b8398ea1154ed5e9.pdf")
                prefix = None
                if "_" in safe_filename:
                    prefix = safe_filename.split("_")[0] + "_"  # "ref_" hoặc "final_"
                
                # Tìm exact match trước trong tất cả sessions
                for session_dir in TEMP_DIR.iterdir():
                    if session_dir.is_dir():
                        potential_path = session_dir / safe_filename
                        search_log.append(f"Searching in: {potential_path} (exists: {potential_path.exists()})")
                        if potential_path.exists() and potential_path.is_file():
                            file_path = potential_path
                            break
                
                # Nếu vẫn không tìm thấy exact match và có prefix, tìm file có cùng prefix
                if (not file_path or not file_path.exists()) and prefix:
                    # Ưu tiên tìm trong session được chỉ định trước (nếu có)
                    if session_id:
                        session_dir = TEMP_DIR / session_id
                        if session_dir.exists() and session_dir.is_dir():
                            for file_in_session in session_dir.iterdir():
                                if file_in_session.is_file() and file_in_session.name.startswith(prefix):
                                    file_path = file_in_session
                                    search_log.append(f"Found file with prefix '{prefix}' in requested session {session_id}: {file_path}")
                                    break
                    
                    # Nếu vẫn không tìm thấy trong session được chỉ định, tìm trong tất cả sessions
                    if not file_path or not file_path.exists():
                        for session_dir in TEMP_DIR.iterdir():
                            if session_dir.is_dir():
                                for file_in_session in session_dir.iterdir():
                                    if file_in_session.is_file() and file_in_session.name.startswith(prefix):
                                        file_path = file_in_session
                                        search_log.append(f"Found file with prefix '{prefix}' in session {session_dir.name}: {file_path}")
                                        break
                            if file_path and file_path.exists():
                                    break
            else:
                search_log.append(f"TEMP_DIR does not exist: {TEMP_DIR}")
        except Exception as e:
            search_log.append(f"Error iterating TEMP_DIR: {str(e)}")
    
    # 3. Nếu vẫn không tìm thấy, thử tìm trong TEMP_DIR trực tiếp (backward compatible)
    if not file_path or not file_path.exists():
        potential_path = TEMP_DIR / safe_filename
        search_log.append(f"Searching in TEMP_DIR directly: {potential_path} (exists: {potential_path.exists()})")
        if potential_path.exists() and potential_path.is_file():
            file_path = potential_path
    
    if not file_path or not file_path.exists():
        # Debug: list các session directories có sẵn
        session_dirs = []
        try:
            if TEMP_DIR.exists():
                for sd in TEMP_DIR.iterdir():
                    if sd.is_dir():
                        session_dirs.append(sd.name)
                        # List files in this session
                        files_in_session = [f.name for f in sd.iterdir() if f.is_file()]
                        search_log.append(f"Session {sd.name} has files: {files_in_session[:5]}")
        except Exception as e:
            search_log.append(f"Error listing sessions: {str(e)}")
        
        return jsonify({
            "error": "File not found",
            "filename": safe_filename,
            "temp_dir": str(TEMP_DIR),
            "base_dir": str(BASE_DIR),
            "searched_path": str(file_path) if file_path else "None",
            "session_id_provided": session_id or "None",
            "available_sessions": session_dirs[:10],
            "search_log": search_log[-5:]  # Last 5 search attempts
        }), 404
    
    if not file_path.is_file():
        return jsonify({"error": "Path is not a file"}), 400
    
    # Kiểm tra file nằm trong các thư mục cho phép để bảo mật
    allowed_roots = [TEMP_DIR.resolve(), REF_DIR.resolve(), FINAL_DIR.resolve()]
    try:
        resolved = file_path.resolve()
        if not any(resolved.is_relative_to(root) for root in allowed_roots):
            return jsonify({"error": "Access denied"}), 403
    except Exception:
        return jsonify({"error": "Access denied"}), 403
    
    # Update last access time cho session
    if session_id:
        _session_last_access[session_id] = time.time()
    
    # Tạo response với headers cho phép iframe embedding
    response = send_file(
        str(file_path),
        as_attachment=False,  # Hiển thị trong browser, không download
        download_name=safe_filename,
        mimetype="application/pdf"
    )
    
    # Thêm headers để cho phép iframe embedding và CORS
    # Xóa X-Frame-Options (hoặc không set) để cho phép embed trong iframe từ mọi origin
    # Dùng Content-Security-Policy với frame-ancestors để cho phép iframe
    response.headers.add('Content-Security-Policy', "frame-ancestors *;")
    response.headers.add('X-Content-Type-Options', 'nosniff')
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Methods', 'GET, OPTIONS')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
    
    # Xóa X-Frame-Options nếu có (để không chặn iframe)
    if 'X-Frame-Options' in response.headers:
        del response.headers['X-Frame-Options']
    
    return response


@app.post("/api/session/<session_id>/ready-for-cleanup")
def mark_session_ready_for_cleanup(session_id: str):
    """Đánh dấu session sẵn sàng để cleanup sau khi PDFs đã load xong."""
    try:
        import time
        # Schedule cleanup sau 5 giây nữa (để đảm bảo tất cả requests đã xong)
        cleanup_time = time.time() + 5
        _sessions_ready_for_cleanup[session_id] = cleanup_time
        response = jsonify({"success": True, "message": f"Session {session_id} marked for cleanup"})
        # Thêm CORS headers để JavaScript có thể gọi từ browser
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        return response
    except Exception as e:
        response = jsonify({"error": str(e)})
        response.headers.add('Access-Control-Allow-Origin', '*')
        return response, 500


@app.route("/api/session/<session_id>/ready-for-cleanup", methods=['OPTIONS'])
def mark_session_ready_for_cleanup_options(session_id: str):
    """Handle CORS preflight request."""
    response = jsonify({})
    response.headers.add('Access-Control-Allow-Origin', '*')
    response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
    response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
    return response


@app.delete("/api/delete/session/<session_id>")
def delete_session(session_id: str):
    """Xóa toàn bộ thư mục session và tất cả files trong đó."""
    try:
        # Chỉ cho phép xóa thư mục session trong TEMP_DIR
        session_dir = TEMP_DIR / session_id
        
        # Kiểm tra session_dir nằm trong TEMP_DIR để bảo mật
        try:
            session_dir.resolve().relative_to(TEMP_DIR.resolve())
        except ValueError:
            return jsonify({"error": "Access denied"}), 403
        
        if session_dir.exists() and session_dir.is_dir():
            shutil.rmtree(session_dir)
            # Xóa khỏi cleanup tracking
            _sessions_ready_for_cleanup.pop(session_id, None)
            return jsonify({"success": True, "message": f"Deleted session {session_id}"})
        else:
            return jsonify({"error": "Session not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _check_and_cleanup_sessions():
    """Kiểm tra và cleanup các sessions đã sẵn sàng hoặc quá cũ."""
    import time
    current_time = time.time()
    
    # 1. Cleanup sessions đã được mark ready
    sessions_to_cleanup = [
        session_id for session_id, cleanup_time in _sessions_ready_for_cleanup.items()
        if current_time >= cleanup_time
    ]
    
    for session_id in sessions_to_cleanup:
        try:
            session_dir = TEMP_DIR / session_id
            if session_dir.exists() and session_dir.is_dir():
                shutil.rmtree(session_dir)
            _sessions_ready_for_cleanup.pop(session_id, None)
            _session_last_access.pop(session_id, None)
        except Exception:
            pass
    
    # 2. Cleanup sessions cũ (không được access > 1 giờ)
    old_sessions = [
        session_id for session_id, last_access in _session_last_access.items()
        if current_time - last_access > 3600  # 1 giờ = 3600 giây
    ]
    
    for session_id in old_sessions:
        try:
            session_dir = TEMP_DIR / session_id
            if session_dir.exists() and session_dir.is_dir():
                shutil.rmtree(session_dir)
            _session_last_access.pop(session_id, None)
            _sessions_ready_for_cleanup.pop(session_id, None)
        except Exception:
            pass


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(host="0.0.0.0", port=port, debug=False)

