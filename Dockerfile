FROM python:3.11-slim

# The containers run as this user, with the uid and gid of the developer on the host, so
# what they write to the mounted project belongs to the developer. start.sh sets them.
ARG HOST_UID=1000
ARG HOST_GID=1000

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/home/healer

RUN apt-get update \
    && apt-get install -y --no-install-recommends git build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --non-unique --gid "$HOST_GID" healer \
    && useradd --non-unique --uid "$HOST_UID" --gid "$HOST_GID" --create-home \
        --home-dir /home/healer --shell /usr/sbin/nologin healer \
    && mkdir -p /data/chroma /data/healer /home/healer/.cache/chroma \
    && chown -R healer:healer /data /home/healer

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install -e ".[dev]"

COPY . .

USER healer

CMD ["tail", "-f", "/dev/null"]
