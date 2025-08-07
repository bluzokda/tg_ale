import os
import logging
import tempfile
import subprocess
import gc
import sys
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

def check_ffmpeg():
    """Проверяет доступность ffmpeg в системе"""
    try:
        result = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True)
        logger.info(f"FFmpeg version: {result.stdout.splitlines()[0] if result.stdout else 'Unknown'}")
        return True
    except Exception as e:
        logger.error(f"FFmpeg check failed: {e}")
        return False

def download_video(url: str) -> str:
    """Скачивает видео и возвращает путь к файлу"""
    try:
        if "youtube.com" in url or "youtu.be" in url:
            logger.info(f"Downloading YouTube video: {url}")
            yt = YouTube(url)
            # Скачиваем только аудиодорожку для экономии ресурсов
            stream = yt.streams.filter(only_audio=True, file_extension='mp4').first()
            if not stream:
                raise ValueError("No suitable stream found")
                
            temp_file = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
            stream.download(filename=temp_file.name)
            logger.info(f"Video downloaded to: {temp_file.name}")
            return temp_file.name
        else:
            raise ValueError("Unsupported video source")
    except Exception as e:
        logger.error(f"Error downloading video: {e}")
        raise

def extract_frame(video_path: str, timestamp: int = 5) -> str:
    """Извлекает кадр из видео в указанной секунде"""
    try:
        frame_path = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False).name
        logger.info(f"Extracting frame at {timestamp}s from: {video_path}")
        
        (
            ffmpeg
            .input(video_path, ss=timestamp)
            .output(frame_path, vframes=1, qscale=0)
            .run(capture_stdout=True, capture_stderr=True, overwrite_output=True)
        )
        logger.info(f"Frame saved to: {frame_path}")
        return frame_path
    except Exception as e:
        logger.error(f"Error extracting frame: {e}")
        raise

def detect_content(image_path: str) -> str:
    """Анализирует изображение через Google Vision API"""
    try:
        client = vision.ImageAnnotatorClient()
        
        with open(image_path, 'rb') as image_file:
            content = image_file.read()
        
        image = vision.Image(content=content)
        
        # Используем web detection для лучших результатов
        response = client.web_detection(image=image)
        web_detection = response.web_detection
        
        # Собираем результаты
        results = []
        
        if web_detection.full_matching_images:
            results.extend([img.url for img in web_detection.full_matching_images][:3])
            
        if web_detection.pages_with_matching_images:
            results.extend([page.url for page in web_detection.pages_with_matching_images][:3])
            
        if web_detection.web_entities:
            results.extend([entity.description for entity in web_detection.web_entities if entity.description][:5])
            
        if web_detection.best_guess_labels:
            results.extend([label.label for label in web_detection.best_guess_labels])
        
        return " ".join(set(results)) if results else ""
    except Exception as e:
        logger.error(f"Error detecting content: {e}")
        return ""

def search_media(title: str) -> str:
    """Ищет медиа-контент через OMDb API"""
    try:
        params = {
            'apikey': os.getenv('OMDB_API_KEY'),
            't': title,
            'type': 'movie,series,episode',
            'plot': 'short'
        }
        
        logger.info(f"Searching media for: {title}")
        response = requests.get(OMDB_API_URL, params=params, timeout=10)
        data = response.json()
        
        if data.get('Response') == 'True':
            result = (
                f"🎬 {data['Title']} ({data['Year']})\n"
                f"⭐ Рейтинг: {data.get('imdbRating', 'N/A')}/10\n"
                f"📀 Тип: {data['Type'].capitalize()}\n"
                f"📝 Описание: {data['Plot']}"
            )
            logger.info(f"Media found: {data['Title']}")
            return result
        return ""
    except Exception as e:
        logger.error(f"Error searching media: {e}")
        return ""

def process_video(url: str) -> str:
    """Обрабатывает видео и возвращает результат"""
    video_path = None
    frame_path = None
    
    try:
        # Шаг 1: Скачивание видео
        video_path = download_video(url)
        
        # Шаг 2: Извлечение кадра
        frame_path = extract_frame(video_path)
        
        # Шаг 3: Анализ изображения
        content = detect_content(frame_path)
        
        if content:
            logger.info(f"Detected content: {content}")
            # Шаг 4: Поиск информации
            media_info = search_media(content)
            return media_info if media_info else f"Распознано: {content[:200]}..."
        
        return "Не удалось распознать контент"
    
    except Exception as e:
        logger.error(f"Error processing video: {e}")
        return "Ошибка обработки видео"
    
    finally:
        # Очистка временных файлов
        for path in [video_path, frame_path]:
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                    logger.info(f"Deleted temp file: {path}")
                except Exception as e:
                    logger.error(f"Error deleting file {path}: {e}")
        
        # Очистка памяти
        gc.collect()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start"""
    try:
        user = update.effective_user
        await update.message.reply_text(
            f"Привет, {user.mention_html()}! Отправь мне ссылку на YouTube видео, "
            "и я попробую определить что это за фильм или сериал!",
            parse_mode='HTML'
        )
        logger.info(f"Start command from {user.id}")
    except Exception as e:
        logger.error(f"Error in start command: {e}")

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений"""
    try:
        url = update.message.text.strip()
        parsed_url = urlparse(url)
        
        if not parsed_url.scheme or not parsed_url.netloc:
            await update.message.reply_text("Пожалуйста, отправьте действительную URL-ссылку")
            return
        
        logger.info(f"Processing URL: {url}")
        await update.message.reply_text("🔍 Анализирую видео...")
        
        result = process_video(url)
        await update.message.reply_text(result or "Не удалось определить контент")
        
    except Exception as e:
        logger.error(f"Error handling message: {e}")
        await update.message.reply_text("⚠️ Произошла ошибка при обработке запроса")

def main() -> None:
    """Запуск бота"""
    try:
        logger.info("="*50)
        logger.info("Starting application...")
        logger.info(f"Python version: {sys.version}")
        logger.info(f"Current directory: {os.getcwd()}")
        logger.info(f"Files in directory: {os.listdir()}")
        
        # Проверка зависимостей
        if not check_ffmpeg():
            logger.critical("FFmpeg not available! Exiting.")
            return
        
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
        
    except Exception as e:
        logger.exception("CRITICAL ERROR DURING STARTUP")
        raise

if __name__ == '__main__':
    main()
