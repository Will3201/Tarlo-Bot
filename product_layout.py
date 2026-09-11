"""Composizione del prodotto nel template Risparmio (coordinate a 1080 px)."""
from PIL import Image, ImageChops, ImageOps, ImageStat


def prepara_prodotto(image):
    """Ritaglia solo margini trasparenti o quasi bianchi, preservando il prodotto."""
    image = ImageOps.exif_transpose(image).convert("RGBA")
    visible = image.getchannel("A").getbbox()
    if visible:
        image = image.crop(visible)
    rgb = Image.new("RGB", image.size, "white")
    rgb.paste(image, mask=image.getchannel("A"))
    w, h = rgb.size
    corner = max(1, min(w, h) // 50)
    corners = [(0, 0, corner, corner), (w-corner, 0, w, corner),
               (0, h-corner, corner, h), (w-corner, h-corner, w, h)]
    # Foto ambientate o con fondo colorato restano intere.
    if all(min(ImageStat.Stat(rgb.crop(box)).mean) >= 245 for box in corners):
        diff = ImageChops.difference(rgb, Image.new("RGB", rgb.size, "white"))
        channels = diff.split()
        mask = ImageChops.lighter(ImageChops.lighter(channels[0], channels[1]), channels[2])
        bbox = mask.point(lambda value: 255 if value > 12 else 0).getbbox()
        if bbox:
            padding = max(3, round(max(bbox[2]-bbox[0], bbox[3]-bbox[1]) * .03))
            image = image.crop((max(0, bbox[0]-padding), max(0, bbox[1]-padding),
                                min(w, bbox[2]+padding), min(h, bbox[3]+padding)))
    return image


def posiziona_prodotto(base, image):
    """Ingrandisce senza deformare: etichetta e decorazioni restano libere."""
    image = prepara_prodotto(image)
    # Sotto l'etichetta (termina a circa y=328), prima delle foglie inferiori.
    left, top, right, bottom = 42, 345, 508, 795
    sx, sy = base.width / 1080, base.height / 1080
    left, right = round(left*sx), round(right*sx)
    top, bottom = round(top*sy), round(bottom*sy)
    scale = min((right-left)/image.width, (bottom-top)/image.height)
    size = (max(1, round(image.width*scale)), max(1, round(image.height*scale)))
    image = image.resize(size, Image.Resampling.LANCZOS)
    x, y = left + (right-left-size[0])//2, top + (bottom-top-size[1])//2
    base.alpha_composite(image, (x, y))
    return x, y, x+size[0], y+size[1]
