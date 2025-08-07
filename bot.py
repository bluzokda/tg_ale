import os
import logging
import tempfile
import gc
import re
import time
import requests
import cv2
import numpy as np
from PIL import Image, ImageEnhance
import pytesseract
import telegram
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

def preprocess_image(image_path: str) -> Image.Image:
    """Улучшает изображение для обработки OCR с использованием комбинированных методов"""
    try:
        # Загрузка изображения
        img = cv2.imread(image_path)
        if img is None:
            raise ValueError("Не удалось загрузить изображение")
        
        # Уменьшение размера с сохранением соотношения сторон
        height, width = img.shape[:2]
        max_dim = 1200
        scale = max_dim / max(height, width)
        new_width = int(width * scale)
        new_height = int(height * scale)
        img = cv2.resize(img, (new_width, new_height), interpolation=cv2.INTER_AREA)
        
        # Конвертация в серый
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # Автоматическая коррекция контраста с CLAHE
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        enhanced = clahe.apply(gray)
        
        # Уменьшение шума с сохранением границ
        denoised = cv2.bilateralFilter(enhanced, 9, 75, 75)
        
        # Определение типа фона и инверсия при необходимости
        mean_val = np.mean(denoised)
        if mean_val < 128:
            denoised = cv2.bitwise_not(denoised)
        
        # Комбинированная бинаризация
        # 1. Адаптивная бинаризация
        adaptive_thresh = cv2.adaptiveThreshold(
            denoised, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY, 31, 8
        )
        
        # 2. Глобальная бинаризация Otsu
        _, otsu_thresh = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        
        # Комбинирование результатов
        combined = cv2.bitwise_and(adaptive_thresh, otsu_thresh)
        
        # Увеличение резкости
        kernel = np.array([[-1, -1, -1], 
                           [-1, 9, -1], 
                           [-1, -1, -1]])
        sharpened = cv2.filter2D(combined, -1, kernel)
        
        return Image.fromarray(sharpened)
        
    except Exception as e:
        logger.error(f"Ошибка обработки изображения: {e}")
        try:
            # Fallback обработка
            img = Image.open(image_path)
            img.thumbnail((1200, 1200))
            img = img.convert('L')
            enhancer = ImageEnhance.Contrast(img)
            img = enhancer.enhance(3.0)
            enhancer = ImageEnhance.Sharpness(img)
            img = enhancer.enhance(3.0)
            return img
        except Exception as e2:
            logger.error(f"Ошибка fallback обработки: {e2}")
            return None

def extract_text_with_tesseract(image: Image.Image) -> str:
    """Извлекает текст с изображения через Tesseract OCR с улучшениями"""
    try:
        # Множественные попытки с разными параметрами и языками
        config_attempts = [
            ('--oem 3 --psm 11', 'eng+rus'),       # Разреженный текст
            ('--oem 3 --psm 6', 'eng+rus'),        # Единый блок текста
            ('--oem 3 --psm 4', 'eng+rus'),        # Вертикальный текст
            ('--oem 1 --psm 7', 'eng+rus'),        # Одна строка
            ('--oem 3 --psm 11', 'equ+osd'),       # Оптическое распознавание символов
            ('--oem 3 --psm 11', 'eng+rus+equ')    # Комбинированный
        ]
        
        best_text = ""
        best_score = 0
        
        for config, lang in config_attempts:
            full_config = f"{config} -l {lang}"
            text = pytesseract.image_to_string(image, config=full_config).strip()
            if not text:
                continue
                
            # Оценка качества текста
            alpha_count = sum(1 for c in text if c.isalpha())
            digit_count = sum(1 for c in text if c.isdigit())
            space_count = sum(1 for c in text if c.isspace())
            total_chars = len(text)
            
            # Штраф за слишком много цифр или спецсимволов
            score = alpha_count - 0.5 * digit_count - 0.3 * (total_chars - alpha_count - digit_count - space_count)
            
            if score > best_score:
                best_text = text
                best_score = score
                logger.info(f"Новый лучший текст: {text} (оценка: {score})")
                
        return best_text
    except Exception as e:
        logger.error(f"Ошибка Tesseract OCR: {e}")
        return ""

def extract_text_regions(image_path: str) -> str:
    """Выделяет области с текстом и распознаёт их отдельно"""
    try:
        # Загрузка изображения
        img = cv2.imread(image_path)
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # Детекция текста
        text_regions = []
        
        # Метод 1: MSER
        mser = cv2.MSER_create()
        regions, _ = mser.detectRegions(gray)
        
        for region in regions:
            x, y, w, h = cv2.boundingRect(region.reshape(-1, 1, 2))
            if w > 20 and h > 10:  # Фильтр маленьких областей
                text_regions.append((x, y, x + w, y + h))
        
        # Если не найдено регионов, попробуем другой метод
        if not text_regions:
            # Метод 2: Контуры
            blurred = cv2.GaussianBlur(gray, (5, 5), 0)
            edged = cv2.Canny(blurred, 50, 150)
            contours, _ = cv2.findContours(edged, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            
            for contour in contours:
                if cv2.contourArea(contour) > 100:
                    x, y, w, h = cv2.boundingRect(contour)
                    text_regions.append((x, y, x + w, y + h))
        
        # Распознавание текста в регионах
        combined_text = []
        for (x1, y1, x2, y2) in text_regions:
            # Увеличиваем область для контекста
            padding = 10
            x1 = max(0, x1 - padding)
            y1 = max(0, y1 - padding)
            x2 = min(img.shape[1], x2 + padding)
            y2 = min(img.shape[0], y2 + padding)
            
            roi = img[y1:y2, x1:x2]
            roi_pil = Image.fromarray(cv2.cvtColor(roi, cv2.COLOR_BGR2RGB))
            text = pytesseract.image_to_string(roi_pil, config=r'--oem 3 --psm 7 -l eng+rus').strip()
            
            if text:
                combined_text.append(text)
        
        return "\n".join(combined_text)
        
    except Exception as e:
        logger.error(f"Ошибка выделения текстовых регионов: {e}")
        return ""

def is_valid_title(text: str) -> bool:
    """Проверяет, похож ли текст на название фильма"""
    # Минимум 3 символа, содержащих буквы
    if len(text) < 3 or not any(char.isalpha() for char in text):
        return False
        
    # Исключаем случайные комбинации символов
    invalid_patterns = [
        r'^[\W\d]+$',  # Только цифры и символы
        r'^[a-z]{1,2}\s*[\W]?\s*[a-z]{1,2}$',  # Пример: "a b" или "a=b"
        r'http[s]?://',  # URL
        r'\d{1,2}[:;]\d{2}',  # Время (10:30)
        r'\d{1,2}/\d{1,2}/\d{2,4}',  # Дата
        r'[0-9]{3,}',  # Много цифр
        r'[^a-zа-я0-9]{4,}',  # Много спецсимволов подряд
    ]
    
    for pattern in invalid_patterns:
        if re.search(pattern, text, re.IGNORECASE):
            return False
            
    return True

def clean_title(text: str) -> str:
    """Очищает и форматирует распознанное название"""
    # Удаляем спецсимволы и лишние пробелы
    cleaned = re.sub(r'[^\w\s\-:.,!?\'"]', '', text, flags=re.UNICODE)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    
    # Выбираем наиболее вероятную строку
    lines = cleaned.split('\n')
    if not lines:
        return cleaned
    
    # Сортируем строки по количеству букв
    lines.sort(key=lambda line: sum(1 for c in line if c.isalpha()), reverse=True)
    best_line = lines[0]
    
    # Берем первые 3-5 слов
    words = best_line.split()
    if len(words) > 5:
        return ' '.join(words[:5])
    return best_line

def search_media(title: str) -> dict:
    """Поиск медиа через API с несколькими попытками"""
    if not title:
        return {}
    
    # Попробуем разные варианты запросов
    search_attempts = [
        title,
        re.sub(r'[^\w\s]', '', title),  # Без пунктуации
        ' '.join(title.split()[:3]),     # Первые 3 слова
        ' '.join(title.split()[-3:]),    # Последние 3 слова
    ]
    
    # Удаляем дубликаты
    search_attempts = list(dict.fromkeys(search_attempts))
    
    for attempt in search_attempts:
        # Попробуем OMDb
        try:
            params = {
                'apikey': os.getenv('OMDB_API_KEY'),
                't': attempt,
                'type': 'movie,series,episode',
                'plot': 'short',
                'r': 'json'
            }
            response = requests.get(OMDB_API_URL, params=params, timeout=15)
            data = response.json()
            if data.get('Response') == 'True':
                return data
        except Exception:
            pass
        
        # Попробуем Kinopoisk
        try:
            headers = {'X-API-KEY': os.getenv('KINOPOISK_API_KEY')}
            params = {'keyword': attempt}
            
            response = requests.get(
                KINOPOISK_API_URL,
                headers=headers,
                params=params,
                timeout=15
            )
            data = response.json()
            if data.get('films'):
                film = data['films'][0]
                return {
                    'Title': film.get('nameRu') or film.get('nameEn', ''),
                    'Year': film.get('year', 'N/A'),
                    'Type': 'movie',
                    'Plot': film.get('description', 'Описание отсутствует'),
                    'Poster': film.get('posterUrl', ''),
                    'imdbRating': film.get('rating', 'N/A'),
                    'Runtime': 'N/A',
                    'Response': 'True',
                    'Genre': ', '.join(genre['genre'] for genre in film.get('genres', [])),
                    'Director': next((p['name'] for p in film.get('staff', []) 
                                    if p.get('professionKey') == 'DIRECTOR'), 'N/A'),
                    'imdbID': f"kp{film.get('filmId', '')}"
                }
        except Exception:
            pass
    
    return {}

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
    
    if genre != 'N/A':
        info += f"\n🎭 Жанр: {genre}"
    
    if director != 'N/A':
        info += f"\n🎥 Режиссер: {director}"
    
    if rating != 'N/A':
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
            "и я найду информацию о нём!\n\n"
            "📌 Советы для лучшего распознавания:\n"
            "1. Выбирайте кадры с чёткими надписями (титры, названия)\n"
            "2. Избегайте слишком тёмных или светлых изображений\n"
            "3. Обрезайте изображение, чтобы текст был в центре\n"
            "4. Идеальные кадры: начальные/конечные титры, экраны с названием\n\n"
            "Если автоматическое распознавание не сработает, попробуйте отправить "
            "название фильма текстовым сообщением.",
            parse_mode='HTML'
        )
    except Exception as e:
        logger.error(f"Ошибка в команде start: {e}")

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик фотографий"""
    temp_file_path = None
    try:
        # Берем фото среднего качества для экономии ресурсов
        photo_file = await update.message.photo[-2].get_file()
        
        # Создаем временный файл
        with tempfile.NamedTemporaryFile(delete=False, suffix='.jpg') as temp_file:
            await photo_file.download_to_drive(temp_file.name)
            temp_file_path = temp_file.name
            logger.info(f"Фото сохранено: {temp_file_path}")
            
            # Предобработка и OCR - попробуем два метода
            processed_img = preprocess_image(temp_file_path)
            if not processed_img:
                await update.message.reply_text("❌ Ошибка обработки изображения")
                return
                
            # Метод 1: Обычное распознавание
            raw_text = extract_text_with_tesseract(processed_img)
            
            # Метод 2: Выделение текстовых областей (если первый метод не дал результата)
            if not raw_text or not any(c.isalpha() for c in raw_text):
                region_text = extract_text_regions(temp_file_path)
                if region_text:
                    raw_text = region_text
            
            if not raw_text or not any(c.isalpha() for c in raw_text):
                await update.message.reply_text(
                    "❌ Не удалось распознать текст на изображении\n"
                    "Попробуйте:\n"
                    "• Отправить другой кадр\n"
                    "• Выбрать изображение с более чётким текстом\n"
                    "• Отправить название фильма текстовым сообщением"
                )
                return
                
            # Очистка и проверка названия
            cleaned_text = clean_title(raw_text)
            
            if not is_valid_title(cleaned_text):
                await update.message.reply_text(
                    f"❌ Распознанный текст не похож на название фильма: «{cleaned_text}»\n"
                    "Попробуйте отправить другой кадр или ввести название вручную"
                )
                return
                
            logger.info(f"Распознанный текст: {cleaned_text}")
            await update.message.reply_text(f"🔍 Распознано: «{cleaned_text}»")
            
            # Поиск информации
            media_data = search_media(cleaned_text)
            response = format_media_info(media_data)
            await update.message.reply_text(response, parse_mode='HTML')
        
    except Exception as e:
        logger.error(f"Ошибка обработки фото: {e}")
        await update.message.reply_text(
            "⚠️ Произошла ошибка при обработке изображения\n"
            "Попробуйте отправить другой кадр или название фильма текстом"
        )
    finally:
        # Очистка временных файлов
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.unlink(temp_file_path)
            except Exception as e:
                logger.error(f"Ошибка удаления временного файла: {e}")
        gc.collect()

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Обработчик текстовых сообщений (ручной ввод названия)"""
    try:
        text = update.message.text.strip()
        if not text:
            await update.message.reply_text("Пожалуйста, введите название фильма")
            return
            
        # Ограничение длины
        if len(text) > 100:
            text = text[:100]
            
        logger.info(f"Поиск по ручному вводу: {text}")
        await update.message.reply_text(f"🔍 Ищем: «{text}»")
        
        # Поиск информации
        media_data = search_media(text)
        response = format_media_info(media_data)
        await update.message.reply_text(response, parse_mode='HTML')
        
    except Exception as e:
        logger.error(f"Ошибка обработки текста: {e}")
        await update.message.reply_text("⚠️ Произошла ошибка при поиске информации")

def main() -> None:
    """Запуск бота с защитой от конфликтов"""
    max_retries = 5
    retry_delay = 10  # секунд
    
    for attempt in range(max_retries):
        try:
            logger.info(f"Попытка запуска бота #{attempt + 1}")
            
            # Проверка обязательных переменных
            token = os.getenv('TELEGRAM_TOKEN')
            if not token:
                raise ValueError("TELEGRAM_TOKEN не установен")
            
            # Проверка API ключей
            if not os.getenv('OMDB_API_KEY') and not os.getenv('KINOPOISK_API_KEY'):
                logger.warning("API ключи для поиска медиа не установлены")
            
            # Инициализация бота
            application = Application.builder().token(token).build()
            
            # Регистрация обработчиков
            application.add_handler(CommandHandler("start", start))
            application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
            application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
            
            # Запуск с увеличенным интервалом опроса
            logger.info("Бот запущен...")
            application.run_polling(
                poll_interval=3.0,  # Увеличенный интервал
                close_loop=False,
                stop_signals=None
            )
            break  # Успешный запуск, выходим из цикла
            
        except telegram.error.Conflict as e:
            logger.error(f"Конфликт обновлений: {e}")
            if attempt < max_retries - 1:
                logger.info(f"Повторная попытка через {retry_delay} секунд...")
                time.sleep(retry_delay)
                retry_delay *= 2  # Экспоненциальная задержка
            else:
                logger.critical("Достигнуто максимальное количество попыток. Завершение работы.")
                raise
        except Exception as e:
            logger.exception("Критическая ошибка при запуске")
            raise

if __name__ == '__main__':
    main()
