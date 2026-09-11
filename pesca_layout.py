"""Layout misurato sul template pesca, distinto dal template Risparmio."""
from product_layout import prepara_prodotto
from text_layout import draw_fitted
from PIL import Image


def componi(base, image, p):
    image = prepara_prodotto(image)
    # Sotto il logo; foglie e bolle inferiori restano libere.
    box = (45, 405, 463, 815)
    factor = min((box[2]-box[0])/image.width, (box[3]-box[1])/image.height)
    image = image.resize((max(1, round(image.width*factor)),
                          max(1, round(image.height*factor))), Image.Resampling.LANCZOS)
    base.alpha_composite(image, (box[0]+(box[2]-box[0]-image.width)//2,
                                 box[1]+(box[3]-box[1]-image.height)//2))
    from PIL import ImageDraw
    draw = ImageDraw.Draw(base)
    results = [draw_fitted(draw, p['titolo'], (585, 225, 985, 395), 52, 22, 3, stroke=2),
               draw_fitted(draw, p['prezzo_attuale']+' €', (535, 485, 1030, 725),
                           126, fill='#111111', stroke_fill='white')]
    if p.get('prezzo_precedente'):
        results.append(draw_fitted(draw, p['prezzo_precedente']+' €',
                                   (550, 810, 970, 887), 62, strike=True))
    if p.get('sconto', 0) > 0:
        results.append(draw_fitted(draw, f"-{p['sconto']}%", (600, 910, 930, 994), 88, stroke=2))
    return results
