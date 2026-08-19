import os
import textwrap
import urllib.request
from PIL import Image, ImageDraw, ImageFont

def scarica_font_se_mancante():
    """Scarica automaticamente il font Montserrat ExtraBold se non è presente."""
    os.makedirs("fonts", exist_ok=True)
    font_path = os.path.join("fonts", "Montserrat-ExtraBold.ttf")
    
    if not os.path.exists(font_path):
        url = "https://github.com/google/fonts/raw/main/ofl/montserrat/Montserrat-ExtraBold.ttf"
        try:
            urllib.request.urlretrieve(url, font_path)
            print("Font Montserrat-ExtraBold scaricato con successo!")
        except Exception as e:
            print(f"Errore download font: {e}")
            font_path = None
            
    return font_path

def crea_grafica_offerta_perfetta(
    sfondo_path,
    immagine_prodotto_path,
    titolo_prodotto,
    prezzo_attuale,
    prezzo_originale=None,
    percentuale_sconto=None,
    output_path="offerta_corretta.png"
):
    # 1. Apri lo sfondo
    if os.path.exists(sfondo_path):
        img = Image.open(sfondo_path).convert("RGBA")
    else:
        img = Image.new("RGBA", (1080, 1080), (15, 60, 30, 255))
        
    draw = ImageDraw.Draw(img)

    # 2. Carica il Font ExtraBold (Download automatico)
    font_path = scarica_font_se_mancante()
    
    try:
        font_titolo = ImageFont.truetype(font_path, 34)
        font_prezzo = ImageFont.truetype(font_path, 85)     # Prezzo grande
        font_barrato = ImageFont.truetype(font_path, 50)    # Prezzo vecchio
        font_sconto = ImageFont.truetype(font_path, 95)     # Sconto %
    except Exception:
        font_titolo = font_prezzo = font_barrato = font_sconto = ImageFont.load_default()

    # 3. Inserisci l'immagine del prodotto a sinistra
    if os.path.exists(immagine_prodotto_path):
        prod_img = Image.open(immagine_prodotto_path).convert("RGBA")
        prod_img.thumbnail((380, 380), Image.Resampling.LANCZOS)
        
        p_w, p_h = prod_img.size
        pos_x = int(255 - p_w / 2)
        pos_y = int(520 - p_h / 2)
        img.paste(prod_img, (pos_x, pos_y), prod_img)

    # Coordinata X centrale per tutti gli elementi a destra
    X_CENTRO = 660  

    # 4. TITOLO SUL CARTELLINO VERDE (Testo Bianco con contorno sottile per massima nitidezza)
    righe_titolo = textwrap.wrap(titolo_prodotto, width=20)[:4]
    start_y = 410 - (len(righe_titolo) * 18)
    
    for i, riga in enumerate(righe_titolo):
        draw.text(
            (X_CENTRO, start_y + (i * 38)),
            riga,
            fill=(255, 255, 255, 255),
            stroke_width=1,
            stroke_fill=(0, 0, 0, 100),  # Lieve ombreggiatura nera per staccare dal verde
            font=font_titolo,
            anchor="mm"
        )

    # 5. PREZZO SPECIALE (Box Arancione - Grassetto e Bordo Chiaro)
    testo_prezzo = f"{prezzo_attuale:.2f} €".replace(".", ",")
    draw.text(
        (X_CENTRO, 515),
        testo_prezzo,
        fill=(255, 255, 255, 255),
        stroke_width=2,
        stroke_fill=(230, 100, 0, 255), # Bordo arancione scuro per dare l'effetto 3D
        font=font_prezzo,
        anchor="mm"
    )

    # 6. PREZZO BARRATO (Box Chiaro)
    if prezzo_originale:
        testo_barrato = f"{prezzo_originale:.2f} €".replace(".", ",")
        draw.text(
            (X_CENTRO, 578),
            testo_barrato,
            fill=(20, 20, 20, 255),  # Nero intenso
            stroke_width=1,
            stroke_fill=(0, 0, 0, 255),
            font=font_barrato,
            anchor="mm"
        )
        
        # Riga rossa marcata di sbarramento
        bbox = draw.textbbox((X_CENTRO, 578), testo_barrato, font=font_barrato, anchor="mm")
        draw.line(
            [(bbox[0] - 8, bbox[1] + (bbox[3]-bbox[1])/2), (bbox[2] + 8, bbox[1] + (bbox[3]-bbox[1])/2)],
            fill=(220, 30, 30, 255),
            width=5
        )

    # 7. PERCENTUALE SCONTO (Banner Arancione In Basso)
    if percentuale_sconto:
        testo_sconto = f"-{abs(percentuale_sconto)}%"
        draw.text(
            (X_CENTRO, 628),
            testo_sconto,
            fill=(255, 255, 255, 255),
            stroke_width=2,
            stroke_fill=(200, 70, 0, 255),
            font=font_sconto,
            anchor="mm"
        )

    img.save(output_path, "PNG")
    print(f"Immagine generata con successo: {output_path}")

# Esempio di test
if __name__ == "__main__":
    crea_grafica_offerta_perfetta(
        sfondo_path="template.png",
        immagine_prodotto_path="prodotto.png",
        titolo_prodotto="Lefant M1 Robot Aspirapolvere Lavapavimenti 5500Pa",
        prezzo_attuale=119.98,
        prezzo_originale=169.00,
        percentuale_sconto=30,
        output_path="offerta_finale.png"
    )
    
