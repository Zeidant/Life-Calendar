from playwright.sync_api import sync_playwright
from PIL import Image, ImageStat
from string import Template
import base64
import datetime
import functools
import html
import io
import os

CALENDAR_URL = "https://lifecal-virid.vercel.app/months?height=2556&width=1179"
MAX_TAREAS = 5  # más que esto empezaría a subir hacia el calendario
MAX_DONE = 2    # lugares reservados para las Done de hoy (0 = que nunca roben espacio)

ANCHO, ALTO = 1179, 2556

# --- Tamaño y posición del calendario ---
CAL_ESCALA = 1.3   # 1.0 = tamaño original
CAL_TOP = 450      # y (px) donde queda el borde superior del calendario (la etiqueta "Jan")

# Medidas del calendario sin escalar (lifecal 1179x2556), tomadas de la captura
CAL_ORIG_TOP = 885
CAL_ORIG_BOTTOM = 1980  # aprox. donde termina el texto "75d left · 79%"
CAL_ORIG_ANCHO = 721
CAL_ORIG_PIE = 1900  # y (sin escalar) desde donde empieza el texto de abajo ("75d left · 79%")

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
    cal_y1 = (CAL_TOP + (CAL_ORIG_BOTTOM - CAL_ORIG_TOP) * CAL_ESCALA) / ALTO
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
        recorte=f"clip-path: inset(0 0 {ALTO - CAL_ORIG_PIE}px 0);" if pie else "",
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

    desplazamiento_y = CAL_TOP - CAL_ORIG_TOP
    margen_x = round((ANCHO - CAL_ORIG_ANCHO * CAL_ESCALA) / 2)

    tareas_html = ""
    for t in visibles:
        if isinstance(t, str):
            t = {"titulo": t, "prioridad": "", "color": "default", "status": "Not started"}

        color_hex = colores_notion.get(t.get("color", "default"), "#8D99AE")
        estado = t.get("status", "Not started")

        clase_estado = "task-normal"
        if estado == "In progress":
            clase_estado = "task-in-progress"
        elif estado == "Done":
            clase_estado = "task-done"

        titulo = html.escape(t["titulo"])
        prioridad = html.escape(t.get("prioridad", ""))

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
            {tag_html}
        </li>
        """

    more_html = f"<div class='more'>+{extra} más</div>" if extra > 0 else ""
    fecha_hoy = datetime.datetime.now().strftime("%A, %d %b")

    # Fondo con imagen: añade capas y CSS extra. Sin imagen, todo queda exactamente como antes.
    if fondo:
        css_fondo, capas_fondo, info = _preparar_fondo(fondo, modo)
        print("Fondo:", os.path.basename(fondo), info)
    else:
        css_fondo = capas_fondo = ""

    html_content = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600&display=swap');

            * {{ box-sizing: border-box; }}

            body {{
                margin: 0; padding: 0; width: 1179px; height: 2556px;
                background-color: #1a1a1a; 
                color: #FFFFFF; font-family: 'Inter', sans-serif;
                position: relative; overflow: hidden;
            }}

            .bg {{
                position: absolute; inset: 0;
                background: url('{CALENDAR_URL}') no-repeat center center;
                filter: grayscale(1);
                transform-origin: 50% {CAL_ORIG_TOP}px;
                transform: translateY({desplazamiento_y}px) scale({CAL_ESCALA});
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

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": ANCHO, "height": ALTO})
        page.set_content(html_content)

        page.wait_for_load_state("networkidle")
        page.evaluate("document.fonts.ready.then(() => true)")

        page.screenshot(path=output_path)
        browser.close()

    return output_path