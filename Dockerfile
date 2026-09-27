# SPDX-FileCopyrightText: 2026 Muckebox contributors
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Plain Dockerfile syntax (no BuildKit features), so that it also builds with
# the Docker Engine of Synology Container Manager.

FROM python:3.13-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data \
    PORT=8484

WORKDIR /app

COPY requirements.txt .
RUN pip install --require-hashes --only-binary=:all: -r requirements.txt

COPY LICENSE ./
COPY muckebox ./muckebox

# Default user; docker-compose.yml usually overrides it with the NAS user
# that owns the data folder.
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin muckebox \
 && mkdir -p /data && chown 10001 /data
USER 10001

EXPOSE 8484
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD ["python", "-m", "muckebox.healthcheck"]

CMD ["python", "-m", "muckebox"]
