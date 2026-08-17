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

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CANALE_CHAT_ID = os.getenv("CANALE_CHAT_ID", "@TarloDelRisparmio")
AMAZON_TAG = os.getenv("AMAZON_TAG", "tarlodelrispa-21")

INTERVALLO_MINUTI = int(
    os.getenv("INTERVALLO_MINUTI", "5")
)

WEBHOOK_TIKTOK_URL = os.getenv(
    "WEBHOOK_TIKTOK_URL",
    ""
)

TELEGRAM_API_ID = int(
    os.getenv("TELEGRAM_API_ID", "0")
)

TELEGRAM_API_HASH = os.getenv(
    "TELEGRAM_API_HASH",
    ""
)

TELEGRAM_SESSION_STRING = os.getenv(
    "TELEGRAM_SESSION_STRING",
    ""
).strip()


CANALI_SPIA = [
    "sparky_offerte",
    "AstroHouse_Casa_Cucina",
    "ultimaofferta",
    "offerte5",
]


BASE_DIR = Path(__file__).resolve().parent

TEMPLATE_PATH = BASE_DIR / "template.png"
TEMPLATE_TIKTOK_PATH = BASE_DIR / "template_tiktok.png"

OUTPUT_PATH = BASE_DIR / "offerta_finale.png"
OUTPUT_TIKTOK_PATH = BASE_DIR / "offerta_tiktok.png"

DB_PATH = BASE_DIR / "offerte.db"


if not TELEGRAM_TOKEN:
    raise RuntimeError(
        "Variabile TELEGRAM_TOKEN non configurata."
    )


bot = Bot(token=TELEGRAM_TOKEN)
app = Flask(__name__)


# ============================================================
# DATABASE SQLITE
# ============================================================

def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
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
            """
        )

        conn.commit()


def prodotto_inviato_recentemente(asin, ore=24):
    try:
        with sqlite3.connect(DB_PATH) as conn:
            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT inviato_il
                FROM prodotti
                WHERE asin = ?
                """,
                (asin,),
            )

            row = cursor.fetchone()

            if not row:
                return False

            inviato_il = row[0]

            # Se è presente nel DB ma non ancora inviato,
            # lo consideriamo già in coda.
            if inviato_il is None:
                return True

            inviato_dt = datetime.fromisoformat(
                inviato_il
            )

            return (
                datetime.now() - inviato_dt
                < timedelta(hours=ore)
            )

    except Exception as e:
        print(
            f"[DB ERRORE CONTROLLO DUPLICATO] "
            f"{asin}: {e}"
        )

        return False


def aggiungi_prodotto_db(prodotto):
    asin = prodotto["asin"]

    if prodotto_inviato_recentemente(
        asin,
        ore=24,
    ):
        print(
            f"[DB] ASIN {asin} già presente "
            f"o inviato nelle ultime 24 ore."
        )

        return False

    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO prodotti
                (
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
                """,
                (
                    asin,
                    prodotto["titolo"],
                    prodotto.get("sconto", 0),
                    prodotto["prezzo_attuale"],
                    prodotto["prezzo_precedente"],
                    prodotto.get(
                        "immagine_url",
                        "",
                    ),
                    datetime.now().isoformat(),
                ),
            )

            conn.commit()

        print(
            f"[DB] Prodotto aggiunto in coda: "
            f"{asin}"
        )

        return True

    except Exception as e:
        print(
            f"[DB ERRORE AGGIUNTA] "
            f"{asin}: {e}"
        )

        traceback.print_exc()

        return False


def ottieni_prossimo_prodotto():
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.row_factory = sqlite3.Row

            cursor = conn.cursor()

            cursor.execute(
                """
                SELECT *
                FROM prodotti
                WHERE inviato_il IS NULL
                ORDER BY inserito_il ASC
                LIMIT 1
                """
            )

            row = cursor.fetchone()

            return (
                dict(row)
                if row
                else None
            )

    except Exception as e:
        print(
            f"[DB ERRORE LETTURA CODA] {e}"
        )

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
                    asin,
                ),
            )

            conn.commit()

        print(
            f"[DB] ASIN {asin} segnato "
            f"come inviato."
        )

    except Exception as e:
        print(
            f"[DB ERRORE SEGNA INVIATO] "
            f"{asin}: {e}"
        )


# ============================================================
# ESTRAZIONE ASIN
# ============================================================

def estrai_asin(testo):
    if not testo:
        return None

    match = re.search(
        r"/(?:dp|gp/product)/([A-Z0-9]{10})",
        testo,
        flags=re.IGNORECASE,
    )

    if match:
        return match.group(1).upper()

    match = re.search(
        r"\b(B[0-9A-Z]{9})\b",
        testo,
        flags=re.IGNORECASE,
    )

    if match:
        return match.group(1).upper()

    return None


# ============================================================
# SCRAPER AMAZON
# ============================================================

def converti_prezzo_testo(testo):
    if not testo:
        return None

    testo = testo.replace(
        "\xa0",
        " ",
    )

    match = re.search(
        r"(\d{1,4}(?:[.\s]\d{3})*,\d{2})",
        testo,
    )

    if not match:
        match = re.search(
            r"(\d+[.,]\d{2})",
            testo,
        )

    if not match:
        return None

    valore = match.group(1)

    valore = valore.replace(
        " ",
        "",
    )

    return valore


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


def scarica_dettagli_amazon(asin):
    url = (
        f"https://www.amazon.it/dp/{asin}"
    )

    headers = {
        "User-Agent": (
            "Mozilla/5.0 "
            "(Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 "
            "(KHTML, like Gecko) "
            "Chrome/126.0.0.0 "
            "Safari/537.36"
        ),
        "Accept-Language": (
            "it-IT,it;q=0.9,en;q=0.7"
        ),
        "Accept": (
            "text/html,"
            "application/xhtml+xml,"
            "application/xml;q=0.9,"
            "image/avif,"
            "image/webp,"
            "*/*;q=0.8"
        ),
        "Connection": "keep-alive",
    }

    try:
        response = requests.get(
            url,
            headers=headers,
            timeout=20,
        )

        if response.status_code != 200:
            print(
                f"[AMAZON] HTTP "
                f"{response.status_code} "
                f"per {asin}"
            )

            return None

        soup = BeautifulSoup(
            response.text,
            "html.parser",
        )


        # ----------------------------------------------------
        # TITOLO
        # ----------------------------------------------------

        titolo_elem = soup.find(
            "span",
            id="productTitle",
        )

        if not titolo_elem:
            print(
                f"[AMAZON] Titolo non trovato "
                f"per {asin}"
            )

            return None

        titolo = (
            titolo_elem
            .get_text(
                " ",
                strip=True,
            )
        )


        # ----------------------------------------------------
        # PREZZO ATTUALE
        # ----------------------------------------------------

        prezzo_attuale = None

        selettori_prezzo = [
            "#corePrice_feature_div "
            ".a-price .a-offscreen",

            "#corePriceDisplay_desktop_feature_div "
            ".a-price .a-offscreen",

            ".priceToPay .a-offscreen",

            ".apexPriceToPay .a-offscreen",

            ".a-price .a-offscreen",
        ]

        for selettore in selettori_prezzo:
            elemento = soup.select_one(
                selettore
            )

            if elemento:
                prezzo_attuale = (
                    converti_prezzo_testo(
                        elemento.get_text(
                            " ",
                            strip=True,
                        )
                    )
                )

                if prezzo_attuale:
                    break


        if not prezzo_attuale:
            whole = soup.find(
                "span",
                class_="a-price-whole",
            )

            fraction = soup.find(
                "span",
                class_="a-price-fraction",
            )

            if whole:
                parte_intera = re.sub(
                    r"[^\d]",
                    "",
                    whole.get_text(),
                )

                parte_decimale = (
                    re.sub(
                        r"[^\d]",
                        "",
                        fraction.get_text(),
                    )
                    if fraction
                    else "00"
                )

                if parte_intera:
                    prezzo_attuale = (
                        f"{parte_intera},"
                        f"{parte_decimale}"
                    )


        if not prezzo_attuale:
            print(
                f"[AMAZON] Prezzo attuale "
                f"non trovato per {asin}"
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

        candidati_prec = []

        selettori_prec = [
            ".basisPrice .a-offscreen",

            ".a-text-price .a-offscreen",

            ".a-price.a-text-price "
            ".a-offscreen",

            "#listPrice",

            "#priceblock_ourprice",
        ]

        for selettore in selettori_prec:
            elementi = soup.select(
                selettore
            )

            for elemento in elementi:
                testo_prezzo = (
                    elemento.get_text(
                        " ",
                        strip=True,
                    )
                )

                prezzo = (
                    converti_prezzo_testo(
                        testo_prezzo
                    )
                )

                valore = prezzo_float(
                    prezzo
                )

                if (
                    prezzo
                    and valore
                    and valore
                    > prezzo_attuale_num
                ):
                    candidati_prec.append(
                        (
                            valore,
                            prezzo,
                        )
                    )


        # Cerca ulteriori prezzi barrati
        for elemento in soup.find_all(
            "span",
            class_=re.compile(
                r"a-text-price|basisPrice"
            ),
        ):
            testo = elemento.get_text(
                " ",
                strip=True,
            )

            prezzo = converti_prezzo_testo(
                testo
            )

            valore = prezzo_float(
                prezzo
            )

            if (
                prezzo
                and valore
                and valore
                > prezzo_attuale_num
            ):
                candidati_prec.append(
                    (
                        valore,
                        prezzo,
                    )
                )


        if candidati_prec:
            candidati_prec.sort(
                key=lambda x: x[0],
                reverse=True,
            )

            prezzo_precedente = (
                candidati_prec[0][1]
            )

        else:
            prezzo_precedente = (
                prezzo_attuale
            )


        # ----------------------------------------------------
        # SCONTO
        # ----------------------------------------------------

        prezzo_prec_num = prezzo_float(
            prezzo_precedente
        )

        if (
            prezzo_prec_num
            and prezzo_prec_num
            > prezzo_attuale_num
        ):
            sconto = int(
                round(
                    (
                        (
                            prezzo_prec_num
                            - prezzo_attuale_num
                        )
                        / prezzo_prec_num
                    )
                    * 100
                )
            )

        else:
            sconto = 0


        # ----------------------------------------------------
        # IMMAGINE
        # ----------------------------------------------------

        immagine_url = ""

        immagine = soup.find(
            "img",
            id="landingImage",
        )

        if not immagine:
            immagine = soup.find(
                "img",
                id="imgBlkFront",
            )

        if immagine:
            immagine_url = (
                immagine.get(
                    "data-old-hires"
                )
                or immagine.get("src")
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
            "prezzo_attuale": (
                prezzo_attuale
            ),
            "prezzo_precedente": (
                prezzo_precedente
            ),
            "sconto": sconto,
            "immagine_url": immagine_url,
        }


    except Exception as e:
        print(
            f"[ERRORE SCRAPING {asin}] "
            f"{e}"
        )

        traceback.print_exc()

        return None


# ============================================================
# CANALE SPIA TELEGRAM
# ============================================================

async def avvia_canale_spia():
    if not TELEGRAM_SESSION_STRING:
        print(
            "[SPIA] TELEGRAM_SESSION_STRING "
            "non configurata. "
            "Canale spia disattivato."
        )

        return


    if not TELEGRAM_API_ID:
        print(
            "[SPIA] TELEGRAM_API_ID "
            "non configurato."
        )

        return


    if not TELEGRAM_API_HASH:
        print(
            "[SPIA] TELEGRAM_API_HASH "
            "non configurato."
        )

        return


    client = TelegramClient(
        StringSession(
            TELEGRAM_SESSION_STRING
        ),
        TELEGRAM_API_ID,
        TELEGRAM_API_HASH,
    )


    try:
        print(
            "[SPIA] Connessione Telegram..."
        )

        await asyncio.wait_for(
            client.connect(),
            timeout=20,
        )


        autorizzato = (
            await client.is_user_authorized()
        )

        if not autorizzato:
            print(
                "[SPIA] Sessione Telegram "
                "non autorizzata."
            )

            await client.disconnect()

            return


        canali_validi = []


        for canale in CANALI_SPIA:
            try:
                entity = await client.get_entity(
                    canale
                )

                canali_validi.append(
                    entity
                )

                print(
                    f"[SPIA] @{canale} OK"
                )

            except Exception as e:
                print(
                    f"[SPIA WARNING] "
                    f"@{canale}: {e}"
                )


        if not canali_validi:
            print(
                "[SPIA] Nessun canale "
                "valido."
            )

            await client.disconnect()

            return


        print(
            f"[SPIA] Monitoraggio attivo "
            f"su {len(canali_validi)} "
            f"canali."
        )


        @client.on(
            events.NewMessage(
                chats=canali_validi
            )
        )
        async def gestisci_nuovo_messaggio(
            event
        ):
            try:
                testo = (
                    event.message.message
                    or ""
                )


                urls = re.findall(
                    r"https?://[^\s<>]+",
                    testo,
                )


                # URL presenti nei pulsanti
                if event.message.buttons:
                    for row in (
                        event.message.buttons
                    ):
                        for btn in row:
                            url_btn = getattr(
                                btn,
                                "url",
                                None,
                            )

                            if url_btn:
                                urls.append(
                                    url_btn
                                )


                # Cerca anche direttamente
                # un ASIN nel testo
                asin_diretto = estrai_asin(
                    testo
                )

                if asin_diretto:
                    urls.insert(
                        0,
                        asin_diretto,
                    )


                asin_trovato = None


                for elemento in urls:
                    asin = estrai_asin(
                        elemento
                    )

                    if not asin:
                        # elemento potrebbe essere
                        # già un ASIN
                        asin = estrai_asin(
                            str(elemento)
                        )

                    if asin:
                        asin_trovato = asin

                        break


                if not asin_trovato:
                    return


                print(
                    f"[SPIA] ASIN trovato: "
                    f"{asin_trovato}"
               
