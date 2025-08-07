FROM python:3.9-slim

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && \
    apt-get install -y ffmpeg tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng \
    libgl1-mesa-glx libglib2.0-0 git build-essential && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Установка CLIP и зависимостей
RUN pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cpu
RUN pip install ftfy regex tqdm
RUN pip install git+https://github.com/openai/CLIP.git

COPY . .

CMD ["python", "bot.py"]
