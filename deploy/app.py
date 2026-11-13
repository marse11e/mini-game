"""WSGI-приложение Fruit Merge.

Отдаёт index.html и простой API общего рейтинга. Рейтинг хранится
в JSON-файле (по умолчанию deploy/data/scores.json):

    {
      "easy":   {"марсель": {"name": "Марсель", "score": 1840, "date": "2026-11-13T22:40:00+00:00"}},
      "medium": {},
      "hard":   {}
    }

Для каждого игрока на каждой сложности хранится только лучший результат.
Имена сравниваются без учёта регистра.

API:
    GET  /api/leaderboard?difficulty=easy   -> {"difficulty": "easy", "scores": [{"name", "score"}, ...]}
    POST /api/scores  {"name", "difficulty", "score"}  -> {"rank", "best", "record"}
    GET  /healthz                            -> ok
"""
import fcntl
import json
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "index.html"
DB_PATH = Path(os.environ.get("FRUIT_MERGE_DB", Path(__file__).resolve().parent / "data" / "scores.json"))

DIFFICULTIES = ("easy", "medium", "hard")
NAME_RE = re.compile(r"^[\w .-]{2,16}$")  # \w в Python 3 — буквы любых алфавитов, цифры и _
MAX_SCORE = 10_000_000
TOP_LIMIT = 10
MAX_BODY = 4096


class ScoreDB:
    """Рейтинг в JSON-файле. Запись атомарная, доступ из нескольких
    воркеров Gunicorn синхронизируется блокировкой файла."""

    def __init__(self, path: Path):
        self.path = path
        self.lock_path = path.with_suffix(".lock")

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            data = {}
        for d in DIFFICULTIES:
            if not isinstance(data.get(d), dict):
                data[d] = {}
        return data

    def _write(self, data: dict) -> None:
        # Пишем во временный файл и подменяем — файл никогда не останется битым
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".scores-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    @staticmethod
    def _ranked(table: dict) -> list:
        return sorted(table.values(), key=lambda e: (-e["score"], e["date"]))

    def top(self, difficulty: str, limit: int = TOP_LIMIT) -> list:
        with self._locked():
            table = self._read()[difficulty]
        return [{"name": e["name"], "score": e["score"]} for e in self._ranked(table)[:limit]]

    def submit(self, name: str, difficulty: str, score: int) -> dict:
        key = name.casefold()
        with self._locked():
            data = self._read()
            table = data[difficulty]
            entry = table.get(key)
            record = entry is None or score > entry["score"]
            if record:
                table[key] = {
                    "name": name,
                    "score": score,
                    "date": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
                self._write(data)
            ranked = self._ranked(table)
        rank = next(i for i, e in enumerate(ranked, 1) if e["name"].casefold() == key)
        return {"rank": rank, "best": table[key]["score"], "record": record}


db = ScoreDB(DB_PATH)


def respond(start_response, status, body, content_type="text/plain; charset=utf-8", extra=()):
    if isinstance(body, str):
        body = body.encode("utf-8")
    headers = [("Content-Type", content_type), ("Content-Length", str(len(body)))]
    headers.extend(extra)
    start_response(status, headers)
    return [body]


def respond_json(start_response, status, payload):
    body = json.dumps(payload, ensure_ascii=False)
    return respond(start_response, status, body, "application/json; charset=utf-8", [("Cache-Control", "no-store")])


def read_json(environ):
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        length = 0
    if length <= 0 or length > MAX_BODY:
        return None
    try:
        return json.loads(environ["wsgi.input"].read(length))
    except (ValueError, UnicodeDecodeError):
        return None


def validate_score(payload):
    """Возвращает (name, difficulty, score) или строку с ошибкой."""
    if not isinstance(payload, dict):
        return "invalid JSON body"
    name = " ".join(str(payload.get("name", "")).split())
    difficulty = payload.get("difficulty")
    score = payload.get("score")
    if not NAME_RE.match(name):
        return "invalid name"
    if difficulty not in DIFFICULTIES:
        return "invalid difficulty"
    if not isinstance(score, int) or isinstance(score, bool) or not 0 < score <= MAX_SCORE:
        return "invalid score"
    return name, difficulty, score


def application(environ, start_response):
    path = environ.get("PATH_INFO", "/")
    method = environ.get("REQUEST_METHOD", "GET")

    if path == "/healthz":
        return respond(start_response, "200 OK", "ok")

    if path == "/api/leaderboard" and method == "GET":
        query = parse_qs(environ.get("QUERY_STRING", ""))
        difficulty = query.get("difficulty", ["easy"])[0]
        if difficulty not in DIFFICULTIES:
            return respond_json(start_response, "400 Bad Request", {"error": "invalid difficulty"})
        return respond_json(start_response, "200 OK", {"difficulty": difficulty, "scores": db.top(difficulty)})

    if path == "/api/scores" and method == "POST":
        result = validate_score(read_json(environ))
        if isinstance(result, str):
            return respond_json(start_response, "400 Bad Request", {"error": result})
        return respond_json(start_response, "200 OK", db.submit(*result))

    if path.startswith("/api/"):
        return respond_json(start_response, "404 Not Found", {"error": "not found"})

    if path in ("/", "/index.html") and method in ("GET", "HEAD"):
        # Файл читается на каждый запрос — правки index.html подхватываются без рестарта
        return respond(start_response, "200 OK", INDEX.read_bytes(), "text/html; charset=utf-8",
                       [("Cache-Control", "no-cache")])

    return respond(start_response, "404 Not Found", "Not found")
