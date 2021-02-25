yarn prod
python manage.py collectstatic
python manage.py migrate --noinput
python manage.py promote_superuser georgeslater