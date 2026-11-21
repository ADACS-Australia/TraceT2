FROM docker.io/library/debian:trixie

RUN apt update && apt install -y --no-install-recommends \
  ca-certificates \
  curl \
  && rm -rf /var/lib/apt/lists/*

# Install UV Python package manager
ADD https://astral.sh/uv/0.9.28/install.sh /uv-installer.sh
RUN sh /uv-installer.sh && rm /uv-installer.sh
ENV PATH="/root/.local/bin/:$PATH"
ENV UV_NO_DEV=1

RUN mkdir /tracet
WORKDIR /tracet

# Install dependencies first (layer caching)
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-install-project

# Copy application source
COPY . .
RUN uv sync --locked

# Collect static files (dummy key for build only)
RUN mkdir -p /tracet/logs && DJANGO_SECRET_KEY=build-placeholder uv run python manage.py collectstatic --noinput

COPY deploy/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

EXPOSE 8000
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uv", "run", "gunicorn", "--bind", "0.0.0.0:8000", "project.wsgi"]
