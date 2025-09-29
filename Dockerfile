# ใช้ Base Image ที่อัปเดตและปลอดภัยกว่า (Debian bookworm, Python 3.12)
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# อัปเดตแพ็กเกจความปลอดภัยของระบบ
RUN apt-get update && apt-get upgrade -y && apt-get install -y wget unzip libaio1 && rm -rf /var/lib/apt/lists/*

# ตั้งค่า Working Directory
WORKDIR /app

RUN wget https://download.oracle.com/otn_software/linux/instantclient/2390000/instantclient-basic-linux.arm64-23.9.0.25.07.zip

RUN unzip instantclient-basic-linux.arm64-23.9.0.25.07.zip && \
    rm instantclient-basic-linux.arm64-23.9.0.25.07.zip



ENV LD_LIBRARY_PATH=/app/instantclient_23_9

# Copy ไฟล์ที่จำเป็นสำหรับการติดตั้ง dependencies
COPY pyproject.toml uv.lock* ./

# สั่ง uv ให้ติดตั้ง dependencies ตาม lock file แบบ reproducible (ติดตั้งเข้า system site-packages)
RUN uv export --frozen --no-emit-project --format requirements-txt > requirements.txt \
    && uv pip install --system --no-cache -r requirements.txt \
    && rm requirements.txt

# Copy โค้ดที่เหลือทั้งหมด (เช่น main.py)
COPY . .

# รันด้วย user ที่ไม่ใช่ root เพื่อลดความเสี่ยง
RUN addgroup --system app && adduser --system --ingroup app app && chown -R app:app /app
USER app

# (ถ้า app ของคุณรันบน port 5000 - ถ้าไม่ ให้แก้)
EXPOSE 5000

# คำสั่งสำหรับรัน app (แก้ 'main.py' ถ้าไฟล์หลักของคุณชื่ออื่น)
CMD ["python", "main.py"]