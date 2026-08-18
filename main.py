import asyncio
import html
import os
import re
import sqlite3
import threading
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from flask import Flask
from PIL import Image, ImageDraw, ImageFont, ImageOps
from telegram import Bot
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# --- CONFIGURAZIONE ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "8670212259:AAFn_21_abtz4vL4WQ5TpekYby-hCnAjzeU")
CANALE_CHAT_ID = os.getenv("CANALE_CHAT_ID", "@TarloDelRisparmio")
AMAZON_TAG = os.getenv("AMAZON_TAG", "tarlodelrispa-21")
TELEGRAM_API_ID = int(os.getenv("TELEGRAM_API_ID", "31134748"))
TELEGRAM_API_HASH = os.getenv("TELEGRAM_API_HASH", "ba4265cff56d0687c6c5171b47f76e02")
SESSION_STRING = os.getenv("TELEGRAM_SESSION_STRING", "")

CANALI_SPIA = ["sparky_offerte", "AstroHouse_Casa_Cucina", "ultimaofferta", "offerte5"]

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = BASE_DIR / "template.png"
OUTPUT_PATH = BASE_DIR / "offerta_finale.png"
DB_PATH = BASE_DIR / "offerte.db"

bot = Bot(token=TELEGRAM_TOKEN)
app = Flask(__name__)

# --- DATABASE ---
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prodotti (
                asin TEXT PRIMARY KEY,
                inviato_il DATETIME
            )
        """)

def gia_inviato(asin):
    with sqlite3.connect(DB_PATH) as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT inviato_il FROM prodotti WHERE asin = ?", (asin,))
        row = cursor.fetchone()
        if not row: return False
        inviato_dt = datetime.fromisoformat(row[0])
        return datetime.now() - inviato_dt < timedelta(hours=24)

def segna_inviato(asin):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("INSERT OR REPLACE INTO prodotti (asin, inviato_il) VALUES (?, ?)", 
                     (asin, datetime.now().isoformat()))

# --- SCRAPER AMAZON (INTELLIGENTE) ---
def estrai_asin(testo):
    match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', testo)
    return match.group(1) if match else None

def scarica_dettagli_amazon(asin):
    url = f"https://www.amazon.it/dp/{asin}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        res = requests.get(url, headers=headers, timeout=12)
        soup = BeautifulSoup(res.text, "html.parser")
        
        titolo_elem = soup.find("span", {"id": "productTitle"})
        titolo = titolo_elem.get_text().strip() if titolo_elem else "Prodotto Amazon"

        # Prezzo Attuale
        p_elem = soup.find("span", {"class": "a-price", "data-a-size": "xl"}) or soup.find("span", {"class": "a-price", "data-a-size": "l"})
        if not p_elem: return None
        prezzo_attuale = p_elem.find("span", class_="a-offscreen").get_text().replace("€", "").strip().replace(".", ",")
        p_att_num = float(prezzo_attuale.replace('.', '').replace(',', '.'))

        # Prezzo Precedente (Solo se è il prezzo barrato ufficiale)
        sconto = 0
        p_strike = soup.find("span", class_="a-text-strike")
        if p_strike:
            p_prec_num = float(p_strike.get_text().replace("€", "").strip().replace('.', '').replace(',', '.'))
            if p_prec_num > p_att_num:
                sconto_calc = int(round(((p_prec_num - p_att_num) / p_prec_num) * 100))
                if 0 < sconto_calc <= 80: # Sanity check: no sconti > 80% (errore dati)
                    sconto = sconto_calc

        img_elem = soup.find("img", {"id": "landingImage"}) or soup.find("img", {"id": "imgBlkFront"})
        return {
            "asin": asin, "titolo": titolo, "prezzo_attuale": prezzo_attuale,
            "prezzo_precedente": p_strike.get_text().replace("€", "").strip() if p_strike else prezzo_attuale,
            "sconto": sconto, "immagine_url": img_elem["src"] if img_elem else ""
        }
    except Exception as e:
        print(f"Errore scraping: {e}")
        return None

# --- GRAFICA (PIL) ---
def crea_immagine(prodotto):
    template = Image.open(TEMPLATE_PATH).convert("RGBA").resize((1536, 1536))
    draw = ImageDraw.Draw(template)
    # [LOGICA DI DISEGNO RIMANE INVARIATA]
    # (Inserisci qui il tuo codice di disegno esistente che funziona bene)
    template.convert("RGB").save(OUTPUT_PATH, "PNG")
    return OUTPUT_PATH

# --- BOT TELEGRAM ---
async def main():
    init_db()
    client = TelegramClient(StringSession(SESSION_STRING), TELEGRAM_API_ID, TELEGRAM_API_HASH)
    await client.start()

    @client.on(events.NewMessage(chats=CANALI_SPIA))
    async def handler(event):
        asin = estrai_asin(event.message.text)
        if asin and not gia_inviato(asin):
            p = await asyncio.to_thread(scarica_dettagli_amazon, asin)
            if p:
                segna_inviato(asin)
                foto = crea_immagine(p)
                didascalia = f"📦 {p['titolo']}\n💰 Prezzo: {p['prezzo_attuale']}€\n👉 https://amazon.it/dp/{p['asin']}?tag={AMAZON_TAG}"
                await bot.send_photo(chat_id=CANALE_CHAT_ID, photo=open(foto, "rb"), caption=didascalia)

    print("Bot avviato...")
    await client.run_until_disconnected()

if __name__ == "__main__":
    # Flask per mantenere attivo Render
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=10000), daemon=True).start()
    asyncio.run(main())
    
