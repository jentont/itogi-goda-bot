"""Тестовые итоги без Telegram — для превью карточек и мини-аппа."""
import facts

def demo(user_id=8781141428, name="Ваня"):
    return {
        "year": 2026,
        "user": {"id": user_id, "name": name, "username": "vanya", "photo": None},
        "age": facts.age(user_id),
        "premium": True,
        "gifts": {"now": 17, "before": 3, "year": 14, "stars_year": 1250, "nft": 2,
                  "top": {"title": "Plush Pepe #1204", "nft": True, "stars": 0, "sticker": None,
                          "colors": ["#5b8def", "#2f4fb5"], "date": "14 февраля 2026"},
                  "best_month": {"name": "феврале", "count": 6}},
        "rating": {"level": 7, "points": 1840, "next": 2500, "left": 660, "progress": 0.62},
        "birthday": "13 сентября",
        "rank": 4812, "invited": 3,
        "nominations": facts.nominations(user_id),
        "prediction": facts.prediction(user_id),
        "at": 0,
    }
