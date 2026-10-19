import os
import requests
import datetime
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

def actualizar_fecha_notion(page_id, fecha_str, nombre_columna):
    url = f"https://api.notion.com/v1/pages/{page_id}"
    date_value = {"start": fecha_str} if fecha_str else None
    payload = {
        "properties": {
            nombre_columna: {
                "date": date_value
            }
        }
    }
    res = requests.patch(url, json=payload, headers=HEADERS)
    if res.status_code != 200:
        print(f"⚠️ Error actualizando Notion: {res.text}")

def obtener_tareas():
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    payload = {"page_size": 20}

    respuesta = requests.post(url, json=payload, headers=HEADERS)
    
    if respuesta.status_code != 200:
        print("⚠️ Error conectando a Notion:", respuesta.text)
        return []

    datos = respuesta.json()
    tareas = []
    
    zona_local = ZoneInfo("America/Santo_Domingo")
    hoy = datetime.datetime.now(zona_local).date()

    for pagina in datos.get("results", []):
        page_id = pagina["id"]
        propiedades = pagina.get("properties", {})
        
        llave_titulo = next((key for key, val in propiedades.items() if val["type"] == "title"), None)
        nombre_tarea = propiedades[llave_titulo]["title"][0]["plain_text"] if llave_titulo and propiedades[llave_titulo]["title"] else "Sin título"
            
        prioridad_data = propiedades.get("Priority", {}).get("select", {})
        nombre_prioridad = prioridad_data.get("name", "") if prioridad_data else ""
        color_prioridad = prioridad_data.get("color", "default") if prioridad_data else "default"

        status_data = propiedades.get("Status", {}).get("status", {})
        nombre_status = status_data.get("name", "Not started") if status_data else "Not started"

        llave_date_done = next((k for k in propiedades.keys() if k.strip().lower() == "date done"), None)
        fecha_completado_str = ""
        prop_date_done = {}

        if llave_date_done:
            prop_date_done = propiedades.get(llave_date_done, {})
            tipo_prop = prop_date_done.get("type")
            
            if tipo_prop == "formula":
                formula_data = prop_date_done.get("formula", {})
                if formula_data.get("type") == "date" and formula_data.get("date"):
                    fecha_completado_str = formula_data.get("date").get("start", "")
            elif tipo_prop == "date":
                date_data = prop_date_done.get("date")
                if date_data:
                    fecha_completado_str = date_data.get("start", "")

        # --- LÓGICA SILENCIOSA ---
        if nombre_status == "Done":
            if not fecha_completado_str and llave_date_done:
                fecha_hoy_str = hoy.strftime("%Y-%m-%d")
                print(f"✨ [Auto-Sync] Añadiendo fecha de hoy a '{nombre_tarea}'")
                actualizar_fecha_notion(page_id, fecha_hoy_str, llave_date_done)
                fecha_completado_str = fecha_hoy_str
            
            if fecha_completado_str:
                try:
                    fecha_completado = datetime.datetime.strptime(fecha_completado_str[:10], "%Y-%m-%d").date()
                    if fecha_completado < hoy:
                        continue # Se omite en silencio total
                except Exception:
                    pass
        else:
            if fecha_completado_str and prop_date_done.get("type") == "date" and llave_date_done:
                print(f"🧹 [Auto-Sync] Limpiando fecha residual de '{nombre_tarea}'")
                actualizar_fecha_notion(page_id, None, llave_date_done)

        due_date_data = propiedades.get("Due date", {}).get("date", {})
        due_date_str = due_date_data.get("start", "2099-12-31") if due_date_data else "2099-12-31"

        tareas.append({
            "titulo": nombre_tarea,
            "prioridad": nombre_prioridad,
            "color": color_prioridad,
            "status": nombre_status,
            "due_date": due_date_str[:10]
        })

    return tareas