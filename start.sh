#!/bin/bash
# Script khởi động Tool Compare BATIMENT
# Chạy cả Backend Flask và Frontend Streamlit

cd /home/hault/Tool_compare_BATIMENT_t1
source venv/bin/activate

echo "============================================================"
echo "🔍 Tool Compare BATIMENT - Starting Services"
echo "============================================================"

# Kill old processes
echo "🛑 Dừng các process cũ..."
pkill -f "backend_flask.py" 2>/dev/null
pkill -f "streamlit run main.py" 2>/dev/null
sleep 1

# Start backend in background
echo "🚀 Khởi động Backend Flask..."
python backend_flask.py &
BACKEND_PID=$!
echo "   Backend PID: $BACKEND_PID"

# Wait for backend to start
echo "⏳ Đợi backend khởi động (2 giây)..."
sleep 2

# Test backend
echo "🔍 Kiểm tra backend..."
if curl -s http://localhost:5000/api/health > /dev/null 2>&1; then
    echo "   ✅ Backend đang chạy!"
else
    echo "   ❌ Backend không phản hồi!"
    exit 1
fi

echo ""
echo "============================================================"
echo "🚀 Khởi động Streamlit Frontend..."
echo "============================================================"
echo ""
echo "📊 Backend Flask:      http://localhost:5000"
echo "📊 Frontend Streamlit: http://localhost:8501"
echo ""
echo "💡 Nhấn Ctrl+C để dừng"
echo "============================================================"
echo ""

# Start frontend (foreground)
streamlit run main.py --server.port=8501 --server.headless=true
