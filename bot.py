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
CORTOS_API_URL = "https://api.cortos.xyz/v1/recognize"

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

def detect_with_google_vision(image_path: str) -> str:
    """Анализирует изображение через Google Vision API"""
    try:
        client = vision.ImageAnnotatorClient()
        
        with open(image_path, 'rb') as image_file:
            content = image_file.read()
        
        image = vision.Image(content=content)
        response = client.web_detection(image=image)
        web_detection = response.web_detection
        
        # Собираем все возможные текстовые описания
        candidates = []
        
        # Лучшая догадка
        if web_detection.best_guess_labels:
            candidates.append(web_detection.best_guess_labels[0].label)
            
        # Веб-сущности с высоким доверием
        if web_detection.web_entities:
            for entity in web_detection.web_entities:
                if entity.description and entity.score > 0.7:
                    candidates.append(entity.description)
        
        # URL совпадающих изображений (может содержать названия в URL)
        if web_detection.full_matching_images:
            for img in web_detection.full_matching_images:
                if img.url:
                    # Извлекаем возможное название из URL
                    parsed = urlparse(img.url)
                    # Берем последнюю часть пути
                    path_part = parsed.path.split('/')[-1]
                    if path_part:
                        candidates.append(path_part.replace('-', ' '))
        
        # Страницы с совпадающими изображениями
        if web_detection.pages_with_matching_images:
            for page in web_detection.pages_with_matching_images:
                if page.url:
                    parsed = urlparse(page.url)
                    path_part = parsed.path.split('/')[-1]
                    if path_part:
                        candidates.append(path_part.replace('-', ' '))
                if page.page_title:
                    candidates.append(page.page_title)
        
        return ", ".join(candidates) if candidates else ""
    except Exception as e:
        logger.error(f"Ошибка Google Vision: {e}")
        return ""

def recognize_with_cortos(image_path: str) -> str:
    """Распознает фильм через Cortos API (специализированный ИИ)"""
    try:
        headers = {"Authorization": f"Bearer {os.getenv('CORTOS_API_KEY')}"}
        
        with open(image_path, 'rb') as img_file:
            files = {'image': img_file}
            response = requests.post(CORTOS_API_URL, headers=headers, files=files, timeout=15)
        
        if response.status_code == 200:
            data = response.json()
            if data.get('matches'):
                # Берем самый лучший результат
                return data['matches'][0]['title']
        return ""
    except Exception as e:
        logger.error(f"Ошибка Cortos API: {e}")
        return ""

def detect_content(image_path: str) -> str:
    """Основная функция распознавания: комбинация Vision + Cortos"""
    # Сначала пробуем Google Vision
    vision_result = detect_with_google_vision(image_path)
    if vision_result:
        logger.info(f"Google Vision распознал: {vision_result}")
        return vision_result
    
    # Если Vision не дал результата, используем Cortos
    cortos_result = recognize_with_cortos(image_path)
    if cortos_result:
        logger.info(f"Cortos распознал: {cortos_result}")
        return cortos_result
    
    return ""

def search_media(title: str) -> dict:
    """Ищет медиа-контент через OMDb API"""
    try:
        # Пробуем несколько кандидатов, если их несколько
        titles = [t.strip() for t in title.split(',')] if ',' in title else [title]
        
        for t in titles:
            params = {
                'apikey': os.getenv('OMDB_API_KEY'),
                't': t,
                'type': 'movie,series,episode',
                'plot': 'short'
            }
            
            logger.info(f"Поиск медиа для: {t}")
            response = requests.get(OMDB_API_URL, params=params, timeout=10)
            data = response.json()
            
            if data.get('Response') == 'True':
                return data
        
        return {}
    except Exception as e:
        logger.error(f"Ошибка поиска медиа: {e}")
        return {}

def format_media_info(media_data: dict) -> str:
    """Форматирует информацию о медиа-контенте"""
    if not media_data:
        return "Информация не найдена"
    
    # Основная информация
    title = media_data.get('Title', 'Без названия')
    year = media_data.get('Year', 'N/A')
    media_type = media_data.get('Type', 'movie').capitalize()
    rating = media_data.get('imdbRating', 'N/A')
    plot = media_data.get('Plot', 'Описание отсутствует')
    
    # Форматирование ответа
    info = f"🎬 {title} ({year})"
    info += f"\n📀 Тип: {media_type}"
    
    # Дополнительная информация в зависимости от типа
    if media_data.get('totalSeasons'):
        info += f"\n🔢 Сезонов: {media_data['totalSeasons']}"
    if media_data.get('Runtime'):
        info += f"\n⏱ Длительность: {media_data['Runtime']}"
    
    # Основная информация
    info += f"\n⭐ Рейтинг: {rating}/10"
    info += f"\n📝 Описание: {plot[:300]}{'...' if len(plot) > 300 else ''}"
    
    # Постер
    if media_data.get('Poster') and media_data['Poster'] != 'N/A':
        info += f"\n\n🖼 {media_data['Poster']}"
    
    # Ссылка на IMDb
    if media_data.get('imdbID'):
        info += f"\n\n🔗 https://www.imdb.com/title/{media_data['imdbID']}/"
    
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
            media_data = search_media(content)
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
            media_data = search_media(content)
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
        
        # Проверка API ключей
        if not os.getenv('OMDB_API_KEY'):
            logger.warning("OMDB_API_KEY не установлен! Поиск медиа будет недоступен")
        if not os.getenv('CORTOS_API_KEY'):
            logger.warning("CORTOS_API_KEY не установлен! Cortos распознавание недоступно")
        
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
