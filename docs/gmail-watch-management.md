# การจัดการ Gmail Watch อัตโนมัติ

## ปัญหา Gmail Watch ที่หมดอายุ

Gmail Push Notifications จะหมดอายุทุก ~7 วัน เมื่อหมดอายุแล้ว Gmail จะหยุดส่ง push notifications และ server จะไม่ได้รับแจ้งเตือนเมื่อมีอีเมลใหม่

## วิธีการแก้ไข

ระบบนี้มีกลไกการต่ออายุอัตโนมัติผ่าน `GmailWatchService` ที่จะ:

1. **ตรวจสอบสถานะ watch** ทุกชั่วโมง
2. **ต่ออายุอัตโนมัติ** เมื่อใกล้หมดอายุ (เหลือน้อยกว่า 1 วัน)
3. **จัดการ error** เมื่อ watch หมดอายุไป

## การตั้งค่า

### 1. เพิ่ม Environment Variables

```bash
# ใน .env file
GCP_PROJECT_ID=your-gcp-project-id
PUBSUB_TOPIC_NAME=your-topic-name
GMAIL_TOKENS_DIR=secrets/tokens
GMAIL_WATCH_USERS=your-email@gmail.com
```

### 2. ตั้งค่า Gmail Watch เบื้องต้น

```bash
# รัน script เพื่อตั้งค่า watch แรกเริ่ม
python setup_gmail_watches.py
```

### 3. เริ่มต้น Server

```bash
# Server จะเริ่ม Gmail Watch Service อัตโนมัติ
python app/main.py
```

## การทำงานของระบบ

### เมื่อ Server เริ่มทำงาน

1. **โหลด Gmail tokens** จาก `secrets/tokens/`
2. **ตั้งค่า watch** สำหรับผู้ใช้ทั้งหมด
3. **เริ่ม background thread** สำหรับตรวจสอบและต่ออายุ
4. **เริ่ม Pub/Sub listener** เพื่อรับ notifications

### การต่ออายุอัตโนมัติ

- ตรวจสอบทุก **1 ชั่วโมง**
- ต่ออายุเมื่อเหลือน้อยกว่า **1 วัน**
- บันทึก log การต่ออายุ
- จัดการ error อัตโนมัติ

### Log Messages ที่ควรเฝ้าดู

```
✅ Gmail watch setup for user@example.com
📝 Gmail Watch Service started with automatic renewal
🔄 Renewing Gmail watch for user@example.com
✅ Watch renewed successfully for user@example.com
```

## การตรวจสอบสถานะ

### 1. ตรวจสอบไฟล์ State

```bash
ls secrets/tokens/*.state.json
cat secrets/tokens/your-email@gmail.com.state.json
```

### 2. ตรวจสอบ Log

```bash
# ดู log ของ server
tail -f logs/app.log

# หรือใช้ docker logs
docker logs -f secretary_agent_app
```

## การแก้ไขปัญหา

### ปัญหา: Watch หมดอายุแล้ว

**อาการ:** ไม่ได้รับแจ้งเตือนอีเมลใหม่

**วิธีแก้:**
```bash
# 1. รีสตาร์ท server เพื่อตั้งค่า watch ใหม่
docker restart secretary_agent_app

# 2. หรือรัน script ตั้งค่าใหม่
python setup_gmail_watches.py
```

### ปัญหา: Pub/Sub Permission

**อาการ:** Error เกี่ยวกับ permission

**วิธีแก้:**
```bash
# ให้สิทธิ์ Gmail push service
gcloud pubsub topics add-iam-policy-binding your-topic-name \
  --member=serviceAccount:gmail-api-push@system.gserviceaccount.com \
  --role=roles/pubsub.publisher \
  --project your-gcp-project-id
```

### ปัญหา: Token หมดอายุ

**อาการ:** Error เกี่ยวกับ OAuth credentials

**วิธีแก้:**
```bash
# ตั้งค่า OAuth token ใหม่
python diagnostics/setup_gmail_watch.py \
  --project your-gcp-project-id \
  --topic your-topic-name
```

## การ Monitor

### 1. Health Check Endpoint

Server มี endpoint สำหรับตรวจสอบสถานะ:

```bash
curl http://localhost:8080/
```

### 2. Log Monitoring

ควรติดตาม log messages เหล่านี้:

- `Gmail Watch Service started` - Service เริ่มทำงาน
- `Watch renewed successfully` - ต่ออายุสำเร็จ
- `Failed to renew watch` - ต่ออายุไม่สำเร็จ
- `Gmail push notification received` - ได้รับแจ้งเตือน

## การปรับแต่ง

### เปลี่ยนความถี่การตรวจสอบ

แก้ไขใน `app/main.py`:

```python
# ตรวจสอบทุก 30 นาที
watch_service.start_automatic_renewal(check_interval_hours=0.5)

# ตรวจสอบทุก 6 ชั่วโมง  
watch_service.start_automatic_renewal(check_interval_hours=6)
```

### เปลี่ยนเงื่อนไขการต่ออายุ

แก้ไขใน `service/gmail_watch_service.py`:

```python
# ต่ออายุเมื่อเหลือน้อยกว่า 2 วัน
return (expiry_time - current_time) < 2 * 24 * 60 * 60

# ต่ออายุเมื่อเหลือน้อยกว่า 12 ชั่วโมง
return (expiry_time - current_time) < 12 * 60 * 60
```

## สรุป

ด้วยระบบนี้ server จะสามารถ:

- **รับแจ้งเตือนอีเมลได้ตลอด** โดยไม่ต้องกังวลเรื่อง watch หมดอายุ
- **ต่ออายุอัตโนมัติ** ก่อนที่ watch จะหมดอายุ  
- **จัดการ error** และ retry อัตโนมัติ
- **Monitor สถานะ** ผ่าน log messages

แค่ให้แน่ใจว่า server รันอยู่ตลอดเวลา ระบบจะดูแล Gmail watch ให้เองโดยอัตโนมัติ!