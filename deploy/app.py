"""Минимальное WSGI-приложение для Gunicorn: отдаёт игру и health-check.

Зависимостей нет — только стандартная библиотека.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "index.html"


def application(environ, start_response):
    path = environ.get("PATH_INFO", "/")

    if path == "/healthz":
        body = b"ok"
        start_response("200 OK", [("Content-Type", "text/plain"), ("Content-Length", str(len(body)))])
        return [body]

    if path in ("/", "/index.html"):
        # Читаем файл на каждый запрос, чтобы правки index.html подхватывались без рестарта
        body = INDEX.read_bytes()
        start_response("200 OK", [
            ("Content-Type", "text/html; charset=utf-8"),
            ("Content-Length", str(len(body))),
            ("Cache-Control", "no-cache"),
        ])
        return [body]

    body = b"Not found"
    start_response("404 Not Found", [("Content-Type", "text/plain"), ("Content-Length", str(len(body)))])
    return [body]
