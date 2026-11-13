# Fruit Merge

Мобильная браузерная мини-игра в стиле «слияния фруктов» — один файл `index.html`, физика на [Matter.js](https://brm.io/matter-js/) (подключается с CDN).

## Запуск
Откройте `index.html` в браузере (нужен интернет для загрузки Matter.js и шрифта). Сборка не требуется.

## Управление
- Палец / мышь — двигать фрукт влево-вправо, отпустить — бросить.
- Десктоп: ← / → и пробел.

## Механика
- 11 уровней: черника → малина → клубника → лайм → персик → апельсин → яблоко → гранат → кокос → ананас → арбуз.
- Два одинаковых фрукта сливаются в следующий; очки растут с уровнем (`n·(n+1)`).
- Два арбуза исчезают и дают +1000 и по бустеру.
- Новый фрукт — случайный из первых 5 уровней, задержка между бросками 0.5 с.
- Фрукты выше края стакана дольше 2 секунд — Game Over.
- Бустеры: «Встряска» (трясёт стакан) и «Бомба» (тап по фрукту уничтожает его). +1 к каждому за каждые 1500 очков.
- Рекорд хранится в `localStorage`.

## Деплой (Gunicorn + Nginx)
Схема: браузер → Nginx (порт 80) → Gunicorn (127.0.0.1:8000) → `deploy/app.py` отдаёт `index.html`.

```bash
# на сервере (Ubuntu), репозиторий склонирован в /opt/mini-game
apt-get install -y nginx && pip install gunicorn
cd /opt/mini-game

# Gunicorn как сервис
cp deploy/fruit-merge.service /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now fruit-merge

# Nginx: в deploy/nginx.conf замените server_name _ на свой домен
cp deploy/nginx.conf /etc/nginx/sites-available/fruit-merge
ln -sf /etc/nginx/sites-available/fruit-merge /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default
nginx -t && systemctl reload nginx

curl http://<домен-или-IP>/healthz   # -> ok
```
Для HTTPS: `apt install certbot python3-certbot-nginx && certbot --nginx -d <домен>`.
Вибрация и звук на телефоне работают надёжнее по HTTPS.
