import os
import math
from PIL import Image, ImageDraw, ImageFont

def genera_grafica_offerta(
    immagine_prodotto_path,
    titolo_prodotto,
    prezzo_attuale,
    prezzo_originale=None,
    percentuale_sconto=None,
    output_path="offerta_output.png"
):
    # 1. Creazione tela base (1080x1080px)
    width, height = 1080, 1080
    
    # Se hai un template di sfondo usalo, altrimenti creiamo una base
    if os.path.exists("template_bg.png"):
        img = Image.open("template_bg.png").convert("RGBA")
    else:
        img = Image.new("RGBA", (width, height), (34, 139, 34, 255)) # Sfondo verde di backup

    draw = ImageDraw.Draw(img)

    # 2. Caricamento Font Locali (Assicurati di avere il file .ttf nella cartella 'fonts/')
    font_path = os.path.join("fonts", "Roboto-Bold.ttf")
    
    # Ingranditi drasticamente i font per leggibilità
    if os.path.exists(font_path):
        font_titolo = ImageFont.truetype(font_path, 36)
        font_prezzo_grande = ImageFont.truetype(font_path, 90)  # Ingrandito
        font_prezzo_barrato = ImageFont.truetype(font_path, 50) # Ingrandito
        font_sconto = ImageFont.truetype(font_path, 70)         # Ingrandito
    else:
        # Fallback se il font non esiste
        font_titolo = font_prezzo_grande = font_prezzo_barrato = font_sconto = ImageFont.load_default()

    # 3. Inserimento Immagine Prodotto (A sinistra)
    if os.path.exists(immagine_prodotto_path):
        prod_img = Image.open(immagine_prodotto_path).convert("RGBA")
        prod_img.thumbnail((450, 450))
        img.paste(prod_img, (60, 300), prod_img)

    # 4. Render Titolo Prodotto (Dentro il cartellino verde)
    # Testo Bianco (#FFFFFF) per massimo contrasto
    colore_bianco = (255, 255, 255, 255)
    colore_grigio_barrato = (200, 200, 200, 255)

    # Tronca titolo se troppo lungo
    titolo_breve = titolo_prodotto[:50] + "..." if len(titolo_prodotto) > 50 else titolo_prodotto
    draw.text((540, 260), titolo_breve, fill=colore_bianco, font=font_titolo)

    # 5. Render Prezzo Speziale / Attuale (Box Arancione)
    testo_prezzo = f"{prezzo_attuale:.2f} €".replace(".", ",")
    # Anchor 'mm' centra il testo rispetto alla coordinata X, Y del box
    draw.text((750, 470), testo_prezzo, fill=colore_bianco, font=font_prezzo_grande, anchor="mm")

    # 6. Render Prezzo Barrato e Percentuale Sconto (Se disponibili)
    if prezzo_originale and percentuale_sconto:
        # Prezzo Vecchio Barrato
        testo_vecchio = f"{prezzo_originale:.2f} €".replace(".", ",")
        draw.text((750, 580), testo_vecchio, fill=colore_grigio_barrato, font=font_prezzo_barrato, anchor="mm")
        
        # Linea sopra il prezzo per barralo
        draw.line([(650, 580), (850, 580)], fill=(255, 0, 0, 255), width=5)

        # Percentuale Sconto in basso
        testo_sconto = f"-{abs(percentuale_sconto)}%"
        draw.text((750, 680), testo_sconto, fill=colore_bianco, font=font_sconto, anchor="mm")

    # Salvataggio
    img.save(output_path)
    return output_path


def genera_testo_telegram(titolo, asin, prezzo_attuale, prezzo_originale=None, percentuale_sconto=None):
    """Compone il messaggio di testo per Telegram senza saltare lo sconto."""
    link_affiliato = f"https://www.amazon.it/dp/{asin}?tag=tarlodelrispa-21"
    
    messaggio = f"🪵 **{titolo}**\n\n"
    
    if percentuale_sconto and prezzo_originale:
        messaggio += f"📉 **Sconto:** -{abs(percentuale_sconto)}%\n"
        messaggio += f"❌ **Invece di:** ~~{prezzo_originale:.2f} €~~\n".replace(".", ",")
    
    messaggio += f"💰 **Prezzo speciale:** {prezzo_attuale:.2f} €\n".replace(".", ",")
    messaggio += f"👉 **Acquista ora:** {link_affiliato}\n\n"
    messaggio += "#IlTarloDelRisparmio"
    
    return messaggio


# --- ESEMPIO DI UTILIZZO ---
if __name__ == "__main__":
    # Dati di test
    asin = "B0DHSFF382"
    titolo = "LEGO Harry Potter Castello di Hogwarts: Lezione di Incantesimi"
    prezzo_speciale = 13.99
    prezzo_vecchio = 19.99
    
    # Calcolo percentuale sconto sicuro
    if prezzo_vecchio and prezzo_vecchio > prezzo_speciale:
        sconto_percentuale = round(((prezzo_vecchio - prezzo_speciale) / prezzo_vecchio) * 100)
    else:
        sconto_percentuale = None
        prezzo_vecchio = None

    # Genera Immagine
    genera_grafica_offerta(
        immagine_prodotto_path="prodotto.png",
        titolo_prodotto=titolo,
        prezzo_attuale=prezzo_speciale,
        prezzo_originale=prezzo_vecchio,
        percentuale_sconto=sconto_percentuale
    )

    # Genera Testo Telegram
    testo_post = genera_testo_telegram(
        titolo=titolo,
        asin=asin,
        prezzo_attuale=prezzo_speciale,
        prezzo_originale=prezzo_vecchio,
        percentuale_sconto=sconto_percentuale
    )

    print("--- TESTO PRONTO PER TELEGRAM ---")
    print(testo_post)
    
