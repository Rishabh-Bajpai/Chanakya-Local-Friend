FROM python:3.11-bookworm

ENV DEBIAN_FRONTEND=noninteractive \
    NPM_CONFIG_UPDATE_NOTIFIER=false \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
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
        wget \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace
