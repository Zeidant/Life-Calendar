import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.concurrency import run_in_threadpool
from notion import obtener_tareas
from generator import crear_imagen

# --- TAREA EN SEGUNDO PLANO (SILENCIOSA) ---
async def sincronizar_notion_periodicamente():
    while True:
        try:
            await run_in_threadpool(obtener_tareas)
        except Exception as e:
            print(f"⚠️ [Auto-Sync] Error: {e}")
        
        # Pausa estricta de 5 horas (18000 segundos)
        print("⏳ [Auto-Sync] Pausando 5 horas antes de la siguiente sincronización...")
        await asyncio.sleep(18000)

@asynccontextmanager
async def lifespan(app: FastAPI):
    tarea_fondo = asyncio.create_task(sincronizar_notion_periodicamente())
    yield
    tarea_fondo.cancel()

app = FastAPI(title="Life Wallpaper API", lifespan=lifespan)
# -------------------------------------------

@app.get("/wallpaper", response_class=FileResponse)
def generar_wallpaper():
    print("📱 Petición del iPhone recibida. Generando wallpaper...")
    tareas = obtener_tareas()
    
    if not tareas:
        tareas = ["¡Día libre!", "No hay tareas pendientes para hoy."]
        
    output_file = "current_wallpaper.png"
    crear_imagen(tareas, output_file)
    
    print("🚀 Imagen enviada con éxito.")
    return FileResponse(output_file, media_type="image/png")