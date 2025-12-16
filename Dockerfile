FROM python:3.11-slim 
WORKDIR /app 
# Copy requirements và cài đặt dependencies 
COPY requirements.txt . 
RUN pip install --no-cache-dir -r requirements.txt 
# Copy toàn bộ source code 
COPY *.py ./ 
# Tạo thư mục temp_pdf 
RUN mkdir -p temp_pdf 
# Expose port cho Flask 
EXPOSE 5000 8501 
# Chạy Flask backend 
CMD ["python", "run.py"]