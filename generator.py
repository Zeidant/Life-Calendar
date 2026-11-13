from playwright.sync_api import sync_playwright
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from PIL import Image, ImageStat
from string import Template
import base64
import datetime
import functools
import glob
import html
import io
import logging
import os

import requests

log = logging.getLogger("wallpaper")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Aquí se guardan el calendario descargado y el último wallpaper bueno (monta esta carpeta como volumen)
CACHE_DIR = os.getenv("CACHE_DIR", os.path.join(BASE_DIR, "cache"))
# Si pones aquí un Inter*.woff2 / Inter*.ttf, se usa sin necesitar internet (si no, Google Fonts)
ASSETS_DIR = os.path.join(BASE_DIR, "assets")

# --- Pantalla: iPhone 17 (1206 x 2622) ---
ANCHO, ALTO = 1206, 2622
# Tamaño con el que se pide el calendario a lifecal y con el que se tomaron las medidas de abajo.
# Después se escala a la pantalla, así no dependemos de cómo reparta lifecal otros tamaños.
REF_W, REF_H = 1179, 2556
CALENDAR_URL = f"https://lifecal-virid.vercel.app/months?height={REF_H}&width={REF_W}"
MAX_TAREAS = 5  # más que esto empezaría a subir hacia el calendario
MAX_DONE = 2    # lugares reservados para las Done de hoy (0 = que nunca roben espacio)

# --- Tamaño y posición del calendario ---
CAL_ESCALA = 1.3   # 1.0 = tamaño original
CAL_TOP = round(450 * ALTO / REF_H)  # y (px de la pantalla) del borde superior del calendario ("Jan")

# Medidas del calendario sin escalar (lifecal 1179x2556), tomadas de la captura
CAL_ORIG_TOP = 885
CAL_ORIG_BOTTOM = 1980  # aprox. donde termina el texto "75d left · 79%"
CAL_ORIG_ANCHO = 721
CAL_ORIG_PIE = 1900  # y (sin escalar) desde donde empieza el texto de abajo ("75d left · 79%")
ESCALA_REAL = CAL_ESCALA * (ANCHO / REF_W)  # CAL_ESCALA ya aplicado al tamaño de esta pantalla

# --- Fondo con imagen (opcional) ---
# El calendario de lifecal trae un fondo oscuro OPACO (26,26,26). Para poder mezclarlo con una
# imagen, este contraste lo vuelve negro puro (y deja el blanco intacto).
CAL_BG = 26 / 255
CAL_CONTRASTE = 0.5 / (0.5 - CAL_BG)
FONDO_BRILLO_OBJETIVO = 0.32  # luminosidad media a la que se atenúa una imagen en modo oscuro
UMBRAL_CLARO = 0.75           # luminosidad media (0-1) del calendario para usar el modo claro
UMBRAL_TEXTO_OSCURO = 0.60    # luminosidad media de la zona de tareas para usar texto oscuro

# Textos tenues un poco más claros para que no se pierdan sobre la imagen oscurecida
CSS_TEXTO_SOBRE_DEGRADADO = """
h1 { color: #CFCFCF; }
.date-subtitle, .more { color: #9A9A9A; }
"""

# Texto de las tareas cuando el fondo es claro abajo (se aplica SOLO en ese caso)
CSS_TEXTO_OSCURO = """
.header { border-bottom-color: rgba(0,0,0,0.18); }
h1 { color: #444444; }
.date-subtitle, .more { color: #7A7A7A; }
.task { border-bottom-color: rgba(0,0,0,0.08); }
.status-indicator { border-color: rgba(0,0,0,0.35); }
.task-title { color: #2B2B2B; }
.priority-tag { filter: brightness(0.65) saturate(1.2); }
.task-in-progress .status-indicator { background: #000000; border-color: #000000; }
.task-in-progress .task-title { color: #000000; }
.task-done .status-indicator { background: rgba(0,0,0,0.55); border-color: transparent; }
"""


# ============================================================ FECHAS EN ESPAÑOL
DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES_ES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]


def _fecha_hoy_es(hoy):
    """Ejemplo: 'Miércoles, 12 nov'."""
    return f"{DIAS_ES[hoy.weekday()].capitalize()}, {hoy.day} {MESES_ES[hoy.month - 1]}"


# ============================================================ FECHA DE ENTREGA POR TAREA (opcional)
# Muestra "hoy", "mañana", "vie 14", "21 nov" o "vencida hace 2 d" a la derecha de cada tarea.
# Para quitarla:
#   - rápido:  MOSTRAR_FECHA_ENTREGA = False
#   - del todo: borra este bloque y todas las líneas que llevan la marca [FECHA ENTREGA]
MOSTRAR_FECHA_ENTREGA = True

CSS_FECHA_ENTREGA = """
.due { flex-shrink: 0; font-size: 18px; font-weight: 400; color: #8A8A8A; white-space: nowrap; }
.due.hoy { color: #D8D8D8; }
.due.vencida { color: #E5736B; }
"""
# Versión para fondos claros abajo (texto oscuro)
CSS_FECHA_ENTREGA_TEXTO_OSCURO = """
.due { color: #6B6B6B; }
.due.hoy { color: #222222; }
.due.vencida { color: #B8372E; }
"""


def _fecha_entrega_html(tarea, hoy):
    """HTML de la fecha de entrega de una tarea ('' si no hay que mostrarla)."""
    if not MOSTRAR_FECHA_ENTREGA or not isinstance(tarea, dict):
        return ""
    if (tarea.get("status") or "").strip().lower() == "done":
        return ""  # una tarea terminada ya no necesita fecha
    try:
        entrega = datetime.date.fromisoformat((tarea.get("due_date") or "")[:10])
    except ValueError:
        return ""
    if entrega.year >= 2099:  # "sin fecha" (el valor de relleno de notion.py)
        return ""

    dias = (entrega - hoy).days
    clase = ""
    if dias < -1:
        texto, clase = f"vencida hace {-dias} d", "vencida"
    elif dias == -1:
        texto, clase = "vencida ayer", "vencida"
    elif dias == 0:
        texto, clase = "hoy", "hoy"
    elif dias == 1:
        texto = "mañana"
    elif dias <= 6:
        texto = f"{DIAS_ES[entrega.weekday()][:3]} {entrega.day}"
    else:
        texto = f"{entrega.day} {MESES_ES[entrega.month - 1]}"
    return f"<div class='due {clase}'>{texto}</div>"
# ============================================================ fin fecha de entrega


def _mime_imagen(datos):
    """Tipo MIME según los primeros bytes (o None si no parece una imagen)."""
    if datos.startswith(b"\x89PNG"):
        return "image/png"
    if datos.startswith(b"\xff\xd8"):
        return "image/jpeg"
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return "image/webp"
    return None


def _calendario_src():
    """Qué poner dentro de url('...') para el calendario.

    Lo descarga UNA vez al día y lo guarda en cache/. Así cada render no depende de lifecal
    (más rápido) y si lifecal se cae se usa la copia más reciente."""
    if CALENDAR_URL.startswith("data:"):
        return CALENDAR_URL
    os.makedirs(CACHE_DIR, exist_ok=True)
    ruta = os.path.join(CACHE_DIR, f"calendario_{datetime.date.today().isoformat()}.img")
    if not os.path.exists(ruta):
        try:
            r = requests.get(CALENDAR_URL, timeout=15)
            r.raise_for_status()
            if len(r.content) < 5000 or not _mime_imagen(r.content):
                raise ValueError("lifecal no devolvió una imagen válida")
            tmp = ruta + ".tmp"
            with open(tmp, "wb") as f:
                f.write(r.content)
            os.replace(tmp, ruta)
            for vieja in sorted(glob.glob(os.path.join(CACHE_DIR, "calendario_*.img")))[:-3]:
                os.remove(vieja)  # deja solo las 3 copias más recientes
        except Exception as e:
            previas = sorted(glob.glob(os.path.join(CACHE_DIR, "calendario_*.img")))
            if not previas:
                log.warning("No pude descargar el calendario (%s); dejo que Chromium lo intente", e)
                return CALENDAR_URL
            ruta = previas[-1]
            log.warning("No pude descargar el calendario (%s); uso la copia %s", e, os.path.basename(ruta))
    with open(ruta, "rb") as f:
        datos = f.read()
    return f"data:{_mime_imagen(datos)};base64," + base64.b64encode(datos).decode()


def _css_fuente():
    """@font-face con Inter incrustada si existe en assets/; si no, Google Fonts como antes."""
    for ruta in sorted(glob.glob(os.path.join(ASSETS_DIR, "Inter*"))):
        ext = os.path.splitext(ruta)[1].lower()
        formato = {".woff2": ("font/woff2", "woff2"), ".ttf": ("font/ttf", "truetype")}.get(ext)
        if formato:
            with open(ruta, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
            return (f"@font-face {{ font-family: 'Inter'; font-weight: 100 900; font-display: block; "
                    f"src: url('data:{formato[0]};base64,{b64}') format('{formato[1]}'); }}")
    return "@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap');"


@functools.lru_cache(maxsize=8)
def _cargar_fondo(ruta, mtime):
    """Lee la imagen, la reduce, la incrusta como data URI y mide sus colores.
    (Con set_content, Chromium no deja cargar archivos locales file://)
    mtime solo sirve para invalidar la caché cuando cambias la imagen."""
    im = Image.open(ruta).convert("RGB")
    im.thumbnail((1800, 3900))
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    uri = "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()

    # Recorte "cover" centrado, igual que hará CSS, para medir lo que de verdad se verá
    w, h = im.size
    objetivo = ANCHO / ALTO
    if w / h > objetivo:
        nw = int(h * objetivo)
        x0 = (w - nw) // 2
        vista = im.crop((x0, 0, x0 + nw, h))
    else:
        nh = int(w / objetivo)
        y0 = (h - nh) // 2
        vista = im.crop((0, y0, w, y0 + nh))
    vista.thumbnail((200, 440))
    vw, vh = vista.size

    def zona(y0f, y1f):
        z = vista.crop((0, int(vh * y0f), vw, int(vh * y1f)))
        lum = ImageStat.Stat(z.convert("L")).mean[0] / 255
        rgb = tuple(int(c) for c in ImageStat.Stat(z).mean)
        return lum, rgb

    cal_y0 = CAL_TOP / ALTO
    cal_y1 = (CAL_TOP + (CAL_ORIG_BOTTOM - CAL_ORIG_TOP) * ESCALA_REAL) / ALTO
    lum_cal, _ = zona(cal_y0, cal_y1)      # zona del calendario
    lum_bajo, rgb_bajo = zona(0.78, 0.96)  # zona de las tareas
    return {"uri": uri, "lum_cal": lum_cal, "lum_bajo": lum_bajo, "rgb_bajo": rgb_bajo}


def _preparar_fondo(ruta, modo="auto"):
    """Devuelve (css, capas_html, info) para el fondo con imagen.

    modo "oscuro": imagen atenuada, calendario claro encima (mezcla "screen").
    modo "claro":  imagen tal cual; los puntos del calendario se invierten según lo que haya
                   debajo (negros sobre blanco, blancos sobre negro) con mezcla "difference".
    modo "auto":   elige según la luminosidad de la zona donde queda el calendario.
    """
    datos = _cargar_fondo(ruta, os.path.getmtime(ruta))
    if modo not in ("claro", "oscuro"):
        modo = "claro" if datos["lum_cal"] >= UMBRAL_CLARO else "oscuro"
    claro = modo == "claro"

    brillo = 1.0 if claro else min(1.0, max(0.4, FONDO_BRILLO_OBJETIVO / max(datos["lum_cal"], 0.01)))
    # Si la imagen es clara abajo, las tareas van con texto oscuro y sin degradado
    texto_oscuro = claro and datos["lum_bajo"] >= UMBRAL_TEXTO_OSCURO
    # En modo claro con degradado oscuro abajo, el texto del pie del calendario se dibuja
    # en una capa aparte (clara) para que no se pierda sobre el degradado
    pie = claro and not texto_oscuro

    # Degradado inferior con el color de la imagen (oscurecido) para que se lea la lista
    factor = 0.12 if claro else 0.3
    r, g, b = (int(c * factor) for c in datos["rgb_bajo"])
    inicio, fin, alpha = (63, 73, 0.88) if claro else (55, 100, 0.9)

    css = Template("""
        .fondo {
            position: absolute; inset: 0;
            background: url('$uri') no-repeat center / cover;
            filter: brightness($brillo);
        }
        .scrim {
            position: absolute; inset: 0;
            background: linear-gradient(to bottom, rgba($r,$g,$b,0) $inicio%, rgba($r,$g,$b,$alpha) $fin%);
        }
        .bg { filter: grayscale(1) contrast($contraste); mix-blend-mode: $mezcla; $recorte }
    """).substitute(
        uri=datos["uri"], brillo=round(brillo, 3), r=r, g=g, b=b,
        inicio=inicio, fin=fin, alpha=alpha, contraste=round(CAL_CONTRASTE, 3),
        mezcla="difference" if claro else "screen",
        recorte=f"clip-path: inset(0 0 {REF_H - CAL_ORIG_PIE}px 0);" if pie else "",
    )
    if pie:
        css += Template("""
        .bg.pie {
            clip-path: inset(${y}px 0 0 0);
            filter: grayscale(1) contrast($contraste) brightness(1.7);
            mix-blend-mode: screen;
        }
        """).substitute(y=CAL_ORIG_PIE, contraste=round(CAL_CONTRASTE, 3))
    if texto_oscuro:
        css += CSS_TEXTO_OSCURO
    else:
        css += CSS_TEXTO_SOBRE_DEGRADADO

    capas = "<div class='fondo'></div>" + ("" if texto_oscuro else "<div class='scrim'></div>")
    if pie:
        capas += "<div class='bg pie'></div>"
    info = {"modo": modo, "texto_oscuro": texto_oscuro, "brillo": round(brillo, 2),
            "lum_cal": round(datos["lum_cal"], 2), "lum_bajo": round(datos["lum_bajo"], 2)}
    return css, capas, info


def crear_imagen(tareas, output_path="wallpaper.png", fondo=None, modo="auto"):
    """fondo: ruta de una imagen (None = el diseño de siempre, sin imagen).
    modo:  "auto" | "claro" | "oscuro" (solo se usa si hay fondo)."""
    colores_notion = {
        "red": "#FF4B4B", "yellow": "#FFD166", "green": "#06D6A0",
        "blue": "#118AB2", "default": "#8D99AE"
    }

    # --- LÓGICA DE ORDENAMIENTO ---
    # Jerarquía estricta (Python compara la tupla elemento por elemento):
    #   1° Done al fondo
    #   2° In progress antes que el resto
    #   3° Due date ascendente (sin fecha = "2099-12-31" va al final)
    #   4° Prioridad: High > Medium > Low > sin prioridad
    def calcular_peso(tarea):
        # Los textos sueltos se tratan como "Not started" sin fecha ni prioridad
        if isinstance(tarea, str):
            return (0, 1, "2099-12-31", 3)

        estado = (tarea.get("status") or "").strip().lower()
        prioridad = (tarea.get("prioridad") or "").strip().lower()
        fecha = (tarea.get("due_date") or "2099-12-31")[:10]

        es_done = 1 if estado == "done" else 0                # 1° Done al fondo
        no_en_progreso = 0 if estado == "in progress" else 1  # 2° In progress primero
        peso_prio = {"high": 0, "medium": 1, "low": 2}.get(prioridad, 3)  # 4° sin prioridad al final

        # 3° fecha ascendente: YYYY-MM-DD se ordena bien como texto y "2099-12-31" queda al final
        return (es_done, no_en_progreso, fecha, peso_prio)

    tareas_ordenadas = sorted(tareas, key=calcular_peso)
    # -------------------------------------------------------------

    def es_done(t):
        return isinstance(t, dict) and (t.get("status") or "").strip().lower() == "done"

    pendientes = [t for t in tareas_ordenadas if not es_done(t)]
    hechas = [t for t in tareas_ordenadas if es_done(t)]

    # Reservamos hasta MAX_DONE lugares para las Done; el resto es para pendientes
    cupo_pendientes = MAX_TAREAS - min(len(hechas), MAX_DONE)
    visibles = pendientes[:cupo_pendientes]
    visibles += hechas[:MAX_TAREAS - len(visibles)]  # si sobra espacio, entran más Done
    extra = len(tareas_ordenadas) - len(visibles)

    # El calendario se escala desde el borde superior de su contenido y luego se mueve a CAL_TOP
    hoy = datetime.date.today()
    desplazamiento_x = (ANCHO - REF_W) / 2
    desplazamiento_y = CAL_TOP - CAL_ORIG_TOP
    margen_x = round((ANCHO - CAL_ORIG_ANCHO * ESCALA_REAL) / 2)

    tareas_html = ""
    for t in visibles:
        if isinstance(t, str):
            t = {"titulo": t, "prioridad": "", "color": "default", "status": "Not started"}

        color_hex = colores_notion.get(t.get("color", "default"), "#8D99AE")
        estado = (t.get("status") or "").strip().lower()

        clase_estado = "task-normal"
        if estado == "in progress":
            clase_estado = "task-in-progress"
        elif estado == "done":
            clase_estado = "task-done"

        titulo = html.escape(t.get("titulo") or "(sin título)")
        prioridad = html.escape(t.get("prioridad") or "")
        due_html = _fecha_entrega_html(t, hoy)  # [FECHA ENTREGA]

        tag_html = (
            f"<div class='priority-tag' style='color: {color_hex}; "
            f"border-color: {color_hex}55; background-color: {color_hex}12;'>{prioridad}</div>"
            if prioridad else ""
        )

        tareas_html += f"""
        <li class='task {clase_estado}'>
            <div class='task-main'>
                <div class='status-indicator'></div>
                <div class='task-title'>{titulo}</div>
            </div>
            {due_html}<!-- [FECHA ENTREGA] -->
            {tag_html}
        </li>
        """

    more_html = f"<div class='more'>+{extra} más</div>" if extra > 0 else ""
    fecha_hoy = _fecha_hoy_es(hoy)

    # Fondo con imagen: añade capas y CSS extra. Sin imagen, todo queda exactamente como antes.
    if fondo:
        css_fondo, capas_fondo, info = _preparar_fondo(fondo, modo)
        log.info("Fondo: %s %s", os.path.basename(fondo), info)
    else:
        css_fondo = capas_fondo = ""

    css_fecha = ""  # [FECHA ENTREGA]
    if MOSTRAR_FECHA_ENTREGA:  # [FECHA ENTREGA]
        css_fecha = CSS_FECHA_ENTREGA + (CSS_FECHA_ENTREGA_TEXTO_OSCURO if fondo and info["texto_oscuro"] else "")  # [FECHA ENTREGA]

    calendario_src = _calendario_src()
    css_fuente = _css_fuente()

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            {css_fuente}

            * {{ box-sizing: border-box; }}

            body {{
                margin: 0; padding: 0; width: {ANCHO}px; height: {ALTO}px;
                background-color: #1a1a1a; 
                color: #FFFFFF; font-family: 'Inter', sans-serif;
                position: relative; overflow: hidden;
            }}

            .bg {{
                position: absolute; left: 0; top: 0; width: {REF_W}px; height: {REF_H}px;
                background: url('{calendario_src}') no-repeat 0 0 / 100% 100%;
                filter: grayscale(1);
                transform-origin: 50% {CAL_ORIG_TOP}px;
                transform: translate({desplazamiento_x}px, {desplazamiento_y}px) scale({ESCALA_REAL});
            }}

            .tasks-container {{
                position: absolute;
                bottom: 110px; left: {margen_x}px; right: {margen_x}px;
            }}

            .header {{
                display: flex; justify-content: space-between; align-items: baseline;
                padding-bottom: 18px;
                border-bottom: 1px solid rgba(255,255,255,0.12);
            }}
            h1 {{ margin: 0; font-size: 24px; font-weight: 500; letter-spacing: 0.5px; color: #B5B5B5; }}
            .date-subtitle {{ font-size: 18px; font-weight: 300; color: #6E6E6E; }}

            ul {{ list-style: none; padding: 0; margin: 0; }}

            .task {{
                display: flex; justify-content: space-between; align-items: center; gap: 24px;
                padding: 21px 0;
                border-bottom: 1px solid rgba(255,255,255,0.06);
            }}
            .task:last-child {{ border-bottom: none; }}

            .task-main {{ display: flex; align-items: center; gap: 22px; flex: 1; min-width: 0; }}

            .status-indicator {{
                width: 16px; height: 16px; border-radius: 50%;
                border: 2px solid rgba(255,255,255,0.3); flex-shrink: 0;
            }}
            .task-title {{
                font-size: 28px; font-weight: 400; letter-spacing: -0.3px; color: #CFCFCF;
                white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
            }}

            .priority-tag {{
                font-size: 16px; font-weight: 500; letter-spacing: 0.3px;
                padding: 5px 14px; border-radius: 30px;
                border: 1px solid; flex-shrink: 0;
            }}

            .task-in-progress .status-indicator {{ background: #FFFFFF; border-color: #FFFFFF; }}
            .task-in-progress .task-title {{ color: #FFFFFF; font-weight: 500; }}

            .task-done {{ opacity: 0.35; }}
            .task-done .task-title {{ text-decoration: line-through; }}
            .task-done .status-indicator {{ background: rgba(255,255,255,0.6); border-color: transparent; }}

            .more {{ padding-top: 14px; font-size: 18px; font-weight: 300; color: #6E6E6E; }}

            {css_fecha}  /* [FECHA ENTREGA] */

            {css_fondo}
        </style>
    </head>
    <body>
        {capas_fondo}
        <div class="bg"></div>
        <div class="tasks-container">
            <div class="header">
                <h1>Pendientes</h1>
                <div class="date-subtitle">{fecha_hoy}</div>
            </div>
            <ul>
                {tareas_html}
            </ul>
            {more_html}
        </div>
    </body>
    </html>
    """

    tmp_path = output_path + ".tmp.png"
    with sync_playwright() as p:
        # --disable-dev-shm-usage: en Docker /dev/shm es pequeño y Chromium se cae con imágenes grandes
        browser = p.chromium.launch(headless=True, args=["--disable-dev-shm-usage"])
        try:
            page = browser.new_page(viewport={"width": ANCHO, "height": ALTO})
            page.set_default_timeout(20000)
            page.set_content(html_content, wait_until="load", timeout=20000)

            # Con calendario y fuente locales no hay red que esperar; si hubiera (Google Fonts,
            # calendario remoto) esperamos un poco, pero sin bloquear el render si tarda.
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except PlaywrightTimeout:
                log.warning("La red no quedó inactiva en 8 s; sigo con lo que hay")
            page.evaluate("document.fonts.ready.then(() => true)")

            page.screenshot(path=tmp_path)
        finally:
            browser.close()
    os.replace(tmp_path, output_path)  # nunca queda un PNG a medias

    return output_path