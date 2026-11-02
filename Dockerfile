FROM python:3.11-slim
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg fonts-noto-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PORT=10000
# 1 worker + threads: jobs run one at a time in a background thread
CMD gunicorn -w 1 --threads 8 -t 0 -b 0.0.0.0:$PORT app:app
