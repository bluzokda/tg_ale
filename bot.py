import os
import logging
import tempfile
import subprocess
import gc
import sys
import re
import sqlite3
import torch
import clip
from urllib.parse import urlparse
from PIL import Image, ImageEnhance
import numpy as np

import pytesseract
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
KINOPOISK_API_URL = "https://kinopoiskapiunofficial.tech/api/v2.1/films/search-by-keyword"

# Инициализация кэша
def init_db():
    conn = sqlite3.connect('cache.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS requests
                 (hash TEXT PRIMARY KEY, response TEXT)''')
    conn.commit()
    return conn

DB_CONN = init_db()

# Глобальные переменные для CLIP
CLIP_MODEL = None
CLIP_PREPROCESS = None
CLIP_DEVICE = "cuda" if torch.cuda.is_available() else "cpu"
MOVIE_DATABASE = []  # Здесь будут храниться названия фильмов

def init_clip():
    """Инициализирует модель CLIP один раз при запуске"""
    global CLIP_MODEL, CLIP_PREPROCESS, MOVIE_DATABASE
    try:
        logger.info(f"Загрузка модели CLIP на устройство: {CLIP_DEVICE}")
        CLIP_MODEL, CLIP_PREPROCESS = clip.load("ViT-B/32", device=CLIP_DEVICE)
        
        # Загрузка базы названий фильмов (упрощенный вариант)
        MOVIE_DATABASE = [
            "The Matrix", "Inception", "Interstellar", "Avatar", "Titanic",
            "Pulp Fiction", "Fight Club", "Forrest Gump", "The Shawshank Redemption",
            "The Godfather", "The Dark Knight", "Schindler's List", "Star Wars"
        ]
        logger.info(f"CLIP инициализирован, база: {len(MOVIE_DATABASE)} фильмов")
    except Exception as e:
        logger.error(f"Ошибка инициализации CLIP: {e}")

def preprocess_image(image_path: str) -> Image:
    """Улучшает изображение для обработки"""
    img = Image.open(image_path)
    # Увеличиваем контраст
    img = ImageEnhance.Contrast(img).enhance(1.5)
    # Увеличиваем резкость
    img = ImageEnhance.Sharpness(img).enhance(1.2)
    return img

def detect_with_clip(image_path: str) -> str:
    """Определяет фильм с помощью CLIP"""
    if not CLIP_MODEL:
        return ""
    
    try:
        # Предобработка изображения
        image = preprocess_image(image_path)
        image_input = CLIP_PREPROCESS(image).unsqueeze(0).to(CLIP_DEVICE)
        
        # Подготовка текстовых запросов
        text_inputs = torch.cat([
            clip.tokenize(f"a scene from {movie}") for movie in MOVIE_DATABASE
        ]).to(CLIP_DEVICE)
        
        # Расчет вероятностей
        with torch.no_grad():
            image_features = CLIP_MODEL.encode_image(image_input)
            text_features = CLIP_MODEL.encode_text(text_inputs)
            
            # Вычисляем сходство
            logits_per_image, _ = CLIP_MODEL(image_input, text_inputs)
            probs = logits_per_image.softmax(dim=-1).cpu().numpy()
        
        # Находим лучший результат
        best_idx = np.argmax(probs)
        best_movie = MOVIE_DATABASE[best_idx]
        confidence = probs[0][best_idx]
        
        logger.info(f"CLIP: {best_movie} (уверенность: {confidence:.2f})")
        return best_movie if confidence > 0.3 else ""
    except Exception as e:
        logger.error(f"Ошибка CLIP: {e}")
        return ""

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

def extract_text_with_tesseract(image_path: str) -> str:
    """Извлекает текст с изображения через Tesseract OCR"""
    try:
        image = preprocess_image(image_path)
        custom_config = r'--oem 3 --psm 6'
        text = pytesseract.image_to_string(image, config=custom_config, lang='eng+rus')
        return text.strip()
    except Exception as e:
        logger.error(f"Ошибка Tesseract OCR: {e}")
        return ""

def detect_with_google_vision(image_path: str) -> str:
    """Анализирует изображение через Google Vision API"""
    try:
        client = vision.ImageAnnotatorClient()
        
        with open(image_path, 'rb') as image_file:
            content = image_file.read()
        
        image = vision.Image(content=content)
        response = client.web_detection(image=image)
        web_detection = response.web_detection
        
        # Фильтруем результаты (только фильмы/сериалы)
        valid_results = []
        pattern = re.compile(r"(фильм|сериал|кин[оа]|movie|series|tv show|episode)", re.I)
        
        # Веб-сущности
        if web_detection.web_entities:
            for entity in web_detection.web_entities:
                if entity.description and pattern.search(entity.description):
                    valid_results.append(entity.description)
        
        # Страницы с изображениями
        if web_detection.pages_with_matching_images:
            for page in web_detection.pages_with_matching_images:
                if page.page_title and pattern.search(page.page_title):
                    valid_results.append(page.page_title)
        
        return ", ".join(set(valid_results)) if valid_results else ""
    except Exception as e:
        logger.error(f"Ошибка Google Vision: {e}")
        return ""

def detect_content(image_path: str) -> str:
    """Основная функция распознавания: Vision → CLIP → Tesseract"""
    # Пробуем Google Vision
    vision_result = detect_with_google_vision(image_path)
    if vision_result:
        logger.info(f"Google Vision распознал: {vision_result}")
        return vision_result
    
    # Пробуем CLIP
    clip_result = detect_with_clip(image_path)
    if clip_result:
        logger.info(f"CLIP распознал: {clip_result}")
        return clip_result
    
    # Fallback: Tesseract OCR
    tesseract_result = extract_text_with_tesseract(image_path)
    if tesseract_result:
        logger.info(f"Tesseract распознал: {tesseract_result}")
        return tesseract_result
    
    return ""

def search_omdb(title: str) -> dict:
    """Поиск через OMDb API"""
    try:
        titles = [t.strip() for t in title.split(',')] if ',' in title else [title]
        
        for t in titles:
            params = {
                'apikey': os.getenv('OMDB_API_KEY'),
                't': t,
                'type': 'movie,series,episode',
                'plot': 'short',
                'r': 'json'
            }
            response = requests.get(OMDB_API_URL, params=params, timeout=10)
            data = response.json()
            
            if data.get('Response') == 'True':
                return data
        
        return {}
    except Exception as e:
        logger.error(f"Ошибка OMDb API: {e}")
        return {}

def search_kinopoisk(title: str) -> dict:
    """Поиск через Kinopoisk Unofficial API"""
    try:
        headers = {
            'X-API-KEY': os.getenv('KINOPOISK_API_KEY'),
            'Content-Type': 'application/json'
        }
        params = {'keyword': title}
        
        response = requests.get(
            KINOPOISK_API_URL,
            headers=headers,
            params=params,
            timeout=10
        )
        response.raise_for_status()
        
        data = response.json()
        if data.get('films'):
            return data['films'][0]  # Берем первый результат
        
        return {}
    except Exception as e:
        logger.error(f"Ошибка Kinopoisk API: {e}")
        return {}

def convert_kinopoisk_to_omdb(kinopoisk_data: dict) -> dict:
    """Конвертирует данные Kinopoisk в формат, похожий на OMDb"""
    return {
        'Title': kinopoisk_data.get('nameRu') or kinopoisk_data.get('nameEn', ''),
        'Year': kinopoisk_data.get('year', 'N/A'),
        'Type': 'movie',  # Kinopoisk API не различает типы
        'Plot': kinopoisk_data.get('description', 'Описание отсутствует'),
        'Poster': kinopoisk_data.get('posterUrl', ''),
        'imdbRating': kinopoisk_data.get('rating', 'N/A'),
        'Runtime': 'N/A',
        'Response': 'True',
        # Дополнительные поля для совместимости
        'Genre': ', '.join(kinopoisk_data.get('genres', [])),
        'Director': ', '.join([p['name'] for p in kinopoisk_data.get('staff', []) 
                              if p.get('professionKey') == 'DIRECTOR']),
        'Actors': ', '.join([p['name'] for p in kinopoisk_data.get('staff', [])
                            if p.get('professionKey') in ['ACTOR', 'HIMSELF']]),
        'imdbID': f"kp{kinopoisk_data.get('filmId', '')}"
    }

def search_media(title: str) -> dict:
    """Поиск медиа через OMDb + Kinopoisk"""
    # Пробуем OMDb первым
    omdb_result = search_omdb(title)
    if omdb_result and omdb_result.get('Response') == 'True':
        return omdb_result
    
    # Если OMDb не дал результатов, пробуем Kinopoisk
    kinopoisk_result = search_kinopoisk(title)
    if kinopoisk_result:
        return convert_kinopoisk_to_omdb(kinopoisk_result)
    
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
        
        # Инициализация CLIP
        init_clip()
        
        # Загрузка переменных окружения
        token = os.getenv('TELEGRAM_TOKEN')
        if not token:
            raise ValueError("TELEGRAM_TOKEN не установлен")
        
        # Проверка API ключей
        if not os.getenv('OMDB_API_KEY') and not os.getenv('KINOPOISK_API_KEY'):
            logger.warning("API ключи для поиска медиа не установлены! Поиск будет недоступен")
        if not os.getenv('GOOGLE_APPLICATION_CREDENTIALS'):
            logger.warning("Google Vision недоступен без учетных данных")
        
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
