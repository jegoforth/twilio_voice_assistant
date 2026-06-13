ARG BUILD_FROM=debian:bookworm-slim
FROM $BUILD_FROM

RUN apt-get update && apt-get install -y \
    python3 \
    python3-venv \
    python3-pip \
    ffmpeg \
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
