FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends espeak-ng && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV HOST=0.0.0.0 PORT=5000
EXPOSE 5000
# Web UI:  docker run -p 5000:5000 pvg
# CLI:     docker run --rm -v "$PWD/out:/app/out" pvg python -m pvg --input sample_data/property.json --output out/video.mp4
CMD ["python", "app.py"]
