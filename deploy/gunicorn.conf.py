# Конфиг Gunicorn. Запуск из корня репозитория:
#   gunicorn -c deploy/gunicorn.conf.py
wsgi_app = "deploy.app:application"
bind = "127.0.0.1:8000"   # наружу смотрит только Nginx
workers = 2
accesslog = "-"
errorlog = "-"
