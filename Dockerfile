FROM python:3.10-slim

# Устанавливаем рабочую директорию
WORKDIR /app

# Установка зависимостей с фиксацией версий пакетов
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    tesseract-ocr=5.1.0-1 \
    tesseract-ocr-rus=1:5.1.0-1 \
    tesseract-ocr-eng=1:5.1.0-1 \
    tesseract-ocr-script-latn=1:5.1.0-1 \
    tesseract-ocr-script-cyrl=1:5.1.0-1 \
    tesseract-ocr-osd=1:5.1.0-1 \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libtiff5 \
    libjpeg62-turbo \
    libopenjp2-7 \
    zlib1g \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Копируем зависимости и устанавливаем их
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копируем исходный код
COPY . .

# Запускаем бота
CMD ["python", "bot.py"]
