#!/usr/bin/env python3
"""
Script để chạy cả backend Flask và frontend Streamlit cùng lúc.
"""

import subprocess
import sys
import signal
import os
from pathlib import Path

# Lưu process IDs để có thể kill khi cần
processes = []


def signal_handler(sig, frame):
    """Xử lý signal để dừng tất cả processes khi nhận Ctrl+C."""
    print("\n🛑 Đang dừng tất cả processes...")
    for proc in processes:
        try:
            proc.terminate()
        except:
            pass
    
    # Đợi processes dừng
    for proc in processes:
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    
    print("✅ Đã dừng tất cả processes.")
    sys.exit(0)


def run_backend():
    """Chạy Flask backend."""
    print("🚀 Khởi động Flask backend...")
    proc = subprocess.Popen(
        [sys.executable, "backend_flask.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    return proc


def run_frontend():
    """Chạy Streamlit frontend."""
    print("🚀 Khởi động Streamlit frontend...")
    # Kiểm tra xem streamlit có sẵn không
    try:
        import streamlit
    except ImportError:
        print("❌ Lỗi: streamlit chưa được cài đặt!")
        print("💡 Chạy: pip install streamlit")
        sys.exit(1)
    
    proc = subprocess.Popen(
        [sys.executable, "-m", "streamlit", "run", "main.py", 
         "--server.port=8501", "--server.address=0.0.0.0"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1
    )
    return proc


def print_output(proc, name):
    """In output từ process."""
    try:
        for line in proc.stdout:
            print(f"[{name}] {line.rstrip()}")
    except:
        pass


def main():
    """Hàm chính."""
    # Đăng ký signal handler
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # Kiểm tra files tồn tại
    if not Path("backend_flask.py").exists():
        print("❌ Không tìm thấy backend_flask.py")
        sys.exit(1)
    
    if not Path("main.py").exists():
        print("❌ Không tìm thấy main.py")
        sys.exit(1)
    
    print("=" * 60)
    print("🔍 Compare Batiment - Starting Services")
    print("=" * 60)
    
    # Chạy backend
    backend_proc = run_backend()
    processes.append(backend_proc)
    
    # Đợi một chút để backend khởi động
    import time
    time.sleep(2)
    
    # Chạy frontend
    frontend_proc = run_frontend()
    processes.append(frontend_proc)
    
    print("\n" + "=" * 60)
    print("✅ Cả 2 services đã được khởi động!")
    print("=" * 60)
    print("📊 Backend Flask:  http://localhost:5000")
    print("📊 Frontend Streamlit: http://localhost:8501")
    print("\n💡 Nhấn Ctrl+C để dừng tất cả services\n")
    print("=" * 60)
    print()
    
    # In output từ cả 2 processes
    try:
        import threading
        
        def print_backend():
            print_output(backend_proc, "BACKEND")
        
        def print_frontend():
            print_output(frontend_proc, "FRONTEND")
        
        # Chạy trong threads riêng
        backend_thread = threading.Thread(target=print_backend, daemon=True)
        frontend_thread = threading.Thread(target=print_frontend, daemon=True)
        
        backend_thread.start()
        frontend_thread.start()
        
        # Đợi cho đến khi có process nào dừng
        while True:
            if backend_proc.poll() is not None:
                print("\n❌ Backend đã dừng!")
                break
            if frontend_proc.poll() is not None:
                print("\n❌ Frontend đã dừng!")
                break
            time.sleep(1)
    
    except KeyboardInterrupt:
        signal_handler(None, None)
    except Exception as e:
        print(f"\n❌ Lỗi: {e}")
        signal_handler(None, None)


if __name__ == "__main__":
    main()

