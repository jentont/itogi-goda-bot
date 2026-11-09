"""Локальный сервер мини-аппа без бота — посмотреть дизайн: http://127.0.0.1:8099/?demo"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from aiohttp import web
import web as webapp
web.run_app(webapp.make_app(None), host="127.0.0.1", port=int(sys.argv[1]) if len(sys.argv) > 1 else 8099)
