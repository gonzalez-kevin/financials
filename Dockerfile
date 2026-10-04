# One image for both the dashboard (Cloud Run service) and the daily job (Cloud Run job).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=America/Los_Angeles

WORKDIR /app

COPY pyproject.toml .
RUN python -c "import subprocess, sys, tomllib; deps = tomllib.load(open('pyproject.toml', 'rb'))['project']['dependencies']; subprocess.check_call([sys.executable, '-m', 'pip', 'install', '--no-cache-dir', *deps])"

COPY main.py ./
COPY dashboard ./dashboard
COPY jobs ./jobs

RUN useradd --create-home app
USER app

# Dashboard by default; the daily job overrides the command with `python -m jobs.daily`.
CMD exec gunicorn --bind :${PORT:-8080} --workers 1 --threads 4 --timeout 120 dashboard.app:app
