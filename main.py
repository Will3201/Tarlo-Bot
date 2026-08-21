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
    "provawill32",
    "scontierrati",
    "tempodisconti",
    "offertedale"
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

# --- HELPER: CONVERSIONE PREZZI ROBUSTA ---
def parse_prezzo(testo):
    if not testo: return None
    t = testo.replace("€", "").strip()
    t = re.sub(r'[^\d.,]', '', t)
    if not t: return None
    
    if ',' in t:
        t = t.replace('.', '').replace(',', '.')
    else:
        if t.count('.') > 1:
            t = t.replace('.', '')
        elif t.count('.') == 1:
            parts = t.split('.')
            if len(parts[1]) == 3:
                t = t.replace('.', '')
    try:
        return float(t)
    except:
        return None

# --- HELPER: CENTRATURA TESTO PRECISA ---
def draw_centrato(draw, center_x, center_y, testo, font, fill, stroke_width=0, stroke_fill=None, align="center"):
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

# --- ESTRAZIONE ASIN (multipli per messaggio) ---
def estrai_tutti_asin(testo):
    """Estrae tutti gli ASIN presenti in un messaggio, gestendo sia link diretti
    Amazon (con /dp/ o /gp/product/) sia link accorciati (es. amzlink.to, amzn.to).
    Ritorna una lista di ASIN unici, nell'ordine in cui appaiono nel testo."""
    if not testo: return []

    trovati = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept-Language": "it-IT,it;q=0.9",
    }

    # 1) ASIN diretti già presenti nel testo (link non accorciati)
    for m in re.finditer(r'/(?:dp|gp/product)/([A-Z0-9]{10})', testo):
        asin = m.group(1)
        if asin not in trovati:
            trovati.append(asin)

    # 2) Tutti gli URL nel messaggio: risolvo quelli che sembrano shortlink
    urls = re.findall(r'https?://[^\s]+', testo)
    for url in urls:
        # Se l'URL contiene già l'ASIN, l'ho già preso al punto 1: salto
        if re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', url):
            continue
        try:
            # GET invece di HEAD: molti shortener (incluso amzlink.to) non
            # rispondono correttamente a HEAD o usano redirect via meta-refresh
            res = requests.get(url, allow_redirects=True, timeout=8, headers=headers, stream=True)
            res.close()
            print(f"[DEBUG LINK] {url} -> status={res.status_code} -> url_finale={res.url}")
            match_redirect = re.search(r'/(?:dp|gp/product)/([A-Z0-9]{10})', res.url)
            if match_redirect:
                asin = match_redirect.group(1)
                if asin not in trovati:
                    trovati.append(asin)
            else:
                print(f"[DEBUG LINK] Nessun ASIN nell'URL finale: {res.url}")
        except Exception as e:
            print(f"[ERRORE RISOLUZIONE LINK] {url}: {e}")
            continue

    return trovati

# --- SCRAPER POTENZIATO ---
def scarica_dettagli_amazon(asin):
    url = f"https://www.amazon.it/dp/{asin}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept-Language": "it-IT,it;q=0.9"}
    try:
        res = requests.get(url, headers=headers, timeout=12)
        print(f"[DEBUG SCRAPER] {asin} -> status_code={res.status_code}, lunghezza_html={len(res.text)}")
        if res.status_code != 200:
            print(f"[DEBUG SCRAPER] {asin} -> status non 200, primi 300 char risposta: {res.text[:300]!r}")
            return None
        soup = BeautifulSoup(res.text, "html.parser")

        # Rilevo pagine di blocco/captcha di Amazon
        if soup.find("form", {"action": re.compile("validateCaptcha")}) or "Inserisci i caratteri" in res.text or "automated access" in res.text.lower():
            print(f"[DEBUG SCRAPER] {asin} -> rilevata pagina CAPTCHA/blocco anti-bot di Amazon")
            return None

        titolo_elem = soup.find("span", {"id": "productTitle"})
        titolo = titolo_elem.get_text().strip() if titolo_elem else "Prodotto Amazon"
        print(f"[DEBUG SCRAPER] {asin} -> titolo trovato: {titolo_elem is not None} ('{titolo[:50]}')")

        prezzo_attuale_str = None
        p_elem = soup.find("span", {"class": "a-price", "data-a-size": "xl"}) or soup.find("span", {"class": "a-price", "data-a-size": "l"})
        if p_elem:
            off_elem = p_elem.find("span", class_="a-offscreen")
            if off_elem: prezzo_attuale_str = off_elem.get_text()

        if not prezzo_attuale_str:
            p_apex = soup.find("span", class_="apexPriceToPay")
            if p_apex:
                off_elem = p_apex.find("span", class_="a-offscreen")
                if off_elem: prezzo_attuale_str = off_elem.get_text()

        p_att_num = parse_prezzo(prezzo_attuale_str)
        print(f"[DEBUG SCRAPER] {asin} -> prezzo_attuale_str={prezzo_attuale_str!r}, p_att_num={p_att_num}")
        if not p_att_num:
            print(f"[DEBUG SCRAPER] {asin} -> NESSUN PREZZO TROVATO -> return None")
            return None
        prezzo_attuale = f"{p_att_num:.2f}".replace(".", ",")

        sconto = 0
        prezzo_precedente = None

        strike_elem = (
            soup.find("span", class_="a-text-strike") or 
            soup.find("span", {"id": "listPrice"}) or
            soup.find("span", class_="basisPrice")
        )
        
        val_strike = None
        if strike_elem:
            off_strike = strike_elem.find("span", class_="a-offscreen")
            val_strike = off_strike.get_text() if off_strike else strike_elem.get_text()
        else:
            text_page = soup.get_text()
            m_mediano = re.search(r'Prezzo\s+(?:mediano|più\s+basso\s+ultimi\s+30gg)[:\s]*([\d.,]+)\s*€', text_page, re.IGNORECASE)
            if m_mediano:
                val_strike = m_mediano.group(1)

        if val_strike:
            p_prec_num = parse_prezzo(val_strike)
            if p_prec_num and p_prec_num > p_att_num:
                sconto = int(round(((p_prec_num - p_att_num) / p_prec_num) * 100))
                prezzo_precedente = f"{p_prec_num:.2f}".replace(".", ",")

        img_elem = soup.find("img", {"id": "landingImage"}) or soup.find("img", {"id": "imgBlkFront"})
        img_url = img_elem["src"] if img_elem else ""

        return {"asin": asin, "titolo": titolo, "prezzo_attuale": prezzo_attuale, "prezzo_precedente": prezzo_precedente, "sconto": sconto, "immagine_url": img_url}
    except Exception as e:
        print(f"[ERRORE SCRAPING]: {e}")
        return None

# --- GENERAZIONE IMMAGINE ---
def crea_immagine(prodotto):
    cairosvg.svg2png(url=str(SVG_TEMPLATE_PATH), write_to=str(OUTPUT_PATH))
    base_img = Image.open(OUTPUT_PATH).convert("RGBA")
    draw = ImageDraw.Draw(base_img)

    font_titolo = carica_font_locale(26)
    font_patt = carica_font_locale(92)
    font_pvec = carica_font_locale(36)
    font_sconto = carica_font_locale(55)

    if prodotto.get("immagine_url"):
        try:
            resp = requests.get(prodotto["immagine_url"], timeout=10)
            img_prod = Image.open(BytesIO(resp.content)).convert("RGBA")
            box_x, box_y = 20, 155
            box_w, box_h = 480, 760

            # Margine azzerato per sfruttare al massimo lo spazio del box
            margine = 0
            img_prod.thumbnail((box_w - margine * 2, box_h - margine * 2), Image.Resampling.LANCZOS)

            base_img.paste(
                img_prod,
                (box_x + (box_w - img_prod.width) // 2, box_y + (box_h - img_prod.height) // 2),
                img_prod
            )
        except: pass

    CENTRO_X = 797
    Y_TITOLO = 291
    Y_PREZZO_ATTUALE = 557
    Y_PREZZO_VECCHIO = 781
    Y_SCONTO = 918

    titolo_txt = textwrap.fill(prodotto["titolo"][:55], width=22)
    draw_centrato(draw, CENTRO_X, Y_TITOLO, titolo_txt, font_titolo, "white",
                  stroke_width=2, stroke_fill="black")

    draw_centrato(draw, CENTRO_X, Y_PREZZO_ATTUALE, f"{prodotto['prezzo_attuale']} €", font_patt, "#111111",
                  stroke_width=1, stroke_fill="white")

    if prodotto.get("prezzo_precedente"):
        p_vec = f"{prodotto['prezzo_precedente']} €"
        bbox, _ = draw_centrato(draw, CENTRO_X, Y_PREZZO_VECCHIO, p_vec, font_pvec, "#333333",
                                 stroke_width=1, stroke_fill="white")
        w = bbox[2] - bbox[0]
        draw.line(
            [(CENTRO_X - w / 2 - 4, Y_PREZZO_VECCHIO), (CENTRO_X + w / 2 + 4, Y_PREZZO_VECCHIO)],
            fill="#CC0000", width=4
        )

    if prodotto.get("sconto") and prodotto["sconto"] > 0:
        draw_centrato(draw, CENTRO_X, Y_SCONTO, f"-{prodotto['sconto']}%", font_sconto, "white",
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
        print(f"[DEBUG] Messaggio ricevuto dal canale: '{chat_username}'")

        if chat_username not in [c.replace("@", "").lower() for c in CANALI_SPIA]:
            print(f"[DEBUG] Canale '{chat_username}' NON è in CANALI_SPIA -> messaggio ignorato")
            return

        print(f"[DEBUG] Testo messaggio: {event.message.text!r}")
        asin_list = estrai_tutti_asin(event.message.text)
        print(f"[DEBUG] ASIN trovati: {asin_list}")
        if not asin_list:
            print("[DEBUG] Nessun ASIN estratto -> nulla da inviare")
            return

        for asin in asin_list:
            if gia_inviato(asin):
                print(f"[DEBUG] ASIN {asin} già inviato nelle ultime 24h -> salto")
                continue

            p = await asyncio.to_thread(scarica_dettagli_amazon, asin)
            if not p:
                print(f"[DEBUG] Scraping fallito per ASIN {asin}")
                continue

            segna_inviato(asin)
            foto = crea_immagine(p)
            url = f"https://www.amazon.it/dp/{p['asin']}?tag={AMAZON_TAG}"

            msg = f"🛒 *{p['titolo']}*\n\n"
            if p['sconto'] > 0:
                msg += f"💰 *{p['prezzo_attuale']} €* anziché {p['prezzo_precedente']} €! (-{p['sconto']}%)\n"
            else:
                msg += f"💰 *{p['prezzo_attuale']} €*\n"
            msg += f"👉 [Apri su Amazon]({url})\n\n"
            msg += "🪵 Segnalata da Il Tarlo del Risparmio\n#IlTarloDelRisparmio"

            await bot.send_photo(chat_id=CANALE_CHAT_ID, photo=open(foto, "rb"), caption=msg, parse_mode="Markdown")

    await client.run_until_disconnected()

if __name__ == "__main__":
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT), daemon=True).start()
    asyncio.run(main())
    
