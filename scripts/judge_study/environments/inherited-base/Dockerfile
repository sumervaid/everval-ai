# Derived native runtime for a new replication; not the historical image digest.
ARG UBUNTU_IMAGE=ubuntu@sha256:a8fb2ba88181746111cbbe2969826d6546b71226785b196c93d2c3445a25000d
FROM --platform=linux/arm64 ${UBUNTU_IMAGE}
ARG DEBIAN_FRONTEND=noninteractive
ENV TZ=Etc/UTC
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates wget git build-essential libffi-dev libtiff-dev \
    python3 python3-pip python-is-python3 jq curl locales locales-all tzdata bzip2 \
    && rm -rf /var/lib/apt/lists/*
# The grading sandbox PATH contains /usr/local/bin but omits /usr/sbin.
RUN test -x /usr/sbin/locale-gen && ln -s /usr/sbin/locale-gen /usr/local/bin/locale-gen
RUN wget -q https://repo.anaconda.com/miniconda/Miniconda3-py311_23.11.0-2-Linux-aarch64.sh -O /miniconda.sh \
    && echo 'decd447fb99dbd0fc5004481ec9bf8c04f9ba28b35a9292afe49ecefe400237f  /miniconda.sh' | sha256sum -c - \
    && bash /miniconda.sh -b -p /opt/miniconda3
COPY conda-packages.lock.json wheels.lock.json runtime-pip.txt provenance.json ubuntu-base.lock.json manifest-sha256.json fetch_locked.py /opt/everval-locks/
RUN /opt/miniconda3/bin/python /opt/everval-locks/fetch_locked.py conda --output /opt/everval-conda \
    && /opt/miniconda3/bin/conda create --yes --offline --prefix /opt/miniconda3/envs/testbed --file /opt/everval-conda/local-explicit.txt \
    && /opt/miniconda3/bin/python /opt/everval-locks/fetch_locked.py wheels --output /opt/everval-wheels \
    && /opt/miniconda3/envs/testbed/bin/python -m pip install --no-index --no-deps --find-links /opt/everval-wheels -r /opt/everval-locks/runtime-pip.txt
ENV PATH=/opt/miniconda3/envs/testbed/bin:/opt/miniconda3/bin:$PATH
ENV PIP_FIND_LINKS=/opt/everval-wheels
RUN python -c "import platform,sys; assert sys.version_info[:3] == (3,9,20); assert platform.machine() == 'aarch64'" \
    && mkdir -p /testbed /opt/everval-provenance \
    && chmod 777 /testbed \
    && conda list --prefix /opt/miniconda3/envs/testbed --explicit --md5 > /opt/everval-provenance/conda-explicit.txt \
    && python -m pip freeze --all > /opt/everval-provenance/pip-freeze.txt \
    && dpkg-query -W > /opt/everval-provenance/dpkg-packages.txt \
    && cp /etc/os-release /opt/everval-provenance/os-release \
    && sha256sum /miniconda.sh > /opt/everval-provenance/miniconda-sha256.txt
WORKDIR /testbed
