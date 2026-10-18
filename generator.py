from playwright.sync_api import sync_playwright
import datetime
import html

CALENDAR_URL = "https://lifecal-virid.vercel.app/months?height=2556&width=1179"
MAX_TAREAS = 5  # más que esto empezaría a subir hacia el calendario
MAX_DONE = 2    # lugares reservados para las Done de hoy (0 = que nunca roben espacio)

# --- Tamaño y posición del calendario ---
CAL_ESCALA = 1.3   # 1.0 = tamaño original
CAL_TOP = 450      # y (px) donde queda el borde superior del calendario (la etiqueta "Jan")

# Medidas del calendario sin escalar (lifecal 1179x2556), tomadas de la captura
CAL_ORIG_TOP = 885
CAL_ORIG_ANCHO = 721


def crear_imagen(tareas, output_path="wallpaper.png"):
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
    margen_x = round((1179 - CAL_ORIG_ANCHO * CAL_ESCALA) / 2)

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
        </style>
    </head>
    <body>
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
        page = browser.new_page(viewport={"width": 1179, "height": 2556})
        page.set_content(html_content)

        page.wait_for_load_state("networkidle")
        page.evaluate("document.fonts.ready.then(() => true)")

        page.screenshot(path=output_path)
        browser.close()

    return output_path