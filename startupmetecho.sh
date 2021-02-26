    python -m daphne --bind 0.0.0.0 --port ${PORT:-8000} metecho.asgi:application
    python manage.py rqworker default
    honcho start -f Procfile_worker_short