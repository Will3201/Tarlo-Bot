import html
import json
import logging
import os
import random
import threading
import time
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from flask import Flask, jsonify, request
from PIL import Image, ImageDraw, ImageFont, ImageOps

# =============================
# Configurazione
# =============================
BASE_DIR = Path(__file__).resolve().parent
TEMPLATE_PATH = BASE_DIR / os.getenv("TEMPLATE_FILE", "template.png")
CATALOG_PATH = BASE_DIR / os.getenv("CATALOG_FILE", "catalogo.json")
OUTPUT_PATH = BASE_DIR / "offerta_finale.png"
STATE_PATH = BASE_DIR / ".bot_state.json"

TELEGRAM_TOKEN = os.environ["TELEGRAM_TOKEN"]
CANALE_CHAT_ID = os.getenv("CANALE_CHAT_ID", "@iltarlodelrisparmio")
AMAZON_TAG = os.getenv("AMAZON_TAG", "iltarlodelrisp-21")
PUBLISH_INTERVAL_MINUTES = int(os.getenv("PUBLISH_INTERVAL_MINUTES", "30"))
PORT = int(os.getenv("PORT", "10000"))
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")

# Coordinate riferite al template quadrato 1254x1254 mostrato in chat.
# Sono scalate automaticamente se template.png ha un'altra risoluzione.
REFERENCE_SIZE = 1254
PRODUCT_BOX = (35, 258, 696, 1085)  # x1, y1, x2, y2
TITLE_BOX = (758, 307, 1190, 500)
CURRENT_PRICE_BOX = (765, 800, 1218, 988)
OLD_PRICE_BOX = (760, 1001, 1215, 1077)

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("tarlo-bot")

app = Flask(__name__)
http = requests.Session()
http.headers.update(
    {
        "User-Agent": (
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "Chrome/124.0 Safari/537.36"
        )
    }
)


@dataclass(frozen=True)
class Product:
    titolo: str
    categoria: str
    sconto: int
    asin: str
    prezzo_attuale: str
    prezzo_precedente: str
    immagine_url: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Product":
        required = {
            "titolo",
            "categoria",
            "sconto",
            "asin",
            "prezzo_attuale",
            "prezzo_precedente",
            "immagine_url",
        }
        missing = required - data.keys()
        if missing:
            raise ValueError(f"Campi mancanti nel catalogo: {sorted(missing)}")

        asin = str(data["asin"]).strip().upper()
        if len(asin) != 10 or not asin.isalnum():
            raise ValueError(f"ASIN non valido: {asin!r}")

        return cls(
            titolo=str(data["titolo"]).strip(),
            categoria=str(data["categoria"]).strip(),
            sconto=int(data["sconto"]),
            asin=asin,
            prezzo_attuale=str(data["prezzo_attuale"]).strip(),
            prezzo_precedente=str(data["prezzo_precedente"]).strip(),
            immagine_url=str(data["immagine_url"]).strip(),
        )


def load_catalog() -> list[Product]:
    if not CATALOG_PATH.exists():
        raise FileNotFoundError(
            f"Manca {CATALOG_PATH.name}. Crea il file partendo da catalogo.example.json."
        )
    raw = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise ValueError("Il catalogo deve essere una lista JSON non vuota.")
    return [Product.from_dict(item) for item in raw]


def _scaled_box(box: tuple[int, int, int, int], size: tuple[int, int]) -> tuple[int, int, int, int]:
    sx = size[0] / REFERENCE_SIZE
    sy = size[1] / REFERENCE_SIZE
    x1, y1, x2, y2 = box
    return round(x1 * sx), round(y1 * sy), round(x2 * sx), round(y2 * sy)


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        BASE_DIR / ("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        Path("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
    ]
    for path in candidates:
        if path.exists():
            return ImageFont.truetype(str(path), size=size)
    raise FileNotFoundError("Nessun font TrueType disponibile. Installa fonts-dejavu-core.")


def _fit_text(
    draw: ImageDraw.ImageDraw,
    text: str,
    box: tuple[int, int, int, int],
    max_size: int,
    min_size: int = 22,
    max_lines: int = 4,
) -> tuple[ImageFont.FreeTypeFont, list[str], int]:
    x1, y1, x2, y2 = box
    width, height = x2 - x1, y2 - y1

    for size in range(max_size, min_size - 1, -2):
        font = _font(size, bold=True)
        words = text.split()
        lines: list[str] = []
        current = ""

        for word in words:
            candidate = f"{current} {word}".strip()
            if draw.textlength(candidate, font=font) <= width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)

        line_height = int(size * 1.18)
        if len(lines) <= max_lines and len(lines) * line_height <= height:
            return font, lines, line_height

    font = _font(min_size, bold=True)
    return font, [text[:65] + ("…" if len(text) > 65 else "")], int(min_size * 1.18)


def _download_product_image(url: str) -> Image.Image:
    response = http.get(url, timeout=(5, 20))
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "")
    if not content_type.startswith("image/"):
        raise ValueError(f"La URL non restituisce un'immagine: {content_type}")
    return Image.open(BytesIO(response.content)).convert("RGBA")


def _paste_contained(canvas: Image.Image, image: Image.Image, box: tuple[int, int, int, int]) -> None:
    x1, y1, x2, y2 = box
    padding = max(18, round((x2 - x1) * 0.05))
    target = (x2 - x1 - 2 * padding, y2 - y1 - 2 * padding)

    # Mantiene le proporzioni: niente prodotti schiacciati o deformati.
    fitted = ImageOps.contain(image, target, method=Image.Resampling.LANCZOS)
    px = x1 + (x2 - x1 - fitted.width) // 2
    py = y1 + (y2 - y1 - fitted.height) // 2
    canvas.alpha_composite(fitted, dest=(px, py))


def create_offer_image(product: Product) -> Path:
    if not TEMPLATE_PATH.exists():
        raise FileNotFoundError(f"Template non trovato: {TEMPLATE_PATH}")

    template = Image.open(TEMPLATE_PATH).convert("RGBA")
    draw = ImageDraw.Draw(template)
    product_box = _scaled_box(PRODUCT_BOX, template.size)
    title_box = _scaled_box(TITLE_BOX, template.size)
    current_price_box = _scaled_box(CURRENT_PRICE_BOX, template.size)
    old_price_box = _scaled_box(OLD_PRICE_BOX, template.size)

    product_image = _download_product_image(product.immagine_url)
    _paste_contained(template, product_image, product_box)

    title_font, title_lines, line_height = _fit_text(
        draw,
        product.titolo,
        title_box,
        max_size=max(34, round(template.width * 0.043)),
        min_size=max(20, round(template.width * 0.022)),
        max_lines=4,
    )
    tx, ty, _, _ = title_box
    for line in title_lines:
        draw.text((tx, ty), line, fill="white", font=title_font, stroke_width=1, stroke_fill="#0B3B24")
        ty += line_height

    price_font = _font(max(48, round(template.width * 0.071)), bold=True)
    old_font = _font(max(28, round(template.width * 0.035)), bold=True)

    # Centra i prezzi dentro i rispettivi riquadri.
    def draw_centered(text: str, box: tuple[int, int, int, int], font: ImageFont.FreeTypeFont, fill: str) -> tuple[int, int, int, int]:
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        bx1, by1, bx2, by2 = box
        x = bx1 + (bx2 - bx1 - tw) // 2
        y = by1 + (by2 - by1 - th) // 2 - bbox[1]
        draw.text((x, y), text, fill=fill, font=font)
        return x, y, x + tw, y + th

    draw_centered(product.prezzo_attuale, current_price_box, price_font, "white")
    old_bbox = draw_centered(product.prezzo_precedente, old_price_box, old_font, "#163D2C")
    strike_y = (old_bbox[1] + old_bbox[3]) // 2
    draw.line((old_bbox[0] - 8, strike_y, old_bbox[2] + 8, strike_y), fill="#D92D20", width=max(4, template.width // 250))

    template.convert("RGB").save(OUTPUT_PATH, "PNG", optimize=True)
    return OUTPUT_PATH


def affiliate_link(product: Product) -> str:
    return f"https://www.amazon.it/dp/{product.asin}?tag={AMAZON_TAG}"


def build_caption(product: Product) -> str:
    # HTML è molto meno fragile del Markdown quando i titoli contengono -, +, parentesi ecc.
    title = html.escape(product.titolo)
    category = "".join(c if c.isalnum() else "" for c in product.categoria.title()) or "Offerte"
    link = html.escape(affiliate_link(product), quote=True)
    return (
        "🐜 <b>Il Tarlo ha colpito ancora!</b>\n\n"
        f"📦 <b>{title}</b>\n"
        f"📉 Sconto indicato: <b>-{product.sconto}%</b>\n"
        f"💰 <s>{html.escape(product.prezzo_precedente)}</s> ➜ <b>{html.escape(product.prezzo_attuale)}</b>\n\n"
        f"👉 <a href=\"{link}\"><b>APRI L'OFFERTA SU AMAZON</b></a>\n\n"
        "<i>Prezzo e disponibilità possono cambiare. In qualità di Affiliato Amazon "
        "ricevo un guadagno dagli acquisti idonei.</i>\n"
        f"#IlTarloDelRisparmio #{category}"
    )


def send_offer(product: Product) -> dict[str, Any]:
    image_path = create_offer_image(product)
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendPhoto"

    with image_path.open("rb") as image_file:
        response = http.post(
            url,
            data={
                "chat_id": CANALE_CHAT_ID,
                "caption": build_caption(product),
                "parse_mode": "HTML",
            },
            files={"photo": (image_path.name, image_file, "image/png")},
            timeout=(10, 60),
        )

    try:
        payload = response.json()
    except ValueError:
        payload = {"ok": False, "description": response.text[:500]}

    if not response.ok or not payload.get("ok"):
        raise RuntimeError(f"Telegram ha rifiutato il messaggio: {payload}")
    return payload


def _read_index() -> int:
    try:
        return int(json.loads(STATE_PATH.read_text(encoding="utf-8")).get("next_index", 0))
    except Exception:
        return 0


def _write_index(index: int) -> None:
    STATE_PATH.write_text(json.dumps({"next_index": index}), encoding="utf-8")


def publish_next() -> Product:
    catalog = load_catalog()
    index = _read_index() % len(catalog)
    product = catalog[index]
    send_offer(product)
    _write_index((index + 1) % len(catalog))
    logger.info("Pubblicato: %s", product.titolo)
    return product


def scheduler_loop() -> None:
    # Piccolo ritardo iniziale per permettere al server web di partire.
    time.sleep(5)
    while True:
        try:
            publish_next()
            sleep_seconds = max(60, PUBLISH_INTERVAL_MINUTES * 60)
        except Exception:
            logger.exception("Errore durante la pubblicazione")
            sleep_seconds = 60
        time.sleep(sleep_seconds)


@app.get("/")
def home():
    return jsonify(status="ok", service="Il Tarlo del Risparmio")


@app.get("/health")
def health():
    try:
        products = len(load_catalog())
        return jsonify(status="ok", products=products), 200
    except Exception as exc:
        return jsonify(status="error", error=str(exc)), 500


@app.post("/publish-next")
def publish_next_endpoint():
    if not ADMIN_SECRET or request.headers.get("X-Admin-Secret") != ADMIN_SECRET:
        return jsonify(error="unauthorized"), 401
    try:
        product = publish_next()
        return jsonify(ok=True, published=product.titolo)
    except Exception as exc:
        logger.exception("Pubblicazione manuale fallita")
        return jsonify(ok=False, error=str(exc)), 500


_scheduler_started = False
_scheduler_lock = threading.Lock()

def start_scheduler_once() -> None:
    global _scheduler_started
    if os.getenv("RUN_SCHEDULER", "1") != "1":
        return
    with _scheduler_lock:
        if _scheduler_started:
            return
        threading.Thread(target=scheduler_loop, name="publisher", daemon=True).start()
        _scheduler_started = True

# Parte anche quando l'app viene importata da Gunicorn. Usare un solo worker.
start_scheduler_once()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=PORT, use_reloader=False)
