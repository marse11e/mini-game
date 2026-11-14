"""Сводка по игрокам и партиям из JSON-файлов сервера.

Запуск из корня репозитория:
    python3 deploy/stats.py            # читает deploy/data
    python3 deploy/stats.py /путь/к/данным
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean, median


def load(path: Path) -> dict:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def main() -> None:
    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "data"
    players = list(load(data_dir / "players.json").values())
    games = load(data_dir / "games.json").get("games", [])

    print(f"Данные: {data_dir}")
    print(f"\nУстройств (игроков): {len(players)}")
    if players:
        # Вернувшиеся — те, кто заходил в другой день после первого
        returned = [p for p in players if p.get("lastSeen", "")[:10] > p.get("firstSeen", "")[:10]]
        print(f"Вернулись в другой день: {len(returned)} ({len(returned) / len(players):.0%})")
        print("Системы: " + ", ".join(f"{k} — {v}" for k, v in Counter(p.get("device", {}).get("os", "?") for p in players).most_common()))
        print("Браузеры: " + ", ".join(f"{k} — {v}" for k, v in Counter(p.get("device", {}).get("browser", "?") for p in players).most_common()))
        music = [p["settings"]["music"] for p in players if "music" in p.get("settings", {})]
        sfx = [p["settings"]["sfx"] for p in players if "sfx" in p.get("settings", {})]
        if music:
            print(f"Громкость музыки: в среднем {mean(music):.0%}, выключили {sum(v == 0 for v in music)}")
        if sfx:
            print(f"Громкость эффектов: в среднем {mean(sfx):.0%}, выключили {sum(v == 0 for v in sfx)}")
        streaks = [p.get("streak", 0) for p in players]
        print(f"Серия дней: максимум {max(streaks)}, в среднем {mean(streaks):.1f}")

    print(f"\nПартий записано: {len(games)}")
    if not games:
        return
    per_device = Counter(g["deviceId"] for g in games)
    print(f"Партий на игрока: в среднем {mean(per_device.values()):.1f}, медиана {median(per_device.values())}")
    by_diff = defaultdict(list)
    for g in games:
        by_diff[g["difficulty"]].append(g)
    names = {"easy": "Лёгкий", "medium": "Средний", "hard": "Сложный", "daily": "Испытание дня"}
    print("\nПо режимам:")
    for diff, rows in by_diff.items():
        scores = [g.get("score", 0) for g in rows]
        durations = [g.get("duration", 0) for g in rows]
        revived = sum(1 for g in rows if g.get("revived"))
        print(f"  {names.get(diff, diff)}: партий {len(rows)}, счёт средний {mean(scores):.0f} / лучший {max(scores)}, "
              f"длительность средняя {mean(durations) / 60:.1f} мин, второй шанс использован в {revived / len(rows):.0%}")
    reached = Counter(g.get("maxLevel", 0) for g in games)
    fruit = ["черника", "малина", "клубника", "лайм", "персик", "апельсин", "яблоко", "гранат", "кокос", "ананас", "арбуз"]
    print("\nДо какого фрукта доходят:")
    for level in sorted(reached):
        print(f"  {fruit[level] if level < len(fruit) else level}: {reached[level]} ({reached[level] / len(games):.0%})")


if __name__ == "__main__":
    main()
