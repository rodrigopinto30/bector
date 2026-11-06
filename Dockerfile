# The base image is pinned to a Python version and a Debian release.
FROM python:3.11.18-slim-trixie

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

# Every package comes from the lock and is checked against its hashes; nothing is resolved
# or fetched unpinned. wheel, which the base image brings and nothing uses, is removed, so
# what is installed is exactly what is locked and audited. The project itself is then
# installed without its dependencies.
COPY requirements.lock ./
RUN pip install --require-hashes --no-deps -r requirements.lock \
    && pip uninstall --yes wheel

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-deps --no-build-isolation -e .

COPY . .

USER healer

CMD ["tail", "-f", "/dev/null"]
