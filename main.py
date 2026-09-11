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
AMAZON_TAG = os.getenv("AMAZON_TAG", "tarlodelrispa-21")
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
    """I titoli Amazon seguono quasi sempre lo schema 'Nome prodotto, poi
    lista infinita di caratteristiche/dettagli marketing'. Taglio al primo
    separatore (virgola, o trattino se non c'è virgola) per tenere solo la
    parte che dice davvero cos'è il prodotto."""
    if not titolo:
        return titolo
    if ',' in titolo:
        base = titolo.split(',')[0].strip()
    elif ' - ' in titolo:
        base = titolo.split(' - ')[0].strip()
    elif ' – ' in titolo:
        base = titolo.split(' – ')[0].strip()
    else:
        base = titolo.strip()
    # Se il pezzo tagliato è troppo corto (es. solo il brand), includo
    # anche il pezzo successivo per non perdere informazione utile
    if len(base) < 12 and ',' in titolo:
        parti = titolo.split(',')
        base = ','.join(parti[:2]).strip()
    return base

# --- SCRAPER POTENZIATO ---
def scarica_dettagli_amazon(asin, strict=False):
    url = f"https://www.amazon.it/dp/{asin}"
    headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "Accept-Language": "it-IT,it;q=0.9"}
    try:
        res = requests.get(url, headers=headers, timeout=12)
        print(f"[DEBUG SCRAPER] {asin} -> status_code={res.status_code}, lunghezza_html={len(res.text)}")
        if res.status_code != 200:
            print(f"[DEBUG SCRAPER] {asin} -> status non 200, primi 300 char risposta: {res.text[:300]!r}")
            return None
        soup = BeautifulSoup(res.text, "html.parser")

        if strict:
            selected_asin = soup.select_one('input#ASIN, input[name="ASIN"]')
            if selected_asin is None or selected_asin.get("value", "").upper() != asin:
                return None

        # Rilevo pagine di blocco/captcha di Amazon
        if soup.find("form", {"action": re.compile("validateCaptcha")}) or "Inserisci i caratteri" in res.text or "automated access" in res.text.lower():
            print(f"[DEBUG SCRAPER] {asin} -> rilevata pagina CAPTCHA/blocco anti-bot di Amazon")
            return None

        titolo_elem = soup.find("span", {"id": "productTitle"})
        if strict and titolo_elem is None:
            return None
        titolo = titolo_elem.get_text().strip() if titolo_elem else "Prodotto Amazon"
        titolo = pulisci_titolo(titolo)
        print(f"[DEBUG SCRAPER] {asin} -> titolo trovato: {titolo_elem is not None} ('{titolo[:50]}')")

        def sembra_prezzo_valido(testo):
            """Scarta prezzi-per-unità tipo '0,25 €/100 ml' o '4,50€/kg':
            un prezzo reale del prodotto non contiene slash né unità di misura."""
            if not testo: return False
            if "/" in testo: return False
            if re.search(r'\b(ml|kg|gr|g|l|pz|cad)\b', testo, re.IGNORECASE): return False
            return bool(re.search(r'\d', testo))

        def e_prezzo_barrato(elem):
            for ancestor in [elem, *list(elem.parents)[:3]]:
                classes = ancestor.get("class") or []
                if set(classes) & {"a-text-price", "a-text-strike", "basisPrice"} or ancestor.get("data-a-strike") == "true":
                    return True
            return False

        prezzo_attuale_str = None

        # Prima individuo il CONTENITORE principale del box prezzo del prodotto,
        # per evitare di pescare prezzi di prodotti correlati/suggeriti altrove in pagina
        contenitore_prezzo = (
            soup.find("div", {"id": "corePriceDisplay_desktop_feature_div"})
            or soup.find("div", {"id": "apex_desktop"})
            or soup.find("div", {"id": "unifiedPrice_feature_div"})
            or soup.find("div", {"id": "centerCol"})  # colonna centrale come ultima risorsa
            or soup  # se proprio non trovo nulla, uso l'intera pagina (comportamento precedente)
        )

        if strict and contenitore_prezzo.get("id") not in (
            "corePriceDisplay_desktop_feature_div", "apex_desktop", "unifiedPrice_feature_div"
        ):
            return None

        # Debug: quale container ho effettivamente trovato?
        for _id in ["corePriceDisplay_desktop_feature_div", "apex_desktop", "unifiedPrice_feature_div", "centerCol"]:
            if contenitore_prezzo is soup.find("div", {"id": _id}):
                print(f"[DEBUG SCRAPER] {asin} -> contenitore prezzo usato: #{_id}")
                break
        else:
            print(f"[DEBUG SCRAPER] {asin} -> contenitore prezzo: NESSUNO trovato, uso pagina intera")

        # Nota: il controllo su "Attualmente non disponibile" è stato rimosso
        # perché quella stringa compare nell'HTML anche di prodotti disponibili
        # (varianti/correlati), dando sempre falsi positivi.

        def e_prezzo_per_unita(elem):
            """Riconosce il prezzo-per-unità (es. '0,25€ /100 ml'). Il testo dentro
            a-offscreen è solo '0,25€', mentre '/100 ml' sta in un elemento vicino.
            IMPORTANTE: guardo solo il genitore diretto e i fratelli immediati, non
            l'intero contenitore, altrimenti scarterei anche il prezzo corretto."""
            pattern_unita = r'/\s*\d*\s*(ml|l|kg|g|gr|cl|pz|conteggio|count)\b'

            # 1) Dimensione esplicitamente piccola = non è mai il prezzo principale
            if elem.get("data-a-size") in ("mini", "small"):
                return True

            # 2) Testo del genitore diretto, MA solo se è un involucro "stretto"
            #    (contiene un solo prezzo). Se il genitore è il contenitore generale
            #    che racchiude più prezzi, il suo testo includerebbe l'unità di un
            #    ALTRO prezzo e scarterebbe per errore quello giusto.
            genitore = elem.parent
            if genitore is not None:
                prezzi_nel_genitore = genitore.find_all("span", class_="a-offscreen")
                if len(prezzi_nel_genitore) <= 1:
                    if re.search(pattern_unita, genitore.get_text(" ", strip=True), re.IGNORECASE):
                        return True

            # 3) Fratelli immediatamente successivi: l'unità segue il prezzo.
            #    Salto però i fratelli che contengono un PROPRIO prezzo, perché
            #    sono blocchi-prezzo autonomi (es. il box del prezzo-per-unità)
            #    e non il semplice suffisso "/100 ml" di QUESTO prezzo.
            for fratello in list(elem.next_siblings)[:3]:
                if hasattr(fratello, "find_all") and fratello.find_all("span", class_="a-offscreen"):
                    continue
                testo_fratello = fratello.get_text(" ", strip=True) if hasattr(fratello, "get_text") else str(fratello).strip()
                if re.search(pattern_unita, testo_fratello, re.IGNORECASE):
                    return True

            return False

        # I selettori per DIMENSIONE (xl/l) per primi: su Amazon il prezzo principale
        # è sempre reso in grande, mentre il prezzo-per-unità è piccolo. Attenzione:
        # la classe 'priceToPay' viene usata da Amazon ANCHE per il prezzo-per-unità,
        # quindi non è affidabile come primo criterio (causava lo scambio 2,49€ -> 0,25€).
        candidati_prezzo = [
            ("span", {"class": "a-price", "data-a-size": "xl"}),
            ("span", {"class": "a-price", "data-a-size": "l"}),
            ("span", {"class": "apexPriceToPay"}),
            ("span", {"class": "priceToPay"}),
        ]
        def testo_prezzo_da_elemento(p_elem):
            """Estrae il prezzo da un elemento a-price. Amazon usa DUE strutture:
            1) il prezzo dentro <span class="a-offscreen">17,99 €</span>
            2) a-offscreen VUOTO e le cifre in a-price-whole + a-price-fraction
               (con a-price-decimal per la virgola). Questa seconda struttura è
               quella usata nelle pagine con sconto/offerta a tempo.
            In più c'è l'etichetta di accessibilità nel genitore, es.
            "2,49 € con 38 un risparmio percentuale", usata come ultima risorsa."""

            # Caso 1: a-offscreen popolato
            off = p_elem.find("span", class_="a-offscreen")
            if off:
                t = off.get_text().strip()
                if sembra_prezzo_valido(t):
                    return t

            # Caso 2: ricostruzione da a-price-whole + a-price-fraction
            whole = p_elem.find("span", class_="a-price-whole")
            if whole:
                # get_text() include già la virgola di a-price-decimal: "2,"
                parte_intera = re.sub(r'[^\d.,]', '', whole.get_text()).rstrip('.,')
                frazione = p_elem.find("span", class_="a-price-fraction")
                parte_dec = re.sub(r'[^\d]', '', frazione.get_text()) if frazione else ""
                if parte_intera:
                    return f"{parte_intera},{parte_dec}€" if parte_dec else f"{parte_intera}€"

            # Caso 3: etichetta di accessibilità nel genitore
            genitore = p_elem.parent
            if genitore is not None:
                etichetta = genitore.find("span", class_="aok-offscreen")
                if etichetta:
                    m = re.search(r'([\d.]*\d[,.]\d{2})\s*€', etichetta.get_text())
                    if m:
                        return f"{m.group(1)}€"

            return None

        for tag, attrs in candidati_prezzo:
            trovati_candidati = contenitore_prezzo.find_all(tag, attrs)
            print(f"[DEBUG SCRAPER] {asin} -> candidato {attrs}: {len(trovati_candidati)} elementi trovati")
            for p_elem in trovati_candidati:
                if e_prezzo_barrato(p_elem):
                    print(f"[DEBUG SCRAPER] {asin} -> scartato BARRATO in {attrs}")
                    continue
                if e_prezzo_per_unita(p_elem):
                    print(f"[DEBUG SCRAPER] {asin} -> scartato prezzo-per-unità in {attrs}")
                    continue
                testo_prezzo = testo_prezzo_da_elemento(p_elem)
                if not testo_prezzo or not sembra_prezzo_valido(testo_prezzo):
                    print(f"[DEBUG SCRAPER] {asin} -> scartato NON VALIDO: {testo_prezzo!r}")
                    continue
                prezzo_attuale_str = testo_prezzo
                break
            if prezzo_attuale_str:
                break

        # Fallback: scansiona gli span a-offscreen SOLO dentro il contenitore prezzo
        # (non più su tutta la pagina, per evitare prezzi di prodotti correlati)
        if not prezzo_attuale_str and not strict:
            tutti_offscreen = contenitore_prezzo.find_all("span", class_="a-offscreen")
            print(f"[DEBUG SCRAPER] {asin} -> fallback: {len(tutti_offscreen)} span a-offscreen nel container")
            for off_elem in tutti_offscreen:
                if e_prezzo_barrato(off_elem):
                    continue
                if e_prezzo_per_unita(off_elem):
                    continue
                testo_prezzo = off_elem.get_text().strip()
                if sembra_prezzo_valido(testo_prezzo):
                    prezzo_attuale_str = testo_prezzo
                    break

        p_att_num = parse_prezzo(prezzo_attuale_str)
        print(f"[DEBUG SCRAPER] {asin} -> prezzo_attuale_str={prezzo_attuale_str!r}, p_att_num={p_att_num}")
        if not p_att_num:
            print(f"[DEBUG SCRAPER] {asin} -> NESSUN PREZZO TROVATO -> return None")
            return None
        prezzo_attuale = f"{p_att_num:.2f}".replace(".", ",")

        sconto = 0
        prezzo_precedente = None

        strike_elem = (
            contenitore_prezzo.find("span", class_="a-text-strike") or
            contenitore_prezzo.find("span", {"id": "listPrice"}) or
            contenitore_prezzo.find("span", class_="basisPrice") or
            contenitore_prezzo.find("span", class_="a-text-price")
        )

        val_strike = None
        if strike_elem:
            off_strike = strike_elem.find("span", class_="a-offscreen")
            testo_strike = (off_strike.get_text() if off_strike else strike_elem.get_text()).strip()
            if sembra_prezzo_valido(testo_strike):
                val_strike = testo_strike

        if not val_strike:
            text_page = contenitore_prezzo.get_text(" ", strip=True)
            m_mediano = re.search(
                r'Prezzo\s+(?:consigliato|mediano|più\s+basso\s+ultimi\s+30gg)[:\s]*([\d.,]+)\s*€',
                text_page, re.IGNORECASE
            )
            if m_mediano:
                val_strike = m_mediano.group(1)

        print(f"[DEBUG SCRAPER] {asin} -> val_strike={val_strike!r}")

        if val_strike:
            p_prec_num = parse_prezzo(val_strike)
            if p_prec_num and p_prec_num > p_att_num:
                sconto = int(round(((p_prec_num - p_att_num) / p_prec_num) * 100))
                prezzo_precedente = f"{p_prec_num:.2f}".replace(".", ",")

        img_elem = soup.find("img", {"id": "landingImage"}) or soup.find("img", {"id": "imgBlkFront"})
        img_url = img_elem["src"] if img_elem else ""

        reference_text = contenitore_prezzo.get_text(" ", strip=True).lower()
        tipo_riferimento = "non_identificato"
        # La classificazione richiede una sola etichetta nel box principale.
        patterns = {
            "consigliato": r"prezzo\s+consigliato",
            "mediano": r"prezzo\s+mediano",
            "piu_basso_30gg": r"prezzo\s+più\s+basso.*?30",
            "precedente": r"prezzo\s+precedente",
        }
        labels = [key for key, pattern in patterns.items() if re.search(pattern, reference_text)]
        if len(labels) == 1:
            tipo_riferimento = labels[0]
        availability = soup.select_one("#availability")
        availability_text = availability.get_text(" ", strip=True).lower() if availability else ""
        unavailable = bool(re.search(r"non disponibile|unavailable|esaurito", availability_text))
        available = bool(soup.select_one("#add-to-cart-button, #buy-now-button")) and not unavailable
        return {"asin": asin, "titolo": titolo, "prezzo_attuale": prezzo_attuale,
                "prezzo_precedente": prezzo_precedente, "sconto": sconto,
                "immagine_url": img_url, "tipo_riferimento": tipo_riferimento,
                "disponibile": available, **estrai_metriche(soup)}
    except Exception as e:
        print(f"[ERRORE SCRAPING]: {e}")
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

    disegna_testi(draw, prodotto)

    result = BytesIO()
    base_img.convert("RGB").save(result, "PNG")
    return result.getvalue()

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
    if os.getenv('FREE_DAILY_ENABLED') == 'true':
        coordinator = FreeDaily(pipeline, client, CANALE_CHAT_ID, asyncio.get_running_loop(), connected)
        register_free_routes(app, coordinator)
    threading.Thread(target=lambda: app.run(host="0.0.0.0", port=PORT), daemon=True).start()
    daily_task = asyncio.create_task(pipeline.run()) if os.getenv("DAILY_ENABLED") == "true" else None
    processing = set()  # Una sola istanza Telethon, come il servizio corrente.
    await client.start()
    connected.set()
    print('[DAILY] Client Telegram connesso')

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
                if not p:
                    continue
                coupon = estrai_coupon(event.message.text, len(asin_list))
                if coupon:
                    p['coupon'] = coupon
                foto = await asyncio.to_thread(crea_immagine, p)
                url = f"https://www.amazon.it/dp/{p['asin']}?tag={AMAZON_TAG}"
                title = escape_markdown(p['titolo'][:180], version=1)
                msg = f"🐛 Il Tarlo ha colpito ancora!\n\n🛒 *{title}*\n\n💰 *{p['prezzo_attuale']} €*\n"
                if p['sconto'] > 0:
                    msg += f"Riferimento Amazon: {p['prezzo_precedente']} € (-{p['sconto']}%).\n"
                msg += avviso_coupon(coupon)
                msg += f"👉 [Apri su Amazon]({url})\n\n🪵 Il Tarlo del Risparmio\n#IlTarloDelRisparmio"
                try:
                    sent = await bot.send_photo(chat_id=CANALE_CHAT_ID, photo=BytesIO(foto),
                                                caption=msg, parse_mode="Markdown")
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
        if daily_task:
            daily_task.cancel()
            await asyncio.gather(daily_task, return_exceptions=True)

if __name__ == "__main__":
    asyncio.run(main())
