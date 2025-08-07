FROM python:3.10-slim

WORKDIR /app

# Установка минимальных зависимостей
RUN apt-get update && \
    apt-get install -y \
    tesseract-ocr \
    tesseract-ocr-rus \
    tesseract-ocr-eng \
    tesseract-ocr-script-latn \
    tesseract-ocr-script-cyrl \
    tesseract-ocr-osd \
    libgl1-mesa-glx \
    libglib2.0-0 \
    libtiff5 \
    libjpeg62-turbo \
    libopenjp2-7 \
    zlib1g \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Установка последней версии Tesseract и языковых данных
RUN apt-get update && \
    apt-get install -y apt-transport-https gnupg2 && \
    echo "deb https://notesalexp.org/tesseract-ocr5/$(lsb_release -cs)/ $(lsb_release -cs) main" | tee /etc/apt/sources.list.d/notesalexp.list && \
    apt-get update -oAcquire::AllowInsecureRepositories=true && \
    apt-get install -y --allow-unauthenticated tesseract-ocr tesseract-ocr-osd tesseract-ocr-eng tesseract-ocr-rus

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["python", "bot.py"]
