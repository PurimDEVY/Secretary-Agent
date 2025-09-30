# ใช้ Base Image ที่อัปเดตและปลอดภัยกว่า (Debian bookworm, Python 3.12)
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# อัปเดตแพ็กเกจความปลอดภัยของระบบ
RUN apt-get update && apt-get upgrade -y && apt-get install -y wget unzip libaio1 && rm -rf /var/lib/apt/lists/*

# ตั้งค่า Working Directory
WORKDIR /app

ARG TARGETARCH

RUN set -eux; \
    case "$TARGETARCH" in \
      amd64) IC_ZIP=instantclient-basiclite-linux.x64-23.9.0.25.07.zip ;; \
      arm64) IC_ZIP=instantclient-basiclite-linux.arm64-23.9.0.25.07.zip ;; \
      *) echo "Unsupported arch: $TARGETARCH" && exit 1 ;; \
    esac; \
    wget -q https://download.oracle.com/otn_software/linux/instantclient/2390000/$IC_ZIP; \
    unzip -q $IC_ZIP; \
    rm $IC_ZIP; \
    mv instantclient_* instantclient


ENV LD_LIBRARY_PATH=/app/instantclient

COPY pyproject.toml uv.lock* ./

RUN uv export --frozen --no-emit-project --format requirements-txt > requirements.txt \
    && uv pip install --system --no-cache -r requirements.txt \
    && rm requirements.txt


COPY . .

RUN addgroup --system app && \
    adduser --system --ingroup app app && \
    chown -R app:app /app 
    
USER app

EXPOSE 5000

CMD ["python", "main.py"]