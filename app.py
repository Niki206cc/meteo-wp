import os
import threading
import tempfile
import time
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Dict, List, Optional

import requests
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse, Response
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool
from cover import CoverSettings, CoverStore, MAX_BYTES, TZ, date_values


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


cover_store = CoverStore(os.getenv("DATA_DIR", "/data"), IMG_DEFAULT_URL, FONT_PATH)
job_lock = threading.Lock()


def crea_immagine_con_titolo(values=None) -> str:
    # The scheduled job and panel preview use exactly the same renderer/settings.
    image = cover_store.render(values=values or date_values(regione=REGIONE))
    with tempfile.NamedTemporaryFile(prefix="meteo_", suffix=".jpg", delete=False) as file:
        file.write(image)
        return file.name


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

    if not job_lock.acquire(blocking=False):
        return

    with lock:
        status.running = True
        status.last_error = None
        status.last_post_url = None

    try:
        oggi = datetime.now(TZ)
        giorno = giorni_it[oggi.weekday()]
        mese = mesi_it[oggi.month - 1]
        oggi_data = f"{giorno} {oggi.day} {mese} {oggi.year}"
        titolo = f"Meteo Lombardia – {oggi_data}"

        meteo_citta = fetch_meteo_oggi()
        testo_html = build_testo_html(oggi_data, meteo_citta)
        img_path = crea_immagine_con_titolo(values=date_values(oggi, REGIONE))

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
            status.last_run = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
            status.last_post_url = post_url

    except Exception as errore:
        with lock:
            status.last_error = str(errore)
            status.last_run = datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")
    finally:
        with lock:
            status.running = False
        job_lock.release()


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
        CronTrigger(hour=RUN_HOUR, minute=RUN_MINUTE, timezone="Europe/Rome"),
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
        {"request": request, "status": s, "meteo": meteo,
         "cover": cover_store.load().model_dump(), "has_upload": cover_store.has_upload()},
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


async def read_cover_form(request: Request):
    length = request.headers.get("content-length")
    if length and int(length) > MAX_BYTES + 1024 * 1024:
        raise ValueError("Il caricamento supera i 15 MB consentiti.")
    async with request.form(max_files=1, max_fields=1) as form:
        settings = CoverSettings.model_validate_json(form.get("settings", "{}"))
        uploaded = form.get("image")
        data = None
        if uploaded is not None and getattr(uploaded, "filename", None):
            data = await uploaded.read(MAX_BYTES + 1)
            if len(data) > MAX_BYTES:
                raise ValueError("L'immagine supera i 15 MB.")
        return settings, data


def cover_error(error):
    if isinstance(error, ValidationError):
        message = "; ".join(".".join(map(str, item["loc"])) + ": " + item["msg"] for item in error.errors())
    elif isinstance(error, OSError):
        message = "Impossibile leggere o salvare la grafica: controlla il volume dati e i permessi."
    else:
        message = str(error)
    return JSONResponse({"error": message}, status_code=400)


@app.post("/cover/preview")
async def cover_preview(request: Request):
    try:
        settings, upload = await read_cover_form(request)
        data = await run_in_threadpool(cover_store.render, settings, upload, date_values(regione=REGIONE))
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
    except (ValueError, OSError) as error:
        return cover_error(error)


@app.get("/cover/preview")
def saved_cover_preview():
    try:
        data = cover_store.render(values=date_values(regione=REGIONE))
        return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
    except (ValueError, OSError) as error:
        return cover_error(error)


@app.post("/cover/settings")
async def save_cover_settings(request: Request):
    try:
        settings, upload = await read_cover_form(request)
        await run_in_threadpool(cover_store.save, settings, upload)
        return JSONResponse({"ok": True, "message": "Grafica salvata: sarà usata nei prossimi invii.",
                             "has_upload": cover_store.has_upload()})
    except (ValueError, OSError) as error:
        return cover_error(error)
