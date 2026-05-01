import cv2
import numpy as np
import pytesseract
import sqlite3
import os
import re
import subprocess
import webbrowser  # লিঙ্ক ওপেন করার জন্য
from collections import deque
from kivymd.app import MDApp
from kivy.uix.floatlayout import FloatLayout
from kivy.clock import Clock
from kivy.properties import StringProperty
from kivy.utils import platform

# --- অ্যান্ড্রয়েড স্পেসিফিক ইম্পোর্ট ---
if platform == 'android':
    from android.permissions import request_permissions, Permission

# --- আপনার অরিজাল RIGID ROI CONFIG ---
PERIOD_ROI = [445, 495, 120, 580]
TIMER_ROI = [445, 495, 780, 930]
PAGE_LOCK_ROI = [530, 575, 60, 280]
RESULT_ROI = [650, 750, 100, 400]

# পাথ সেটআপ
BASE_PATH = "/sdcard/dkwin_project/" if platform == 'android' else "./dkwin_project/"
DB_PATH = os.path.join(BASE_PATH, "dkwin_boss_v20.db")
GAME_URL = "https://dkwin9.com/#/" # আপনার দেওয়া সেই লিঙ্ক

class DkwinEngine(FloatLayout):
    prediction = StringProperty("SYSTEM ACTIVE")
    timer_display = StringProperty("TIMER: --")
    period_display = StringProperty("PERIOD: --")
    ocr_status = StringProperty("STATUS: READY")
    confidence = StringProperty("0%")

class DkwinBossApp(MDApp):

    def build(self):
        if platform == 'android':
            request_permissions([
                Permission.WRITE_EXTERNAL_STORAGE,
                Permission.READ_EXTERNAL_STORAGE,
                Permission.CAMERA
            ])

        self.init_db()
        self.load_history()

        self.last_timer_sec = None
        self.last_predicted_period = None
        self.last_result_period = None

        self.matrix = {"BIG": {"BIG": 0, "SMALL": 0},
                       "SMALL": {"BIG": 0, "SMALL": 0}}

        self.update_matrix()

        # অ্যাপ চালু হওয়ার সাথে সাথে ক্রোমে গেম পেজ ওপেন হবে
        Clock.schedule_once(lambda dt: webbrowser.open(GAME_URL), 1)
        
        return DkwinEngine()

    # ---------------- DB (আপনার অরিজিনাল লজিক) ----------------
    def init_db(self):
        os.makedirs(BASE_PATH, exist_ok=True)
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("CREATE TABLE IF NOT EXISTS history (period TEXT, result TEXT)")
        conn.commit()
        conn.close()

    def load_history(self):
        self.history = deque(maxlen=500)
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("SELECT result FROM history ORDER BY rowid DESC LIMIT 500")
        rows = c.fetchall()
        conn.close()
        for r in reversed(rows):
            self.history.append(r[0])

    def save_result(self, period, result):
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("INSERT INTO history (period, result) VALUES (?,?)",
                  (period, result))
        conn.commit()
        conn.close()
        self.history.append(result)
        self.update_matrix()

    def update_matrix(self):
        hist = list(self.history)
        self.matrix = {"BIG": {"BIG": 0, "SMALL": 0},
                       "SMALL": {"BIG": 0, "SMALL": 0}}
        for i in range(len(hist) - 1):
            if hist[i] in self.matrix and hist[i + 1] in self.matrix:
                self.matrix[hist[i]][hist[i + 1]] += 1

    # ---------------- SCREENSHOT (আপনার অরিজিনাল লজিক) ----------------
    def take_screenshot(self):
        path = os.path.join(BASE_PATH, "screen.png")
        try:
            subprocess.run(
                ["screencap", "-p", path], 
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3
            )
            return cv2.imread(path)
        except:
            return None

    # ---------------- ENGINE (আপনার অরিজিনাল লজিক) ----------------
    def on_start(self):
        Clock.schedule_interval(self.engine, 2)

    def engine(self, dt):
        try:
            img = self.take_screenshot()
            if img is None:
                self.root.ocr_status = "SCREEN FAIL"
                return
            gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
            gray = cv2.GaussianBlur(gray, (3, 3), 0)
            _, thresh = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)

            page = pytesseract.image_to_string(
                thresh[PAGE_LOCK_ROI[0]:PAGE_LOCK_ROI[1],
                       PAGE_LOCK_ROI[2]:PAGE_LOCK_ROI[3]]
            ).upper()
            if not any(x in page for x in ["1MIN", "GO"]):
                self.root.ocr_status = "WRONG PAGE"
                return
            self.root.ocr_status = "SYNC OK"

            timer_raw = pytesseract.image_to_string(thresh[TIMER_ROI[0]:TIMER_ROI[1], TIMER_ROI[2]:TIMER_ROI[3]]).strip()
            sec = None
            match = re.search(r'\d{1,2}:\d{2}', timer_raw)
            if match:
                sec = int(match.group().split(":")[1])
                self.root.timer_display = f"TIMER: {match.group()}"
            else:
                digits = re.sub(r'\D', '', timer_raw)
                if len(digits) >= 2:
                    sec = int(digits[-2:])
                    if sec > 59: return
                    self.root.timer_display = f"TIMER: {sec}s"
                else: return

            period_raw = pytesseract.image_to_string(thresh[PERIOD_ROI[0]:PERIOD_ROI[1], PERIOD_ROI[2]:PERIOD_ROI[3]])
            period_clean = re.sub(r'\D', '', period_raw)
            if len(period_clean) < 5: return
            self.root.period_display = f"PERIOD: {period_clean[-4:]}"

            if self.last_timer_sec is not None:
                if self.last_timer_sec <= 3 and sec >= 50:
                    result = self.detect_result(thresh)
                    if result and period_clean != self.last_result_period:
                        self.save_result(period_clean, result)
                        self.last_result_period = period_clean
                        self.root.ocr_status = f"SAVED: {result}"

            if sec <= 8 and sec > 1 and period_clean != self.last_predicted_period:
                pred, conf = self.predict_logic()
                self.root.prediction = pred
                self.root.confidence = f"{conf}%"
                self.last_predicted_period = period_clean
            self.last_timer_sec = sec
        except:
            self.root.ocr_status = "RECOVERING..."

    def detect_result(self, img):
        zone = img[RESULT_ROI[0]:RESULT_ROI[1], RESULT_ROI[2]:RESULT_ROI[3]]
        text = pytesseract.image_to_string(zone).upper()
        text = text.replace("8", "B").replace("1", "I")
        if "BIG" in text: return "BIG"
        if "SMALL" in text: return "SMALL"
        return None

    def predict_logic(self):
        hist = list(self.history)
        if len(hist) < 10: return "COLLECTING", 0
        last = hist[-1]
        b = self.matrix[last]["BIG"]
        s = self.matrix[last]["SMALL"]
        recent = hist[-20:]
        b += recent.count("BIG")
        s += recent.count("SMALL")
        total = b + s
        if total == 0: return "BIG", 50
        pred = "BIG" if b > s else "SMALL"
        conf = int((max(b, s) / total) * 100)
        return pred, conf

if __name__ == "__main__":
    DkwinBossApp().run()
