import os
import json
import logging
import requests
import tempfile
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters
from google.oauth2 import service_account
from google.cloud import vision

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# === Переменные окружения ===
TELEGRAM_TOKEN = os.getenv('TELEGRAM_TOKEN')
OMDB_API_KEY = os.getenv('OMDB_API_KEY')
GOOGLE_CREDENTIALS_JSON = os.getenv('GOOGLE_CREDENTIALS_JSON')  # Весь JSON-ключ в одной переменной

# Проверка критических переменных
if not TELEGRAM_TOKEN:
    logger.critical("🛑 ОШИБКА: Переменная окружения TELEGRAM_TOKEN не установлена!")
    raise EnvironmentError("TELEGRAM_TOKEN не установлена")

if not OMDB_API_KEY:
    logger.critical("🛑 ОШИБКА: Переменная окружения OMDB_API_KEY не установлена!")
    raise EnvironmentError("OMDB_API_KEY не установлена")

if not GOOGLE_CREDENTIALS_JSON:
    logger.critical("🛑 ОШИБКА: Переменная окружения GOOGLE_CREDENTIALS_JSON не установлена!")
    raise EnvironmentError("GOOGLE_CREDENTIALS_JSON не установлена")

# URL для OMDB API
OMDB_API_URL = "http://www.omdbapi.com/"


# === Инициализация Google Vision API клиента ===
def get_vision_client():
    """
    Создаёт клиент Google Vision API из JSON-ключа в переменной окружения
    """
    try:
        credentials_dict = json.loads(GOOGLE_CREDENTIALS_JSON)
        credentials = service_account.Credentials.from_service_account_info(credentials_dict)
        return vision.ImageAnnotatorClient(credentials=credentials)
    except json.JSONDecodeError as e:
        logger.error(f"❌ Ошибка парсинга JSON-ключа Google: {e}")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации Google Vision API: {e}")
    return None


# === Поиск фильма/сериала в OMDB API ===
def search_omdb(query: str) -> dict:
    """
    Ищет фильм/сериал по названию через OMDB API
    """
    if not query or len(query.strip()) < 2:
        return {}
    query = query.strip()

    params = {
        "apikey": OMDB_API_KEY,
        "t": query,
        "plot": "full",
        "r": "json"
    }

    try:
        response = requests.get(OMDB_API_URL, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get("Response") == "True":
                return {
                    "title": data["Title"],
                    "year": data["Year"],
                    "plot": data["Plot"],
                    "rating": data["imdbRating"],
                    "poster": data["Poster"] if data["Poster"] != "N/A" else None,
                    "type": data["Type"].capitalize(),
                    "imdb_id": data["imdbID"]
                }
    except requests.RequestException as e:
        logger.error(f"🌐 Ошибка запроса к OMDB API: {e}")
    except Exception as e:
        logger.error(f"❌ Ошибка обработки ответа OMDB: {e}")

    return {}


# === Форматирование ответа бота ===
def format_response(media_info: dict) -> tuple[str, str | None]:
    """
    Возвращает (текст, URL_постера)
    """
    if not media_info:
        text = (
            "❌ Не удалось распознать фильм или сериал.\n\n"
            "Попробуй отправить:\n"
            "• Кадр с названием\n"
            "• Лицо известного актёра\n"
            "• Постер или логотип"
        )
        return text, None

    title = media_info['title']
    year = media_info['year']
    media_type = media_info['type']
    rating = media_info['rating']
    plot = media_info['plot']
    poster_url = media_info['poster']
    imdb_id = media_info['imdb_id']

    # Формируем текст
    info = f"🎬 <b>{title}</b> ({year})"
    info += f"\n📌 <b>Тип:</b> {media_type}"

    if rating and rating != "N/A":
        info += f"\n⭐ <b>Рейтинг:</b> {rating}/10"

    info += f"\n\n📝 <i>{plot}</i>"

    # Ссылка на OMDB
    link = f"https://www.omdb.com/title/tt{imdb_id}" if imdb_id else "https://www.omdb.com"
    info += f"\n\n🔗 <a href='{link}'>Подробнее на OMDB</a>"

    return info, poster_url


# === Обработчик команды /start ===
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🎬 Привет! Отправь мне скриншот из фильма или сериала — "
        "я попробую определить, что это за кино!\n\n"
        "Чем лучше кадр (с названием, лицом, постером), тем выше шанс найти."
    )


# === Обработчик фото ===
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    temp_file_path = None
    try:
        # Получаем лучшее качество фото
        photo_file = await update.message.photo[-1].get_file()

        # Сохраняем во временный файл
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as tmp_file:
            await photo_file.download_to_drive(tmp_file.name)
            temp_file_path = tmp_file.name

        logger.info(f"📸 Фото сохранено: {temp_file_path}")

        # Отправляем статус
        await update.message.reply_text("🔍 Анализирую кадр...")

        # === Google Vision API ===
        client = get_vision_client()
        if not client:
            await update.message.reply_text(
                "❌ Ошибка подключения к Google Vision API.\n"
                "Администратор уже получил уведомление."
            )
            return

        # Читаем изображение
        with open(temp_file_path, 'rb') as image_file:
            content = image_file.read()
        image = vision.Image(content=content)

        # 1. OCR: распознавание текста
        text_response = client.text_detection(image=image)
        detected_text = ""
        if text_response.text_annotations:
            detected_text = text_response.text_annotations[0].description.strip()

        # 2. Web Detection: знаменитости, объекты, логотипы
        web_response = client.web_detection(image=image)
        web = web_response.web_detection
        celebrities = [ent.description for ent in web.web_entities if len(ent.description) < 40][:3]

        # Формируем запросы для поиска
        search_queries = []
        if detected_text:
            # Берём первую строку текста
            first_line = detected_text.split('\n')[0].strip()
            if len(first_line) > 2:
                search_queries.append(first_line)

        # Добавляем знаменитостей
        search_queries.extend([c for c in celebrities if len(c) > 2])

        # Убираем дубли
        search_queries = list(dict.fromkeys(search_queries))

        logger.info(f"🔍 Поисковые запросы: {search_queries}")

        # === Поиск в OMDB ===
        media_info = {}
        for query in search_queries:
            logger.info(f"🔎 Поиск: {query}")
            media_info = search_omdb(query)
            if media_info:
                break

        # === Отправка результата ===
        text, poster_url = format_response(media_info)

        if poster_url and "http" in poster_url:
            try:
                await update.message.reply_photo(
                    photo=poster_url,
                    caption=text,
                    parse_mode='HTML'
                )
            except Exception:
                # Если фото не отправилось — отправим просто текст
                await update.message.reply_text(text, parse_mode='HTML')
        else:
            await update.message.reply_text(text, parse_mode='HTML')

    except Exception as e:
        logger.error(f"❌ Критическая ошибка обработки фото: {e}")
        try:
            await update.message.reply_text(
                "⚠️ Произошла ошибка при анализе изображения.\n"
                "Попробуй позже или отправь другой кадр."
            )
        except:
            pass  # Игнорируем, если не можем отправить сообщение

    finally:
        # Удаление временного файла
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except Exception as e:
                logger.error(f"❌ Не удалось удалить временный файл: {e}")


# === Запуск бота ===
def main() -> None:
    try:
        application = Application.builder().token(TELEGRAM_TOKEN).build()

        # Обработчики
        application.add_handler(CommandHandler("start", start))
        application.add_handler(MessageHandler(filters.PHOTO, handle_photo))

        logger.info("🚀 Бот успешно запущен и готов к работе!")
        application.run_polling()

    except Exception as e:
        logger.critical(f"🛑 КРИТИЧЕСКАЯ ОШИБКА при запуске бота: {e}")
        raise


if __name__ == '__main__':
    main()
