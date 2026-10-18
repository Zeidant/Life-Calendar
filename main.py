from fastapi import FastAPI
from fastapi.responses import FileResponse
from notion import obtener_tareas
from generator import crear_imagen

app = FastAPI(title="Life Wallpaper API")

@app.get("/wallpaper", response_class=FileResponse)
def generar_wallpaper():
    print("1. Obteniendo tareas de Notion...")
    tareas = obtener_tareas()
    
    if not tareas:
        tareas = ["¡Día libre!", "No hay tareas pendientes para hoy."]
        
    print("2. Generando imagen...")
    output_file = "current_wallpaper.png"
    crear_imagen(tareas, output_file)
    
    print("3. Imagen enviada al iPhone.")
    # FileResponse le dice al navegador/iPhone que descargue el archivo como imagen
    return FileResponse(output_file, media_type="image/png")