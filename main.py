"""
Streamlit frontend: upload 2 PDF, chọn mode, gọi Flask backend để xử lý.
"""

from __future__ import annotations

import os
import base64
import shutil
import tempfile
import uuid
import socket
import threading
import time
from pathlib import Path
from typing import Dict, Tuple
from http.server import HTTPServer, SimpleHTTPRequestHandler

import requests
import streamlit as st
import streamlit.components.v1 as components


st.set_page_config(page_title="Compare Batiment", page_icon="📄", layout="wide")

# HTTP Server để serve PDF files
PDF_SERVE_DIR = Path(tempfile.gettempdir()) / "compare_batiment_pdfs"
PDF_SERVE_DIR.mkdir(parents=True, exist_ok=True)
HTTP_SERVER_PORT = 8765


class PDFHandler(SimpleHTTPRequestHandler):
    """HTTP Handler với CORS headers để serve PDF."""
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PDF_SERVE_DIR), **kwargs)
    
    def end_headers(self):
        # Thêm CORS headers
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET')
        self.send_header('Access-Control-Allow-Headers', '*')
        super().end_headers()
    
    def log_message(self, format, *args):
        # Ẩn log messages
        pass


def _is_port_available(port: int) -> bool:
    """Kiểm tra port có sẵn không."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(('', port))
            return True
        except OSError:
            return False


def _start_pdf_server() -> int:
    """Khởi động HTTP server để serve PDF. Trả về port number."""
    # Kiểm tra xem server đã chạy chưa
    if 'pdf_server_running' in st.session_state and st.session_state.pdf_server_running:
        return st.session_state.get('pdf_server_port', HTTP_SERVER_PORT)
    
    # Tìm port khả dụng
    port = HTTP_SERVER_PORT
    for _ in range(10):
        if _is_port_available(port):
            try:
                server = HTTPServer(("", port), PDFHandler)
                server.allow_reuse_address = True
                
                # Chạy server trong daemon thread
                server_thread = threading.Thread(target=server.serve_forever, daemon=True)
                server_thread.start()
                
                st.session_state['pdf_server'] = server
                st.session_state['pdf_server_running'] = True
                st.session_state['pdf_server_port'] = port
                
                time.sleep(0.3)  # Đợi server khởi động
                return port
            except Exception:
                port += 1
        else:
            port += 1
    
    return None


def _upload_ref_to_backend(backend_url: str, ref_file, session_id: str = None) -> Tuple[str, str]:
    """Upload reference PDF lên backend và trả về (filename, session_id)."""
    url = backend_url.rstrip("/") + "/api/upload/ref"
    files = {
        "ref_pdf": (ref_file.name, ref_file.getvalue(), "application/pdf"),
    }
    data = {}
    if session_id:
        data["session_id"] = session_id
    resp = requests.post(url, files=files, data=data, timeout=60)
    resp.raise_for_status()
    result = resp.json()
    if result.get("success"):
        return result.get("filename"), result.get("session_id")
    raise RuntimeError(result.get("error", "Upload failed"))


def _upload_final_to_backend(backend_url: str, final_file, session_id: str = None) -> Tuple[str, str]:
    """Upload final PDF lên backend và trả về (filename, session_id)."""
    url = backend_url.rstrip("/") + "/api/upload/final"
    files = {
        "final_pdf": (final_file.name, final_file.getvalue(), "application/pdf"),
    }
    data = {}
    if session_id:
        data["session_id"] = session_id
    resp = requests.post(url, files=files, data=data, timeout=60)
    resp.raise_for_status()
    result = resp.json()
    if result.get("success"):
        return result.get("filename"), result.get("session_id")
    raise RuntimeError(result.get("error", "Upload failed"))


def _create_new_session() -> str:
    """Tạo session ID mới."""
    return str(uuid.uuid4())


def _cleanup_session(backend_url: str, session_id: str):
    """Xóa session: xóa files trong session state và thư mục session trên backend."""
    if not session_id:
        return
    
    # Xóa files trong session state
    session_key_ref = f"session_{session_id}_ref_filename"
    session_key_final = f"session_{session_id}_final_filename"
    
    st.session_state.pop(session_key_ref, None)
    st.session_state.pop(session_key_final, None)
    
    # Xóa toàn bộ thư mục session trên backend
    try:
        url = backend_url.rstrip("/") + f"/api/delete/session/{session_id}"
        requests.delete(url, timeout=5)
    except:
        pass  # Ignore errors when deleting
    
    # Xóa session ID khỏi session state
    if st.session_state.get("current_session_id") == session_id:
        st.session_state.pop("current_session_id", None)


def _send_request(
    backend_url: str,
    endpoint: str,
    ref_file,
    final_file,
    data: Dict,
    ref_filename: str = None,
    final_filename: str = None,
    session_id: str = None,
) -> Dict:
    """
    Gửi request đến backend.
    Nếu có ref_filename và final_filename → gửi filename thay vì upload lại file.
    """
    url = backend_url.rstrip("/") + endpoint
    
    # Nếu có filename đã upload → dùng filename, không upload lại
    if ref_filename and final_filename and session_id:
        data = data.copy()
        data["ref_filename"] = ref_filename
        data["final_filename"] = final_filename
        data["session_id"] = session_id
        # Vẫn cần gửi empty files để đảm bảo content-type là multipart/form-data
        files = {}
        resp = requests.post(url, files=files, data=data, timeout=300)
    else:
        # Upload file như cũ
        files = {
            "ref_pdf": (ref_file.name, ref_file.getvalue(), "application/pdf"),
            "final_pdf": (final_file.name, final_file.getvalue(), "application/pdf"),
        }
        if session_id:
            data = data.copy()
            data["session_id"] = session_id
        resp = requests.post(url, files=files, data=data, timeout=300)
    
    resp.raise_for_status()
    return resp.json()


def _download_pdf_from_backend(backend_url: str, pdf_path: str, local_filename: str) -> str:
    """Tải PDF từ backend về local và trả về đường dẫn local."""
    # Lấy tên file từ đường dẫn (có thể là full path hoặc chỉ tên file)
    filename = os.path.basename(pdf_path)
    url = backend_url.rstrip("/") + "/api/download/" + filename
    
    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()
        
        # Lưu vào temp directory của Streamlit
        temp_dir = Path(tempfile.gettempdir()) / "compare_batiment_pdfs"
        temp_dir.mkdir(parents=True, exist_ok=True)
        
        local_path = temp_dir / local_filename
        with open(local_path, "wb") as f:
            f.write(resp.content)
        
        return str(local_path)
    except requests.exceptions.HTTPError as e:
        st.error(f"Erreur lors du chargement du PDF depuis le backend: {e}")
        st.info(f"URL: {url}")
        return None
    except Exception as e:
        st.error(f"Erreur lors du chargement du PDF: {e}")
        return None


def _display_pdf_iframe(pdf_path: str, height: int = 800):
    """
    Hiển thị PDF qua iframe HTML sử dụng HTTP server (phù hợp với file lớn).
    """
    if not pdf_path or not os.path.exists(pdf_path):
        st.error(f"PDF n'existe pas: {pdf_path}")
        return
    
    try:
        # Copy file vào serve directory nếu chưa có
        filename = os.path.basename(pdf_path)
        serve_path = PDF_SERVE_DIR / filename
        
        # Chỉ copy nếu file khác hoặc chưa tồn tại
        if not serve_path.exists() or serve_path.stat().st_mtime < Path(pdf_path).stat().st_mtime:
            shutil.copy2(pdf_path, serve_path)
        
        # Khởi động HTTP server
        port = _start_pdf_server()
        if port is None:
            st.error("Impossible de démarrer le serveur HTTP pour afficher le PDF")
            return
        
        # Tạo URL để serve PDF
        pdf_url = f"http://localhost:{port}/{filename}"
        
        # Tạo iframe HTML
        iframe_html = f'''
        <iframe
            src="{pdf_url}"
            width="100%"
            height="{height}px"
            type="application/pdf"
            style="border: 2px solid #444; border-radius: 8px;"
        ></iframe>
        '''
        
        components.html(iframe_html, height=height + 10)
    except Exception as e:
        st.error(f"Erreur lors de l'affichage du PDF: {e}")


def _mark_session_ready_for_cleanup(backend_url: str, session_id: str):
    """
    Gọi backend để đánh dấu session sẵn sàng cleanup ngay lập tức.
    """
    if not session_id:
        return
    
    cleanup_key = f"cleanup_called_{session_id}"
    
    # Chỉ gọi một lần cho mỗi session
    if cleanup_key not in st.session_state:
        st.session_state[cleanup_key] = True
        
        def cleanup_now():
            """Gọi backend để mark session ready for cleanup."""
            try:
                url = backend_url.rstrip("/") + f"/api/session/{session_id}/ready-for-cleanup"
                response = requests.post(url, timeout=5)
                if response.status_code == 200:
                    print(f"✅ Session {session_id} marked for cleanup")
                else:
                    print(f"⚠️ Failed to mark session {session_id} for cleanup: {response.status_code}")
            except Exception as e:
                print(f"❌ Error marking session {session_id} for cleanup: {e}")
        
        # Chạy cleanup trong background thread
        cleanup_thread = threading.Thread(target=cleanup_now, daemon=True)
        cleanup_thread.start()


def _display_pdf_from_backend(backend_url_external: str, pdf_filename: str, height: int = 800, session_id: str = None, auto_cleanup: bool = True):
    """
    Hiển thị PDF trực tiếp từ backend qua iframe (không cần tải về máy khách).
    Dùng backend_url_external vì browser (client-side) cần truy cập được.
    
    Args:
        backend_url_external: URL mà browser có thể truy cập (IP công cộng)
        auto_cleanup: Nếu True, tự động cleanup session khi PDF đã load xong.
                      Mặc định True vì hàm này thường dùng cho kết quả sau khi chạy so sánh.
    """
    pdf_url = f"{backend_url_external.rstrip('/')}/api/download/{pdf_filename}"
    if session_id:
        pdf_url += f"?session_id={session_id}"
    
    # Tạo unique ID cho iframe để track
    iframe_id = f"pdf_iframe_{uuid.uuid4().hex[:8]}"
    
    # JavaScript để detect khi PDF load xong và gọi cleanup (chỉ khi auto_cleanup=True)
    cleanup_js = ""
    if session_id and auto_cleanup:
        cleanup_url = f"{backend_url_external.rstrip('/')}/api/session/{session_id}/ready-for-cleanup"
        cleanup_js = f'''
        <script>
        (function() {{
            var pdfElement = document.getElementById('{iframe_id}');
            var cleanupCalled = false;
            
            function markCleanup() {{
                if (cleanupCalled) return;
                cleanupCalled = true;
                
                // Gọi API cleanup
                fetch('{cleanup_url}', {{
                    method: 'POST',
                    mode: 'cors'
                }}).then(function(response) {{
                    console.log('✅ Session cleanup marked');
                }}).catch(function(error) {{
                    console.log('⚠️ Cleanup error:', error);
                }});
            }}
            
            // Detect khi embed load xong
            if (pdfElement && pdfElement.onload !== undefined) {{
                pdfElement.onload = function() {{
                    // Đợi thêm 1 giây để đảm bảo PDF đã render xong
                    setTimeout(markCleanup, 1000);
                }};
            }}
            
            // Fallback: nếu onload không fire, đợi 3 giây
            setTimeout(markCleanup, 3000);
        }})();
        </script>
        '''
    
    # Render PDF - dùng object tag thay vì iframe để tránh nested iframe với components.html
    # Hoặc dùng embed tag
    pdf_html = f'''
    <div style="width: 100%; height: {height}px; border: 2px solid #444; border-radius: 8px; overflow: hidden;">
        <embed
            id="{iframe_id}"
            src="{pdf_url}"
            type="application/pdf"
            width="100%"
            height="{height}px"
            style="border: none;"
        />
        {cleanup_js}
    </div>
    '''
    # Dùng st.markdown với unsafe_allow_html để tránh nested iframe
    st.markdown(pdf_html, unsafe_allow_html=True)


def _display_pdf_from_backend_url(backend_url_external: str, filename: str, height: int = 800, session_id: str = None, auto_cleanup: bool = False):
    """
    Hiển thị PDF từ backend URL.
    Dùng backend_url_external vì browser (client-side) cần truy cập được.
    
    Args:
        backend_url_external: URL mà browser có thể truy cập (IP công cộng)
        auto_cleanup: Nếu True, tự động cleanup session khi PDF đã load xong.
                      Chỉ nên True khi đã chạy so sánh xong, không dùng cho preview.
    """
    if not backend_url_external or not filename:
        return
    
    try:
        pdf_url = f"{backend_url_external.rstrip('/')}/api/download/{filename}"
        if session_id:
            pdf_url += f"?session_id={session_id}"
        
        # Tạo unique ID cho iframe để track
        iframe_id = f"pdf_iframe_{uuid.uuid4().hex[:8]}"
        
        # JavaScript để detect khi PDF load xong và gọi cleanup (chỉ khi auto_cleanup=True)
        cleanup_js = ""
        if session_id and auto_cleanup:
            cleanup_url = f"{backend_url_external.rstrip('/')}/api/session/{session_id}/ready-for-cleanup"
            cleanup_js = f'''
            <script>
            (function() {{
                var iframe = document.getElementById('{iframe_id}');
                var cleanupCalled = false;
                
                function markCleanup() {{
                    if (cleanupCalled) return;
                    cleanupCalled = true;
                    
                    // Gọi API cleanup
                    fetch('{cleanup_url}', {{
                        method: 'POST',
                        mode: 'cors'
                    }}).then(function(response) {{
                        console.log('✅ Session cleanup marked');
                    }}).catch(function(error) {{
                        console.log('⚠️ Cleanup error:', error);
                    }});
                }}
                
                // Detect khi iframe load xong
                iframe.onload = function() {{
                    // Đợi thêm 1 giây để đảm bảo PDF đã render xong
                    setTimeout(markCleanup, 1000);
                }};
                
                // Fallback: nếu onload không fire, đợi 3 giây
                setTimeout(markCleanup, 3000);
            }})();
            </script>
            '''
        
        # Render PDF - dùng embed tag thay vì iframe để tránh nested iframe với components.html
        pdf_html = f'''
        <div style="width: 100%; height: {height}px; border: 2px solid #444; border-radius: 8px; overflow: hidden;">
            <embed
                id="{iframe_id}"
                src="{pdf_url}"
                type="application/pdf"
                width="100%"
                height="{height}px"
                style="border: none;"
            />
            {cleanup_js}
        </div>
        '''
        # Dùng st.markdown với unsafe_allow_html để tránh nested iframe
        st.markdown(pdf_html, unsafe_allow_html=True)
    except Exception as e:
        st.error(f"Lỗi khi hiển thị PDF từ backend: {e}")


def _display_pdf_from_upload_preview(uploaded_file, height: int = 700):
    """
    Hiển thị PDF từ file upload của Streamlit (preview, dùng base64).
    """
    if uploaded_file is None:
        return
    
    try:
        pdf_bytes = uploaded_file.getvalue()
        base64_pdf = base64.b64encode(pdf_bytes).decode('utf-8')
        
        iframe_html = f'''
        <iframe
            src="data:application/pdf;base64,{base64_pdf}"
            width="100%"
            height="{height}px"
            type="application/pdf"
            style="border: 2px solid #444; border-radius: 8px;"
        ></iframe>
        '''
        components.html(iframe_html, height=height + 10)
    except Exception as e:
        st.error(f"Lỗi khi hiển thị PDF preview: {e}")


def _download_button(label: str, path: str, key: str):
    """Download button từ file local (dùng khi cần)."""
    if not path or not os.path.exists(path):
        st.warning(f"Không tìm thấy file: {path}")
        return
    with open(path, "rb") as f:
        st.download_button(
            label,
            data=f,
            file_name=Path(path).name,
            mime="application/pdf",
            key=key,
            use_container_width=True,
        )


st.title("🔍 Comparaison Bâtiment (Streamlit + Flask)")
st.markdown(
    "Téléchargez 2 PDF, choisissez le mode de comparaison et recevez le PDF annoté. "
    "Le backend Flask fonctionne sur la même machine ou une autre machine du réseau."
)



# Tách 2 loại URL:
# - backend_url_internal: dùng cho server-side API calls (localhost trong Docker)
# - backend_url_external: dùng cho client-side iframe (IP công cộng để browser truy cập)

# Internal URL: dùng cho server-side calls
if os.environ.get("BACKEND_URL_INTERNAL"):
    default_backend_url_internal = os.environ.get("BACKEND_URL_INTERNAL")
elif os.path.exists("/.dockerenv"):  # Chạy trong Docker
    default_backend_url_internal = "http://localhost:5000/"
else:
    default_backend_url_internal = "http://localhost:5000"

# External URL: dùng cho client-side iframe (browser cần truy cập được)
# Nếu chạy local trên Windows → dùng localhost
# Nếu chạy trên server/Docker → dùng IP công cộng
if os.environ.get("BACKEND_URL_EXTERNAL"):
    default_backend_url_external = os.environ.get("BACKEND_URL_EXTERNAL")
elif os.environ.get("BACKEND_URL"):  # Fallback to BACKEND_URL nếu có
    default_backend_url_external = os.environ.get("BACKEND_URL")
elif os.path.exists("/.dockerenv"):  # Chạy trong Docker
    # Trong Docker, dùng IP công cộng
    default_backend_url_external = "http://localhost:5000/"
else:
    # Chạy local trên Windows → dùng localhost
    default_backend_url_external = "http://localhost:5000/"

# External URL: tự động từ environment variable, không cho user thay đổi
backend_url_external = default_backend_url_external

# Internal URL: tự động, không cho user thay đổi
backend_url_internal = default_backend_url_internal

# # Test connection button (dùng internal URL cho server-side calls)
# if st.sidebar.button("🔌 Test kết nối Backend", use_container_width=True):
#     try:
#         resp = requests.get(f"{backend_url_internal.rstrip('/')}/api/health", timeout=5)
#         if resp.status_code == 200:
#             st.sidebar.success("✅ Kết nối thành công!")
#         else:
#             st.sidebar.error(f"❌ Backend trả về lỗi: {resp.status_code}")
#     except requests.exceptions.ConnectionError:
#         st.sidebar.error("❌ Không thể kết nối đến backend!")
#         st.sidebar.info("Kiểm tra:\n• Backend đã chạy chưa?\n• URL đúng chưa?\n• Firewall đã mở port chưa?")
#     except Exception as e:
#         st.sidebar.error(f"❌ Lỗi: {str(e)}")

st.sidebar.markdown("---")

mode = st.sidebar.radio(
    "Choisir le mode",
    (
        "mode1",
        "mode2",
        "mode3",
    ),
    format_func=lambda x: {
        "mode1": "Mode 1 - PAGES 2025",
        "mode2": "Mode 2 - LaSolution GPT",
        "mode3": "Mode 3 - Assemblage text diff",
    }[x],
)

api_key = None
if mode == "mode2":
    # Thử load từ .env file trước (nếu có) - cùng cấp với main.py và backend_flask.py
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        try:
            with open(env_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        key, value = line.split("=", 1)
                        key = key.strip()
                        value = value.strip().strip('"').strip("'")
                        if key == "OPENAI_API_KEY":
                            os.environ[key] = value
        except Exception as e:
            pass  # Ignore errors when reading .env
    
    # Đọc từ environment variable
    api_key = os.environ.get("OPENAI_API_KEY")
    
    # Debug: hiển thị thông tin
    if not api_key:
        st.sidebar.warning("⚠️ OPENAI_API_KEY không tìm thấy")
        st.sidebar.info("💡 Cách set:\n1. Set env: `$env:OPENAI_API_KEY='your-key'` (PowerShell)\n2. Hoặc tạo file `.env` trong thư mục `compare_Batiment_flaskapp/` với nội dung: `OPENAI_API_KEY=your-key`")

st.sidebar.markdown("---")
ref_file = st.sidebar.file_uploader("Reference PDF", type=["pdf"], key="ref_pdf")
final_file = st.sidebar.file_uploader("Final PDF", type=["pdf"], key="final_pdf")

# Hiển thị preview PDF ngay khi upload
if ref_file and final_file:
    st.sidebar.success("✅ Đã upload đủ 2 file PDF")
    st.sidebar.markdown("---")
    
    # Tạo session key dựa trên file signature và mode
    current_file_signature = f"{ref_file.name}_{ref_file.size}_{final_file.name}_{final_file.size}"
    current_session_id = st.session_state.get("current_session_id")
    stored_signature = st.session_state.get("current_file_signature")
    stored_mode = st.session_state.get("current_mode")
    
    # Kiểm tra xem file có thay đổi không HOẶC mode có thay đổi không
    file_changed = (stored_signature != current_file_signature) or (current_session_id is None)
    mode_changed = (stored_mode != mode)
    
    # Nếu file thay đổi HOẶC mode thay đổi → xóa session cũ và tạo session mới
    if (file_changed or mode_changed) and current_session_id:
        old_session_id = current_session_id
        # Xóa session cũ trên backend (nhưng không xóa session keys ngay để tránh race condition)
        try:
            url = backend_url_internal.rstrip("/") + f"/api/delete/session/{old_session_id}"
            requests.delete(url, timeout=5)
        except:
            pass  # Ignore errors when deleting
        
        # Xóa tất cả keys liên quan đến session cũ
        old_session_key_ref = f"session_{old_session_id}_ref_filename"
        old_session_key_final = f"session_{old_session_id}_final_filename"
        st.session_state.pop(old_session_key_ref, None)
        st.session_state.pop(old_session_key_final, None)
        # Xóa cleanup keys nếu có
        cleanup_key = f"cleanup_called_{old_session_id}"
        st.session_state.pop(cleanup_key, None)
        
        # Reset session state
        st.session_state.pop("current_session_id", None)
        st.session_state.pop("current_file_signature", None)
        st.session_state.pop("current_mode", None)
        current_session_id = None  # Reset để tạo mới
    
    # Tạo session mới nếu chưa có hoặc file/mode đã thay đổi
    if not current_session_id or file_changed or mode_changed:
        current_session_id = _create_new_session()
        st.session_state["current_session_id"] = current_session_id
        st.session_state["current_file_signature"] = current_file_signature
        st.session_state["current_mode"] = mode
        # Force upload lại bằng cách xóa session keys cũ (nếu có)
        session_key_ref = f"session_{current_session_id}_ref_filename"
        session_key_final = f"session_{current_session_id}_final_filename"
        st.session_state.pop(session_key_ref, None)
        st.session_state.pop(session_key_final, None)
        if file_changed or mode_changed:
            if mode_changed:
                st.sidebar.info(f"🔄 Đã đổi sang {mode}. Đang upload lại files...")
            else:
                st.sidebar.info(f"🔄 File đã thay đổi. Đang upload lại files...")
    
    # Upload cả 2 file lên backend để hiển thị
    ref_filename = None
    final_filename = None
    
    # Kiểm tra và upload ref file
    session_key_ref = f"session_{current_session_id}_ref_filename"
    if session_key_ref not in st.session_state:
        try:
            with st.spinner("Đang tải file reference lên backend..."):
                ref_filename, returned_session_id = _upload_ref_to_backend(backend_url_internal, ref_file, current_session_id)
                if ref_filename:
                    # Cập nhật session_id nếu backend trả về mới
                    if returned_session_id and returned_session_id != current_session_id:
                        current_session_id = returned_session_id
                        st.session_state["current_session_id"] = current_session_id
                    st.session_state[session_key_ref] = ref_filename
                else:
                    st.sidebar.error("❌ Échec du téléchargement du fichier de référence - nom de fichier non reçu")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                st.sidebar.error("❌ Backend chưa có endpoint /api/upload/ref. Vui lòng restart backend Flask.")
            else:
                st.sidebar.error(f"❌ Lỗi HTTP {e.response.status_code} khi upload ref file: {e}")
        except Exception as e:
            st.sidebar.error(f"❌ Impossible de télécharger le fichier de référence: {e}")
    else:
        ref_filename = st.session_state[session_key_ref]
    
    # Kiểm tra và upload final file (dùng session_id đã được cập nhật)
    current_session_id = st.session_state.get("current_session_id")
    session_key_final = f"session_{current_session_id}_final_filename"
    if session_key_final not in st.session_state:
        try:
            with st.spinner("Đang tải file final lên backend..."):
                final_filename, returned_session_id = _upload_final_to_backend(backend_url_internal, final_file, current_session_id)
                if final_filename:
                    # Cập nhật session_id nếu backend trả về mới
                    if returned_session_id and returned_session_id != current_session_id:
                        current_session_id = returned_session_id
                        st.session_state["current_session_id"] = current_session_id
                    st.session_state[session_key_final] = final_filename
                else:
                    st.sidebar.error("❌ Échec du téléchargement du fichier final - nom de fichier non reçu")
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                st.sidebar.error("❌ Backend chưa có endpoint /api/upload/final. Vui lòng restart backend Flask.")
            else:
                st.sidebar.error(f"❌ Lỗi HTTP {e.response.status_code} khi upload final file: {e}")
        except Exception as e:
            st.sidebar.error(f"❌ Impossible de télécharger le fichier final: {e}")
            import traceback
            st.sidebar.code(traceback.format_exc())
    else:
        final_filename = st.session_state[session_key_final]
    
    # Hiển thị preview 2 PDF từ backend URL
    st.markdown("---")
    st.markdown("### 📄 PDF Preview")
    st.info("👁️ PDFs loaded! View them below. Click 'Chạy so sánh' button to analyze differences.")
    
    col1, col2 = st.columns(2)
    
    # Lấy session_id hiện tại để hiển thị PDF
    current_session_id = st.session_state.get("current_session_id")
    
    with col1:
        st.markdown("**📄 Reference PDF**")
        st.caption(f"📁 {ref_file.name}")
        if ref_filename:
            if not current_session_id:
                st.warning("⚠️ Không có session_id. Đang thử tìm file không cần session_id...")
            _display_pdf_from_backend_url(backend_url_external, ref_filename, height=700, session_id=current_session_id)
        else:
            st.error("Impossible d'afficher le PDF de référence")
    
    with col2:
        st.markdown("**📄 Final PDF**")
        st.caption(f"📁 {final_file.name}")
        if final_filename:
            if not current_session_id:
                st.warning("⚠️ Không có session_id. Đang thử tìm file không cần session_id...")
            _display_pdf_from_backend_url(backend_url_external, final_filename, height=700, session_id=current_session_id)
        else:
            st.error("Impossible d'afficher le PDF final")
            current_session_id = st.session_state.get("current_session_id")
            if st.button("🔄 Thử lại upload final file", key="retry_final"):
                if current_session_id:
                    session_key_final = f"session_{current_session_id}_final_filename"
                    if session_key_final in st.session_state:
                        del st.session_state[session_key_final]
                st.rerun()
    
    # Nút retry nếu cả 2 đều lỗi
    current_session_id = st.session_state.get("current_session_id")
    if not ref_filename or not final_filename:
        if st.button("🔄 Xóa cache và upload lại", key="retry_all"):
            if current_session_id:
                _cleanup_session(backend_url_internal, current_session_id)
            st.session_state.pop("current_session_id", None)
            st.session_state.pop("current_file_signature", None)
            st.rerun()
    
    st.markdown("---")
    run_clicked = st.sidebar.button("🔍 Lancer la comparaison", type="primary", use_container_width=True)
    
    if not run_clicked:
        st.stop()
else:
    st.info("👈 Veuillez télécharger 2 fichiers PDF pour commencer.")
    st.stop()

with st.spinner("Đang xử lý..."):
    try:
        # Lấy filename từ session hiện tại
        current_session_id = st.session_state.get("current_session_id")
        session_key_ref = f"session_{current_session_id}_ref_filename"
        session_key_final = f"session_{current_session_id}_final_filename"
        ref_filename = st.session_state.get(session_key_ref)
        final_filename = st.session_state.get(session_key_final)
        
        # Debug info (ẩn trong production)
        if not ref_filename or not final_filename:
            st.warning("⚠️ Không tìm thấy filename đã upload. Sẽ upload lại file...")
            # Upload lại nếu chưa có
            if not ref_filename:
                try:
                    ref_filename, returned_session_id = _upload_ref_to_backend(backend_url_internal, ref_file, current_session_id)
                    if returned_session_id and returned_session_id != current_session_id:
                        current_session_id = returned_session_id
                        st.session_state["current_session_id"] = current_session_id
                        session_key_ref = f"session_{current_session_id}_ref_filename"  # Update key
                    st.session_state[session_key_ref] = ref_filename
                except Exception as e:
                    st.error(f"Không thể upload ref file: {e}")
            if not final_filename:
                try:
                    # Cập nhật session_id nếu đã thay đổi
                    current_session_id = st.session_state.get("current_session_id")
                    final_filename, returned_session_id = _upload_final_to_backend(backend_url_internal, final_file, current_session_id)
                    if returned_session_id and returned_session_id != current_session_id:
                        current_session_id = returned_session_id
                        st.session_state["current_session_id"] = current_session_id
                        session_key_final = f"session_{current_session_id}_final_filename"  # Update key
                    st.session_state[session_key_final] = final_filename
                except Exception as e:
                    st.error(f"Không thể upload final file: {e}")
        
        # Lấy session_id hiện tại
        current_session_id = st.session_state.get("current_session_id")
        
        data = {}
        endpoint = f"/api/compare/{mode}"
        if mode == "mode2" and api_key:
            data["api_key"] = api_key

        response = _send_request(
            backend_url=backend_url_internal,
            endpoint=endpoint,
            ref_file=ref_file,
            final_file=final_file,
            data=data,
            ref_filename=ref_filename,
            final_filename=final_filename,
            session_id=current_session_id,
        )

        if not response.get("success"):
            error_msg = response.get("error", "Unknown error")
            error_type = response.get("type", "")
            error_detail = response.get("detail", "")
            
            st.error(f"❌ Erreur: {error_msg}")
            if error_type:
                st.info(f"Loại lỗi: {error_type}")
            if error_detail and st.checkbox("Hiển thị chi tiết lỗi"):
                st.code(error_detail)
            
            raise RuntimeError(error_msg)

        result = response.get("data", {})
        # Lấy session_id từ response (backend trả về)
        response_session_id = response.get("session_id")
        if response_session_id:
            # Cập nhật session_id nếu backend trả về
            st.session_state["current_session_id"] = response_session_id
        
        st.success("Terminé!")
        
        # Setup PDF load tracker để cleanup session sau khi PDFs load xong
        current_session_id = st.session_state.get("current_session_id")

        if mode == "mode1":
            st.subheader("Résultat Mode 1")
            
            # Hiển thị thông tin
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Produits dans PDF1", result.get("num_products_ref", 0))
            with col2:
                st.metric("Produits dans PDF2", result.get("num_products_final", 0))
            with col3:
                st.metric("Comparaisons", result.get("num_comparisons", 0))
            
            # Hiển thị cả 2 PDF với annotations
            output_pdf1 = result.get("output_pdf1")
            output_pdf2 = result.get("output_pdf2")
            
            if output_pdf1 and output_pdf2:
                current_session_id = st.session_state.get("current_session_id")
                
                st.markdown("### 📄 Comparaison PDF (Les 2 sont annotés)")
                st.markdown("**🔵 Bleu**: Correspondant | **🔴 Rouge**: Non-correspondant/Manquant")
                cols = st.columns(2)
                
                with cols[0]:
                    st.markdown("**📄 PDF Référence (avec annotations)**")
                    _display_pdf_from_backend(backend_url_external, output_pdf1, height=700, session_id=current_session_id)
                
                with cols[1]:
                    st.markdown("**📄 PDF Final (avec annotations)**")
                    _display_pdf_from_backend(backend_url_external, output_pdf2, height=700, session_id=current_session_id)
                    # Cleanup sẽ tự động khi PDF load xong (qua JavaScript trong iframe)
            
            # Chi tiết comparisons
            with st.expander("📊 Chi tiết so sánh"):
                st.json(result.get("comparisons", []))

        elif mode == "mode2":
            st.subheader("Résultat Mode 2")
            
            # Hiển thị summary
            summary = result.get("summary", {})
            col1, col2, col3, col4 = st.columns(4)
            with col1:
                st.metric("Total annotations", summary.get("total_annotations", 0))
            with col2:
                st.metric("✅ Réalisé", summary.get("implemented", 0))
            with col3:
                st.metric("❌ Non réalisé", summary.get("not_implemented", 0))
            with col4:
                st.metric("⚠️ Partiel", summary.get("partial", 0))
            
            # Hiển thị PDF Reference và Kết quả cạnh nhau
            output_pdf = result.get("output_pdf")
            if output_pdf:
                current_session_id = st.session_state.get("current_session_id")
                
                st.markdown("### 📄 Comparaison PDF")
                cols = st.columns(2)
                
                with cols[0]:
                    st.markdown("**📄 PDF Référence**")
                    if ref_filename:
                        _display_pdf_from_backend_url(backend_url_external, ref_filename, height=700, session_id=current_session_id)
                
                with cols[1]:
                    st.markdown("**✅ PDF Résultat (avec annotations)**")
                    _display_pdf_from_backend(backend_url_external, output_pdf, height=700, session_id=current_session_id)
                    # Cleanup sẽ tự động khi PDF load xong (qua JavaScript trong iframe)
            
            # Chi tiết results
            with st.expander("📊 Chi tiết từng annotation"):
                st.json(result.get("results", []))

        else:
            st.subheader("Résultat Mode 3")
            
            # Hiển thị stats
            stats = result.get("stats", {})
            col1, col2, col3 = st.columns(3)
            with col1:
                st.metric("Total des pages", stats.get("total_pages", 0))
            with col2:
                st.metric("🔴 Highlights ref", stats.get("ref_highlights", 0))
            with col3:
                st.metric("🟢 Highlights final", stats.get("final_highlights", 0))
            
            # Hiển thị cả 2 PDF trực tiếp từ backend
            output_ref = result.get("output_ref")
            output_final = result.get("output_final")
            
            if output_ref and output_final:
                current_session_id = st.session_state.get("current_session_id")
                
                st.markdown("### 📄 So sánh PDF")
                cols = st.columns(2)
                
                # PDF Reference
                with cols[0]:
                    st.markdown("**📄 PDF Reference (annotated)**")
                    _display_pdf_from_backend(backend_url_external, output_ref, height=700, session_id=current_session_id)
                
                # PDF Final
                with cols[1]:
                    st.markdown("**✅ PDF Final (annotated)**")
                    _display_pdf_from_backend(backend_url_external, output_final, height=700, session_id=current_session_id)
                    # Cleanup sẽ tự động khi PDF load xong (qua JavaScript trong iframe)

    except requests.exceptions.HTTPError as e:
        st.error(f"❌ Erreur HTTP {e.response.status_code}: {e}")
        try:
            error_detail = e.response.json()
            if isinstance(error_detail, dict) and "error" in error_detail:
                st.error(f"Chi tiết: {error_detail['error']}")
                if "detail" in error_detail and st.checkbox("Hiển thị chi tiết lỗi", key="show_detail"):
                    st.code(error_detail["detail"])
        except:
            st.text(f"Response: {e.response.text[:500]}")
    except requests.exceptions.RequestException as e:
        st.error(f"❌ Erreur de connexion au backend: {e}")
        st.info("Vérifier:\n• Le backend est-il en cours d'exécution?\n• L'URL du backend est-elle correcte?\n• La connexion réseau est-elle stable?")
    except Exception as e:
        st.error(f"❌ Erreur: {e}")
        import traceback
        if st.checkbox("Hiển thị traceback", key="show_traceback"):
            st.code(traceback.format_exc())

