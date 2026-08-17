import asyncio
import html
import os
import re
import sqlite3
import threading
import traceback
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


# ============================================================
# CONFIGURAZIONE
# ============================================================

TELEGRAM_TOKEN = os.getenv(
    "TELEGRAM_TOKEN",
    "8670212259:AAFn_21_abtz4vL4WQ5TpekYby-hCnAjzeU"
)

CANALE_CHAT_ID = os.getenv(
    "CANALE_CHAT_ID",
    "@TarloDelRisparmio"
)

AMAZON_TAG = os.getenv(
    "AMAZON_TAG",
    "tarlodelrispa-21"
)

INTERVALLO_MINUTI = int(
    os.getenv("INTERVALLO_MINUTI", "5")
)

WEBHOOK_TIKTOK_URL = os.getenv(
    "WEBHOOK_TIKTOK_URL",
    "https://hook.eu1.make.com/sex4ialbchbtuuja1jzdo3yhgzef42dt"
)

TELEGRAM_API_ID = int(
    os.getenv("TELEGRAM_API_ID", "31134748")
)

TELEGRAM_API_HASH = os.getenv(
    "TELEGRAM_API_HASH",
    "ba4265cff56d0687c6c5171b47f76e02"
)

TELEGRAM_SESSION_STRING = os.getenv(
    "TELEGRAM_SESSION_STRING",
    ""
).strip()


CANALI_SPIA = [
    "sparky_offerte",
    "AstroHouse_Casa_Cucina",
    "ultimaofferta",
    "offerte5"
]


BASE_DIR = Path(__file__).resolve().parent

TEMPLATE_PATH = BASE_DIR / "template.png"
TEMPLATE_TIKTOK_PATH = BASE_DIR / "template_tiktok.png"

OUTPUT_PATH = BASE_DIR / "offerta_finale.png"
OUTPUT_TIKTOK_PATH = BASE_DIR / "offerta_tiktok.png"

DB_PATH = BASE_DIR / "offerte.db"


bot = Bot(token=TELEGRAM_TOKEN)
app = Flask(__name__)


# ============================================================
# DATABASE
# ============================================================

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS prodotti (
                asin TEXT PRIMARY KEY,
                titolo TEXT,
                sconto INTEGER,
                prezzo_attuale TEXT,
                prezzo_precedente TEXT,
                immagine_url TEXT,
                inserito_il TEXT,
                inviato_il TEXT
            )
        """)

        conn.commit()

    print("[DB] Database inizializzato.")


def prodotto_inviato_recentemente(asin, ore=24):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()

            cursor.execute(
                "SELECT inviato_il FROM prodotti WHERE asin = ?",
                (asin,)
            )

            row = cursor.fetchone()

        if not row:
            return False

        inviato_il = row[0]

        # Se esiste ma inviato_il è NULL,
        # significa che è già in coda.
        if inviato_il is None:
            return True

        inviato_dt = datetime.fromisoformat(inviato_il)

        return (
            datetime.now() - inviato_dt
            < timedelta(hours=ore)
        )

    except Exception as e:
        print(f"[DB] Errore controllo duplicato {asin}: {e}")
        return False


def aggiungi_prodotto_db(prodotto):
    asin = prodotto["asin"]

    if prodotto_inviato_recentemente(asin, 24):
        print(
            f"[DB] {asin} già presente/inviato recentemente."
        )
        return False

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                INSERT OR REPLACE INTO prodotti (
                    asin,
                    titolo,
                    sconto,
                    prezzo_attuale,
                    prezzo_precedente,
                    immagine_url,
                    inserito_il,
                    inviato_il
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, NULL)
            """, (
                asin,
                prodotto.get("titolo", ""),
                int(prodotto.get("sconto", 0) or 0),
                prodotto.get("prezzo_attuale", ""),
                prodotto.get("prezzo_precedente", ""),
                prodotto.get("immagine_url", ""),
                datetime.now().isoformat()
            ))

            conn.commit()

        print(f"[DB] Prodotto aggiunto in coda: {asin}")
        return True

    except Exception as e:
        print(f"[DB] Errore aggiunta {asin}: {e}")
        traceback.print_exc()
        return False


def ottieni_prossimo_prodotto():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row

            cursor = conn.cursor()

            cursor.execute("""
                SELECT *
                FROM prodotti
                WHERE inviato_il IS NULL
                ORDER BY inserito_il ASC
                LIMIT 1
            """)

            row = cursor.fetchone()

        return dict(row) if row else None

    except Exception as e:
        print(f"[DB] Errore lettura coda: {e}")
        traceback.print_exc()
        return None


def segna_inviato(asin):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                UPDATE prodotti
                SET inviato_il = ?
                WHERE asin = ?
                """,
                (
                    datetime.now().isoformat(),
                    asin
                )
            )

            conn.commit()

        print(f"[DB] {asin} segnato come inviato.")

    except Exception as e:
        print(f"[DB] Errore segna_inviato {asin}: {e}")
        traceback.print_exc()


# ============================================================
# ASIN
# ============================================================

def estrai_asin(testo):
    if not testo:
        return None

    match = re.search(
        r"/(?:dp|gp/product)/([A-Z0-9]{10})",
        testo,
        re.IGNORECASE
    )

    if match:
        return match.group(1).upper()

    match = re.search(
        r"\b(B[0-9A-Z]{9})\b",
        testo,
        re.IGNORECASE
    )

    if match:
        return match.group(1).upper()

    return None


# ============================================================
# PREZZI
# ============================================================

def estrai_prezzo(testo):
    if not testo:
        return None

    testo = testo.replace("\xa0", " ")

    match = re.search(
        r"(\d{1,4}(?:\.\d{3})*,\d{2})",
        testo
    )

    if not match:
        match = re.search(
            r"(\d+[.,]\d{2})",
            testo
        )

    if not match:
        return None

    return match.group(1).strip()


def prezzo_float(prezzo):
    if not prezzo:
        return None

    try:
        valore = (
            str(prezzo)
            .replace("€", "")
            .replace(" ", "")
            .replace(".", "")
            .replace(",", ".")
        )

        return float(valore)

    except Exception:
        return None


# ============================================================
# SCRAPER AMAZON
# ============================================================

def scarica_dettagli_amazon(asin):
    url = f"https://www.amazon.it/dp/{asin}"

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/126.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
        "Accept": (
            "text/html,application/xhtml+xml,"
            "application/xml;q=0.9,image/webp,*/*;q=0.8"
        )
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=20
        )

        if response.status_code != 200:
            print(
                f"[AMAZON] HTTP {response.status_code} "
                f"per {asin}"
            )
            return None

        soup = BeautifulSoup(
            response.text,
            "html.parser"
        )

        # ----------------------------------------------------
        # TITOLO
        # ----------------------------------------------------

        titolo_elem = soup.find(
            "span",
            id="productTitle"
        )

        if not titolo_elem:
            print(
                f"[AMAZON] Titolo non trovato per {asin}"
            )
            return None

        titolo = titolo_elem.get_text(
            " ",
            strip=True
        )

        # ----------------------------------------------------
        # PREZZO ATTUALE
        # ----------------------------------------------------

        prezzo_attuale = None

        selettori_prezzo = [
            "#corePrice_feature_div .a-price .a-offscreen",
            "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
            ".priceToPay .a-offscreen",
            ".apexPriceToPay .a-offscreen",
            ".a-price .a-offscreen"
        ]

        for selettore in selettori_prezzo:
            elemento = soup.select_one(selettore)

            if not elemento:
                continue

            prezzo = estrai_prezzo(
                elemento.get_text(" ", strip=True)
            )

            if prezzo:
                prezzo_attuale = prezzo
                break

        # Fallback whole + fraction
        if not prezzo_attuale:
            whole = soup.find(
                "span",
                class_="a-price-whole"
            )

            fraction = soup.find(
                "span",
                class_="a-price-fraction"
            )

            if whole:
                parte_intera = re.sub(
                    r"[^\d]",
                    "",
                    whole.get_text()
                )

                if fraction:
                    parte_decimale = re.sub(
                        r"[^\d]",
                        "",
                        fraction.get_text()
                    )
                else:
                    parte_decimale = "00"

                if parte_intera:
                    prezzo_attuale = (
                        f"{parte_intera},{parte_decimale}"
                    )

        if not prezzo_attuale:
            print(
                f"[AMAZON] Prezzo non trovato per {asin}"
            )
            return None

        prezzo_attuale_num = prezzo_float(
            prezzo_attuale
        )

        if prezzo_attuale_num is None:
            return None

        # ----------------------------------------------------
        # PREZZO PRECEDENTE
        # ----------------------------------------------------

        candidati = []

        selettori_precedenti = [
            ".basisPrice .a-offscreen",
            ".a-text-price .a-offscreen",
            ".a-price.a-text-price .a-offscreen",
            "#listPrice"
        ]

        for selettore in selettori_precedenti:
            elementi = soup.select(selettore)

            for elemento in elementi:
                testo = elemento.get_text(
                    " ",
                    strip=True
                )

                prezzo = estrai_prezzo(testo)
                valore = prezzo_float(prezzo)

                if (
                    prezzo
                    and valore is not None
                    and valore > prezzo_attuale_num
                ):
                    candidati.append(
                        (valore, prezzo)
                    )

        if candidati:
            candidati.sort(
                key=lambda x: x[0],
                reverse=True
            )

            prezzo_precedente = candidati[0][1]

        else:
            prezzo_precedente = prezzo_attuale

        # ----------------------------------------------------
        # SCONTO
        # ----------------------------------------------------

        prezzo_precedente_num = prezzo_float(
            prezzo_precedente
        )

        if (
            prezzo_precedente_num
            and prezzo_precedente_num > prezzo_attuale_num
        ):
            sconto = int(
                round(
                    (
                        (
                            prezzo_precedente_num
                            - prezzo_attuale_num
                        )
                        / prezzo_precedente_num
                    )
                    * 100
                )
            )
        else:
            sconto = 0

        # ----------------------------------------------------
        # IMMAGINE
        # ----------------------------------------------------

        img = (
            soup.find("img", id="landingImage")
            or soup.find("img", id="imgBlkFront")
        )

        immagine_url = ""

        if img:
            immagine_url = (
                img.get("data-old-hires")
                or img.get("src")
                or ""
            )

        print(
            f"[AMAZON] {asin} | "
            f"{prezzo_attuale}€ | "
            f"-{sconto}%"
        )

        return {
            "asin": asin,
            "titolo": titolo,
            "prezzo_attuale": prezzo_attuale,
            "prezzo_precedente": prezzo_precedente,
            "sconto": sconto,
            "immagine_url": immagine_url
        }

    except Exception as e:
        print(
            f"[AMAZON] Errore scraping {asin}: {e}"
        )
        traceback.print_exc()
        return None


# ============================================================
# CANALI SPIA
# ============================================================

async def avvia_canale_spia():
    if not TELEGRAM_SESSION_STRING:
        print(
            "[SPIA] TELEGRAM_SESSION_STRING assente. "
            "Canale spia disattivato."
        )
        return

    if not TELEGRAM_API_ID:
        print(
            "[SPIA] TELEGRAM_API_ID non configurato."
        )
        return

    if not TELEGRAM_API_HASH:
        print(
            "[SPIA] TELEGRAM_API_HASH non configurato."
        )
        return

    client = TelegramClient(
        StringSession(TELEGRAM_SESSION_STRING),
        TELEGRAM_API_ID,
        TELEGRAM_API_HASH
    )

    try:
        print("[SPIA] Connessione a Telegram...")

        await asyncio.wait_for(
            client.connect(),
            timeout=20
        )

        if not await client.is_user_authorized():
            print(
                "[SPIA] Sessione Telegram non autorizzata."
            )

            await client.disconnect()
            return

        canali_validi = []

        for canale in CANALI_SPIA:
            try:
                entity = await client.get_entity(
                    canale
                )

                canali_validi.append(entity)

                print(
                    f"[SPIA] @{canale} OK"
                )

            except Exception as e:
                print(
                    f"[SPIA] Errore @{canale}: {e}"
                )

        if not canali_validi:
            print(
                "[SPIA] Nessun canale valido."
            )

            await client.disconnect()
            return

        print(
            f"[SPIA] Monitoraggio attivo su "
            f"{len(canali_validi)} canali."
        )

        @client.on(
            events.NewMessage(
                chats=canali_validi
            )
        )
        async def gestisci_messaggio(event):
            try:
                testo = (
                    event.message.message
                    or ""
                )

                urls = re.findall(
                    r"https?://[^\s<>]+",
                    testo
                )

                # URL nei pulsanti Telegram
                if event.message.buttons:
                    for row in event.message.buttons:
                        for bottone in row:
                            url_bottone = getattr(
                                bottone,
                                "url",
                                None
                            )

                            if url_bottone:
                                urls.append(
                                    url_bottone
                                )

                # Prima cerca direttamente nel testo.
                asin = estrai_asin(testo)

                # Poi negli URL.
                if not asin:
                    for url in urls:
                        asin = estrai_asin(url)

                        if asin:
                            break

                if not asin:
                    return

                print(
                    f"[SPIA] ASIN trovato: {asin}"
                )

                duplicato = await asyncio.to_thread(
                    prodotto_inviato_recentemente,
                    asin,
                    24
                )

                if duplicato:
                    print(
                        f"[SPIA] {asin} già in coda "
                        f"o inviato recentemente."
                    )
                    return

                prodotto = await asyncio.to_thread(
                    scarica_dettagli_amazon,
                    asin
                )

                if not prodotto:
                    print(
                        f"[SPIA] Impossibile ottenere "
                        f"dettagli per {asin}"
                    )
                    return

                await asyncio.to_thread(
                    aggiungi_prodotto_db,
                    prodotto
                )

            except Exception as e:
                print(
                    f"[SPIA] Errore messaggio: {e}"
                )
                traceback.print_exc()

        await client.run_until_disconnected()

    except asyncio.CancelledError:
        print("[SPIA] Task cancellato.")

    except Exception as e:
        print(
            f"[SPIA] Errore generale: {e}"
        )
        traceback.print_exc()

    finally:
        try:
            if client.is_connected():
                await client.disconnect()
        except Exception:
            pass


# ============================================================
# FONT
# ============================================================

def carica_font(dimensione, grassetto=False):
    if grassetto:
        percorsi = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
        ]
    else:
        percorsi = [
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
            "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"
        ]

    for percorso in percorsi:
        if Path(percorso).exists():
            return ImageFont.truetype(
                percorso,
                dimensione
            )

    return ImageFont.load_default()


# ============================================================
# TESTO GRAFICO
# ============================================================

def spezza_testo(
    draw,
    testo,
    font,
    larghezza_massima
):
    parole = str(testo).split()

    righe = []
    riga = ""

    for parola in parole:
        prova = f"{riga} {parola}".strip()

        bbox = draw.textbbox(
            (0, 0),
            prova,
            font=font
        )

        larghezza = bbox[2] - bbox[0]

        if larghezza <= larghezza_massima:
            riga = prova
        else:
            if riga:
                righe.append(riga)

            riga = parola

    if riga:
        righe.append(riga)

    return righe


def centra_testo(
    draw,
    testo,
    box,
    font,
    colore
):
    x1, y1, x2, y2 = box

    bbox = draw.textbbox(
        (0, 0),
        testo,
        font=font
    )

    larghezza = bbox[2] - bbox[0]
    altezza = bbox[3] - bbox[1]

    x = (
        x1
        + ((x2 - x1) - larghezza) // 2
    )

    y = (
        y1
        + ((y2 - y1) - altezza) // 2
        - bbox[1]
    )

    draw.text(
        (x, y),
        testo,
        fill=colore,
    
