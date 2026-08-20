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

# --- HELPER: CENTRATURA TESTO PRECISA ---
def draw_centrato(draw, center_x, center_y, testo, font, fill, stroke_width=0, stroke_fill=None, align="center"):
    """
    Centra il testo (anche multi-riga) esattamente su (center_x, center_y),
    usando il bounding box reale del testo renderizzato (comprensivo di stroke),
    invece di affidarsi ad anchor='mm' che con stroke_width e testo multi-riga
    puo' risultare impreciso.
    """
    bbox = draw.multiline_textbbox(
        (0, 0), testo, font=font, stroke_width=stroke_width, align=align
    )
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x = center_x - w / 2 - bbox[0]
    y = center_y - h / 2 - bbox[1]
    draw.multiline_text(
        (x, y), testo, fill=fill, font=font,
        align=align, stroke_width=stroke_width, stroke_fill=stroke_fill
    )
    return bbox, (x, y)

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

# --- GENERAZIONE IMMAGINE (CENTRATURA CORRETTA + IMMAGINE PRODOTTO INGRANDITA) ---
def crea_immagine(prodotto):
    cairosvg.svg2png(url=str(SVG_TEMPLATE_PATH), write_to=str(OUTPUT_PATH))
    base_img = Image.open(OUTPUT_PATH).convert("RGBA")
    draw = ImageDraw.Draw(base_img)

    font_titolo = carica_font_locale(26)
    font_patt = carica_font_locale(75)
    font_pvec = carica_font_locale(36)
    font_sconto = carica_font_locale(55)

    # 1. Immagine Prodotto (Box Bianco Sinistra - Ingrandita ulteriormente)
    if prodotto.get("immagine_url"):
        try:
            resp = requests.get(prodotto["immagine_url"], timeout=10)
            img_prod = Image.open(BytesIO(resp.content)).convert("RGBA")
            box_x, box_y = 30, 170
            box_w, box_h = 460, 730

            # Margine ridotto a 6px (prima 20px) cosi' l'immagine occupa quasi
            # tutto lo spazio disponibile nel box bianco
            margine = 6
            img_prod.thumbnail((box_w - margine * 2, box_h - margine * 2), Image.Resampling.LANCZOS)

            base_img.paste(
                img_prod,
                (box_x + (box_w - img_prod.width) // 2, box_y + (box_h - img_prod.height) // 2),
                img_prod
            )
        except: pass

    # Centro orizzontale reale della colonna di destra, misurato sui box del
    # template (bordi da x=550 a x=1044 -> centro = 797). Il vecchio valore
    # 738 non corrispondeva al centro reale del box ed era la causa dello
    # spostamento a sinistra di tutti i testi.
    CENTRO_X = 797

    # 2. Titolo (Box Verde) - centratura precisa multi-riga
    titolo_txt = textwrap.fill(prodotto["titolo"][:55], width=22)
    draw_centrato(draw, CENTRO_X, 265, titolo_txt, font_titolo, "white",
                  stroke_width=2, stroke_fill="black")

    # 3. Prezzo Attuale (Box Arancione Grande)
    draw_centrato(draw, CENTRO_X, 510, f"{prodotto['prezzo_attuale']} €", font_patt, "#111111",
                  stroke_width=1, stroke_fill="white")

    # 4. Prezzo Vecchio (Box Grigio) + linea di sbarramento centrata sul testo reale
    if prodotto.get("prezzo_precedente"):
        p_vec = f"{prodotto['prezzo_precedente']} €"
        box_grigio_center_y = 755

        bbox, _ = draw_centrato(draw, CENTRO_X, box_grigio_center_y, p_vec, font_pvec, "#333333",
                                 stroke_width=1, stroke_fill="white")

        w = bbox[2] - bbox[0]
        draw.line(
            [(CENTRO_X - w / 2 - 4, box_grigio_center_y), (CENTRO_X + w / 2 + 4, box_grigio_center_y)],
            fill="#CC0000", width=4
        )

    # 5. Sconto (Box Arancione Basso)
    if prodotto.get("sconto") and prodotto["sconto"] > 0:
        box_arancione_basso_center_y = 860
        draw_centrato(draw, CENTRO_X, box_arancione_basso_center_y, f"-{prodotto['sconto']}%", font_sconto, "white",
                      stroke_width=2, stroke_fill="black")

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
