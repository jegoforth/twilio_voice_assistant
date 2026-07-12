ARG BUILD_FROM=debian:bookworm-slim
FROM $BUILD_FROM

ARG SOURCE_VERSION=unknown
ARG SOURCE_REVISION=unknown
ENV SOURCE_VERSION=$SOURCE_VERSION
ENV SOURCE_REVISION=$SOURCE_REVISION

RUN apt-get update && apt-get install -y \
    python3 \
    python3-venv \
    python3-pip \
    build-essential \
    && apt-get clean

WORKDIR /app

RUN python3 -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY run.sh /run.sh
RUN chmod a+x /run.sh

COPY main.py .

CMD ["/run.sh"]
