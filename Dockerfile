FROM python:3.10-slim-bookworm

WORKDIR /app

# Установка базовых зависимостей
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    wget \
    gnupg2 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Добавление официального репозитория Tesseract 5
RUN wget -qO- https://github.com/tesseract-ocr/tessdata/raw/main/eng.traineddata > /usr/share/tesseract-ocr/5/tessdata/eng.traineddata && \
    wget -qO- https://github.com/tesseract-ocr/tessdata/raw/main/rus.traineddata > /usr/share/tesseract-ocr/5/tessdata/rus.traineddata && \
    wget -qO- https://github.com/tesseract-ocr/tessdata/raw/main/osd.traineddata > /usr/share/tesseract-ocr/5/tessdata/osd.traineddata && \
    wget -qO- https://github.com/tesseract-ocr/tessdata/raw/main/script/Latin.traineddata > /usr/share/tesseract-ocr/5/tessdata/script/Latin.traineddata && \
    wget -qO- https://github.com/tesseract-ocr/tessdata/raw/main/script/Cyrillic.traineddata > /usr/share/tesseract-ocr/5/tessdata/script/Cyrillic.traineddata

# Установка Tesseract и runtime-зависимостей
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    tesseract-ocr \
    libtesseract-dev \
    libleptonica-dev \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libtiff5 \
    libjpeg62-turbo \
    libopenjp2-7 \
    zlib1g \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Установка Python зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
