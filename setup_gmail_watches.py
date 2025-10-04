#!/usr/bin/env python3
"""
Script สำหรับตั้งค่า Gmail watch เบื้องต้น
Usage: python setup_gmail_watches.py
"""

import os
import sys
from pathlib import Path

# เพิ่ม path เพื่อให้ import service ได้
sys.path.append(str(Path(__file__).parent))

from dotenv import load_dotenv
from service.gmail_watch_service import GmailWatchService

# โหลด environment variables
load_dotenv()

def main():
    # ตรวจสอบ environment variables ที่จำเป็น
    project_id = os.getenv("GCP_PROJECT_ID")
    topic_name = os.getenv("PUBSUB_TOPIC_NAME")
    tokens_dir = os.getenv("GMAIL_TOKENS_DIR", "secrets/tokens")
    
    if not project_id:
        print("❌ GCP_PROJECT_ID not set in environment variables")
        return False
        
    if not topic_name:
        print("❌ PUBSUB_TOPIC_NAME not set in environment variables")
        return False
    
    print(f"🚀 Setting up Gmail watches...")
    print(f"   Project ID: {project_id}")
    print(f"   Topic: {topic_name}")
    print(f"   Tokens directory: {tokens_dir}")
    print()
    
    # สร้าง watch service
    try:
        watch_service = GmailWatchService(
            tokens_dir=tokens_dir,
            project_id=project_id,
            topic_name=topic_name
        )
        
        # ดึงรายชื่อผู้ใช้ทั้งหมด
        emails = watch_service.get_all_user_emails()
        if not emails:
            print("❌ No Gmail users found!")
            print(f"   Please ensure you have OAuth token files in: {tokens_dir}")
            print("   Token files should be named: email@domain.com.json")
            return False
        
        print(f"📧 Found {len(emails)} Gmail accounts:")
        for email in emails:
            print(f"   - {email}")
        print()
        
        # ตั้งค่า watch สำหรับผู้ใช้ทั้งหมด
        print("🔧 Setting up Gmail watches...")
        results = watch_service.setup_all_watches()
        
        success_count = 0
        for email, success in results.items():
            if success:
                print(f"   ✅ {email} - Watch setup successfully")
                success_count += 1
            else:
                print(f"   ❌ {email} - Failed to setup watch")
        
        print()
        print(f"📊 Summary: {success_count}/{len(emails)} watches setup successfully")
        
        if success_count > 0:
            print()
            print("🎉 Gmail watches are now active!")
            print("📝 Important notes:")
            print("   - Watches will expire in ~7 days")
            print("   - Your server will automatically renew them when running")
            print("   - Make sure your server is running to receive notifications")
            print()
            print("🔧 Next steps:")
            print("   1. Ensure your Pub/Sub topic has proper permissions:")
            print(f"      gcloud pubsub topics add-iam-policy-binding {topic_name} \\")
            print("        --member=serviceAccount:gmail-api-push@system.gserviceaccount.com \\")
            print(f"        --role=roles/pubsub.publisher --project {project_id}")
            print()
            print("   2. Start your server:")
            print("      python app/main.py")
            return True
        else:
            print("❌ No watches were setup successfully")
            return False
        
    except Exception as e:
        print(f"❌ Error setting up Gmail watches: {e}")
        return False

if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)