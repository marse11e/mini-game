"""WSGI-приложение Fruit Merge.

Отдаёт игру (index.html, иконки, манифест) и API. Данные хранятся
в JSON-файлах в каталоге deploy/data/ (меняется переменной FRUIT_MERGE_DATA):

scores.json — общий рейтинг, лучший результат игрока на каждой сложности:
    {
      "easy":   {"марсель": {"name": "Марсель", "score": 1840, "date": "2026-11-13T22:40:00+00:00"}},
      "medium": {},
      "hard":   {}
    }

    Там же раздел "daily" — таблицы испытания дня, отдельная на каждую дату:
      "daily": {"2026-11-14": {"марсель": {...}}}

games.json — итоги партий для статистики (последние 5000):
    {"games": [{"date": "...", "deviceId": "...", "name": "Марсель", "difficulty": "easy",
                "score": 1840, "duration": 312, "drops": 96, "merges": 71, "maxLevel": 8,
                "maxCombo": 4, "missions": 3, "revived": true}]}
    Сводку по этим данным печатает deploy/stats.py.

players.json — кто играет, по устройствам:
    {
      "<deviceId>": {
        "deviceId": "...", "names": ["Марсель"], "lastName": "Марсель",
        "device": {"os": "iOS", "browser": "Safari", "screen": "390x844", ...},
        "settings": {"music": 0.5, "sfx": 0.8, "vibration": true},
        "difficulty": "easy", "gamesPlayed": 12, "xp": 640, "streak": 3,
        "best": {"easy": 1840, ...},
        "firstSeen": "...", "lastSeen": "...", "visits": 7
      }
    }

API:
    GET  /api/leaderboard?difficulty=easy   -> {"difficulty": "easy", "scores": [{"name", "score"}, ...]}
         (для испытания дня: difficulty=daily&day=2026-11-14)
    POST /api/scores   {"name", "difficulty", "score"[, "day"]}  -> {"rank", "best", "record", "gap"}
         gap — сколько очков не хватает до следующего места
    POST /api/games    {итог партии}                             -> {"ok": true}
    POST /api/players  {"name", "device", "settings", ...}       -> {"ok": true}
    GET  /healthz                                                -> ok

CORS: если игра открыта с другого сайта (например, GitHub Pages), разрешённые
источники задаются переменной FRUIT_MERGE_CORS (по умолчанию «*»).
"""
import fcntl
import json
import mimetypes
import os
import re
import tempfile
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get("FRUIT_MERGE_DATA", Path(__file__).resolve().parent / "data"))
CORS_ORIGINS = [o.strip() for o in os.environ.get("FRUIT_MERGE_CORS", "*").split(",") if o.strip()]

DIFFICULTIES = ("easy", "medium", "hard")
BOARDS = DIFFICULTIES + ("daily",)   # таблицы рейтинга: три сложности и испытание дня
DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DAILY_KEEP_DAYS = 7
MAX_GAMES = 5000
NAME_RE = re.compile(r"^[\w .-]{2,16}$")  # \w в Python 3 — буквы любых алфавитов, цифры и _
DEVICE_ID_RE = re.compile(r"^[\w-]{8,64}$")
MAX_SCORE = 10_000_000
TOP_LIMIT = 10
MAX_BODY = 8192

# Файлы, которые сервер отдаёт как статику
STATIC_FILES = {"/manifest.webmanifest"}
STATIC_DIRS = ("/icons/",)
mimetypes.add_type("application/manifest+json", ".webmanifest")
mimetypes.add_type("image/svg+xml", ".svg")


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JsonStore:
    """JSON-файл с атомарной записью. Доступ из нескольких воркеров
    Gunicorn синхронизируется блокировкой отдельного lock-файла."""

    def __init__(self, path: Path):
        self.path = path
        self.lock_path = path.with_suffix(".lock")

    @contextmanager
    def locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "w") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def read(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def write(self, data: dict) -> None:
        # Пишем во временный файл и подменяем — файл никогда не останется битым
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix="." + self.path.stem + "-", suffix=".json")
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)


class ScoreDB(JsonStore):
    """Общий рейтинг: лучший результат каждого игрока в каждой таблице."""

    def _table(self, data: dict, board: str, day: str) -> dict:
        """Возвращает (создавая при необходимости) нужную таблицу внутри data."""
        if board != "daily":
            if not isinstance(data.get(board), dict):
                data[board] = {}
            return data[board]
        daily = data.setdefault("daily", {})
        if not isinstance(daily, dict):
            daily = data["daily"] = {}
        # Старые дни не храним
        oldest = (date.today() - timedelta(days=DAILY_KEEP_DAYS)).isoformat()
        for old in [d for d in daily if d < oldest]:
            del daily[old]
        return daily.setdefault(day, {})

    @staticmethod
    def _ranked(table: dict) -> list:
        return sorted(table.values(), key=lambda e: (-e["score"], e["date"]))

    def top(self, board: str, day: str = "", limit: int = TOP_LIMIT) -> list:
        with self.locked():
            table = self._table(self.read(), board, day)
        return [{"name": e["name"], "score": e["score"]} for e in self._ranked(table)[:limit]]

    def submit(self, name: str, board: str, score: int, day: str = "") -> dict:
        key = name.casefold()
        with self.locked():
            data = self.read()
            table = self._table(data, board, day)
            entry = table.get(key)
            record = entry is None or score > entry["score"]
            if record:
                table[key] = {"name": name, "score": score, "date": now_iso()}
                self.write(data)
            ranked = self._ranked(table)
        rank = next(i for i, e in enumerate(ranked, 1) if e["name"].casefold() == key)
        best = table[key]["score"]
        # Сколько очков не хватает, чтобы обойти игрока на место выше
        gap = ranked[rank - 2]["score"] - best + 1 if rank > 1 else 0
        return {"rank": rank, "best": best, "record": record, "gap": gap}


class GamesDB(JsonStore):
    """Журнал партий для статистики: длительность, счёт, чем закончилась."""

    INT_FIELDS = ("score", "duration", "drops", "merges", "maxLevel", "maxCombo", "missions")

    def add(self, payload: dict):
        name = " ".join(str(payload.get("name", "")).split())
        device_id = payload.get("deviceId")
        if not NAME_RE.match(name):
            return "invalid name"
        if not isinstance(device_id, str) or not DEVICE_ID_RE.match(device_id):
            return "invalid deviceId"
        if payload.get("difficulty") not in BOARDS:
            return "invalid difficulty"
        entry = {"date": now_iso(), "deviceId": device_id, "name": name, "difficulty": payload["difficulty"]}
        for key in self.INT_FIELDS:
            value = payload.get(key)
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= MAX_SCORE:
                entry[key] = value
        entry["revived"] = payload.get("revived") is True
        with self.locked():
            data = self.read()
            games = data.get("games") if isinstance(data.get("games"), list) else []
            games.append(entry)
            data["games"] = games[-MAX_GAMES:]
            self.write(data)
        return None


class PlayersDB(JsonStore):
    """Сведения об игроках по устройствам: имена, устройство, настройки звука."""

    DEVICE_KEYS = {
        "os": str, "browser": str, "userAgent": str, "language": str, "timezone": str,
        "screen": str, "pixelRatio": (int, float), "touch": bool, "standalone": bool,
    }

    @classmethod
    def clean_device(cls, raw) -> dict:
        if not isinstance(raw, dict):
            return {}
        out = {}
        for key, kind in cls.DEVICE_KEYS.items():
            value = raw.get(key)
            if isinstance(value, kind) and not (kind is not bool and isinstance(value, bool)):
                out[key] = value[:300] if isinstance(value, str) else value
        return out

    @staticmethod
    def clean_settings(raw) -> dict:
        if not isinstance(raw, dict):
            return {}
        out = {}
        for key in ("music", "sfx"):
            value = raw.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out[key] = round(min(1.0, max(0.0, float(value))), 2)
        if isinstance(raw.get("vibration"), bool):
            out["vibration"] = raw["vibration"]
        return out

    @staticmethod
    def clean_best(raw) -> dict:
        if not isinstance(raw, dict):
            return {}
        return {d: raw[d] for d in BOARDS
                if isinstance(raw.get(d), int) and not isinstance(raw.get(d), bool) and 0 <= raw[d] <= MAX_SCORE}

    def upsert(self, payload: dict):
        """Возвращает None при успехе или текст ошибки."""
        name = " ".join(str(payload.get("name", "")).split())
        device = self.clean_device(payload.get("device"))
        device_id = (payload.get("device") or {}).get("deviceId") if isinstance(payload.get("device"), dict) else None
        if not NAME_RE.match(name):
            return "invalid name"
        if not isinstance(device_id, str) or not DEVICE_ID_RE.match(device_id):
            return "invalid deviceId"
        with self.locked():
            data = self.read()
            entry = data.get(device_id) or {"deviceId": device_id, "names": [], "firstSeen": now_iso(), "visits": 0}
            if name not in entry["names"]:
                entry["names"] = (entry["names"] + [name])[-20:]
            entry["lastName"] = name
            # Пустые поля не затирают уже сохранённые данные
            if device:
                entry["device"] = device
            settings = self.clean_settings(payload.get("settings"))
            if settings:
                entry["settings"] = {**entry.get("settings", {}), **settings}
            if payload.get("difficulty") in BOARDS:
                entry["difficulty"] = payload["difficulty"]
            # Счётчики прогресса: число партий, опыт, серия дней
            for key in ("gamesPlayed", "xp", "streak"):
                value = payload.get(key)
                if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 10**9:
                    entry[key] = value
            best = self.clean_best(payload.get("best"))
            if best:
                entry["best"] = {**entry.get("best", {}), **best}
            entry["visits"] = entry.get("visits", 0) + 1
            entry["lastSeen"] = now_iso()
            data[device_id] = entry
            self.write(data)
        return None


scores = ScoreDB(DATA_DIR / "scores.json")
players = PlayersDB(DATA_DIR / "players.json")
games = GamesDB(DATA_DIR / "games.json")


def cors_headers(environ):
    origin = environ.get("HTTP_ORIGIN")
    if not origin:
        return []
    if "*" in CORS_ORIGINS:
        allowed = "*"
    elif origin in CORS_ORIGINS:
        allowed = origin
    else:
        return []
    return [
        ("Access-Control-Allow-Origin", allowed),
        ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
        ("Access-Control-Allow-Headers", "Content-Type"),
        ("Access-Control-Max-Age", "86400"),
        ("Vary", "Origin"),
    ]


def respond(start_response, status, body, content_type="text/plain; charset=utf-8", extra=()):
    if isinstance(body, str):
        body = body.encode("utf-8")
    headers = [("Content-Type", content_type), ("Content-Length", str(len(body)))]
    headers.extend(extra)
    start_response(status, headers)
    return [body]


def respond_json(start_response, status, payload, environ):
    body = json.dumps(payload, ensure_ascii=False)
    extra = [("Cache-Control", "no-store")] + cors_headers(environ)
    return respond(start_response, status, body, "application/json; charset=utf-8", extra)


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


def valid_day(day) -> bool:
    """Дата испытания: формат ГГГГ-ММ-ДД и не дальше суток от серверной (часовые пояса)."""
    if not isinstance(day, str) or not DAY_RE.match(day):
        return False
    try:
        return abs((date.fromisoformat(day) - datetime.now(timezone.utc).date()).days) <= 1
    except ValueError:
        return False


def validate_score(payload):
    """Возвращает (name, board, score, day) или строку с ошибкой."""
    if not isinstance(payload, dict):
        return "invalid JSON body"
    name = " ".join(str(payload.get("name", "")).split())
    board = payload.get("difficulty")
    score = payload.get("score")
    day = payload.get("day", "")
    if not NAME_RE.match(name):
        return "invalid name"
    if board not in BOARDS:
        return "invalid difficulty"
    if board == "daily" and not valid_day(day):
        return "invalid day"
    if not isinstance(score, int) or isinstance(score, bool) or not 0 < score <= MAX_SCORE:
        return "invalid score"
    return name, board, score, day if board == "daily" else ""


def static_file(path):
    """Путь к разрешённому статическому файлу или None."""
    file = (ROOT / path.lstrip("/")).resolve()
    if path in STATIC_FILES:
        allowed = file.parent == ROOT
    else:
        # Файл обязан лежать внутри разрешённого каталога — никаких «../»
        allowed = any(path.startswith(d) and (ROOT / d.strip("/")).resolve() in file.parents for d in STATIC_DIRS)
    return file if allowed and file.is_file() else None


def application(environ, start_response):
    path = environ.get("PATH_INFO", "/")
    method = environ.get("REQUEST_METHOD", "GET")

    if path == "/healthz":
        return respond(start_response, "200 OK", "ok")

    if path.startswith("/api/"):
        if method == "OPTIONS":  # preflight-запрос CORS
            return respond(start_response, "204 No Content", b"", extra=cors_headers(environ))

        if path == "/api/leaderboard" and method == "GET":
            query = parse_qs(environ.get("QUERY_STRING", ""))
            board = query.get("difficulty", ["easy"])[0]
            day = query.get("day", [""])[0]
            if board not in BOARDS:
                return respond_json(start_response, "400 Bad Request", {"error": "invalid difficulty"}, environ)
            if board == "daily" and not valid_day(day):
                return respond_json(start_response, "400 Bad Request", {"error": "invalid day"}, environ)
            return respond_json(start_response, "200 OK",
                                {"difficulty": board, "scores": scores.top(board, day)}, environ)

        if path == "/api/scores" and method == "POST":
            result = validate_score(read_json(environ))
            if isinstance(result, str):
                return respond_json(start_response, "400 Bad Request", {"error": result}, environ)
            return respond_json(start_response, "200 OK", scores.submit(*result), environ)

        if path == "/api/games" and method == "POST":
            payload = read_json(environ)
            error = games.add(payload) if isinstance(payload, dict) else "invalid JSON body"
            if error:
                return respond_json(start_response, "400 Bad Request", {"error": error}, environ)
            return respond_json(start_response, "200 OK", {"ok": True}, environ)

        if path == "/api/players" and method == "POST":
            payload = read_json(environ)
            error = players.upsert(payload) if isinstance(payload, dict) else "invalid JSON body"
            if error:
                return respond_json(start_response, "400 Bad Request", {"error": error}, environ)
            return respond_json(start_response, "200 OK", {"ok": True}, environ)

        return respond_json(start_response, "404 Not Found", {"error": "not found"}, environ)

    if method not in ("GET", "HEAD"):
        return respond(start_response, "405 Method Not Allowed", "Method not allowed")

    if path in ("/", "/index.html"):
        # Файл читается на каждый запрос — правки index.html подхватываются без рестарта
        return respond(start_response, "200 OK", (ROOT / "index.html").read_bytes(),
                       "text/html; charset=utf-8", [("Cache-Control", "no-cache")])

    file = static_file(path)
    if file:
        ctype = mimetypes.guess_type(file.name)[0] or "application/octet-stream"
        return respond(start_response, "200 OK", file.read_bytes(), ctype, [("Cache-Control", "public, max-age=86400")])

    return respond(start_response, "404 Not Found", "Not found")
