import os
import re
import threading
import asyncio
from io import BytesIO
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.concurrency import run_in_threadpool
from PIL import Image, UnidentifiedImageError

from generator import crear_imagen
from notion import obtener_tareas

# --- TAREA EN SEGUNDO PLANO (SILENCIOSA) ---
async def sincronizar_notion_periodicamente():
    while True:
        try:
            await run_in_threadpool(obtener_tareas)
        except Exception:
            pass
        # Pausa estricta de 5 horas (18000 segundos)
        await asyncio.sleep(18000)

@asynccontextmanager
async def lifespan(app: FastAPI):
    tarea_fondo = asyncio.create_task(sincronizar_notion_periodicamente())
    yield
    tarea_fondo.cancel()

# -------------------------------------------
app = FastAPI(title="Life Wallpaper API", lifespan=lifespan)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FONDOS_DIR = os.getenv("FONDOS_DIR", os.path.join(BASE_DIR, "fondos"))
ACTIVO_FILE = os.path.join(FONDOS_DIR, "activo.txt")

MAX_UPLOAD = 20 * 1024 * 1024
EXTENSIONES = (".jpg", ".jpeg", ".png", ".webp")
NOMBRE_OK = re.compile(r"^[a-z0-9_-]{1,40}$")
MODOS = ("auto", "claro", "oscuro")

Image.MAX_IMAGE_PIXELS = 60_000_000
os.makedirs(FONDOS_DIR, exist_ok=True)
_lock = threading.Lock() 

# --- HELPERS DEL SISTEMA DE ARCHIVOS ---
def _buscar_fondo(nombre):
    if not NOMBRE_OK.match(nombre): return None
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
    with open(ACTIVO_FILE, "w", encoding="utf-8") as f:
        f.write(nombre or "")

def _slug(texto):
    return re.sub(r"[^a-z0-9_-]+", "_", texto.lower()).strip("_")[:40]


# --- 1. RUTA PRINCIPAL (IPHONE) ---
@app.get("/wallpaper")
def generar_wallpaper(modo: str = Query("auto")):
    """Genera la imagen con el fondo que esté activado desde el panel."""
    if modo not in MODOS:
        raise HTTPException(status_code=400, detail=f"modo debe ser uno de {MODOS}")

    fondo_nombre = _fondo_activo()
    ruta = _buscar_fondo(fondo_nombre) if fondo_nombre else None

    print("📱 Petición del iPhone recibida. Obteniendo tareas...")
    tareas = obtener_tareas()

    if not tareas:
        tareas = ["¡Día libre!", "No hay tareas pendientes para hoy."]

    print(f"🎨 Generando imagen... (Fondo activo: {fondo_nombre or 'Diseño original sin imagen'})")
    output_file = os.path.join(BASE_DIR, "current_wallpaper.png")
    
    with _lock:
        crear_imagen(tareas, output_file, fondo=ruta, modo=modo)
        with open(output_file, "rb") as f:
            datos = f.read()

    print("🚀 Imagen enviada con éxito.")
    return Response(content=datos, media_type="image/png")


# --- 2. RUTAS DEL PANEL DE CONTROL ---
PANEL_HTML = """<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Centro de Control - Life Wallpaper</title>
<style>
  body { font: 16px system-ui, sans-serif; background: #111; color: #eee; max-width: 640px; margin: auto; padding: 20px; }
  .f { display: flex; align-items: center; gap: 12px; padding: 10px; border-bottom: 1px solid #2a2a2a; border-radius: 10px; margin-bottom: 8px;}
  .f.on { background: #1d2a38; border: 1px solid #3a5a78;}
  img { width: 48px; height: 104px; object-fit: cover; border-radius: 6px; background: #222; }
  .sin { width: 48px; height: 104px; border-radius: 6px; background: #1a1a1a; border: 1px dashed #444; }
  button, input { font: inherit; padding: 8px 12px; border-radius: 8px; border: 1px solid #444; background: #222; color: #eee; cursor: pointer;}
  button:hover { background: #333; }
  a { color: #7ab8ff; text-decoration: none; }
  a:hover { text-decoration: underline; }
  #msg { color: #06D6A0; min-height: 1.4em; font-weight: 500;}
  .btn-usar { background: #06D6A0; color: #000; border: none; font-weight: 600;}
  .btn-usar:hover { background: #05b586; }
</style></head><body>
<h2>⚙️ Control de Wallpaper</h2>
<div id="lista"></div>
<h3 style="margin-top: 30px;">➕ Subir nuevo fondo</h3>
<p><input id="nombre" placeholder="Nombre (ej. gojo_color)"></p>
<p><input id="file" type="file" accept="image/*"> <button onclick="subir()">Subir a la nube</button></p>
<p id="msg"></p>
<script>
const $ = s => document.querySelector(s);

async function cargar() {
  const d = await (await fetch('/fondos')).json();
  const activo = d.activo || 'ninguno';
  const items = [{n: 'ninguno', t: 'Ninguno (Fondo oscuro original)'}].concat(d.fondos.map(n => ({n, t: n})));
  $('#lista').innerHTML = items.map(i => `
    <div class="f ${activo === i.n ? 'on' : ''}">
      ${i.n === 'ninguno' ? '<div class="sin"></div>' : `<img src="/fondos/${i.n}">`}
      <b style="flex:1">${i.t}</b>
      ${i.n === 'ninguno' ? '' : `<a href="/fondos/${i.n}" target="_blank">Ver</a>`}
      <button class="${activo !== i.n ? 'btn-usar' : ''}" onclick="activar('${i.n}')">${activo === i.n ? '✅ En uso' : 'Usar este'}</button>
      ${i.n === 'ninguno' ? '' : `<button onclick="borrar('${i.n}')">🗑️</button>`}
    </div>`).join('');
}
async function llamar(url, opts) {
  $('#msg').textContent = 'Procesando...';
  const r = await fetch(url, opts);
  $('#msg').textContent = r.ok ? '¡Listo!' : 'Error: ' + (await r.text());
  setTimeout(() => $('#msg').textContent = '', 3000);
  cargar();
}
const activar = n => llamar(`/fondos/${n}/activar`, {method: 'POST'});
const borrar = n => confirm('¿Seguro que quieres borrar ' + n + '?') && llamar(`/fondos/${n}`, {method: 'DELETE'});
function subir() {
  if (!$('#file').files[0]) { $('#msg').textContent = 'Elige una imagen primero'; return; }
  const f = new FormData();
  f.append('archivo', $('#file').files[0]);
  f.append('nombre', $('#nombre').value);
  llamar('/fondos', {method: 'POST', body: f});
}
cargar();
</script></body></html>"""

@app.get("/panel", response_class=HTMLResponse)
def panel():
    """El centro de control visual."""
    return PANEL_HTML

# --- RUTAS INTERNAS DEL PANEL (Invisibles para el iPhone) ---
@app.get("/fondos")
def listar_fondos():
    return {"activo": _fondo_activo(), "fondos": _listar_fondos()}

@app.get("/fondos/{nombre}")
def ver_fondo(nombre: str):
    ruta = _buscar_fondo(nombre)
    if not ruta: raise HTTPException(status_code=404, detail="Fondo no encontrado")
    return FileResponse(ruta)

@app.post("/fondos")
def subir_fondo(archivo: UploadFile = File(...), nombre: str = Form("")):
    datos = archivo.file.read(MAX_UPLOAD + 1)
    if len(datos) > MAX_UPLOAD: raise HTTPException(status_code=413, detail="Máximo 20 MB")
    base = _slug(nombre) or _slug(os.path.splitext(archivo.filename or "")[0]) or "fondo"
    if base == "ninguno": raise HTTPException(status_code=400, detail="Nombre reservado")
    try:
        im = Image.open(BytesIO(datos)).convert("RGB")
    except Exception:
        raise HTTPException(status_code=400, detail="Imagen inválida")
    im.thumbnail((1800, 3900))
    previa = _buscar_fondo(base)
    if previa: os.remove(previa)
    im.save(os.path.join(FONDOS_DIR, base + ".jpg"), "JPEG", quality=90)
    return {"ok": True, "nombre": base}

@app.delete("/fondos/{nombre}")
def borrar_fondo(nombre: str):
    ruta = _buscar_fondo(nombre)
    if not ruta: raise HTTPException(status_code=404, detail="No existe")
    era_activo = _fondo_activo() == nombre
    os.remove(ruta)
    if era_activo: _guardar_activo(None)
    return {"ok": True}

@app.post("/fondos/{nombre}/activar")
def activar_fondo(nombre: str):
    if nombre == "ninguno":
        _guardar_activo(None)
    elif _buscar_fondo(nombre):
        _guardar_activo(nombre)
    else:
        raise HTTPException(status_code=404, detail="No existe")
    return {"activo": _fondo_activo()}