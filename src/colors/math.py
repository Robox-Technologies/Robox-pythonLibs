def rgb_to_hsv(r, g, b):
    max_c = max(r, g, b)
    min_c = min(r, g, b)
    delta = max_c - min_c

    if delta == 0:
        h = 0
    elif max_c == r:
        h = (60 * ((g - b) / delta)) % 360
    elif max_c == g:
        h = (60 * ((b - r) / delta)) + 120
    elif max_c == b:
        h = (60 * ((r - g) / delta)) + 240

    s = 0 if max_c == 0 else delta / max_c

    v = max_c

    return h, s, v  # h in [0,360), s and v in [0,1]



def hsv_to_rgb(h, s, v):
    c = v * s
    x = c * (1 - abs((h / 60) % 2 - 1))
    m = v - c

    if 0 <= h < 60:
        rp, gp, bp = c, x, 0
    elif 60 <= h < 120:
        rp, gp, bp = x, c, 0
    elif 120 <= h < 180:
        rp, gp, bp = 0, c, x
    elif 180 <= h < 240:
        rp, gp, bp = 0, x, c
    elif 240 <= h < 300:
        rp, gp, bp = x, 0, c
    elif 300 <= h < 360:
        rp, gp, bp = c, 0, x
    else:
        rp, gp, bp = 0, 0, 0  # fallback

    r = int((rp + m) * 255)
    g = int((gp + m) * 255)
    b = int((bp + m) * 255)

    return r, g, b
