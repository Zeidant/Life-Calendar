from fastapi import FastAPI
from fastapi.responses import HTMLResponse

app = FastAPI(title="Life Wallpaper API")

@app.get("/", response_class=HTMLResponse)
def leer_raiz():
    return """
    <html>
        <head><title>Life Wallpaper</title></head>
        <body style="background-color: black; color: white; text-align: center; font-family: sans-serif; padding-top: 20%;">
            <h1>🚀 Servidor Activo</h1>
            <p>Esperando la conexión del iPhone...</p>
        </body>
    </html>
    """

# Para correr el servidor localmente ejecuta:
# uvicorn main:app --reload