yarn prod
python manage.py collectstatic
python manage.py migrate --noinput
python manage.py rqworker default
honcho start -f Procfile_worker_short
python manage.py promote_superuser georgeslater
