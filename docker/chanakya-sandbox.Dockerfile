FROM python:3.11-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN set -eux; \
    apt_get_install() { \
        apt-get update -o Acquire::Retries=5 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30; \
        apt-get install -y --no-install-recommends \
            -o Acquire::Retries=5 \
            -o Acquire::http::Timeout=30 \
            -o Acquire::https::Timeout=30 \
            bash \
            build-essential \
            ca-certificates \
            coreutils \
            curl \
            git \
            iproute2 \
            jq \
            net-tools \
            nodejs \
            npm \
            procps \
            unzip \
            wget; \
    }; \
    for attempt in 1 2 3; do \
        if apt_get_install; then \
            rm -rf /var/lib/apt/lists/*; \
            exit 0; \
        fi; \
        rm -rf /var/lib/apt/lists/*; \
        sleep 5; \
    done; \
    exit 1

WORKDIR /workspace
