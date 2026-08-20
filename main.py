import asyncio
import os
import re
import sqlite3
import textwrap
import threading
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

import cairosvg
import requests
from bs4 import BeautifulSoup
from flask import Flask
from PIL import Image, ImageDraw, ImageFont
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

PORT = int(os.getenv("PORT", 10000))

CANALI_SPIA = [
    "sparky_offerte", 
    "AstroHouse_Casa_Cucina", 
    "ultimaofferta", 
    "offerte5",
    "offerte_supermercato", 
    "SpesaScontata",
    "provawill32"
]

BASE_DIR = Path(__file__).resolve().parent
SVG_TEMPLATE_PATH = BASE_DIR / "template.svg"
OUTPUT_PATH = BASE_DIR / "offerta_finale.png"
DB_PATH = BASE_DIR / "offerte.db"

bot = Bot(token=TELEGRAM_TOKEN)
app = Flask(__name__)

# --- RICERCA AUTOMATICA FONT ---
def carica_font_locale(size):
    font_files = list(BASE_DIR.rglob("*.ttf")) + list(BASE_DIR.rglob("*.otf"))
    if font_files:
        try:
            return ImageFont.truetype(str(font_files[0]), size)
        except Exception as e:
            print(f"[ERRORE CARICAMENTO FONT]: {e}")
    return ImageFont.load_default()

# --- WEB SERVER ---
@app.route("/")
def home():
    return "Bot Online", 200

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

# --- ESTRAZIONE ASIN ---
def estrai_asin(testo):
    if not testo: return None
    match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', testo)
    if match: return match.group(1)
    urls = re.findall(r'https?://[^\s]+', testo)
    headers = {"User-Agent": "Mozilla/5.0"}
    for url in urls:
        try:
            res = requests.head(url, allow_redirects=True, timeout=5, headers=headers)
            match_redirect = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', res.url)
            if match_redirect: return match_redirect.group(1)
        except: continue
    return None

# --- SCRAPER ---
def scarica_dettagli_amazon(asin):
    url = f"https://www.amazon.it/dp/{asin}"
    headers = {"User-Agent": "Mozilla/5.0", "Accept-Language": "it-IT,it;q=0.9"}
    try:
        res = requests.get(url, headers=headers, timeout=12)
        if res.status_code != 200: return None
        soup = BeautifulSoup(res.text, "html.parser")
        
        titolo_elem = soup.find("span", {"id": "productTitle"})
        titolo = titolo_elem.get_text().strip() if titolo_elem else "Prodotto Amazon"

        prezzo_attuale = None
        p_elem = soup.find("span", {"class": "a-price", "data-a-size": "xl"}) or soup.find("span", {"class": "a-price", "data-a-size": "l"})
        if p_elem:
            off_elem = p_elem.find("span", class_="a-offscreen")
            if off_elem: prezzo_attuale = off_elem.get_text().replace("€", "").strip().replace(".", ",")

        if not prezzo_attuale:
            p_apex = soup.find("span", class_="apexPriceToPay")
            if p_apex:
                off_elem = p_apex.find("span", class_="a-offscreen")
                if off_elem: prezzo_attuale = off_elem.get_text().replace("€", "").strip().replace(".", ",")

        if not prezzo_attuale: return None

        p_att_num = float(prezzo_attuale.replace(',', '.'))
        sconto = 0
        prezzo_precedente = None
        
        strike_elem = soup.find("span", class_="a-text-strike") or soup.find("span", {"id": "listPrice"})
        if strike_elem:
            off_strike = strike_elem.find("span", class_="a-offscreen")
            val_strike = off_strike.get_text() if off_strike else strike_elem.get_text()
            try:
                p_prec_num = float(re.sub(r'[^\d,]', '', val_strike).replace(',', '.'))
                if p_prec_num > p_att_num:
                    sconto = int(round(((p_prec_num - p_att_num) / p_prec_num) * 100))
                    prezzo_precedente = f"{p_prec_num:.2f}".replace(".", ",")
            except: pass

        img_elem = soup.find("img", {"id": "landingImage"}) or soup.find("img", {"id": "imgBlkFront"})
        img_url = img_elem["src"] if img_elem else ""

        return {"asin": asin, "titolo": titolo, "prezzo_attuale": prezzo_attuale, "prezzo_precedente": prezzo_precedente, "sconto": sconto, "immagine_url": img_url}
    except Exception as e:
        print(f"[ERRORE SCRAPING]: {e}")
        return None

# --- GENERAZIONE IMMAGINE (CON CENTRATURA CORRETTA) ---
def crea_immagine(prodotto):
    cairosvg.svg2png(url=str(SVG_TEMPLATE_PATH), write_to=str(OUTPUT_PATH))
    base_img = Image.open(OUTPUT_PATH).convert("RGBA")
    draw = ImageDraw.Draw(base_img)

    # Font dimensioni ottimizzate
    font_titolo = carica_font_locale(38)
    font_patt = carica_font_locale(85)
    font_pvec = carica_font_locale(45)
    font_sconto = carica_font_locale(75)

    # Incolla Immagine
    if prodotto.get("immagine_url"):
        try:
            resp = requests.get(prodotto["immagine_url"], timeout=10)
            img_prod = Image.open(BytesIO(resp.content)).convert("RGBA")
            box_x, box_y = 25, 240
            box_w, box_h = 510, 770
            img_prod.thumbnail((box_w - 40, box_h - 40), Image.Resampling.LANCZOS)
            base_img.paste(img_prod, (box_x + (box_w - img_prod.width) // 2, box_y + (box_h - img_prod.height) // 2), img_prod)
        except: pass

    # Titolo (Centrato nel riquadro verde)
    titolo_txt = textwrap.fill(prodotto["titolo"][:50], width=17)
    draw.text((750, 260), titolo_txt, fill="white", font=font_titolo, anchor="mm", align="center", stroke_width=2, stroke_fill="black")

    # Prezzo Attuale (Centro perfetto del riquadro arancione grande)
    # Coordinate centro stimato: X=750, Y=550
    draw.text((750, 550), f"{prodotto['prezzo_attuale']} €", fill="#111111", font=font_patt, anchor="mm", stroke_width=1, stroke_fill="white")

    # Prezzo Vecchio (Centro perfetto della casella bianca)
    # Coordinate centro stimato: X=750, Y=720
    if prodotto.get("prezzo_precedente"):
        p_vec = f"{prodotto['prezzo_precedente']} €"
        draw.text((750, 720), p_vec, fill="#333333", font=font_pvec, anchor="mm", stroke_width=1, stroke_fill="white")
        # Linea sbarrata
        bbox = draw.textbbox((750, 720), p_vec, font=font_pvec, anchor="mm")
        draw.line([(bbox[0]-6, (bbox[1]+bbox[3])//2), (bbox[2]+6, (bbox[1]+bbox[3])//2)], fill="#CC0000", width=4)

    # Sconto (Centro perfetto della fascia in basso a destra)
    # Coordinate centro stimato: X=750, Y=860
    if prodotto.get("sconto") and prodotto["sconto"] > 0:
        draw.text((750, 860), f"-{prodotto['sconto']}%", fill="white", font=font_sconto, anchor="mm", stroke_width=3, stroke_fill="black")

    base_img.convert("RGB").save(OUTPUT_PATH, "PNG")
    return OUTPUT_PATH

# --- BOT TELEGRAM ---
async def main():
    init_db()
    client = TelegramClient(StringSession(SESSION_STRING), TELEGRAM_API_ID, TELEGRAM_API_HASH)
    await client.start()

    @client.on(events.NewMessage())
    async def handler(event):
        chat = await event.get_chat()
        chat_username = (getattr(chat, 'username', '') or '').replace("@", "").lower()
        if chat_username not in [c.replace("@", "").lower() for c in CANALI_SPIA]: return

        asin = estrai_asin(event.message.text)
        if not asin or gia_inviato(asin): return

        p = await asyncio.to_thread(scarica_dettagli_amazon, asin)
        if not p: return

        segna_inviato(asin)
        foto = crea_immagine(p)
        url = f"https://www.amazon.it/dp/{p['asin']}?tag={AMAZON_TAG}"
        
        msg = f"🪵 **Il Tarlo ha colpito ancora!**\n\n📦 **{p['titolo']}**\n"
        if p['sconto'] > 0:
            msg += f"📉 **Sconto:** -{p['sconto']}%\n💰 ~~{p['prezzo_precedente']} €~~ ➔ **{p['prezzo_attuale']} €**\n\n"
        else:
            msg += f"💰 **Prezzo:** {p['prezzo_attuale']} €\n\n"
        msg += f"👉 **[ACQUISTA SUBITO]({url})**\n\n#IlTarloDelRisparmio"

        await bot.send_photo(chat_id=CANALE_CHAT_ID, photo=open(foto, "rb"), caption=msg, parse_mode="Markdown")

    await client.run_until_disconnected()

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT), daemon=True).start()
    asyncio.run(main())
