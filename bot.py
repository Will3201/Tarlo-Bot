import html
import io
import json
import logging
import os
import threading
from pathlib import Path

from config import TELEGRAM_TOKEN, CANALE_CHAT_ID, AMAZON_TAG, ADMIN_SECRET

import requests
from flask import Flask, jsonify, request
from PIL import Image, ImageDraw, ImageFont, ImageOps

BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = BASE_DIR / "template.png"
CATALOG_PATH = BASE_DIR / "catalogo.json"
OUTPUT_PATH = BASE_DIR / "offerta_finale.png"

TELEGRAM_TOKEN = TELEGRAM_TOKEN.strip()
CANALE_CHAT_ID = CANALE_CHAT_ID.strip()
AMAZON_TAG = AMAZON_TAG.strip()
ADMIN_SECRET = ADMIN_SECRET.strip()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
log = logging.getLogger("tarlo")

app = Flask(__name__)
publish_lock = threading.Lock()
next_index = 0

FALLBACK_CATALOG = [
    {
        "titolo": "Prodotto dimostrativo: sostituiscimi in catalogo.json",
        "categoria": "Offerte",
        "sconto": 25,
        "asin": "B000000000",
        "prezzo_attuale": "19,99 €",
        "prezzo_precedente": "26,99 €",
        "immagine_url": ""
    }
]


def require_config():
    missing = []
    if not TELEGRAM_TOKEN:
        missing.append("TELEGRAM_TOKEN")
    if not CANALE_CHAT_ID:
        missing.append("CANALE_CHAT_ID")
    if not ADMIN_SECRET:
        missing.append("ADMIN_SECRET")
    if missing:
        raise RuntimeError(
            "Mancano queste variabili su Render: " + ", ".join(missing)
        )


def load_catalog():
    """Non manda mai in crash il server: usa un catalogo di emergenza."""
    if not CATALOG_PATH.exists():
        log.warning("catalogo.json assente: uso il prodotto dimostrativo.")
        return FALLBACK_CATALOG

    try:
        data = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        log.error("catalogo.json non valido: %s", exc)
        return FALLBACK_CATALOG

    if not isinstance(data, list) or not data:
        log.error("catalogo.json deve contenere una lista non vuota.")
        return FALLBACK_CATALOG

    required = {
        "titolo", "categoria", "sconto", "asin",
        "prezzo_attuale", "prezzo_precedente", "immagine_url"
    }
    valid = []
    for item in data:
        if isinstance(item, dict) and required.issubset(item):
            valid.append(item)
        else:
            log.warning("Prodotto ignorato perché incompleto: %r", item)

    return valid or FALLBACK_CATALOG


def font(size, bold=False):
    candidates = (
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
         "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"]
        if bold else
        ["/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
         "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"]
    )
    for candidate in candidates:
        if Path(candidate).exists():
            return ImageFont.truetype(candidate, size)
    return ImageFont.load_default()


def fit_text(draw, text, box, max_size, min_size=24, bold=True, max_lines=3):
    x1, y1, x2, y2 = box
    width = x2 - x1
    height = y2 - y1

    for size in range(max_size, min_size - 1, -2):
        fnt = font(size, bold=bold)
        words = str(text).split()
        lines, current = [], ""

        for word in words:
            candidate = f"{current} {word}".strip()
            if draw.textbbox((0, 0), candidate, font=fnt)[2] <= width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word

        if current:
            lines.append(current)

        lines = lines[:max_lines]
        line_height = int(size * 1.18)
        if len(lines) * line_height <= height:
            return lines, fnt, line_height

    return [str(text)[:50]], font(min_size, bold=bold), int(min_size * 1.18)


def download_product_image(url):
    if not url:
        return None

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) "
            "AppleWebKit/537.36 Chrome/124 Safari/537.36"
        )
    }
    response = requests.get(url, headers=headers, timeout=20)
    response.raise_for_status()

    content_type = response.headers.get("content-type", "")
    if "image" not in content_type.lower():
        raise ValueError(f"L'URL non restituisce un'immagine: {content_type}")

    return Image.open(io.BytesIO(response.content)).convert("RGBA")


def create_offer_image(product):
    if TEMPLATE_PATH.exists():
        canvas = Image.open(TEMPLATE_PATH).convert("RGBA")
    else:
        log.warning("template.png assente: creo un fondo di emergenza.")
        canvas = Image.new("RGBA", (1536, 1536), "#064E3B")

    canvas = canvas.resize((1536, 1536), Image.Resampling.LANCZOS)
    draw = ImageDraw.Draw(canvas)

    # Pulisce le aree testuali già presenti nel template.
    draw.rounded_rectangle((900, 355, 1485, 665), radius=24, fill="#064E3B")
    draw.rounded_rectangle((918, 952, 1488, 1188), radius=30, fill="#F58213")
    draw.rounded_rectangle((918, 1202, 1488, 1290), radius=30, fill="#F5F1E8")

    # Immagine prodotto: proporzioni mantenute e centratura nel box bianco.
    try:
        product_image = download_product_image(str(product.get("immagine_url", "")))
        if product_image is not None:
            fitted = ImageOps.contain(product_image, (700, 820), Image.Resampling.LANCZOS)
            px = 435 - fitted.width // 2
            py = 790 - fitted.height // 2
            canvas.alpha_composite(fitted, (px, py))
    except Exception as exc:
        log.warning("Immagine prodotto non caricata: %s", exc)
        draw.text(
            (180, 750),
            "IMMAGINE\nNON DISPONIBILE",
            fill="#777777",
            font=font(52, bold=True),
            spacing=12,
            align="center",
        )

    # Titolo.
    title_lines, title_font, line_height = fit_text(
        draw,
        product["titolo"],
        (920, 375, 1465, 650),
        max_size=70,
        min_size=32,
        max_lines=4,
    )
    y = 385
    for line in title_lines:
        draw.text((920, y), line, font=title_font, fill="white")
        y += line_height

    # Sconto e prezzi.
    discount_font = font(50, bold=True)
    draw.text(
        (930, 880),
        f"SCONTO -{product['sconto']}%",
        font=discount_font,
        fill="#A8D840",
    )

    current = str(product["prezzo_attuale"])
    current_font = font(96, bold=True)
    bbox = draw.textbbox((0, 0), current, font=current_font)
    draw.text(
        (1205 - (bbox[2] - bbox[0]) // 2, 1015),
        current,
        font=current_font,
        fill="white",
    )

    old = str(product["prezzo_precedente"])
    old_font = font(46, bold=True)
    old_bbox = draw.textbbox((0, 0), old, font=old_font)
    old_x = 1240 - (old_bbox[2] - old_bbox[0]) // 2
    draw.text((old_x, 1218), old, font=old_font, fill="#143D2D")
    draw.line(
        (old_x - 10, 1248, old_x + old_bbox[2] - old_bbox[0] + 10, 1248),
        fill="#E24A2A",
        width=8,
    )

    canvas.convert("RGB").save(OUTPUT_PATH, "PNG", optimize=True)
    return OUTPUT_PATH


def affiliate_link(product):
    asin = str(product["asin"]).strip()
    return f"https://www.amazon.it/dp/{asin}?tag={AMAZON_TAG}"


def send_to_telegram(product, image_path):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"
    link = affiliate_link(product)

    caption = (
        "🐜 <b>Il Tarlo ha colpito ancora!</b>\n\n"
        f"📦 <b>{html.escape(str(product['titolo']))}</b>\n"
        f"📉 Sconto: <b>-{html.escape(str(product['sconto']))}%</b>\n"
        f"💰 <s>{html.escape(str(product['prezzo_precedente']))}</s> "
        f"➜ <b>{html.escape(str(product['prezzo_attuale']))}</b>\n\n"
        f'👉 <a href="{html.escape(link, quote=True)}">APRI L’OFFERTA AMAZON</a>\n\n'
        "In qualità di Affiliato Amazon ricevo un guadagno dagli acquisti idonei.\n"
        "#IlTarloDelRisparmio"
    )

    with image_path.open("rb") as photo:
        response = requests.post(
            url,
            data={
                "chat_id": CANALE_CHAT_ID,
                "caption": caption,
                "parse_mode": "HTML",
            },
            files={"photo": ("offerta.png", photo, "image/png")},
            timeout=45,
        )

    try:
        payload = response.json()
    except Exception:
        payload = {"ok": False, "description": response.text[:500]}

    if not response.ok or not payload.get("ok"):
        raise RuntimeError(
            f"Telegram ha rifiutato il messaggio: "
            f"{payload.get('description', response.status_code)}"
        )

    return payload


def publish_next_offer():
    global next_index
    require_config()

    with publish_lock:
        catalog = load_catalog()
        product = catalog[next_index % len(catalog)]
        image_path = create_offer_image(product)
        result = send_to_telegram(product, image_path)
        next_index = (next_index + 1) % len(catalog)

    return product, result


def valid_secret():
    supplied = request.args.get("secret", "") or request.headers.get("X-Admin-Secret", "")
    return bool(ADMIN_SECRET) and supplied == ADMIN_SECRET


@app.get("/")
def home():
    return jsonify(
        status="online",
        bot="Il Tarlo del Risparmio",
        instructions="Usa /publish?secret=LA_TUA_PASSWORD per pubblicare.",
    )


@app.get("/health")
def health():
    return jsonify(
        status="ok",
        template_exists=TEMPLATE_PATH.exists(),
        catalog_exists=CATALOG_PATH.exists(),
        telegram_token_set=bool(TELEGRAM_TOKEN),
        channel_set=bool(CANALE_CHAT_ID),
        admin_secret_set=bool(ADMIN_SECRET),
        products=len(load_catalog()),
    )


@app.route("/publish", methods=["GET", "POST"])
def publish():
    if not valid_secret():
        return jsonify(error="Password non valida."), 403

    try:
        product, _ = publish_next_offer()
        return jsonify(ok=True, published=product["titolo"])
    except Exception as exc:
        log.exception("Pubblicazione fallita")
        return jsonify(ok=False, error=str(exc)), 500


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    app.run(host="0.0.0.0", port=port)
