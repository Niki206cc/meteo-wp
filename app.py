import os
import threading
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from io import BytesIO
from typing import Dict, List, Optional

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from PIL import Image, ImageDraw, ImageFont


# ======================
# CONFIG da ENV
# ======================
WP_URL = os.getenv("WP_URL", "https://www.montagneepaesi.com/wp-json/wp/v2/posts")
WP_USER = os.getenv("WP_USER", "")
WP_APP_PASSWORD = os.getenv("WP_APP_PASSWORD", "")
WP_CATEGORY_ID = int(os.getenv("WP_CATEGORY_ID", "2496"))

REGIONE = os.getenv("REGIONE", "Lombardia")
RUN_HOUR = int(os.getenv("RUN_HOUR", "7"))
RUN_MINUTE = int(os.getenv("RUN_MINUTE", "0"))

API_URL = "https://api.open-meteo.com/v1/forecast"
IMG_DEFAULT_URL = os.getenv(
    "IMG_DEFAULT_URL",
    "https://www.montagneepaesi.com/wp-content/uploads/2025/09/Lombardia.jpg",
)
FONT_PATH = os.getenv(
    "FONT_PATH",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)

CITTA: Dict[str, tuple] = {
    "Milano": (45.4642, 9.19),
    "Bergamo": (45.6983, 9.6773),
    "Brescia": (45.5416, 10.2118),
    "Mantova": (45.1564, 10.7914),
    "Sondrio": (46.1699, 9.8787),
}

descrizioni = {
    0: ("cieli sereni", "https://img.icons8.com/color/48/000000/sun.png"),
    1: ("poche nuvole", "https://img.icons8.com/color/48/000000/partly-cloudy-day.png"),
    2: ("variabile", "https://img.icons8.com/color/48/000000/cloud.png"),
    3: ("cielo coperto", "https://img.icons8.com/color/48/000000/clouds.png"),
    45: ("nebbie", "https://img.icons8.com/color/48/000000/fog-day.png"),
    48: ("nebbie con brina", "https://img.icons8.com/color/48/000000/fog-day.png"),
    51: ("pioviggini", "https://img.icons8.com/color/48/000000/light-rain.png"),
    53: ("pioviggini", "https://img.icons8.com/color/48/000000/light-rain.png"),
    55: ("pioviggini intense", "https://img.icons8.com/color/48/000000/light-rain.png"),
    61: ("piogge", "https://img.icons8.com/color/48/000000/rain.png"),
    63: ("piogge", "https://img.icons8.com/color/48/000000/rain.png"),
    65: ("piogge intense", "https://img.icons8.com/color/48/000000/rain.png"),
    71: ("nevicate", "https://img.icons8.com/color/48/000000/snow.png"),
    73: ("nevicate", "https://img.icons8.com/color/48/000000/snow.png"),
    75: ("nevicate intense", "https://img.icons8.com/color/48/000000/snow.png"),
    80: ("rovesci", "https://img.icons8.com/color/48/000000/rain.png"),
    81: ("rovesci", "https://img.icons8.com/color/48/000000/rain.png"),
    82: ("forti rovesci", "https://img.icons8.com/color/48/000000/rain.png"),
    95: ("temporali", "https://img.icons8.com/color/48/000000/storm.png"),
    96: ("temporali con grandine", "https://img.icons8.com/color/48/000000/storm.png"),
    99: ("forti temporali con grandine", "https://img.icons8.com/color/48/000000/storm.png"),
}

giorni_it = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
mesi_it = [
    "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
]


@dataclass
class Status:
    running: bool = False
    last_run: Optional[str] = None
    next_run: Optional[str] = None
    last_error: Optional[str] = None
    last_post_url: Optional[str] = None


status = Status()
last_meteo: List[dict] = []
lock = threading.Lock()


def crea_immagine_con_titolo(titolo_testo: str) -> str:
    import textwrap

    r = requests.get(IMG_DEFAULT_URL, timeout=30)
    r.raise_for_status()
    img = Image.open(BytesIO(r.content)).convert("RGBA")

    overlay = Image.new("RGBA", img.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(overlay)
    img_w, img_h = img.size
    margin_x = int(img_w * 0.06)
    max_text_w = img_w - 2 * margin_x
    padding = 16
    y = 30
    start_size = 56
    min_size = 28

    def load_font(sz: int):
        try:
            return ImageFont.truetype(FONT_PATH, sz)
        except Exception:
            return ImageFont.load_default()

    def measure(lines, font):
        heights = []
        widths = []
        for line in lines:
            bbox = draw.textbbox((0, 0), line, font=font)
            widths.append(bbox[2] - bbox[0])
            heights.append(bbox[3] - bbox[1])
        return max(widths) if widths else 0, sum(heights) + (len(lines) - 1) * 6

    best_lines = [titolo_testo]
    best_font = load_font(start_size)

    for size in range(start_size, min_size - 1, -2):
        font = load_font(size)
        approx_chars = max(12, int(max_text_w / (size * 0.55)))
        wrapped = textwrap.wrap(titolo_testo, width=approx_chars)
        if len(wrapped) <= 3:
            w, h = measure(wrapped, font)
            if w <= max_text_w and (y + h + 2 * padding) < img_h * 0.5:
                best_lines = wrapped
                best_font = font
                break

    text_w, text_h = measure(best_lines, best_font)
    box_x0 = (img_w - text_w) / 2 - padding
    box_y0 = y - padding
    box_x1 = (img_w + text_w) / 2 + padding
    box_y1 = y + text_h + padding
    draw.rectangle((box_x0, box_y0, box_x1, box_y1), fill=(0, 0, 0, 150))

    cur_y = y
    for line in best_lines:
        bbox = draw.textbbox((0, 0), line, font=best_font)
        lw = bbox[2] - bbox[0]
        lh = bbox[3] - bbox[1]
        draw.text(((img_w - lw) / 2, cur_y), line, font=best_font, fill=(255, 255, 255, 255))
        cur_y += lh + 6

    out = Image.alpha_composite(img, overlay).convert("RGB")
    fname = f"/tmp/meteo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg"
    out.save(fname, quality=92)
    return fname


def richiesta_open_meteo(params: dict) -> requests.Response:
    ultimo_errore = None

    for tentativo in range(1, 6):
        try:
            response = requests.get(
                API_URL,
                params=params,
                timeout=45,
                headers={"User-Agent": "Meteo-Montagne-Paesi/1.1"},
            )

            if response.status_code in (429, 500, 502, 503, 504):
                raise requests.HTTPError(
                    f"HTTP {response.status_code}: {response.text[:200]}",
                    response=response,
                )

            response.raise_for_status()
            return response

        except requests.RequestException as errore:
            ultimo_errore = errore
            if tentativo < 5:
                time.sleep(tentativo * 10)

    raise RuntimeError(
        "Open-Meteo non disponibile dopo 5 tentativi. "
        f"Ultimo errore: {ultimo_errore}"
    )


def fetch_meteo_oggi() -> List[dict]:
    nomi = list(CITTA.keys())
    latitudini = ",".join(str(CITTA[nome][0]) for nome in nomi)
    longitudini = ",".join(str(CITTA[nome][1]) for nome in nomi)

    params = {
        "latitude": latitudini,
        "longitude": longitudini,
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,weather_code",
        "timezone": "Europe/Rome",
        "forecast_days": 1,
    }

    response = richiesta_open_meteo(params)
    payload = response.json()
    risultati = payload if isinstance(payload, list) else [payload]

    if len(risultati) != len(nomi):
        raise RuntimeError(
            f"Open-Meteo ha restituito {len(risultati)} località invece di {len(nomi)}."
        )

    meteo_citta = []
    for nome, dati in zip(nomi, risultati):
        daily = dati.get("daily", {})
        try:
            t_min = float(daily["temperature_2m_min"][0])
            t_max = float(daily["temperature_2m_max"][0])
            pioggia = float(daily["precipitation_sum"][0] or 0)
            wcode = int(daily["weather_code"][0])
        except (KeyError, IndexError, TypeError, ValueError) as errore:
            raise RuntimeError(f"Dati meteo non validi per {nome}: {errore}") from errore

        descr, icona = descrizioni.get(
            wcode,
            ("tempo variabile", "https://img.icons8.com/color/48/000000/cloud.png"),
        )
        meteo_citta.append({
            "nome": nome,
            "t_min": t_min,
            "t_max": t_max,
            "pioggia": pioggia,
            "descrizione": descr,
            "icona": icona,
        })

    return meteo_citta


def build_testo_html(oggi_data: str, meteo_citta: List[dict]) -> str:
    testo = f"<p>Previsioni meteo per la {REGIONE} per la giornata di {oggi_data}.</p>\n"
    for c in meteo_citta:
        testo += (
            f"<p><strong>{c['nome']}:</strong> {c['descrizione']} con temperature "
            f"tra {c['t_min']:.1f}° e {c['t_max']:.1f}°."
        )
        if c["pioggia"] > 0.5:
            testo += f" Precipitazioni circa {c['pioggia']:.1f} mm."
        else:
            testo += " Nessuna pioggia significativa."
        testo += f"<br><img src='{c['icona']}' alt='Meteo {c['nome']}' style='width:32px;height:32px;'></p>\n"
    return testo


def publish_to_wordpress(titolo: str, testo_html: str, immagine_path: str) -> Optional[str]:
    if not WP_USER or not WP_APP_PASSWORD:
        raise RuntimeError("Credenziali WordPress mancanti (WP_USER/WP_APP_PASSWORD).")

    media_endpoint = WP_URL.replace("/posts", "/media")
    with open(immagine_path, "rb") as f:
        files = {"file": (os.path.basename(immagine_path), f, "image/jpeg")}
        res_upload = requests.post(
            media_endpoint,
            auth=(WP_USER, WP_APP_PASSWORD),
            files=files,
            timeout=60,
        )
        res_upload.raise_for_status()
        media_id = res_upload.json()["id"]

    post = {
        "title": titolo,
        "status": "publish",
        "content": testo_html,
        "featured_media": media_id,
        "categories": [WP_CATEGORY_ID],
    }
    res_post = requests.post(
        WP_URL,
        auth=(WP_USER, WP_APP_PASSWORD),
        json=post,
        timeout=60,
    )
    if res_post.status_code != 201:
        raise RuntimeError(f"Errore pubblicazione WP: {res_post.status_code} {res_post.text}")

    return res_post.json().get("link")


def run_job():
    global last_meteo

    with lock:
        status.running = True
        status.last_error = None
        status.last_post_url = None

    try:
        oggi = datetime.now()
        giorno = giorni_it[oggi.weekday()]
        mese = mesi_it[oggi.month - 1]
        oggi_data = f"{giorno} {oggi.day} {mese} {oggi.year}"
        titolo = f"Meteo Lombardia – {oggi_data}"

        meteo_citta = fetch_meteo_oggi()
        testo_html = build_testo_html(oggi_data, meteo_citta)
        img_path = crea_immagine_con_titolo(f"Meteo di {oggi_data}")

        try:
            post_url = publish_to_wordpress(titolo, testo_html, img_path)
        finally:
            try:
                if os.path.exists(img_path):
                    os.remove(img_path)
            except Exception:
                pass

        with lock:
            last_meteo = meteo_citta
            status.last_run = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            status.last_post_url = post_url

    except Exception as errore:
        with lock:
            status.last_error = str(errore)
            status.last_run = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    finally:
        with lock:
            status.running = False


def run_job_async():
    threading.Thread(target=run_job, daemon=True).start()


app = FastAPI()
templates = Jinja2Templates(directory="templates")
scheduler = BackgroundScheduler(timezone="Europe/Rome")


def update_next_run():
    try:
        job = scheduler.get_job("daily_meteo")
        if job and job.next_run_time:
            with lock:
                status.next_run = job.next_run_time.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        pass


@app.on_event("startup")
def on_startup():
    scheduler.add_job(
        run_job,
        CronTrigger(hour=RUN_HOUR, minute=RUN_MINUTE),
        id="daily_meteo",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
        misfire_grace_time=3600,
    )
    scheduler.start()
    update_next_run()


@app.on_event("shutdown")
def on_shutdown():
    scheduler.shutdown(wait=False)


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    update_next_run()
    with lock:
        s = asdict(status)
        meteo = list(last_meteo)
    return templates.TemplateResponse(
        "index.html",
        {"request": request, "status": s, "meteo": meteo},
    )


@app.post("/run")
def run_now():
    run_job_async()
    return RedirectResponse(url="/", status_code=303)


@app.get("/health")
def health():
    with lock:
        s = asdict(status)
    return JSONResponse({"ok": True, "status": s})
