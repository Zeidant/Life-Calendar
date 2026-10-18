import os
import requests
import datetime
from dotenv import load_dotenv

load_dotenv()

NOTION_TOKEN = os.getenv("NOTION_TOKEN")
DATABASE_ID = os.getenv("NOTION_DATABASE_ID")

HEADERS = {
    "Authorization": f"Bearer {NOTION_TOKEN}",
    "Content-Type": "application/json",
    "Notion-Version": "2022-06-28"
}

def obtener_tareas():
    url = f"https://api.notion.com/v1/databases/{DATABASE_ID}/query"
    payload = {"page_size": 20}

    respuesta = requests.post(url, json=payload, headers=HEADERS)
    
    if respuesta.status_code != 200:
        print("Error conectando a Notion:", respuesta.text)
        return []

    datos = respuesta.json()
    tareas = []
    hoy = datetime.datetime.now().date()

    for pagina in datos.get("results", []):
        propiedades = pagina.get("properties", {})
        
        # 1. Título
        llave_titulo = next((key for key, val in propiedades.items() if val["type"] == "title"), None)
        nombre_tarea = propiedades[llave_titulo]["title"][0]["plain_text"] if llave_titulo and propiedades[llave_titulo]["title"] else "Sin título"
            
        # 2. Prioridad y Status
        prioridad_data = propiedades.get("Priority", {}).get("select", {})
        nombre_prioridad = prioridad_data.get("name", "") if prioridad_data else ""
        color_prioridad = prioridad_data.get("color", "default") if prioridad_data else "default"

        status_data = propiedades.get("Status", {}).get("status", {})
        nombre_status = status_data.get("name", "Not started") if status_data else "Not started"

        # 3. FILTRO DE COMPLETADAS
        formula_data = propiedades.get("Date Done", {}).get("formula", {})
        fecha_completado_str = formula_data.get("date", {}).get("start", "") if formula_data.get("type") == "date" else ""
        if nombre_status == "Done" and fecha_completado_str:
            fecha_completado = datetime.datetime.strptime(fecha_completado_str[:10], "%Y-%m-%d").date()
            if fecha_completado < hoy:
                continue

        # 4. EXTRACCIÓN DE FECHA DE ENTREGA (DUE DATE)
        due_date_data = propiedades.get("Due date", {}).get("date", {})
        # Si no tiene fecha, le asignamos una muy lejana (año 2099) para que se vaya al fondo al ordenar
        due_date_str = due_date_data.get("start", "2099-12-31") if due_date_data else "2099-12-31"

        tareas.append({
            "titulo": nombre_tarea,
            "prioridad": nombre_prioridad,
            "color": color_prioridad,
            "status": nombre_status,
            "due_date": due_date_str[:10] # Solo tomamos YYYY-MM-DD
        })

    return tareas