# Stage 1: Builder — compile Python deps and npm packages
FROM debian:13.4 AS builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-dev \
    gcc g++ make cmake libffi-dev git curl nodejs npm && \
    ln -sf /usr/bin/make /usr/local/bin/gmake && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY . .

RUN pip install --break-system-packages -e ".[all]"
RUN npm install
RUN cd scripts/whatsapp-bridge && npm install

# Stage 2: Runtime — minimal image with non-root user
FROM debian:13.4

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    python3 python3-pip nodejs npm ripgrep ffmpeg git curl jq && \
    rm -rf /var/lib/apt/lists/* && \
    useradd -m -s /bin/bash -u 1000 hermes

# Copy built application and installed Python packages
COPY --from=builder /build /opt/hermes
COPY --from=builder /usr/local/lib/python3.13/dist-packages /usr/local/lib/python3.13/dist-packages/

WORKDIR /opt/hermes

# Install Playwright browser (needs to be in runtime stage)
RUN npx playwright install --with-deps chromium

RUN chmod +x /opt/hermes/docker/entrypoint.sh && \
    chown -R hermes:hermes /opt/hermes

# Data volume
ENV HERMES_HOME=/opt/data
RUN mkdir -p /opt/data && chown hermes:hermes /opt/data
VOLUME ["/opt/data"]

USER hermes
ENTRYPOINT ["/opt/hermes/docker/entrypoint.sh"]
