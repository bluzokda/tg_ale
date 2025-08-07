FROM python:3.10-slim-bookworm

WORKDIR /app

# Установка базовых зависимостей
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    wget \
    gnupg2 \
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

# Создаем директории для tessdata
RUN mkdir -p /usr/share/tesseract-ocr/tessdata /usr/share/tesseract-ocr/tessdata/script

# Скачиваем языковые данные с проверкой
RUN wget -qO /usr/share/tesseract-ocr/tessdata/eng.traineddata https://github.com/tesseract-ocr/tessdata/raw/main/eng.traineddata && \
    wget -qO /usr/share/tesseract-ocr/tessdata/rus.traineddata https://github.com/tesseract-ocr/tessdata/raw/main/rus.traineddata && \
    wget -qO /usr/share/tesseract-ocr/tessdata/osd.traineddata https://github.com/tesseract-ocr/tessdata/raw/main/osd.traineddata && \
    wget -qO /usr/share/tesseract-ocr/tessdata/script/Latin.traineddata https://github.com/tesseract-ocr/tessdata/raw/main/script/Latin.traineddata && \
    wget -qO /usr/share/tesseract-ocr/tessdata/script/Cyrillic.traineddata https://github.com/tesseract-ocr/tessdata/raw/main/script/Cyrillic.traineddata

# Проверяем установку Tesseract
RUN tesseract --version && \
    tesseract --list-langs

# Установка Python зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
