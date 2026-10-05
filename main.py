import asyncio
import functools
import os
import re
import sqlite3
try:
    import psycopg2
    PSYCOPG2_DISPONIBILE = True
except ImportError:
    PSYCOPG2_DISPONIBILE = False
import sys
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
from product_layout import posiziona_prodotto
from text_layout import disegna_testi
from coupon_notice import estrai_coupon, avviso_coupon
from coupon_pricing import leggi_coupon_amazon, prezzo_con_coupon
from daily_dedup import DailyDedup
from prime_event import PrimeCampaign
from amazon_product import extract_product, normalizza_titolo
from category_recaps import CategoryRecaps, register_routes as register_recap_routes
from telegram import Bot
from telegram.error import NetworkError
from telegram.helpers import escape_markdown
from tarlo_daily import ArchivioOfferte, database_path, estrai_metriche
from daily_pipeline import DailyPipeline, register_routes
from free_daily import FreeDaily, register_free_routes
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# Forza il flush immediato di ogni print(): su Render/hosting cloud lo stdout
# viene spesso bufferizzato quando non è un terminale interattivo, causando
# righe di log mancanti o ritardate. Questo garantisce che ogni riga di debug
# compaia subito nei log, nell'ordine corretto.
print = functools.partial(print, flush=True)
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)

# --- CONFIGURAZIONE ---
TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CANALE_CHAT_ID = os.getenv("CANALE_CHAT_ID", "@TarloDelRisparmio")
AMAZON_TAG = os.getenv("AMAZON_TAG", "tarlodelris06-21")
TELEGRAM_API_ID = int(os.environ["TELEGRAM_API_ID"])
TELEGRAM_API_HASH = os.environ["TELEGRAM_API_HASH"]
SESSION_STRING = os.environ["TELEGRAM_SESSION_STRING"]

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
DB_PATH = database_path("offerte.db")
# Se impostata (es. connection string di Neon/Postgres), il bot usa un DB
# persistente che sopravvive ai deploy. Se assente, usa SQLite locale come
# prima (funziona, ma si azzera ad ogni deploy su Render free tier).
DATABASE_URL = os.getenv("DATABASE_URL", "")
if DATABASE_URL and not PSYCOPG2_DISPONIBILE:
    raise RuntimeError("DATABASE_URL impostata: installare psycopg2-binary")
USA_POSTGRES = bool(DATABASE_URL)

from telegram.request import HTTPXRequest

# Timeout generosi: l'istanza free di Render ha rete lenta e l'upload di una
# foto con i valori di default (5s) finisce spesso in telegram.error.TimedOut
_request = HTTPXRequest(
    connect_timeout=30.0,
    read_timeout=30.0,
    write_timeout=60.0,
    pool_timeout=30.0,
)
bot = Bot(token=TELEGRAM_TOKEN, request=_request)
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

# --- DATABASE (Postgres persistente se DATABASE_URL è configurata, altrimenti SQLite locale) ---
def init_db():
    if USA_POSTGRES:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS prodotti (
                        asin TEXT PRIMARY KEY,
                        inviato_il TIMESTAMP
                    )
                """)
            conn.commit()
        print("[DEBUG] Database: Postgres (persistente)")
    else:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS prodotti (
                    asin TEXT PRIMARY KEY,
                    inviato_il DATETIME
                )
            """)
        print(f"[DEBUG] Database: SQLite in {DB_PATH}; persistenza solo con disco montato")

def gia_inviato(asin):
    if USA_POSTGRES:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT inviato_il FROM prodotti WHERE asin = %s", (asin,))
                row = cur.fetchone()
        if not row:
            return False
        return datetime.now() - row[0] < timedelta(hours=24)
    else:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT inviato_il FROM prodotti WHERE asin = ?", (asin,))
            row = cursor.fetchone()
        if not row:
            return False
        inviato_dt = datetime.fromisoformat(row[0])
        return datetime.now() - inviato_dt < timedelta(hours=24)

def segna_inviato(asin):
    if USA_POSTGRES:
        with psycopg2.connect(DATABASE_URL) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    INSERT INTO prodotti (asin, inviato_il) VALUES (%s, %s)
                    ON CONFLICT (asin) DO UPDATE SET inviato_il = EXCLUDED.inviato_il
                """, (asin, datetime.now()))
            conn.commit()
    else:
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

# --- PULIZIA TITOLO: dal mega-titolo Amazon estraggo solo il nome essenziale ---
def pulisci_titolo(titolo):
    """Preserve model, size and quantity; image fitting handles visual length."""
    return normalizza_titolo(titolo)


def scarica_dettagli_amazon(asin, strict=False):
    """Both normal posts and recaps require verified product identity."""
    try:
        response = requests.get(f"https://www.amazon.it/dp/{asin}",
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
                     "Accept-Language": "it-IT,it;q=0.9"}, timeout=12)
        if response.status_code != 200:
            return None
        product = extract_product(response.text, asin)
        if product:
            print(f"[PRODUCT_VERIFIED] {asin}: prezzo={product['prezzo_attuale']}, riferimento={product['prezzo_precedente']}, tipo={product['tipo_riferimento']}", flush=True)
        else:
            print(f"[PRODUCT_SKIPPED] {asin}: identità, titolo o prezzo principale non verificabili", flush=True)
        return product
    except Exception as exc:
        print(f"[PRODUCT_SKIPPED] {asin}: {type(exc).__name__}", flush=True)
        return None

# --- GENERAZIONE IMMAGINE ---
def crea_immagine(prodotto, require_image=False):
    template_bytes = cairosvg.svg2png(url=str(SVG_TEMPLATE_PATH))
    base_img = Image.open(BytesIO(template_bytes)).convert("RGBA")
    draw = ImageDraw.Draw(base_img)

    if prodotto.get("immagine_url"):
        try:
            resp = requests.get(prodotto["immagine_url"], timeout=10)
            resp.raise_for_status()
            img_prod = Image.open(BytesIO(resp.content)).convert("RGBA")
            posiziona_prodotto(base_img, img_prod)
        except Exception:
            if require_image:
                raise
    elif require_image:
        raise ValueError("Immagine prodotto mancante")

    layout = disegna_testi(draw, prodotto)
    if not layout.get('titolo') or not layout.get('prezzo'):
        raise ValueError("Titolo o prezzo non disegnabili: immagine non pubblicata")

    result = BytesIO()
    base_img.convert("RGB").save(result, "PNG")
    return result.getvalue()

def frase_iniziale(sconto):
    """Sceglie una sola apertura, dalla fascia di sconto più alta."""
    sconto = float(sconto or 0)
    if sconto > 50:
        return "🔥 OFFERTA IN EVIDENZA! 🔥"
    if sconto >= 30:
        return "🌟 OFFERTA SONTUOSA! 🌟"
    if sconto > 15:
        return "🔥 OFFERTA SPECIALE! 🔥"
    return "🐛 Il Tarlo ha colpito ancora! 🐛"


# --- BOT TELEGRAM ---
async def main():
    init_db()
    archive = ArchivioOfferte()
    pipeline = DailyPipeline(archive, scarica_dettagli_amazon,
                             lambda p: crea_immagine(p, require_image=True),
                             os.getenv("DAILY_PREPARE_TIME", "18:00"))
    register_routes(app, pipeline)
    client = TelegramClient(StringSession(SESSION_STRING), TELEGRAM_API_ID, TELEGRAM_API_HASH)
    connected = asyncio.Event()
    recaps = None
    if os.getenv('CATEGORY_RECAP_ENABLED', 'true') == 'true':
        recaps = CategoryRecaps(client, bot, archive, scarica_dettagli_amazon, CANALE_CHAT_ID, AMAZON_TAG)
        register_recap_routes(app, recaps)
    if os.getenv('FREE_DAILY_ENABLED') == 'true':
        coordinator = FreeDaily(pipeline, client, CANALE_CHAT_ID, asyncio.get_running_loop(), connected)
        register_free_routes(app, coordinator)
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT), daemon=True).start()
    daily_task = asyncio.create_task(pipeline.run()) if os.getenv("DAILY_ENABLED") == "true" else None
    processing = set()  # Una sola istanza Telethon, come il servizio corrente.
    await client.start()
    daily_dedup = DailyDedup(client, CANALE_CHAT_ID)
    try:
        await daily_dedup.refresh()
        print(f'[ANTI DUPLICATI] Recuperati {len(daily_dedup.seen)} ASIN pubblicati oggi.')
    except Exception as exc:
        print(f'[ANTI DUPLICATI] Storico non disponibile: invii sospesi fino al recupero ({type(exc).__name__}).')
    connected.set()
    print('[DAILY] Client Telegram connesso')
    campaign_task = None
    recap_task = None
    if os.getenv('PRIME_EVENT_ENABLED', 'true') == 'true':
        campaign = PrimeCampaign(client, bot, archive, scarica_dettagli_amazon, CANALE_CHAT_ID, AMAZON_TAG, recaps_enabled=os.getenv('CATEGORY_RECAP_ENABLED', 'true') != 'true')
        campaign_task = asyncio.create_task(campaign.run())

    if recaps is not None:
        recap_task = asyncio.create_task(recaps.run())

    @client.on(events.NewMessage())
    async def handler(event):
        chat = await event.get_chat()
        chat_username = (getattr(chat, 'username', '') or '').replace("@", "").lower()
        print(f"[DEBUG] Messaggio ricevuto dal canale: '{chat_username}'")

        if not chat_username:
            # Log extra per capire da dove arriva davvero il messaggio
            chat_id = getattr(chat, 'id', None)
            chat_title = getattr(chat, 'title', None)
            chat_tipo = type(chat).__name__
            print(f"[DEBUG] Username vuoto -> chat_id={chat_id}, titolo='{chat_title}', tipo={chat_tipo}")

        if chat_username not in [c.replace("@", "").lower() for c in CANALI_SPIA]:
            print(f"[DEBUG] Canale '{chat_username}' NON è in CANALI_SPIA -> messaggio ignorato")
            return

        print(f"[DEBUG] Testo messaggio: {event.message.text!r}")
        asin_list = await asyncio.to_thread(estrai_tutti_asin, event.message.text)
        print(f"[DEBUG] ASIN trovati: {asin_list}")
        if not asin_list:
            print("[DEBUG] Nessun ASIN estratto -> nulla da inviare")
            return

        for asin in asin_list:
            if asin in processing or gia_inviato(asin):
                continue
            processing.add(asin)
            try:
                p = await asyncio.to_thread(scarica_dettagli_amazon, asin)
                if not p or p.get('disponibile') is not True:
                    continue
                coupon = p.get('coupon') or estrai_coupon(event.message.text, len(asin_list))
                if coupon:
                    p['coupon'] = coupon
                visual = prezzo_con_coupon(p)
                foto = await asyncio.to_thread(crea_immagine, visual, require_image=True)
                url = f"https://www.amazon.it/dp/{p['asin']}?tag={AMAZON_TAG}"
                title = escape_markdown(p['titolo'][:180], version=1)
                msg = f"{frase_iniziale(visual.get('sconto'))}\n\n🛒 *{title}*\n\n💰 *{visual['prezzo_attuale']} €*"
                msg += " con coupon attivato\n" if visual.get('coupon_applicato') else "\n"
                if visual.get('coupon_applicato'):
                    msg += f"Prezzo senza coupon: {p['prezzo_attuale']} € (-{visual['sconto']}% con coupon).\n"
                    msg += f"🎟️ Coupon: {coupon['importo']} {coupon['unita']} di sconto.\n✅ Spunta la casella coupon su Amazon.\nℹ️ Prezzo calcolato con coupon: verifica requisiti e totale al carrello.\n"
                elif p['sconto'] > 0:
                    msg += f"Riferimento Amazon: {p['prezzo_precedente']} € (-{p['sconto']}%).\n"
                if not visual.get('coupon_applicato'):
                    msg += avviso_coupon(coupon)
                msg += f"👉 [Apri su Amazon]({url})\n\n🪵 Il Tarlo del Risparmio\n#IlTarloDelRisparmio"
                try:
                    sent = await daily_dedup.send_once(
                        asin,
                        lambda: bot.send_photo(chat_id=CANALE_CHAT_ID, photo=BytesIO(foto),
                                               caption=msg, parse_mode="Markdown"),
                        segna_inviato,
                    )
                    if sent is None:
                        print(f"[ANTI DUPLICATI] {asin} già pubblicato o tentato oggi: salto.")
                        continue
                except NetworkError:
                    # Esito ambiguo: potrebbe essere stato pubblicato. Evitare retry ciechi.
                    segna_inviato(asin)
                    print(f"[INVIO INCERTO] {asin}: controllare il canale; nessun retry automatico")
                    continue
                segna_inviato(asin)
                try:
                    await asyncio.to_thread(archive.registra, p, sent.message_id, CANALE_CHAT_ID)
                except Exception as exc:
                    print(f"[DAILY ARCHIVIO FALLITO] {asin}: {type(exc).__name__}")
                print(f"[INVIATO] {asin}")
            except Exception as exc:
                print(f"[ERRORE PRODOTTO] {asin}: {type(exc).__name__}")
            finally:
                processing.discard(asin)

    try:
        await client.run_until_disconnected()
    finally:
        if recap_task:
            recap_task.cancel()
            await asyncio.gather(recap_task, return_exceptions=True)
        if campaign_task:
            campaign_task.cancel()
            await asyncio.gather(campaign_task, return_exceptions=True)
        if daily_task:
            daily_task.cancel()
            await asyncio.gather(daily_task, return_exceptions=True)

if __name__ == "__main__":
    asyncio.run(main())
