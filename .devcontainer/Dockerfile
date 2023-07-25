# FROM homeassistant/home-assistant:2023.3
FROM mcr.microsoft.com/vscode/devcontainers/python:0-3.11

ARG ha_version
ARG apt_deps
ARG pip_deps


RUN export DEBIAN_FRONTEND=noninteractive \
 && apt-get update \
 && apt-get install -y --no-install-recommends sudo python3-dev libffi-dev libssl-dev python3-venv $apt_deps \
 && apt-get clean \
 && rm -rf /var/lib/apt/lists/*

RUN pip3 --disable-pip-version-check --no-cache-dir install black Pyright pylint $pip_deps \
 && rm -rf /tmp/pip-tmp

RUN echo "alias ll='ls -l'" >> /etc/bash.bashrc \
 && echo "alias la='ls -A'" >> /etc/bash.bashrc \
 && echo "alias l='ls -CF'" >> /etc/bash.bashrc

# RUN useradd -m -s /bin/bash -G sudo vscode \
#  && echo "vscode ALL=(root) NOPASSWD:ALL" > /etc/sudoers.d/vscode \
#  && chmod 0440 /etc/sudoers.d/vscode


RUN mkdir /srv/homeassistant \
 && virtualenv -p python3 /srv/homeassistant \
 && /srv/homeassistant/bin/pip3 --disable-pip-version-check --no-cache-dir install homeassistant==$ha_version black Pyright pylint $pip_deps \
 && rm -rf /tmp/pip-tmp \
 && chown -R vscode:vscode /srv/homeassistant

# to run execute:
#/srv/homeassistant/bin/hass