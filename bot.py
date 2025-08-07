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
TMDB_API_URL = "https://api.themoviedb.org/3/"

def check_ffmpeg():
    """Проверяет доступность ffmpeg в системе"""
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, check=True)
        logger.info("FFmpeg доступен")
        return True
    except Exception as e:
        logger.error(f"Ошибка проверки FFmpeg: {e}")
        return False

def download_video(url: str) -> str:
    """Скачивает видео и возвращает путь к файлу"""
    try:
        if "youtube.com" in url or "youtu.be" in url:
            logger.info(f"Скачивание YouTube видео: {url}")
            yt = YouTube(url)
            stream = yt.streams.filter(only_audio=True, file_extension='mp4').first()
            if not stream:
                raise ValueError("Подходящий поток не найден")
                
            temp_file = tempfile.NamedTemporaryFile(suffix='.mp4', delete=False)
            stream.download(filename=temp_file.name)
            logger.info(f"Видео сохранено: {temp_file.name}")
            return temp_file.name
        else:
            raise ValueError("Неподдерживаемый источник видео")
    except Exception as e:
        logger.error(f"Ошибка скачивания видео: {e}")
        raise

def extract_frame(video_path: str, timestamp: int = 5) -> str:
    """Извлекает кадр из видео в указанной секунде"""
    try:
        frame_path = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False).name
        logger.info(f"Извлечение кадра из: {video_path}")
        
        (
            ffmpeg
            .input(video_path, ss=timestamp)
            .output(frame_path, vframes=1, qscale=0)
            .run(capture_stdout=True, capture_stderr=True, overwrite_output=True)
        )
        logger.info(f"Кадр сохранен: {frame_path}")
        return frame_path
    except Exception as e:
        logger.error(f"Ошибка извлечения кадра: {e}")
        raise

def detect_content(image_path: str) -> str:
    """Анализирует изображение через Google Vision API"""
    try:
        client = vision.ImageAnnotatorClient()
        
        with open(image_path, 'rb') as image_file:
            content = image_file.read()
        
        image = vision.Image(content=content)
        
        # Улучшенное распознавание с фокусировкой на медиа-контенте
        response = client.web_detection(image=image)
        web_detection = response.web_detection
        
        # Приоритет для лучших совпадений и веб-сущностей
        best_guess = ""
        if web_detection.best_guess_labels:
            best_guess = web_detection.best_guess_labels[0].label
        
        # Собираем все релевантные описания
        descriptions = set()
        if best_guess:
            descriptions.add(best_guess)
            
        if web_detection.web_entities:
            for entity in web_detection.web_entities:
                if entity.description and entity.score > 0.7:
                    descriptions.add(entity.description)
        
        # Фильтруем результаты для медиа-контента
        media_keywords = {"movie", "film", "tv", "series", "episode", "show", "scene"}
        filtered = [desc for desc in descriptions 
                   if any(kw in desc.lower() for kw in media_keywords)]
        
        return filtered[0] if filtered else best_guess
    except Exception as e:
        logger.error(f"Ошибка распознавания контента: {e}")
        return ""

def search_tmdb(query: str) -> dict:
    """Ищет медиа-контент через TMDB API"""
    try:
        # Прямой поиск по названию
        search_url = f"{TMDB_API_URL}search/multi"
        params = {
            'api_key': os.getenv('TMDB_API_KEY'),
            'query': query,
            'language': 'ru',
            'include_adult': False
        }
        
        logger.info(f"Поиск в TMDB: {query}")
        response = requests.get(search_url, params=params, timeout=10)
        results = response.json().get('results', [])
        
        if not results:
            return {}
        
        # Выбираем лучший результат
        best_result = max(results, key=lambda x: x.get('popularity', 0))
        media_type = best_result['media_type']
        
        # Получаем детальную информацию
        details_url = f"{TMDB_API_URL}{media_type}/{best_result['id']}"
        details_params = {
            'api_key': os.getenv('TMDB_API_KEY'),
            'language': 'ru',
            'append_to_response': 'videos'
        }
        
        details = requests.get(details_url, params=details_params).json()
        return details
    except Exception as e:
        logger.error(f"Ошибка поиска в TMDB: {e}")
        return {}

def format_media_info(media_data: dict) -> str:
    """Форматирует информацию о медиа-контенте"""
    if not media_data:
        return "Информация не найдена"
    
    # Определяем тип контента
    media_type = media_data.get('media_type', 'movie')
    title = media_data.get('title') or media_data.get('name', 'Без названия')
    
    # Базовая информация
    year = media_data.get('release_date', '')[:4] or media_data.get('first_air_date', '')[:4]
    rating = media_data.get('vote_average', 'N/A')
    overview = media_data.get('overview', 'Описание отсутствует')
    
    # Дополнительная информация в зависимости от типа
    if media_type == 'tv':
        info = f"📺 Сериал: {title}"
        if year:
            info += f" ({year})"
        if media_data.get('number_of_seasons'):
            info += f"\n🔢 Сезонов: {media_data['number_of_seasons']}"
        if media_data.get('number_of_episodes'):
            info += f"\n🎬 Эпизодов: {media_data['number_of_episodes']}"
    else:
        info = f"🎬 Фильм: {title}"
        if year:
            info += f" ({year})"
        if media_data.get('runtime'):
            info += f"\n⏱ Длительность: {media_data['runtime']} мин"
    
    # Основная информация
    info += f"\n⭐ Рейтинг: {rating}/10"
    info += f"\n📝 Описание: {overview[:300]}{'...' if len(overview) > 300 else ''}"
    
    # Постер
    if media_data.get('poster_path'):
        info += f"\n\n🖼 https://image.tmdb.org/t/p/original{media_data['poster_path']}"
    
    # Трейлер
    videos = media_data.get('videos', {}).get('results', [])
    if videos:
        youtube_trailers = [v for v in videos if v['site'] == 'YouTube' and v['type'] == 'Trailer']
        if youtube_trailers:
            info += f"\n\n🎥 Трейлер: https://www.youtube.com/watch?v={youtube_trailers[0]['key']}"
    
    return info

def process_video(url: str) -> str:
    """Обрабатывает видео и возвращает результат"""
    video_path, frame_path = None, None
    
    try:
        video_path = download_video(url)
        frame_path = extract_frame(video_path)
        content = detect_content(frame_path)
        
        if content:
            logger.info(f"Распознано: {content}")
            media_data = search_tmdb(content)
            return format_media_info(media_data)
        return "Не удалось распознать контент"
    
    except Exception as e:
        logger.error(f"Ошибка обработки видео: {e}")
        return "Ошибка обработки видео"
    
    finally:
        # Очистка временных файлов
        for path in [video_path, frame_path]:
            if path and os.path.exists(path):
                try:
                    os.unlink(path)
                except Exception as e:
                    logger.error(f"Ошибка удаления файла {path}: {e}")
        gc.collect()

def process_image(image_path: str) -> str:
    """Обрабатывает изображение и возвращает результат"""
    try:
        content = detect_content(image_path)
        if content:
            logger.info(f"Распознано: {content}")
            media_data = search_tmdb(content)
            return format_media_info(media_data)
        return "Не удалось распознать контент на изображении"
    except Exception as e:
        logger.error(f"Ошибка обработки изображения: {e}")
        return "Ошибка обработки изображения"
    finally:
        if image_path and os.path.exists(image_path):
            try:
                os.unlink(image_path)
            except Exception as e:
                logger.error(f"Ошибка удаления файла {image_path}: {e}")
        gc.collect()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start"""
    try:
        user = update.effective_user
        await update.message.reply_text(
            f"Привет, {user.mention_html()}! Отправь мне:\n"
            "1. Ссылку на YouTube видео\n"
            "2. Скриншот из фильма/сериала\n"
            "и я найду информацию о нем!",
            parse_mode='HTML'
        )
        logger.info(f"Команда start от {user.id}")
    except Exception as e:
        logger.error(f"Ошибка в команде start: {e}")

async def handle_url(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений со ссылками"""
    try:
        url = update.message.text.strip()
        parsed_url = urlparse(url)
        
        if not parsed_url.scheme or not parsed_url.netloc:
            await update.message.reply_text("Пожалуйста, отправьте действительную URL-ссылку")
            return
        
        logger.info(f"Обработка URL: {url}")
        await update.message.reply_text("🔍 Анализирую видео...")
        
        result = process_video(url)
        await update.message.reply_text(result)
        
    except Exception as e:
        logger.error(f"Ошибка обработки URL: {e}")
        await update.message.reply_text("⚠️ Произошла ошибка при обработке видео")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик фотографий"""
    try:
        # Берем фото с самым высоким разрешением
        photo_file = await update.message.photo[-1].get_file()
        
        # Сохраняем во временный файл
        temp_file = tempfile.NamedTemporaryFile(suffix='.jpg', delete=False)
        await photo_file.download_to_drive(temp_file.name)
        logger.info(f"Фото сохранено: {temp_file.name}")
        
        await update.message.reply_text("🔍 Анализирую изображение...")
        
        # Обрабатываем изображение
        result = process_image(temp_file.name)
        await update.message.reply_text(result)
        
    except Exception as e:
        logger.error(f"Ошибка обработки фото: {e}")
        await update.message.reply_text("⚠️ Произошла ошибка при обработке изображения")

def main() -> None:
    """Запуск бота"""
    try:
        logger.info("="*50)
        logger.info("Запуск приложения...")
        logger.info(f"Версия Python: {sys.version}")
        
        # Проверка зависимостей
        if not check_ffmpeg():
            logger.critical("FFmpeg недоступен! Завершение работы.")
            return
        
        # Загрузка переменных окружения
        token = os.getenv('TELEGRAM_TOKEN')
        if not token:
            raise ValueError("TELEGRAM_TOKEN не установлен")
        
        # Инициализация бота
        application = Application.builder().token(token).build()
        
        # Регистрация обработчиков
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_url))
        application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
        
        # Запуск
        logger.info("Бот запущен...")
        application.run_polling()
        
    except Exception as e:
        logger.exception("КРИТИЧЕСКАЯ ОШИБКА ПРИ ЗАПУСКЕ")
        raise

if __name__ == '__main__':
    main()
