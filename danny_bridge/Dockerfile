# App de Home Assistant: Danny Bridge.
# El código se descarga del tag v<versión> del repositorio, así la versión de la app
# (config.yaml) y el código instalado siempre coinciden.
FROM ghcr.io/home-assistant/base-python:3.13-alpine3.24-2026.10.0

ARG BUILD_VERSION
ARG BUILD_ARCH
ARG DANNY_REPO=https://github.com/NaturalDevCR/m5-stack-assist

LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}"

ENV PYTHONUNBUFFERED=1 \
    DANNY_GREETINGS_FILE=/opt/danny/personas/danny/saludos.txt

RUN set -eux; \
    mkdir -p /opt/danny; \
    curl -fsSL "${DANNY_REPO}/archive/refs/tags/v${BUILD_VERSION}.tar.gz" \
        | tar -xz --strip-components=1 -C /opt/danny; \
    pip install --no-cache-dir -r /opt/danny/bridge/requirements.txt; \
    pip install --no-cache-dir --no-deps /opt/danny/bridge; \
    rm -rf /opt/danny/tools /opt/danny/docs /opt/danny/install

COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
