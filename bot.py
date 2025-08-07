import os
import logging
import tempfile
from urllib.parse import urlparse

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from pytube import YouTube
from google.cloud import vision
import requests
import ffmpeg

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация API
OMDB_API_URL = "http://www.omdbapi.com/"

def download_video(url: str) -> str:
    """Скачивает видео и возвращает путь к файлу"""
    if "youtube.com" in url or "youtu.be" in url:
        yt = YouTube(url)
        stream = yt.streams.filter(file_extension='mp4').first()
        temp_file = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
        stream.download(filename=temp_file.name)
        return temp_file.name
    else:
        raise ValueError("Неподдерживаемый источник видео")

def extract_frame(video_path: str, timestamp: int = 5) -> str:
    """Извлекает кадр из видео в указанной секунде"""
    frame_path = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False).name
    (
        ffmpeg
        .input(video_path, ss=timestamp)
        .output(frame_path, vframes=1, qscale=0)
        .run(capture_stdout=True, capture_stderr=True)
    )
    return frame_path

def detect_content(image_path: str) -> str:
    """Анализирует изображение через Google Vision API"""
    client = vision.ImageAnnotatorClient()
    
    with open(image_path, 'rb') as image_file:
        content = image_file.read()
    
    image = vision.Image(content=content)
    
    # Детекция текста и логотипов
    text_response = client.text_detection(image=image)
    logo_response = client.logo_detection(image=image)
    
    # Сбор результатов
    detected_text = [text.description for text in text_response.text_annotations]
    detected_logos = [logo.description for logo in logo_response.logo_annotations]
    
    return " ".join(detected_text + detected_logos)

def search_media(title: str) -> str:
    """Ищет медиа-контент через OMDb API"""
    params = {
        'apikey': os.getenv('OMDB_API_KEY'),
        't': title,
        'type': 'movie,series,episode',
        'plot': 'short'
    }
    
    response = requests.get(OMDB_API_URL, params=params)
    data = response.json()
    
    if data.get('Response') == 'True':
        return (
            f"🎬 {data['Title']} ({data['Year']})\n"
            f"⭐ Рейтинг: {data['imdbRating']}/10\n"
            f"📀 Тип: {data['Type'].capitalize()}\n"
            f"📝 Описание: {data['Plot']}"
        )
    return ""

def process_video(url: str) -> str:
    """Обрабатывает видео и возвращает результат"""
    try:
        # Шаг 1: Скачивание видео
        video_path = download_video(url)
        
        # Шаг 2: Извлечение кадра
        frame_path = extract_frame(video_path)
        
        # Шаг 3: Анализ изображения
        content = detect_content(frame_path)
        
        # Шаг 4: Поиск информации
        if content:
            media_info = search_media(content)
            return media_info if media_info else f"Распознано: {content}"
        
        return "Не удалось распознать контент"
    
    except Exception as e:
        logger.error(f"Ошибка обработки: {e}")
        return "Ошибка обработки видео"
    
    finally:
        # Очистка временных файлов
        for path in [video_path, frame_path]:
            if path and os.path.exists(path):
                os.unlink(path)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start"""
    user = update.effective_user
    await update.message.reply_markdown_v2(
        f"Привет {user.mention_markdown_v2()}\! Отправь мне ссылку на YouTube видео, "
        "и я попробую определить что это за фильм или сериал!"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений"""
    url = update.message.text
    parsed_url = urlparse(url)
    
    if not parsed_url.scheme or not parsed_url.netloc:
        await update.message.reply_text("Пожалуйста, отправьте действительную URL-ссылку")
        return
    
    await update.message.reply_text("🔍 Анализирую видео...")
    result = process_video(url)
    await update.message.reply_text(result or "Не удалось определить контент")

def main() -> None:
    """Запуск бота"""
    # Загрузка переменных окружения
    token = os.getenv('TELEGRAM_TOKEN')
    if not token:
        raise ValueError("TELEGRAM_TOKEN не установлен")
    
    # Инициализация бота
    application = Application.builder().token(token).build()
    
    # Регистрация обработчиков
    application.add_handler(CommandHandler("start", start))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    # Запуск
    logger.info("Бот запущен...")
    application.run_polling()

if __name__ == '__main__':
    main()
