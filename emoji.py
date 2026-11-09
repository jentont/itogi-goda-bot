"""Свой пак премиум-эмодзи с уточками и прослойка, которая их расставляет.

Пак собирает сам бот командой /makepack из TGS-файлов в assets/tgs.
Каждой картинке назначен обычный эмодзи. Дальше всё автоматически:

* в тексте сообщений обычный эмодзи из пака превращается в
  `<tg-emoji emoji-id="…">` — анимированную уточку;
* у кнопки эмодзи в начале подписи уезжает в icon_custom_emoji_id —
  премиум-иконку перед текстом.

Telegram пускает кастомные эмодзи только у ботов с купленным на Fragment
юзернеймом. Если бот такого не имеет, первый же отказ выключает разметку,
и сообщение уходит заново с обычными эмодзи. Сломаться из-за украшения
бот не должен.
"""

from __future__ import annotations

import logging
import re
from typing import Any

import config

log = logging.getLogger("itogi.emoji")

#: Какая уточка за каким эмодзи. Файл → эмодзи. Порядок = порядок в паке.
PACK_ITEMS: tuple[tuple[str, str], ...] = (
    ("001.tgs", "🦆"),
    ("010.tgs", "✨"),
    ("013.tgs", "📢"),
    ("003.tgs", "✅"),
    ("018.tgs", "🎁"),
    ("029.tgs", "👥"),
    ("039.tgs", "🎉"),
    ("032.tgs", "⚙️"),
    ("017.tgs", "📊"),
    ("005.tgs", "➕"),
    ("026.tgs", "🗑"),
    ("024.tgs", "❤️"),
    ("014.tgs", "🔥"),
    ("028.tgs", "😎"),
    ("036.tgs", "📨"),
    ("022.tgs", "⬅️"),
    ("030.tgs", "🚀"),
    ("025.tgs", "🌙"),
    ("002.tgs", "👋"),
    ("021.tgs", "❄️"),
    ("004.tgs", "😱"),
    ("031.tgs", "📅"),
    ("037.tgs", "😂"),
    ("012.tgs", "🙏"),
    ("006.tgs", "😢"),
    ("009.tgs", "😈"),
    ("033.tgs", "☔"),
    ("040.tgs", "🏖"),
    ("011.tgs", "🧘"),
    ("038.tgs", "😭"),
    ("027.tgs", "👀"),
    ("023.tgs", "🙈"),
    ("035.tgs", "💀"),
    ("034.tgs", "🫂"),
)

#: Белые иконки кнопок в стиле iOS (tools/icons.py) — отдельный пак с
#: needs_repainting: Telegram красит их в цвет текста. На кнопках они главнее уточек.
ICON_ITEMS: tuple[tuple[str, str], ...] = (
    ("back.tgs", "⬅️"), ("plus.tgs", "➕"), ("close.tgs", "✖️"), ("check.tgs", "✅"),
    ("sparkles.tgs", "✨"), ("gift.tgs", "🎁"), ("people.tgs", "👥"), ("megaphone.tgs", "📢"),
    ("trash.tgs", "🗑"), ("refresh.tgs", "🔄"), ("chart.tgs", "📈"), ("chart2.tgs", "📊"),
    ("mail.tgs", "📨"), ("lock.tgs", "🔒"), ("target.tgs", "🎯"), ("rocket.tgs", "🚀"),
    ("key.tgs", "🔑"), ("coin.tgs", "💰"), ("star.tgs", "⭐"),
)

#: Обычный эмодзи → id премиального. Заполняется из пака при старте.
PACK: dict[str, str] = {}
#: То же для белых иконок кнопок.
ICONS: dict[str, str] = {}

TAG = '<tg-emoji emoji-id="{id}">{emoji}</tg-emoji>'
UNTAG = re.compile(r'<tg-emoji emoji-id="\d+">(.*?)</tg-emoji>', re.S)

_allowed = True
#: Цветные кнопки (style). Выключается, только если Telegram их не принимает.
_styles = True
_pattern: re.Pattern | None = None
_loaded = "пак ещё не читали"


def _forms(plain: str) -> set[str]:
    base = plain.replace("️", "")
    return {plain, base, base + "️"} if len(base) == 1 else {plain, base}


def on() -> bool:
    # Хватит любого пака: уточек (свой) или иконок (готовый, например tgiosicons).
    return _allowed and bool(PACK or ICONS) and config.PREMIUM_EMOJI_ON


def status() -> str:
    state = "включены" if on() else ("выключены Telegram'ом" if not _allowed else "выключены")
    return f"{_loaded}\nпремиум-эмодзи: {state}"


def pack_name(bot_username: str) -> str:
    return f"itogi_ducks_by_{bot_username}"


def icons_name(bot_username: str) -> str:
    return f"itogi_icons_by_{bot_username}"


async def load_icons(bot: Any, names: list[str] | str) -> int:
    """Подключить готовые паки иконок для кнопок (например tgiosicons).

    Свой пак не нужен: бот ставит на кнопку custom emoji из любого
    публичного пака — по эмодзи, к которому привязана иконка. Первый пак в
    списке главнее. Вернёт, сколько разных эмодзи покрыто.
    """
    if isinstance(names, str):
        names = [names]
    found: dict[str, str] = {}
    for name in names:
        try:
            got = await bot.get_sticker_set(name)
        except Exception as error:
            log.info("пак иконок «%s» не прочитать: %s", name, error)
            continue
        for one in got.stickers or []:
            if one.custom_emoji_id and one.emoji:
                for form in _forms(one.emoji):
                    found.setdefault(form, one.custom_emoji_id)
        log.info("пак иконок «%s»: %s шт.", name, len(got.stickers or []))
    global _pattern
    ICONS.clear()
    ICONS.update(found)
    _pattern = None
    return len(found)


async def icon_packs() -> list[str]:
    """Какие паки иконок подключены: из админки, иначе из .env (ICON_PACKS)."""
    import config
    import db

    raw = await db.get_setting("icon_packs")
    if raw:
        return [x for x in raw.split(",") if x]
    return list(config.ICON_PACKS)


async def load(bot: Any, name: str) -> str:
    """Прочитать пак у Telegram и запомнить id эмодзи."""
    global _loaded, _pattern
    try:
        got = await bot.get_sticker_set(name)
    except Exception as error:
        _loaded = f"пак «{name}» не прочитать: {error}"
        log.info(_loaded)
        return _loaded
    PACK.clear()
    for one in got.stickers or []:
        if one.custom_emoji_id and one.emoji:
            for form in _forms(one.emoji):
                PACK.setdefault(form, one.custom_emoji_id)
    _pattern = None
    _loaded = f"пак «{name}»: {len(got.stickers or [])} уточек"
    log.info(_loaded)
    return _loaded


def icon(plain: str) -> str | None:
    """id премиум-эмодзи для кнопки или None."""
    return PACK.get(plain) if on() else None


def _all() -> dict[str, str]:
    """Эмодзи для текста: уточки главнее, иконки из готовых паков — где уточки нет."""
    return {**ICONS, **PACK}


def _regex() -> re.Pattern | None:
    global _pattern
    if _pattern is None and (PACK or ICONS):
        keys = sorted(_all(), key=len, reverse=True)
        _pattern = re.compile("|".join(re.escape(k) for k in keys))
    return _pattern


def dress(text: str | None) -> str | None:
    if not text or not on() or "<tg-emoji" in text:
        return text
    rx = _regex()
    if rx is None:
        return text
    table = _all()
    return rx.sub(lambda m: TAG.format(id=table[m.group(0)], emoji=m.group(0)), text)


def strip(text: str | None) -> str | None:
    return UNTAG.sub(r"\1", text) if text else text


def _holders(method: Any) -> list[Any]:
    """Где в запросе лежит текст: сам метод, медиа или инлайн-результаты."""
    out = [method]
    for name in ("results", "media"):
        value = getattr(method, name, None)
        if isinstance(value, list):
            out.extend(value)
        elif value is not None:
            out.append(value)
    nested = []
    for holder in out:
        content = getattr(holder, "input_message_content", None) or getattr(holder, "result", None)
        if content is not None:
            nested.append(content)
            inner = getattr(content, "input_message_content", None)
            if inner is not None:
                nested.append(inner)
    return out + nested


def _rewrite(method: Any, change) -> bool:
    touched = False
    for holder in _holders(method):
        for field in ("text", "caption", "message_text"):
            value = getattr(holder, field, None)
            if isinstance(value, str):
                new = change(value)
                if new != value:
                    try:
                        object.__setattr__(holder, field, new)
                        touched = True
                    except Exception:
                        pass
    return touched


def _buttons(method: Any, all_methods: bool = False) -> list[Any]:
    # У клавиатур инлайн-результатов Telegram иконки молча выбрасывает —
    # такие кнопки оставляем с обычным эмодзи в подписи.
    if not all_methods and type(method).__name__ in ("AnswerInlineQuery", "SavePreparedInlineMessage"):
        return []
    markup = getattr(method, "reply_markup", None)
    rows = getattr(markup, "inline_keyboard", None) or []
    return [b for row in rows for b in row]


def _set(obj: Any, field: str, value: Any) -> None:
    try:
        object.__setattr__(obj, field, value)
    except Exception:
        pass


def dress_buttons(method: Any) -> None:
    for button in _buttons(method):
        text = getattr(button, "text", None)
        if not isinstance(text, str) or getattr(button, "icon_custom_emoji_id", None):
            continue
        # Белые иконки iOS — первыми, уточки — если иконки под этот эмодзи нет.
        for source in (ICONS, PACK):
            done = False
            for plain in sorted(source, key=len, reverse=True):
                if text.startswith(plain):
                    rest = text[len(plain):].lstrip()
                    if rest:
                        _set(button, "text", rest)
                        _set(button, "icon_custom_emoji_id", source[plain])
                    done = True
                    break
            if done:
                break


def strip_styles(method: Any) -> bool:
    """Снять цвет с кнопок — только если Telegram ругается именно на него."""
    touched = False
    for button in _buttons(method, all_methods=True):
        if getattr(button, "style", None):
            _set(button, "style", None)
            touched = True
    return touched


def strip_buttons(method: Any) -> bool:
    """Снять премиум-иконки с кнопок. Цвет (синяя/зелёная) при этом остаётся."""
    back = {v: k for k, v in {**ICONS, **PACK}.items()}
    touched = False
    for button in _buttons(method):
        icon_id = getattr(button, "icon_custom_emoji_id", None)
        if icon_id:
            plain = back.get(icon_id, "")
            _set(button, "text", f"{plain} {button.text}".strip())
            _set(button, "icon_custom_emoji_id", None)
            touched = True
    return touched


def refused(error: Exception) -> bool:
    text = str(error).lower()
    return any(k in text for k in ("custom emoji", "custom_emoji", "emoji_id", "tg-emoji", "document_invalid", "button_style"))


class Premium:
    """Прослойка на сессии бота: одевает эмодзи, на отказе раздевает."""

    async def __call__(self, make_request, bot, method):
        global _allowed, _styles
        if on():
            _rewrite(method, lambda t: dress(t) or t)
            dress_buttons(method)
        if not _styles:
            strip_styles(method)
        for _ in range(3):
            try:
                return await make_request(bot, method)
            except Exception as error:
                if not refused(error):
                    raise
                text = str(error).lower()
                if "style" in text:
                    # Старый клиент/API не знает цветных кнопок — снимаем только цвет.
                    if not strip_styles(method):
                        raise
                    _styles = False
                    log.warning("Telegram не принимает цвет кнопок (%s) — выключаю", error)
                    continue
                changed = _rewrite(method, lambda t: strip(t) or t)
                changed = strip_buttons(method) or changed
                if not changed:
                    raise
                _allowed = False
                log.warning("Telegram не пускает премиум-эмодзи (%s) — выключаю до перезапуска", error)
        return await make_request(bot, method)


def register(bot: Any) -> None:
    bot.session.middleware(Premium())
