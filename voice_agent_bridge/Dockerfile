# App de Home Assistant: Voice Agent Bridge (terminal de voz ⇄ ElevenLabs Agents ⇄ HA).
# El código se descarga del tag v<versión> del repositorio, así la versión de la app
# (config.yaml) y el código instalado siempre coinciden.
FROM ghcr.io/home-assistant/base-python:3.13-alpine3.24-2026.10.0

ARG BUILD_VERSION
ARG BUILD_ARCH
ARG REPO=https://github.com/NaturalDevCR/m5-stack-assist

LABEL \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.type="app" \
    io.hass.arch="${BUILD_ARCH}"

ENV PYTHONUNBUFFERED=1 \
    BRIDGE_PERSONAS_DIR=/opt/voice-agent-bridge/personas

RUN set -eux; \
    mkdir -p /opt/voice-agent-bridge; \
    curl -fsSL "${REPO}/archive/refs/tags/v${BUILD_VERSION}.tar.gz" \
        | tar -xz --strip-components=1 -C /opt/voice-agent-bridge; \
    pip install --no-cache-dir -r /opt/voice-agent-bridge/bridge/requirements.txt; \
    pip install --no-cache-dir --no-deps /opt/voice-agent-bridge/bridge; \
    rm -rf /opt/voice-agent-bridge/tools /opt/voice-agent-bridge/docs /opt/voice-agent-bridge/install

COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD ["/run.sh"]
