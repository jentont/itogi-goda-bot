"""Настройки бота «Итоги года». Всё берётся из .env рядом с main.py."""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env")


def _ids(raw: str) -> tuple[int, ...]:
    return tuple(int(x) for x in raw.replace(";", ",").split(",") if x.strip().lstrip("-").isdigit())


#: Токен от @BotFather.
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

#: Кто видит /admin. Через запятую.
ADMIN_IDS = _ids(os.getenv("ADMIN_IDS", ""))

#: Публичный адрес мини-аппа, обязательно https. Например https://itogi.example.com
#: Этот же адрес отдаёт карточки для инлайна и историй.
PUBLIC_URL = os.getenv("PUBLIC_URL", "").rstrip("/")

#: Где слушает встроенный веб-сервер. Снаружи его закрывает nginx с https.
WEB_HOST = os.getenv("WEB_HOST", "127.0.0.1")
WEB_PORT = int(os.getenv("WEB_PORT") or os.getenv("PORT") or "8080")

#: Какой год подводим.
YEAR = int(os.getenv("YEAR", "2026"))

#: Файл базы.
DB_PATH = Path(os.getenv("DB_PATH", str(ROOT / "data" / "itogi.db")))
#: PostgreSQL (postgresql://user:pass@host:5432/db). Задан — база там, а не в файле DB_PATH.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()

#: Куда складывать готовые карточки (mp4/jpg). Чистится само по возрасту.
CARDS_DIR = Path(os.getenv("CARDS_DIR", str(ROOT / "data" / "cards")))

#: Закрытый канал/чат, куда бот молча заливает карточки, чтобы получить
#: file_id для инлайна. Бот должен быть там админом. Можно не задавать —
#: тогда инлайн отдаёт карточку по ссылке PUBLIC_URL.
STORAGE_CHAT_ID = int(os.getenv("STORAGE_CHAT_ID", "0") or 0)

#: Сколько верить проверке подписки, секунд.
SUBSCRIBE_TTL = int(os.getenv("SUBSCRIBE_TTL", "600"))

#: Пак премиум-эмодзи бота (имя из t.me/addemoji/ИМЯ). Пусто — возьмём
#: тот, что бот соберёт командой /makepack.
EMOJI_PACK = os.getenv("EMOJI_PACK", "")

#: Включать ли премиум-эмодзи в текстах и на кнопках. Telegram пускает их
#: только у ботов с купленным на Fragment юзернеймом — если нельзя, бот
#: сам тихо откатится на обычные эмодзи.
PREMIUM_EMOJI_ON = os.getenv("PREMIUM_EMOJI_ON", "1") not in ("0", "false", "no")

#: Сколько друзей открывают «бонусные» слайды. 0 — бонусов нет, всё сразу.
FRIENDS_FOR_BONUS = int(os.getenv("FRIENDS_FOR_BONUS", "0"))

#: Ссылка на канал проекта (кнопка в меню и в финале мини-аппа).
PROJECT_CHANNEL = os.getenv("PROJECT_CHANNEL", "")

#: Ссылка «Реклама и сотрудничество».
ADS_CONTACT = os.getenv("ADS_CONTACT", "")

#: Час суточного отчёта по спонсорам, по Москве.
REPORT_HOUR = int(os.getenv("REPORT_HOUR", "21"))

ASSETS = ROOT / "assets"
WEBAPP_DIR = ROOT / "webapp"
FONTS = ASSETS / "fonts"
TGS_DIR = ASSETS / "tgs"
#: Белые иконки кнопок (tools/icons.py) — отдельный пак, перекрашивается под тему.
ICONS_DIR = ASSETS / "icons"
#: Готовые паки иконок для кнопок (имена из t.me/addemoji/ИМЯ), через запятую.
#: Свой пак создавать не нужно: бот берёт эмодзи из любого публичного пака.
ICON_PACKS = [x.strip() for x in os.getenv("ICON_PACKS", "tgiosicons").split(",") if x.strip()]

#: Примерная цена одной звезды в рублях — для «коллекция стоит ≈ N ₽».
STAR_RUB = float(os.getenv("STAR_RUB", "1.8"))

#: Сколько долларов стоит звезда — для оценки NFT-юзернеймов (TON → звёзды), как в mogbattle.
USD_PER_STAR = float(os.getenv("USD_PER_STAR", "0.015"))

#: Аккаунт-сборщик (как в mogbattle): ключи приложения с my.telegram.org.
#: Можно не задавать здесь — админка спросит их при подключении.
TG_API_ID = int(os.getenv("TG_API_ID", "0") or 0)
TG_API_HASH = os.getenv("TG_API_HASH", "")
#: Анонимный номер +888 (Fragment, ~2000 TON) — сборщик видит, если номер открыт.
ANON_NUMBER_USD = float(os.getenv("ANON_NUMBER_USD", "2500"))
