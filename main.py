import asyncio
import os
import re
import sqlite3
import textwrap
import threading
import urllib.request
from datetime import datetime, timedelta
from io import BytesIO
from pathlib import Path

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
    "SpesaScontata"
]

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = BASE_DIR / "template.png"
OUTPUT_PATH = BASE_DIR / "offerta_finale.png"
DB_PATH = BASE_DIR / "offerte.db"

FONT_DIR = BASE_DIR / "fonts"
FONT_PATH = FONT_DIR / "Montserrat-ExtraBold.ttf"

bot = Bot(token=TELEGRAM_TOKEN)
app = Flask(__name__)

# --- WEB SERVER PER RENDER ---
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

# --- GESTIONE FONT AUTOMATICA ---
def carica_font_extra_bold():
    """Garantisce il caricamento del font ExtraBold."""
    os.makedirs(FONT_DIR, exist_ok=True)
    if not FONT_PATH.exists():
        url = "https://cdn.jsdelivr.net/gh/google/fonts@main/ofl/montserrat/static/Montserrat-ExtraBold.ttf"
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response, open(FONT_PATH, 'wb') as out_file:
                out_file.write(response.read())
            print("[INFO] Font Montserrat-ExtraBold scaricato correttamente.")
        except Exception as e:
            print(f"[WARNING] Impossibile scaricare Montserrat-ExtraBold: {e}")
            
    return str(FONT_PATH) if FONT_PATH.exists() else None

# --- ESTRAZIONE ASIN & SROTOLAMENTO LINK BREVI ---
def estrai_asin(testo):
    if not testo:
        return None
        
    # 1. Cerca ASIN diretto (es. amazon.it/dp/B000000000)
    match = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', testo)
    if match:
        return match.group(1)
        
    # 2. Se trova un link corto (amzn.to, bit.ly, ecc.), segue il reindirizzamento
    urls = re.findall(r'https?://[^\s]+', testo)
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    
    for url in urls:
        try:
            res = requests.head(url, allow_redirects=True, timeout=5, headers=headers)
            match_redirect = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', res.url)
            if match_redirect:
                return match_redirect.group(1)
        except Exception as e:
            print(f"Errore risoluzione link corto {url}: {e}")
            
    return None

# --- SCRAPER AMAZON ---
def scarica_dettagli_amazon(asin):
    url = f"https://www.amazon.it/dp/{asin}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    try:
        res = requests.get(url, headers=headers, timeout=12)
        soup = BeautifulSoup(res.text, "html.parser")
        
        titolo_elem = soup.find("span", {"id": "productTitle"})
        titolo = titolo_elem.get_text().strip() if titolo_elem else "Prodotto Amazon"

        prezzo_attuale = None
        p_elem = soup.find("span", {"class": "a-price", "data-a-size": "xl"}) or soup.find("span", {"class": "a-price", "data-a-size": "l"})
        if p_elem:
            off_elem = p_elem.find("span", class_="a-offscreen")
            if off_elem:
                prezzo_attuale = off_elem.get_text().replace("€", "").strip().replace(".", ",")

        if not prezzo_attuale:
            p_apex = soup.find("span", class_="apexPriceToPay") or soup.find("div", {"id": "corePrice_feature_div"})
            if p_apex:
                off_elem = p_apex.find("span", class_="a-offscreen")
                if off_elem:
                    prezzo_attuale = off_elem.get_text().replace("€", "").strip().replace(".", ",")

        if not prezzo_attuale: 
            return None

        p_att_clean = prezzo_attuale.replace(' ', '').replace('\xa0', '')
        if ',' in p_att_clean and '.' in p_att_clean:
            p_att_clean = p_att_clean.replace('.', '').replace(',', '.')
        else:
            p_att_clean = p_att_clean.replace(',', '.')
            
        p_att_num = float(p_att_clean)

        sconto = 0
        prezzo_precedente = None
        p_strike = soup.find("span", class_="a-text-strike")
        
        if p_strike:
            val_strike = p_strike.get_text().replace("€", "").strip()
            p_prec_clean = val_strike.replace(' ', '').replace('\xa0', '')
            if ',' in p_prec_clean and '.' in p_prec_clean:
                p_prec_clean = p_prec_clean.replace('.', '').replace(',', '.')
            else:
                p_prec_clean = p_prec_clean.replace(',', '.')
                
            p_prec_num = float(p_prec_clean)
            
            if p_prec_num > p_att_num:
                sconto_calc = int(round(((p_prec_num - p_att_num) / p_prec_num) * 100))
                if 0 < sconto_calc <= 85:
                    prezzo_precedente = val_strike.replace(".", ",")
                    sconto = sconto_calc

        img_elem = (
            soup.find("img", {"id": "landingImage"}) or 
            soup.find("img", {"id": "imgBlkFront"}) or 
            soup.find("img", {"class": "a-dynamic-image"})
        )
        img_url = img_elem["src"] if img_elem else ""

        return {
            "asin": asin, 
            "titolo": titolo, 
            "prezzo_attuale": prezzo_attuale,
            "prezzo_precedente": prezzo_precedente,
            "sconto": sconto, 
            "immagine_url": img_url
        }
    except Exception as e:
        print(f"Errore scraping ASIN {asin}: {e}")
        return None

# --- GENERAZIONE GRAFICA PERFETTA E CENTRATA ---
def crea_immagine(prodotto):
    template = Image.open(TEMPLATE_PATH).convert("RGBA").resize((1080, 1080), Image.Resampling.LANCZOS)
    
    # Riquadro immagine prodotto a sinistra
    box_x, box_y = 23, 238
    box_w, box_h = 542, 778
    
    if prodotto.get("immagine_url"):
        try:
            resp = requests.get(prodotto["immagine_url"], timeout=10)
            img_prod = Image.open(BytesIO(resp.content)).convert("RGBA")
            img_prod.thumbnail((box_w - 60, box_h - 60), Image.Resampling.LANCZOS)
            
            offset_x = box_x + (box_w - img_prod.width) // 2
            offset_y = box_y + (box_h - img_prod.height) // 2
            
            template.paste(img_prod, (offset_x, offset_y), img_prod)
        except Exception as e:
            print(f"[ERRORE INCOLLA IMMAGINE]: {e}")

    draw = ImageDraw.Draw(template)
    font_file = carica_font_extra_bold()
    
    try:
        font_titolo = ImageFont.truetype(font_file, 24)        # Più piccolo per stare nell'etichetta verde
        font_prezzo_grande = ImageFont.truetype(font_file, 75) # Prezzo principale
        font_prezzo_vecchio = ImageFont.truetype(font_file, 42)# Prezzo barrato
        font_sconto = ImageFont.truetype(font_file, 70)        # Percentuale sconto
    except Exception as e:
        print(f"[WARNING] Fallback su font default: {e}")
        font_titolo = font_prezzo_grande = font_prezzo_vecchio = font_sconto = ImageFont.load_default()

    # Coordinata X centrale esatta per tutti i testi del lato destro
    X_CENTRO = 765

    # 1. TITOLO PRODOTTO (Cartellino Verde - Y: 405)
    titolo_breve = prodotto["titolo"][:45]  # Tronca il titolo per farlo entrare perfettamente
    righe = textwrap.wrap(titolo_breve, width=18)[:3] # Max 3 righe
    start_y = 405 - (len(righe) * 12)
    
    for i, riga in enumerate(righe):
        draw.text(
            (X_CENTRO, start_y + (i * 28)),
            riga,
            fill=(255, 255, 255, 255),
            stroke_width=1,
            stroke_fill=(0, 0, 0, 120),
            font=font_titolo,
            anchor="mm"
        )

    # 2. PREZZO ATTUALE (Box Arancione Grande - Y: 500)
    testo_prezzo = f"{prodotto['prezzo_attuale']} €"
    draw.text(
        (X_CENTRO, 500),
        testo_prezzo,
        fill=(255, 255, 255, 255),
        stroke_width=2,
        stroke_fill=(200, 80, 0, 255),
        font=font_prezzo_grande,
        anchor="mm"
    )

    # 3. PREZZO BARRATO (Box Bianco - Y: 572)
    if prodotto.get("prezzo_precedente"):
        testo_vecchio = f"{prodotto['prezzo_precedente']} €"
        draw.text(
            (X_CENTRO, 572),
            testo_vecchio,
            fill=(30, 30, 30, 255),
            font=font_prezzo_vecchio,
            anchor="mm"
        )
        
        # Linea rossa di sbarramento
        bbox = draw.textbbox((X_CENTRO, 572), testo_vecchio, font=font_prezzo_vecchio, anchor="mm")
        draw.line(
            [(bbox[0] - 6, bbox[1] + (bbox[3]-bbox[1])/2), (bbox[2] + 6, bbox[1] + (bbox[3]-bbox[1])/2)],
            fill=(220, 30, 30, 255),
            width=4
        )

    # 4. PERCENTUALE SCONTO (Banner In Basso Arancione - Y: 632)
    if prodotto.get("sconto") and prodotto["sconto"] > 0:
        testo_sconto = f"-{prodotto['sconto']}%"
        draw.text(
            (X_CENTRO, 632),
            testo_sconto,
            fill=(255, 255, 255, 255),
            stroke_width=2,
            stroke_fill=(180, 60, 0, 255),
            font=font_sconto,
            anchor="mm"
        )

    template.convert("RGB").save(OUTPUT_PATH, "PNG")
    return OUTPUT_PATH

# --- BOT TELEGRAM ---
async def main():
    init_db()
    client = TelegramClient(StringSession(SESSION_STRING), TELEGRAM_API_ID, TELEGRAM_API_HASH)
    await client.start()

    @client.on(events.NewMessage())
    async def handler(event):
        try:
            chat = await event.get_chat()
            chat_username = getattr(chat, 'username', '') or ''
            
            if chat_username.lower() in [c.lower() for c in CANALI_SPIA]:
                asin = estrai_asin(event.message.text)
                if asin and not gia_inviato(asin):
                    p = await asyncio.to_thread(scarica_dettagli_amazon, asin)
                    if p:
                        segna_inviato(asin)
                        foto = crea_immagine(p)
                        
                        didascalia = f"🪵 **{p['titolo']}**\n\n"
                        if p['sconto'] > 0 and p['prezzo_precedente']:
                            didascalia += f"📉 **Sconto:** -{p['sconto']}%\n"
                            didascalia += f"❌ **Invece di:** ~~{p['prezzo_precedente']} €~~\n"
                        didascalia += f"💰 **Prezzo speciale:** {p['prezzo_attuale']} €\n"
                        didascalia += f"👉 **Acquista ora:** https://amazon.it/dp/{p['asin']}?tag={AMAZON_TAG}\n\n"
                        didascalia += "#IlTarloDelRisparmio"

                        await bot.send_photo(
                            chat_id=CANALE_CHAT_ID, 
                            photo=open(foto, "rb"), 
                            caption=didascalia, 
                            parse_mode="Markdown"
                        )
        except Exception as e:
            print(f"Errore gestione messaggio: {e}")

    print("Bot Il Tarlo del Risparmio avviato...")
    await client.run_until_disconnected()

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT), daemon=True).start()
    asyncio.run(main())
    
