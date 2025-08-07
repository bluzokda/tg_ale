import os
import logging
import tempfile
import gc
import re
import requests
from PIL import Image, ImageEnhance
import pytesseract
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация API
OMDB_API_URL = "http://www.omdbapi.com/"
KINOPOISK_API_URL = "https://kinopoiskapiunofficial.tech/api/v2.1/films/search-by-keyword"

def preprocess_image(image_bytes: bytes) -> Image.Image:
    """Улучшает изображение для обработки OCR"""
    with Image.open(image_bytes) as img:
        # Уменьшаем размер
        img.thumbnail((512, 512))
        
        # Улучшаем качество для OCR
        img = img.convert('L')  # Ч/Б
        enhancer = ImageEnhance.Contrast(img)
        img = enhancer.enhance(2.0)
        enhancer = ImageEnhance.Sharpness(img)
        img = enhancer.enhance(2.0)
        return img

def extract_text_with_tesseract(image: Image.Image) -> str:
    """Извлекает текст с изображения через Tesseract OCR"""
    try:
        custom_config = r'--oem 3 --psm 6 -l eng+rus'
        text = pytesseract.image_to_string(image, config=custom_config)
        return text.strip()
    except Exception as e:
        logger.error(f"Ошибка Tesseract OCR: {e}")
        return ""

def search_omdb(title: str) -> dict:
    """Поиск через OMDb API"""
    try:
        params = {
            'apikey': os.getenv('OMDB_API_KEY'),
            't': title,
            'type': 'movie,series,episode',
            'plot': 'short',
            'r': 'json'
        }
        response = requests.get(OMDB_API_URL, params=params, timeout=15)
        data = response.json()
        return data if data.get('Response') == 'True' else {}
    except Exception as e:
        logger.error(f"Ошибка OMDb API: {e}")
        return {}

def search_kinopoisk(title: str) -> dict:
    """Поиск через Kinopoisk Unofficial API"""
    try:
        headers = {'X-API-KEY': os.getenv('KINOPOISK_API_KEY')}
        params = {'keyword': title}
        
        response = requests.get(
            KINOPOISK_API_URL,
            headers=headers,
            params=params,
            timeout=15
        )
        data = response.json()
        return data['films'][0] if data.get('films') else {}
    except Exception as e:
        logger.error(f"Ошибка Kinopoisk API: {e}")
        return {}

def convert_kinopoisk_to_omdb(kinopoisk_data: dict) -> dict:
    """Конвертирует данные Kinopoisk в формат OMDb"""
    return {
        'Title': kinopoisk_data.get('nameRu') or kinopoisk_data.get('nameEn', ''),
        'Year': kinopoisk_data.get('year', 'N/A'),
        'Type': 'movie',
        'Plot': kinopoisk_data.get('description', 'Описание отсутствует'),
        'Poster': kinopoisk_data.get('posterUrl', ''),
        'imdbRating': kinopoisk_data.get('rating', 'N/A'),
        'Runtime': 'N/A',
        'Response': 'True',
        'Genre': ', '.join(genre['genre'] for genre in kinopoisk_data.get('genres', [])),
        'Director': next((p['name'] for p in kinopoisk_data.get('staff', []) 
                         if p.get('professionKey') == 'DIRECTOR'), 'N/A'),
        'imdbID': f"kp{kinopoisk_data.get('filmId', '')}"
    }

def search_media(title: str) -> dict:
    """Поиск медиа через API"""
    # Пробуем OMDb первым
    omdb_result = search_omdb(title)
    if omdb_result:
        return omdb_result
    
    # Если OMDb не дал результатов, пробуем Kinopoisk
    kinopoisk_result = search_kinopoisk(title)
    return convert_kinopoisk_to_omdb(kinopoisk_result) if kinopoisk_result else {}

def format_media_info(media_data: dict) -> str:
    """Форматирует информацию о медиа-контенте"""
    if not media_data:
        return "❌ Информация не найдена"
    
    title = media_data.get('Title', 'Без названия')
    year = media_data.get('Year', 'N/A')
    media_type = "Фильм" if media_data.get('Type') == 'movie' else "Сериал"
    rating = media_data.get('imdbRating', 'N/A')
    plot = media_data.get('Plot', 'Описание отсутствует')
    director = media_data.get('Director', 'N/A')
    genre = media_data.get('Genre', 'N/A')
    
    # Форматирование ответа
    info = f"🎬 <b>{title}</b> ({year})"
    info += f"\n📀 Тип: {media_type}"
    info += f"\n🎭 Жанр: {genre}"
    info += f"\n🎥 Режиссер: {director}"
    info += f"\n⭐ Рейтинг: {rating}/10"
    info += f"\n\n📝 <i>{plot[:300]}{'...' if len(plot) > 300 else ''}</i>"
    
    # Постер
    if media_data.get('Poster') and media_data['Poster'] != 'N/A':
        info += f"\n\n🖼 <a href='{media_data['Poster']}'>Постер</a>"
    
    # Ссылка на источник
    if media_data.get('imdbID', '').startswith('tt'):
        info += f"\n\n🔗 <a href='https://www.imdb.com/title/{media_data['imdbID']}/'>IMDb</a>"
    elif media_data.get('imdbID', '').startswith('kp'):
        kp_id = media_data['imdbID'][2:]
        info += f"\n\n🔗 <a href='https://www.kinopoisk.ru/film/{kp_id}/'>Кинопоиск</a>"
    
    return info

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start"""
    try:
        user = update.effective_user
        await update.message.reply_text(
            f"Привет, {user.mention_html()}! Отправь мне скриншот из фильма/сериала, "
            "и я найду информацию о нём!",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Ошибка в команде start: {e}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик фотографий"""
    try:
        # Берем фото среднего качества для экономии ресурсов
        photo_file = await update.message.photo[-2].get_file()
        
        # Создаем временный файл в памяти
        with tempfile.NamedTemporaryFile(delete=False) as temp_file:
            await photo_file.download_to_drive(temp_file.name)
            logger.info(f"Фото сохранено: {temp_file.name}")
            
            # Предобработка и OCR
            processed_img = preprocess_image(temp_file.name)
            text = extract_text_with_tesseract(processed_img)
            
            if text:
                logger.info(f"Распознанный текст: {text}")
                # Ищем наиболее вероятное название (первая строка или 3 слова)
                cleaned_text = re.sub(r'\s+', ' ', text).strip()
                possible_title = ' '.join(cleaned_text.split('\n')[0].split()[:5])
                
                await update.message.reply_text(f"🔍 Распознано: {possible_title}")
                
                # Поиск информации
                media_data = search_media(possible_title)
                response = format_media_info(media_data)
                await update.message.reply_text(response, parse_mode='HTML')
            else:
                await update.message.reply_text("❌ Не удалось распознать текст на изображении")
        
        # Ручной вызов сборщика мусора
        gc.collect()
        
    except Exception as e:
        logger.error(f"Ошибка обработки фото: {e}")
        await update.message.reply_text("⚠️ Произошла ошибка при обработке изображения")
    finally:
        try:
            os.unlink(temp_file.name)
        except Exception:
            pass

def main() -> None:
    """Запуск бота"""
    try:
        logger.info("Запуск оптимизированного бота...")
        
        # Проверка обязательных переменных
        token = os.getenv('TELEGRAM_TOKEN')
        if not token:
            raise ValueError("TELEGRAM_TOKEN не установлен")
        
        # Проверка API ключей
        if not os.getenv('OMDB_API_KEY') and not os.getenv('KINOPOISK_API_KEY'):
            logger.warning("API ключи для поиска медиа не установлены")
        
        # Инициализация бота
        application = Application.builder().token(token).build()
        
        # Регистрация обработчиков
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
        
        # Запуск
        logger.info("Бот запущен...")
        application.run_polling()
        
    except Exception as e:
        logger.exception("Критическая ошибка при запуске")
        raise

if __name__ == '__main__':
    main()
