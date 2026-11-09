"""Проверка без Telegram: подпись initData, сбор итогов на фейковом боте, API мини-аппа."""
import asyncio, hashlib, hmac, json, os, sys, tempfile, time, pathlib
from urllib.parse import urlencode
from datetime import datetime, timezone
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
tmp = tempfile.mkdtemp()
os.environ.update(BOT_TOKEN="123:TEST", DB_PATH=f"{tmp}/t.db", CARDS_DIR=f"{tmp}/cards", PUBLIC_URL="https://example.com")
import config, db, web as webapp, share, facts
from types import SimpleNamespace as NS
from aiogram.types import UserRating

def sign(user, start_param=None):
    data = {"auth_date": str(int(time.time())), "query_id": "q", "user": json.dumps(user)}
    if start_param: data["start_param"] = start_param
    check = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))
    secret = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    data["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(data)

class FakeBot:
    id = 1
    sent = []
    async def get_user_gifts(self, user_id, offset=None, limit=100):
        g = lambda y, m, stars: NS(type="regular", send_date=datetime(y, m, 5, tzinfo=timezone.utc), gift=NS(star_count=stars, sticker=None))
        return NS(total_count=5, next_offset=None, gifts=[g(2025, 3, 50), g(2025, 9, 15), g(2026, 2, 25), g(2026, 2, 100), g(2026, 7, 15)])
    async def get_chat(self, user_id):
        return NS(rating=UserRating(level=7, rating=1840, current_level_rating=1500, next_level_rating=2500), birthdate=NS(day=13, month=9))
    async def get_chat_member(self, chat_id, user_id):
        return NS(status="left" if user_id == 777 else "member")
    async def send_message(self, *a, **k):
        self.sent.append(a)

async def main():
    await db.open_db()
    bot = FakeBot()
    share.set_username("itogi_test_bot")
    assert webapp.check_init(sign({"id": 5, "first_name": "Ваня"})), "подпись не прошла"
    assert webapp.check_init(sign({"id": 5}) + "x") is None, "битая подпись прошла"
    await db.add_sponsor(-1001, "Канал", "chan", "", 0)
    from aiohttp.test_utils import TestClient, TestServer
    app = webapp.make_app(bot)
    async with TestClient(TestServer(app)) as c:
        # Без /start в боте мини-апп не пускает
        r = await c.post("/api/start", json={"init": sign({"id": 5, "first_name": "Ваня"})})
        j = await r.json(); assert j["state"] == "start", j
        # «Нажали /start» — все, кто дальше пользуется ботом
        NS2 = __import__("types").SimpleNamespace
        for uid in (5, 777):
            await db.touch_user(NS2(id=uid, username=None, first_name="T", language_code="ru", is_premium=False))
        await db.touch_user(NS2(id=888, username=None, first_name="Друг", language_code="ru", is_premium=False), 5)
        # Пригласивший
        r = await c.post("/api/start", json={"init": sign({"id": 5, "first_name": "Ваня", "is_premium": True})})
        j = await r.json(); assert j["state"] == "ready", j
        d = j["data"]
        print("age:", d["age"]["days"], d["age"]["since"], "| gifts:", d["gifts"], "| rating:", d["rating"])
        assert d["gifts"]["now"] == 5 and d["gifts"]["before"] == 2 and d["gifts"]["year"] == 3
        assert d["gifts"]["best_month"]["name"] == "феврале"
        assert d["rating"]["level"] == 7 and d["rating"]["left"] == 660
        # Не подписан
        r = await c.post("/api/start", json={"init": sign({"id": 777, "first_name": "Нет"}, "r5")})
        j = await r.json(); assert j["state"] == "subscribe", j; print("gate:", j["sponsors"])
        # Подписанный друг по ссылке
        r = await c.post("/api/start", json={"init": sign({"id": 888, "first_name": "Друг"}, "r5")})
        j = await r.json(); assert j["state"] == "ready"
        assert await db.invited_count(5) == 1 and bot.sent, "реферал не засчитан"
        r = await c.post("/api/start", json={"init": "user=%7B%7D&hash=00"}); assert r.status == 401
        r = await c.get("/"); assert r.status == 200
        r = await c.get("/app/stickers/031.json"); assert r.status == 200
        # Карточка для истории
        r = await c.post("/api/story", json={"init": sign({"id": 5, "first_name": "Ваня"})})
        j = await r.json(); print("story:", j); assert j["url"].startswith("https://example.com/cards/")
        name = j["url"].rsplit("/", 1)[1]
        r = await c.get(f"/cards/{name}"); assert r.status == 200 and len(await r.read()) > 50000
        r = await c.get("/dl/" + name); assert r.status == 200 and "attachment" in r.headers.get("Content-Disposition", "")
        # План на следующий год: чужие ключи отбрасываются, максимум 3
        r = await c.post("/api/plan", json={"init": sign({"id": 5, "first_name": "Ваня"}),
                                           "goals": ["travel", "hack", "love", "love", "sport", "read"]})
        j = await r.json(); print("plan:", j); assert j["goals"] == ["travel", "love", "sport"]
        r = await c.post("/api/start", json={"init": sign({"id": 5, "first_name": "Ваня"})})
        p = (await r.json())["data"]["plan"]; assert p["chosen"] == ["travel", "love", "sport"] and len(p["catalog"]) == 12
        assert d["age"]["earlier"] >= 90 and d["age"]["users_now"] > 1000, d["age"]
    print("caption:\n" + share.caption(d))
    await db.close_db()
    print("OK")

asyncio.run(main())
