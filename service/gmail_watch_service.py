from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Dict, Optional

from googleapiclient.discovery import build
from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)


class GmailWatchService:
    """Service สำหรับจัดการ Gmail Watch และต่ออายุอัตโนมัติ"""

    def __init__(self, tokens_dir: str = "secrets/tokens", project_id: str = None, topic_name: str = None):
        self.tokens_dir = Path(tokens_dir)
        self.project_id = project_id or os.getenv("GCP_PROJECT_ID")
        self.topic_name = topic_name or os.getenv("PUBSUB_TOPIC_NAME")
        self.watch_renewal_thread: Optional[threading.Thread] = None
        self.stop_renewal = False
        
        if not self.project_id or not self.topic_name:
            raise ValueError("GCP_PROJECT_ID and PUBSUB_TOPIC_NAME must be set")

    def get_all_user_emails(self) -> list[str]:
        """ดึงรายชื่ออีเมลทั้งหมดที่มี token file"""
        emails = []
        if not self.tokens_dir.exists():
            return emails
            
        for token_file in self.tokens_dir.glob("*.json"):
            if not token_file.name.endswith('.state.json'):
                # อีเมลจะเป็นชื่อไฟล์ก่อน .json
                email = token_file.stem
                if '@' in email:  # ตรวจสอบว่าเป็นอีเมล
                    emails.append(email)
        return emails

    def load_credentials(self, email: str) -> Optional[Credentials]:
        """โหลด credentials สำหรับอีเมลที่กำหนด"""
        token_file = self.tokens_dir / f"{email}.json"
        if not token_file.exists():
            logger.warning(f"Token file not found for {email}")
            return None
            
        try:
            return Credentials.from_authorized_user_file(str(token_file))
        except Exception:
            logger.exception(f"Failed to load credentials for {email}")
            return None

    def get_watch_state(self, email: str) -> Optional[Dict]:
        """ดึงสถานะ watch จาก state file"""
        state_file = self.tokens_dir / f"{email}.state.json"
        if not state_file.exists():
            return None
            
        try:
            with open(state_file, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            logger.exception(f"Failed to load watch state for {email}")
            return None

    def save_watch_state(self, email: str, watch_response: Dict) -> None:
        """บันทึกสถานะ watch ลง state file"""
        state_file = self.tokens_dir / f"{email}.state.json"
        state = {
            "emailAddress": email,
            "projectId": self.project_id,
            "topic": self.topic_name,
            "watchResponse": watch_response,
            "lastRenewed": int(time.time())
        }
        
        try:
            with open(state_file, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2)
        except Exception:
            logger.exception(f"Failed to save watch state for {email}")

    def setup_watch(self, email: str) -> bool:
        """เซ็ตอัพ Gmail watch สำหรับอีเมลที่กำหนด"""
        creds = self.load_credentials(email)
        if not creds:
            return False
            
        try:
            service = build("gmail", "v1", credentials=creds)
            body = {
                "topicName": f"projects/{self.project_id}/topics/{self.topic_name}",
                "labelFilterBehavior": "INCLUDE",
                "labelIds": ["INBOX"]
            }
            
            response = service.users().watch(userId="me", body=body).execute()
            self.save_watch_state(email, response)
            
            logger.info(f"✅ Gmail watch setup successfully for {email}")
            logger.info(f"Watch expires: {response.get('expiration', 'unknown')}")
            return True
            
        except Exception:
            logger.exception(f"❌ Failed to setup Gmail watch for {email}")
            return False

    def is_watch_expired(self, email: str) -> bool:
        """ตรวจสอบว่า watch หมดอายุหรือไม่"""
        state = self.get_watch_state(email)
        if not state:
            return True
            
        watch_response = state.get("watchResponse", {})
        expiration = watch_response.get("expiration")
        
        if not expiration:
            # หากไม่มี expiration ให้ถือว่าหมดอายุ
            return True
            
        try:
            # expiration เป็น timestamp ในหน่วย milliseconds
            expiry_time = int(expiration) / 1000
            current_time = time.time()
            
            # ตรวจสอบว่าใกล้หมดอายุ (เหลือน้อยกว่า 1 วัน)
            return (expiry_time - current_time) < 24 * 60 * 60
            
        except (ValueError, TypeError):
            logger.warning(f"Invalid expiration format for {email}: {expiration}")
            return True

    def renew_watch_for_all_users(self) -> None:
        """ต่ออายุ watch สำหรับผู้ใช้ทั้งหมด"""
        emails = self.get_all_user_emails()
        
        for email in emails:
            try:
                if self.is_watch_expired(email):
                    logger.info(f"Renewing Gmail watch for {email}")
                    success = self.setup_watch(email)
                    if success:
                        logger.info(f"✅ Watch renewed successfully for {email}")
                    else:
                        logger.error(f"❌ Failed to renew watch for {email}")
                else:
                    logger.debug(f"Watch for {email} is still valid")
                    
            except Exception:
                logger.exception(f"Error checking/renewing watch for {email}")

    def start_automatic_renewal(self, check_interval_hours: int = 1) -> None:
        """เริ่มการต่ออายุอัตโนมัติในพื้นหลัง"""
        if self.watch_renewal_thread and self.watch_renewal_thread.is_alive():
            logger.warning("Watch renewal thread is already running")
            return
            
        self.stop_renewal = False
        
        def renewal_loop():
            logger.info(f"Started Gmail watch renewal thread (check every {check_interval_hours} hours)")
            
            while not self.stop_renewal:
                try:
                    self.renew_watch_for_all_users()
                except Exception:
                    logger.exception("Error in watch renewal loop")
                
                # รอ check_interval_hours ชั่วโมง
                for _ in range(check_interval_hours * 3600):  # แปลงเป็นวินาที
                    if self.stop_renewal:
                        break
                    time.sleep(1)
            
            logger.info("Gmail watch renewal thread stopped")
        
        self.watch_renewal_thread = threading.Thread(target=renewal_loop, name="gmail-watch-renewal", daemon=True)
        self.watch_renewal_thread.start()

    def stop_automatic_renewal(self) -> None:
        """หยุดการต่ออายุอัตโนมัติ"""
        self.stop_renewal = True
        
        if self.watch_renewal_thread and self.watch_renewal_thread.is_alive():
            logger.info("Stopping Gmail watch renewal thread...")
            self.watch_renewal_thread.join(timeout=5)
            
    def setup_all_watches(self) -> Dict[str, bool]:
        """เซ็ตอัพ watch สำหรับผู้ใช้ทั้งหมด"""
        emails = self.get_all_user_emails()
        results = {}
        
        for email in emails:
            results[email] = self.setup_watch(email)
            
        return results