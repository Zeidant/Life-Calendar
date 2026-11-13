import asyncio
import datetime
import hashlib
import json
import logging
import os
import re
import secrets
import threading
import time
from contextlib import asynccontextmanager
from io import BytesIO
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, Header, HTTPException, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse, HTMLResponse, Response
from PIL import Image, ImageOps

from generator import ALTO, ANCHO, CACHE_DIR, crear_imagen
from notion import obtener_tareas

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("wallpaper")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONDOS_DIR = os.getenv("FONDOS_DIR", os.path.join(BASE_DIR, "fondos"))
ACTIVO_FILE = os.path.join(FONDOS_DIR, "activo.txt")
OUTPUT_FILE = os.path.join(CACHE_DIR, "current_wallpaper.png")
ULTIMO_BUENO = os.path.join(CACHE_DIR, "ultimo_wallpaper.png")

# Si defines PANEL_TOKEN, subir/borrar/activar fondos exige ese token (el panel te lo pide solo).
PANEL_TOKEN = os.getenv("PANEL_TOKEN", "")
# Las tareas de Notion se reutilizan este tiempo (segundos) antes de volver a pedirlas.
TTL_TAREAS = int(os.getenv("TTL_TAREAS", "120"))
# Cada cuánto las refresca en segundo plano, para que el iPhone casi siempre reciba la imagen al instante.
REFRESCO_NOTION = int(os.getenv("REFRESCO_NOTION", "300"))
# Si Notion falla, se siguen usando las últimas tareas buenas hasta este tiempo.
MAX_EDAD_TAREAS = 6 * 3600

MAX_UPLOAD = 20 * 1024 * 1024
EXTENSIONES = (".jpg", ".jpeg", ".png", ".webp")
NOMBRE_OK = re.compile(r"^[a-z0-9_-]{1,40}$")
MODOS = ("auto", "claro", "oscuro")

Image.MAX_IMAGE_PIXELS = 60_000_000
os.makedirs(FONDOS_DIR, exist_ok=True)
os.makedirs(CACHE_DIR, exist_ok=True)

_lock = threading.Lock()          # un solo render a la vez
_tareas_lock = threading.Lock()   # una sola consulta a Notion a la vez
_tareas_cache = {"datos": None, "t": 0.0}
_render_cache = {"clave": None, "datos": None, "t": 0.0, "ms": None}


# ---------------------------------------------------------------- Notion con caché
def _tareas(forzar=False):
    """Tareas de Notion con caché corta. Si Notion falla, usa la última copia buena (hasta 6 h);
    si no hay ninguna, deja pasar la excepción."""
    with _tareas_lock:
        c = _tareas_cache
        ahora = time.time()
        if not forzar and c["datos"] is not None and ahora - c["t"] < TTL_TAREAS:
            return c["datos"]
        try:
            datos = obtener_tareas()
        except Exception as e:
            log.warning("Notion falló: %s", e)
            if c["datos"] is not None and ahora - c["t"] < MAX_EDAD_TAREAS:
                return c["datos"]
            raise
        c["datos"], c["t"] = datos, ahora
        return datos


async def refrescar_notion_periodicamente():
    while True:
        try:
            await run_in_threadpool(_tareas, True)
        except Exception as e:
            log.warning("Refresco de Notion falló: %s", e)
        await asyncio.sleep(REFRESCO_NOTION)


@asynccontextmanager
async def lifespan(app: FastAPI):
    tarea = asyncio.create_task(refrescar_notion_periodicamente())
    yield
    tarea.cancel()


app = FastAPI(title="Life Wallpaper API", lifespan=lifespan)


# ---------------------------------------------------------------- helpers de fondos
def _buscar_fondo(nombre):
    if not NOMBRE_OK.match(nombre):
        return None
    for f in os.listdir(FONDOS_DIR):
        base, ext = os.path.splitext(f)
        if base == nombre and ext.lower() in EXTENSIONES:
            return os.path.join(FONDOS_DIR, f)
    return None


def _listar_fondos():
    nombres = {os.path.splitext(f)[0] for f in os.listdir(FONDOS_DIR)
               if os.path.splitext(f)[1].lower() in EXTENSIONES}
    return sorted(n for n in nombres if NOMBRE_OK.match(n))


def _fondo_activo():
    try:
        with open(ACTIVO_FILE, encoding="utf-8") as f:
            nombre = f.read().strip()
    except FileNotFoundError:
        return None
    return nombre if nombre and _buscar_fondo(nombre) else None


def _guardar_activo(nombre):
    tmp = ACTIVO_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(nombre or "")
    os.replace(tmp, ACTIVO_FILE)


def _slug(texto):
    return re.sub(r"[^a-z0-9_-]+", "_", texto.lower()).strip("_")[:40]


def _auth(x_token: str = Header(default="")):
    if PANEL_TOKEN and not secrets.compare_digest(x_token.encode(), PANEL_TOKEN.encode()):
        raise HTTPException(status_code=401, detail="Token inválido")


# ---------------------------------------------------------------- generar la imagen
def _responder(datos, estado):
    # X-Cache: HIT = reutilizada | MISS = recién generada | STALE = falló y se sirvió la última buena
    return Response(content=datos, media_type="image/png",
                    headers={"Cache-Control": "no-store", "X-Cache": estado})


def _leer_ultimo_bueno():
    try:
        with open(ULTIMO_BUENO, "rb") as f:
            return f.read()
    except FileNotFoundError:
        return None


def _clave_render(tareas, fondo_nombre, ruta, modo):
    """Misma clave = misma imagen. Incluye la fecha para que cambie al empezar el día."""
    mtime = os.path.getmtime(ruta) if ruta else 0
    base = json.dumps([tareas, fondo_nombre, mtime, modo, datetime.date.today().isoformat()],
                      sort_keys=True, default=str)
    return hashlib.sha1(base.encode("utf-8")).hexdigest()


def _generar(fondo_nombre: Optional[str], modo: str):
    if modo not in MODOS:
        raise HTTPException(status_code=400, detail=f"modo debe ser uno de {MODOS}")

    ruta = None
    if fondo_nombre and fondo_nombre != "ninguno":
        ruta = _buscar_fondo(fondo_nombre)
        if not ruta:
            raise HTTPException(status_code=404, detail=f"No existe el fondo '{fondo_nombre}'")
    else:
        fondo_nombre = None

    try:
        tareas = _tareas()
    except Exception:
        # Notion caído y sin copia reciente: mejor la última imagen buena que un error
        ultimo = _leer_ultimo_bueno()
        if ultimo:
            log.warning("Notion no responde y no hay tareas en caché: sirvo el último wallpaper bueno")
            return _responder(ultimo, "STALE")
        tareas = ["Sin conexión con Notion"]

    if not tareas:
        tareas = ["¡Día libre!", "No hay tareas pendientes para hoy."]

    clave = _clave_render(tareas, fondo_nombre, ruta, modo)
    c = _render_cache
    if c["clave"] == clave and c["datos"]:
        return _responder(c["datos"], "HIT")

    with _lock:
        if c["clave"] == clave and c["datos"]:  # otro hilo ya la generó mientras esperábamos
            return _responder(c["datos"], "HIT")

        log.info("Generando imagen (fondo: %s, modo: %s)", fondo_nombre or "ninguno", modo)
        t0 = time.time()
        try:
            crear_imagen(tareas, OUTPUT_FILE, fondo=ruta, modo=modo)
            with open(OUTPUT_FILE, "rb") as f:
                datos = f.read()
        except Exception:
            log.exception("Falló la generación de la imagen")
            ultimo = c["datos"] or _leer_ultimo_bueno()
            if ultimo:
                return _responder(ultimo, "STALE")
            raise HTTPException(status_code=503, detail="No se pudo generar la imagen")

        c.update(clave=clave, datos=datos, t=time.time(), ms=int((time.time() - t0) * 1000))
        tmp = ULTIMO_BUENO + ".tmp"
        with open(tmp, "wb") as f:
            f.write(datos)
        os.replace(tmp, ULTIMO_BUENO)
        log.info("Imagen lista en %d ms", c["ms"])

    return _responder(datos, "MISS")


# --- 1. RUTA PRINCIPAL (IPHONE) ---
@app.get("/wallpaper")
def generar_wallpaper(modo: str = Query("auto"), fondo: Optional[str] = Query(None)):
    """Sin parámetros usa el fondo activado en el panel.
    ?fondo=nombre fuerza un fondo; ?fondo=ninguno fuerza el diseño sin imagen."""
    return _generar(fondo if fondo is not None else _fondo_activo(), modo)


@app.get("/health")
def health():
    """Estado rápido (no llama a Notion ni abre Chromium). Lo usa el healthcheck de Docker."""
    ahora = time.time()
    t, r = _tareas_cache, _render_cache
    return {
        "ok": True,
        "fondo_activo": _fondo_activo(),
        "fondos": len(_listar_fondos()),
        "tareas_en_cache": None if t["datos"] is None else len(t["datos"]),
        "tareas_edad_s": None if t["datos"] is None else int(ahora - t["t"]),
        "ultimo_render_hace_s": None if not r["t"] else int(ahora - r["t"]),
        "ultimo_render_ms": r["ms"],
    }


# --- 2. PANEL DE CONTROL ---
# La interfaz vive en panel.html (junto a este archivo). Se lee en cada petición, así que
# puedes retocarla sin reiniciar. En Docker, asegúrate de que el Dockerfile copie panel.html.
PANEL_FILE = os.path.join(BASE_DIR, "panel.html")


@app.get("/panel", response_class=HTMLResponse)
def panel():
    try:
        with open(PANEL_FILE, encoding="utf-8") as f:
            contenido = f.read()
    except FileNotFoundError:
        raise HTTPException(status_code=500, detail="Falta panel.html junto a main.py")
    # El panel necesita la proporción de la pantalla para el recorte y las miniaturas
    return contenido.replace("__ANCHO__", str(ANCHO)).replace("__ALTO__", str(ALTO))


# --- RUTAS INTERNAS DEL PANEL ---
@app.get("/fondos")
def listar_fondos():
    return {"activo": _fondo_activo(), "fondos": _listar_fondos()}


@app.get("/fondos/{nombre}")
def ver_fondo(nombre: str):
    ruta = _buscar_fondo(nombre)
    if not ruta:
        raise HTTPException(status_code=404, detail="Fondo no encontrado")
    return FileResponse(ruta)


@app.post("/fondos", dependencies=[Depends(_auth)])
def subir_fondo(
    archivo: UploadFile = File(...),
    nombre: str = Form(""),
    # Recorte opcional, como fracciones (0 a 1) de la imagen ya orientada: izquierda, arriba, derecha, abajo
    x0: Optional[float] = Form(None),
    y0: Optional[float] = Form(None),
    x1: Optional[float] = Form(None),
    y1: Optional[float] = Form(None),
):
    datos = archivo.file.read(MAX_UPLOAD + 1)
    if len(datos) > MAX_UPLOAD:
        raise HTTPException(status_code=413, detail="Máximo 20 MB")
    base = _slug(nombre) or _slug(os.path.splitext(archivo.filename or "")[0]) or "fondo"
    if base == "ninguno":
        raise HTTPException(status_code=400, detail="Nombre reservado")
    try:
        # exif_transpose: las fotos del iPhone guardan la rotación en EXIF; sin esto salen de lado
        im = ImageOps.exif_transpose(Image.open(BytesIO(datos))).convert("RGB")
    except Exception:
        raise HTTPException(status_code=400, detail="Imagen inválida")
    if None not in (x0, y0, x1, y1):
        if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
            raise HTTPException(status_code=400, detail="Recorte inválido")
        w, h = im.size
        im = im.crop((round(x0 * w), round(y0 * h), round(x1 * w), round(y1 * h)))
        if min(im.size) < 200:
            raise HTTPException(status_code=400, detail="El recorte es demasiado pequeño")
    im.thumbnail((1800, 3900))
    previa = _buscar_fondo(base)
    if previa:
        os.remove(previa)
    im.save(os.path.join(FONDOS_DIR, base + ".jpg"), "JPEG", quality=90)
    log.info("Fondo subido: %s", base)
    return {"ok": True, "nombre": base}


@app.delete("/fondos/{nombre}", dependencies=[Depends(_auth)])
def borrar_fondo(nombre: str):
    ruta = _buscar_fondo(nombre)
    if not ruta:
        raise HTTPException(status_code=404, detail="No existe")
    era_activo = _fondo_activo() == nombre
    os.remove(ruta)
    if era_activo:
        _guardar_activo(None)
    return {"ok": True}


@app.post("/fondos/{nombre}/activar", dependencies=[Depends(_auth)])
def activar_fondo(nombre: str):
    if nombre == "ninguno":
        _guardar_activo(None)
    elif _buscar_fondo(nombre):
        _guardar_activo(nombre)
    else:
        raise HTTPException(status_code=404, detail="No existe")
    return {"activo": _fondo_activo()}