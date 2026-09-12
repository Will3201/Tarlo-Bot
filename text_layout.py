"""Testi misurati in pixel e centrati nell'area utile di ogni riquadro."""
from functools import lru_cache
from pathlib import Path
from PIL import ImageFont


@lru_cache(maxsize=128)
def font(size):
    path = Path(__file__).resolve().parent / 'fonts' / 'Montserrat-ExtraBold.ttf'
    return ImageFont.truetype(str(path), size)


def wrap_pixels(draw, text, face, width, stroke):
    def fits(value):
        b = draw.textbbox((0, 0), value, font=face, stroke_width=stroke)
        return b[2]-b[0] <= width
    lines, line = [], ''
    for word in text.split():
        candidate = f'{line} {word}'.strip()
        if fits(candidate):
            line = candidate
            continue
        if line:
            lines.append(line)
        line = ''
        # Anche codici prodotto senza spazi devono rientrare nel box.
        for char in word:
            if line and not fits(line+char):
                lines.append(line)
                line = ''
            line += char
    if line:
        lines.append(line)
    return lines or ['']


def draw_fitted(draw, text, box, max_size, min_size=16, max_lines=1,
                fill='white', stroke=1, stroke_fill='black', strike=False):
    text = ' '.join(str(text).split())
    if not text:
        return None
    left, top, right, bottom = box
    width, height = right-left, bottom-top
    for size in range(max_size, min_size-1, -1):
        face = font(size)
        lines = wrap_pixels(draw, text, face, width, stroke) if max_lines > 1 else [text]
        value = '\n'.join(lines)
        spacing = max(4, round(size*.16))
        b = draw.multiline_textbbox((0, 0), value, font=face, spacing=spacing,
                                    align='center', stroke_width=stroke)
        if len(lines) <= max_lines and b[2]-b[0] <= width and b[3]-b[1] <= height:
            break
    else:
        # Titoli eccezionalmente lunghi: ellissi, mai testo fuori cornice.
        value = '\n'.join(lines[:max_lines]).rstrip()
        while value:
            candidate = value.rstrip()+'…'
            b = draw.multiline_textbbox((0, 0), candidate, font=face, spacing=spacing,
                                        align='center', stroke_width=stroke)
            if b[2]-b[0] <= width and b[3]-b[1] <= height:
                value = candidate
                break
            value = value[:-1].rstrip()
        if not value:
            return None
    w, h = b[2]-b[0], b[3]-b[1]
    x, y = (left+right-w)/2-b[0], (top+bottom-h)/2-b[1]
    draw.multiline_text((x, y), value, font=face, fill=fill, spacing=spacing,
                        align='center', stroke_width=stroke, stroke_fill=stroke_fill)
    ink = (x+b[0], y+b[1], x+b[2], y+b[3])
    if strike:
        draw.line((ink[0], (top+bottom)/2, ink[2], (top+bottom)/2), fill='#CC0000', width=4)
    return {'text': value, 'size': size, 'bounds': ink}


def disegna_testi(draw, prodotto):
    # Cornici a 1080x1080: margini interni, badge e foglie sono esclusi.
    result = {}
    result['titolo'] = draw_fitted(draw, prodotto['titolo'], (570, 224, 1034, 378),
                                   56, min_size=22, max_lines=3, stroke=2)
    result['prezzo'] = draw_fitted(draw, f"{prodotto['prezzo_attuale']} €",
                                   (580, 478, 1024, 620 if prodotto.get('coupon_applicato') else 672), 120, fill='#111111', stroke_fill='white')
    if prodotto.get('prezzo_precedente'):
        result['precedente'] = draw_fitted(draw, f"{prodotto['prezzo_precedente']} €",
                                           (580, 750 if prodotto.get('coupon_applicato') else 733, 1024, 807), 60, fill='#333333',
                                           stroke_fill='white', strike=True)
    if prodotto.get('sconto', 0) > 0:
        result['sconto'] = draw_fitted(draw, f"-{prodotto['sconto']}%",
                                       (703, 858, 985, 946), 86, stroke=2)
    if prodotto.get('coupon_applicato'):
        result['coupon'] = draw_fitted(draw, 'CON COUPON', (595, 632, 1010, 677),
                                       32, fill='#111111', stroke=0)
        result['base_coupon'] = draw_fitted(draw, 'SENZA COUPON', (590, 716, 1014, 739),
                                            19, fill='#333333', stroke=0)
    return result
