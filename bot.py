import time
import asyncio
import threading
from PIL import Image, ImageDraw, ImageFont
import requests
from io import BytesIO
from telegram import Bot
from flask import Flask

# --- CONFIGURAZIONE PERSONALE ---
TELEGRAM_TOKEN = "INSERISCI_QUI_IL_TUO_TOKEN_BOTFATHER"
CANALE_CHAT_ID = "@iltarlodelrisparmio"  
AMAZON_TAG = "iltarlodelrisp-21"          

# Parametri di filtro e categorie mirate
SCONTO_MINIMO = 20  
CATEGORIE_ACCETTATE = ["casa", "elettrodomestici", "consumabili", "igiene e pulizia"]

bot = Bot(token=TELEGRAM_TOKEN)

# --- MINI SERVER WEB PER IL PIANO FREE DI RENDER ---
app = Flask(__name__)

@app.route('/')
def home():
    return "Il Tarlo del Risparmio Bot è attivo e online!"

def run_flask():
    app.run(host="0.0.0.0", port=10000)
# --------------------------------------------------

def crea_immagine_offerta(prodotto):
    try:
        template = Image.open("template.png").convert("RGBA")
    except:
        template = Image.new("RGBA", (1000, 1000), "#0B3B24")
    
    draw = ImageDraw.Draw(template)
    
    try:
        font_titolo = ImageFont.truetype("arialbd.ttf", 36)
        font_prezzo_1 = ImageFont.truetype("arialbd.ttf", 55)
        font_prezzo_2 = ImageFont.truetype("arial.ttf", 30)
    except:
        font_titolo = ImageFont.load_default()
        font_prezzo_1 = ImageFont.load_default()
        font_prezzo_2 = ImageFont.load_default()

    try:
        response = requests.get(prodotto['immagine_url'])
        img_prodotto = Image.open(BytesIO(response.content)).convert("RGBA")
        img_prodotto = img_prodotto.resize((420, 420))
        template.paste(img_prodotto, (75, 230), img_prodotto)
    except Exception as e:
        print(f"Errore immagine prodotto: {e}")

    titolo_brevi = prodotto['titolo'][:50] + ("..." if len(prodotto['titolo']) > 50 else "")
    draw.text((605, 250), titolo_brevi, fill="white", font=font_titolo)
    draw.text((660, 685), str(prodotto['prezzo_attuale']), fill="white", font=font_prezzo_1)
    draw.text((800, 830), str(prodotto['prezzo_precedente']), fill="#333333", font=font_prezzo_2)
    draw.line([(790, 845), (940, 845)], fill="red", width=4)

    percorso_finale = "offerta_finale.png"
    template.convert("RGB").save(percorso_finale)
    return percorso_finale

def simula_ricerca_offerte():
    return [
        {
            "titolo": "Detersivo Lavatrice Liquido 100 Lavaggi + Carta Igienica Scorta",
            "categoria": "Consumabili",
            "sconto": 30,
            "asin": "B07XYZ1234",
            "prezzo_attuale": "14,99€",
            "prezzo_precedente": "21,99€",
            "immagine_url": "https://m.media-amazon.com/images/I/71s5834e2bL._AC_SL1500_.jpg"
        }
    ]

async def invia_offerta(prodotto):
    link_affiliato = f"https://www.amazon.it/dp/{prodotto['asin']}?tag={AMAZON_TAG}"
    percorso_foto = crea_immagine_offerta(prodotto)
    
    didascalia = (
        f"🐜 **Il Tarlo ha colpito ancora!**\n\n"
        f"📦 **{prodotto['titolo']}**\n"
        f"📉 Sconto bomba: **-{prodotto['sconto']}%**\n"
        f"💰 Crollo prezzo: ~~{prodotto['prezzo_precedente']}~~ ➔ **{prodotto['prezzo_attuale']}**\n\n"
        f"👉 [ACQUISTA SUBITO IN OFFERTA]({link_affiliato})\n\n"
        f"In qualità di Affiliato Amazon ricevo un guadagno dagli acquisti idonei.\n"
        f"#IlTarloDelRisparmio #Casa #{prodotto['categoria']}"
    )
    
    with open(percorso_foto, 'rb') as foto_file:
        await bot.send_photo(
            chat_id=CANALE_CHAT_ID,
            photo=foto_file,
            caption=didascalia,
            parse_mode="Markdown"
        )

async def main():
    print("Il Tarlo del Risparmio - Bot avviato con Web Server integrato!")
    inviati = set()
    
    while True:
        try:
            offerte = simula_ricerca_offerte()
            for off in offerte:
                if off["categoria"].lower() in CATEGORIE_ACCETTATE and off["sconto"] >= SCONTO_MINIMO:
                    if off["asin"] not in inviati:
                        await invia_offerta(off)
                        inviati.add(off["asin"])
            await asyncio.sleep(10) 
        except Exception as e:
            print(f"Errore nel ciclo: {e}")
            await asyncio.sleep(60)

if __name__ == "__main__":
    # Avvia Flask in un thread separato così non blocca il bot di Telegram
    t = threading.Thread(target=run_flask)
    t.daemon = True
    t.start()
    
    # Avvia il loop principale del bot
    asyncio.run(main())
