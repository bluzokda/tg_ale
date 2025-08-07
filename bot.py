import os
import logging
import requests
import tempfile
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from google.cloud import vision

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Конфигурация API
OMDB_API_KEY = os.getenv('OMDB_API_KEY')
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')

# Проверка переменных окружения
if not OMDB_API_KEY:
    raise EnvironmentError("Переменная окружения OMDB_API_KEY не установлена!")
if not TELEGRAM_TOKEN:
    raise EnvironmentError("Переменная окружения TELEGRAM_TOKEN не установлена!")

OMDB_API_URL = "http://www.omdbapi.com/"

def detect_text_and_entities(image_path):
    """Анализирует изображение через Google Vision API"""
    try:
        client = vision.ImageAnnotatorClient()

        with open(image_path, 'rb') as image_file:
            content = image_file.read()
        image = vision.Image(content=content)

        # OCR
        text_response = client.text_detection(image=image)
        texts = text_response.full_text_annotation.text.strip() if text_response.full_text_annotation else ""

        # Распознавание знаменитостей и объектов
        web_response = client.web_detection(image=image)
        celebrities = [
            entity.description for entity in web_response.web_entities
            if 'celebrity' in entity.description.lower()
        ]

        return {
            'text': texts,
            'celebrities': celebrities,
        }
    except Exception as e:
        logger.error(f"Ошибка Google Vision API: {e}")
        return {}

def search_media_by_omdb(query):
    """Поиск фильма/сериала через OMDB API"""
    params = {
        "apikey": OMDB_API_KEY,
        "t": query,
        "plot": "short",
        "r": "json"
    }
    response = requests.get(OMDB_API_URL, params=params, timeout=10)
    if response.status_code == 200:
        data = response.json()
        if data.get('Response') == 'True':
            return {
                'title': data.get('Title'),
                'year': data.get('Year'),
                'plot': data.get('Plot'),
                'rating': data.get('imdbRating'),
                'poster': data.get('Poster'),
                'type': data.get('Type').capitalize(),
            }
    return {}

def format_media_info(media_data) -> tuple[str, str | None]:
    """Форматирует информацию о фильме/сериале"""
    if not media_data:
        return "❌ Не удалось найти фильм или сериал. Попробуй другой кадр.", None

    title = media_data['title']
    year = media_data['year'] or 'N/A'
    media_type = media_data['type']
    rating = media_data['rating'] or 'N/A'
    plot = media_data['plot'] or 'Описание отсутствует.'
    poster_url = media_data['poster']

    info = f"🎬 <b>{title}</b> ({year})"
    info += f"\n📀 Тип: {media_type}"
    info += f"\n⭐ Рейтинг: {rating}/10"
    info += f"\n\n📝 <i>{plot}</i>"

    return info, poster_url

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик команды /start"""
    await update.message.reply_text(
        "Привет! Отправь мне скриншот из фильма/сериала, "
        "и я найду информацию о нём по визуальному содержанию кадра!"
    )

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик фото"""
    temp_file_path = None
    try:
        # Берем фото (лучшее качество)
        photo_file = await update.message.photo[-1].get_file()

        # Сохраняем во временный файл
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp_file:
            await photo_file.download_to_drive(tmp_file.name)
            temp_file_path = tmp_file.name
        logger.info(f"Фото загружено: {temp_file_path}")

        # Анализируем изображение
        await update.message.reply_text("🔍 Анализирую кадр...")
        media_data = detect_text_and_entities(temp_file_path)

        # Получаем текст и знаменитостей
        detected_text = media_data.get('text', '').strip()
        celebrities = media_data.get('celebrities', [])

        # Поиск в OMDB API
        search_queries = [detected_text] + celebrities
        for query in search_queries:
            if not query:
                continue
            logger.info(f"Поиск по запросу: {query}")
            media_info = search_media_by_omdb(query)
            if media_info:
                break

        # Форматируем ответ
        text, poster_url = format_media_info(media_info)

        # Отправляем результат
        if poster_url:
            await update.message.reply_photo(
                photo=poster_url,
                caption=text,
                parse_mode='HTML'
            )
        else:
            await update.message.reply_text(text, parse_mode='HTML')

    except Exception as e:
        logger.error(f"Ошибка обработки фото: {e}")
        await update.message.reply_text(
            "⚠️ Произошла ошибка при анализе изображения.\n"
            "Попробуй отправить другой скриншот."
        )
    finally:
        # Удаление временного файла
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except Exception as e:
                logger.error(f"Не удалось удалить временный файл: {e}")

def main() -> None:
    """Запуск бота"""
    try:
        application = Application.builder().token(TELEGRAM_TOKEN).build()

        # Обработчики
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

        logger.info("🚀 Бот запущен и готов к работе!")
        application.run_polling()

    except Exception as e:
        logger.critical(f"Критическая ошибка: {e}")
        raise

if __name__ == '__main__':
    main()
