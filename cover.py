"""Persistent cover editor shared by the panel preview and WordPress jobs."""
import json
import os
import re
import tempfile
import threading
import warnings
from datetime import datetime
from io import BytesIO
from pathlib import Path
from string import Formatter
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import requests
from PIL import Image, ImageDraw, ImageFont, ImageOps
from pydantic import BaseModel, ConfigDict, Field, field_validator

MAX_BYTES = 15 * 1024 * 1024
MAX_PIXELS = 40_000_000
TZ = ZoneInfo("Europe/Rome")
DAYS = ["Lunedì", "Martedì", "Mercoledì", "Giovedì", "Venerdì", "Sabato", "Domenica"]
MONTHS = ["gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno", "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre"]


class CoverSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    source: str = "url"
    image_url: str = ""
    fit: str = "contain"
    background: str = "#f8f7f3"
    title: str = Field(default="Meteo di {data}", max_length=300)
    subtitle: str = Field(default="", max_length=300)
    title_x: float = Field(default=50, ge=0, le=100)
    title_y: float = Field(default=10, ge=0, le=100)
    subtitle_x: float = Field(default=50, ge=0, le=100)
    subtitle_y: float = Field(default=88, ge=0, le=100)
    title_size: int = Field(default=64, ge=16, le=180)
    subtitle_size: int = Field(default=40, ge=16, le=180)
    text_color: str = "#ffffff"
    stroke_color: str = "#000000"
    stroke_width: int = Field(default=2, ge=0, le=10)
    box_color: str = "#000000"
    box_opacity: int = Field(default=60, ge=0, le=100)
    text_enabled: bool = True

    @field_validator("source")
    @classmethod
    def valid_source(cls, value):
        if value not in ("url", "upload"):
            raise ValueError("Scegli URL o file caricato.")
        return value

    @field_validator("fit")
    @classmethod
    def valid_fit(cls, value):
        if value not in ("contain", "cover"):
            raise ValueError("Adattamento immagine non valido.")
        return value

    @field_validator("background", "text_color", "stroke_color", "box_color")
    @classmethod
    def valid_color(cls, value):
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            raise ValueError("Usa un colore nel formato #RRGGBB.")
        return value

    @field_validator("image_url")
    @classmethod
    def valid_url(cls, value):
        value = value.strip()
        if value:
            parts = urlsplit(value)
            if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password or len(value) > 2048:
                raise ValueError("Inserisci un URL immagine HTTP o HTTPS senza credenziali.")
        return value

    @field_validator("title", "subtitle")
    @classmethod
    def valid_template(cls, value):
        try:
            for _, field, spec, conversion in Formatter().parse(value):
                if field is not None and (field not in ("data", "giorno", "regione") or spec or conversion):
                    raise ValueError("Sono disponibili solo {data}, {giorno} e {regione}.")
        except ValueError as error:
            raise ValueError(f"Testo non valido: {error}") from error
        return value


def date_values(now=None, regione="Lombardia"):
    now = now or datetime.now(TZ)
    return {"data": f"{DAYS[now.weekday()]} {now.day} {MONTHS[now.month - 1]} {now.year}",
            "giorno": now.strftime("%d/%m/%Y"), "regione": regione}


def decode_image(data):
    if not data or len(data) > MAX_BYTES:
        raise ValueError("L'immagine deve pesare al massimo 15 MB.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(data)) as original:
                if original.format not in ("JPEG", "PNG", "WEBP"):
                    raise ValueError("Scegli un'immagine JPG, PNG o WebP.")
                if original.width * original.height > MAX_PIXELS:
                    raise ValueError("L'immagine supera i 40 milioni di pixel.")
                image = ImageOps.exif_transpose(original).convert("RGBA")
                image.load()
                return image
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError("L'immagine è troppo grande.") from None
    except (OSError, SyntaxError):
        raise ValueError("Il file non è un'immagine valida.") from None


def download_image(url):
    if not url:
        raise ValueError("Inserisci l'URL dell'immagine o carica un file.")
    try:
        with requests.get(url, timeout=(10, 30), stream=True) as response:
            response.raise_for_status()
            chunks, total = [], 0
            for chunk in response.iter_content(65536):
                total += len(chunk)
                if total > MAX_BYTES:
                    raise ValueError("L'immagine supera i 15 MB.")
                chunks.append(chunk)
        return b"".join(chunks)
    except requests.RequestException:
        raise ValueError("Impossibile scaricare l'immagine: controlla l'URL e riprova.") from None


def render_cover(image, settings, values, font_path):
    """Render one 1600x900 JPEG; position is the text block's centre in percent."""
    canvas = Image.new("RGBA", (1600, 900), settings.background)
    if settings.fit == "cover":
        fitted = ImageOps.fit(image, canvas.size, method=Image.Resampling.LANCZOS)
    else:
        fitted = ImageOps.contain(image, canvas.size, method=Image.Resampling.LANCZOS)
    canvas.alpha_composite(fitted, ((1600 - fitted.width) // 2, (900 - fitted.height) // 2))
    overlay = Image.new("RGBA", canvas.size)
    draw = ImageDraw.Draw(overlay)

    def font_at(size):
        for path in (font_path, "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                continue
        raise ValueError("Font non disponibile: controlla FONT_PATH o installa DejaVu Sans.")

    def lines_for(text, font):
        # Wrap using actual font metrics, splitting even unusually long words.
        result = []
        for paragraph in text.split("\n"):
            line = ""
            for char in paragraph:
                if draw.textlength(line + char, font=font) > 1480:
                    split = line.rfind(" ")
                    if split > 0:
                        result.append(line[:split])
                        line = line[split + 1:] + char
                    else:
                        result.append(line)
                        line = char
                else:
                    line += char
            result.append(line.rstrip())
        return "\n".join(result)

    if settings.text_enabled:
        for name in ("title", "subtitle"):
            text = getattr(settings, name).format_map(values).strip()
            if not text:
                continue
            for size in range(getattr(settings, name + "_size"), 7, -1):
                font = font_at(size)
                wrapped = lines_for(text, font)
                bbox = draw.multiline_textbbox((0, 0), wrapped, font=font, spacing=8,
                                               stroke_width=settings.stroke_width, align="center")
                width, height = bbox[2] - bbox[0], bbox[3] - bbox[1]
                if width <= 1480 and height <= 760:
                    break
            x = max(30, min(1570 - width, 1600 * getattr(settings, name + "_x") / 100 - width / 2))
            y = max(25, min(875 - height, 900 * getattr(settings, name + "_y") / 100 - height / 2))
            if settings.box_opacity:
                color = tuple(bytes.fromhex(settings.box_color[1:])) + (round(settings.box_opacity * 2.55),)
                draw.rounded_rectangle((x - 16, y - 12, x + width + 16, y + height + 12), radius=10, fill=color)
            draw.multiline_text((x - bbox[0], y - bbox[1]), wrapped, font=font, spacing=8, align="center",
                                fill=settings.text_color, stroke_fill=settings.stroke_color,
                                stroke_width=settings.stroke_width)
    output = BytesIO()
    Image.alpha_composite(canvas, overlay).convert("RGB").save(output, "JPEG", quality=94, optimize=True)
    return output.getvalue()


class CoverStore:
    def __init__(self, directory, default_url, font_path):
        self.directory = Path(directory)
        self.default_url = default_url
        self.font_path = font_path
        self.lock = threading.RLock()

    def load(self):
        with self.lock:
            path = self.directory / "cover.json"
            if path.exists():
                return CoverSettings.model_validate_json(path.read_text())
            return CoverSettings(image_url=self.default_url)

    def has_upload(self):
        return (self.directory / "background.png").is_file()

    def base_image(self, settings, upload=None):
        if settings.source == "upload":
            if upload:
                image = decode_image(upload)
                image.thumbnail((3200, 3200), Image.Resampling.LANCZOS)
                return image
            path = self.directory / "background.png"
            if not path.exists():
                raise ValueError("Carica un'immagine prima di scegliere File caricato.")
            # The file was already validated, normalised and bounded on save.
            with Image.open(path) as image:
                return image.convert("RGBA")
        return decode_image(download_image(settings.image_url))

    def render(self, settings=None, upload=None, values=None):
        with self.lock:
            settings = settings or self.load()
            image = self.base_image(settings, upload)
        return render_cover(image, settings, values or date_values(), self.font_path)

    def save(self, settings, upload=None):
        # Download/decode before changing any saved state. Preview has no side effects.
        with self.lock:
            image = self.base_image(settings, upload)
            self.directory.mkdir(parents=True, exist_ok=True)
            if settings.source == "upload" and upload:
                data = BytesIO()
                image.save(data, "PNG")
                self._atomic_write("background.png", data.getvalue())
            self._atomic_write("cover.json", settings.model_dump_json(indent=2).encode())

    def _atomic_write(self, name, data):
        fd, path = tempfile.mkstemp(dir=self.directory, prefix=".cover-")
        try:
            with os.fdopen(fd, "wb") as file:
                file.write(data)
            os.replace(path, self.directory / name)
        finally:
            if os.path.exists(path):
                os.unlink(path)
