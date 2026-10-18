# 📱 Life Calendar: Dynamic iOS Wallpaper (Notion + FastAPI)

Life Calendar es un generador de fondos de pantalla dinámicos para iOS que combina un calendario anual minimalista con tu lista de tareas prioritarias ("To-Do") extraída directamente de tu base de datos de Notion. 

Está diseñado para ofrecer una **visualización pasiva de tus prioridades diarias**. Cada vez que bloqueas tu iPhone, ves exactamente qué debes hacer, ordenado de forma inteligente.

![Demo Banner](https://i.imgur.com/PpoZ2QI.png)

## ✨ Características

- 🧠 **Integración Nativa con Notion:** Extrae tareas, estados y fechas de entrega en tiempo real.
- 🎨 **Diseño Glassmorphism:** Interfaz minimalista y translúcida para las tareas, que no compite con el calendario.
- 🚦 **Lógica de Ordenamiento Estricta:** 
  - Prioriza las tareas en "In progress".
  - Ordena por fecha de entrega cercana.
  - Empuja automáticamente las tareas completadas ("Done") al fondo.
- 🐳 **Docker Ready:** Listo para desplegarse en servidores en la nube (ej. Oracle Cloud, AWS, DigitalOcean) sin dolores de cabeza con las dependencias de Chromium.
- ⚡ **Automatizable vía Atajos de iOS:** Se actualiza silenciosamente en el fondo de tu teléfono.

## 🛠️ Tecnologías

- **Python 3.10+** (FastAPI)
- **Playwright** (Motor de renderizado y captura de pantalla)
- **Notion API**
- **Docker & Docker Compose**

---

## 🚀 Guía de Instalación Rápida

### 1. Preparar Notion
1. Crea una base de datos en Notion con estas propiedades exactas (respetando mayúsculas):
   - `Title` (Nombre de la tarea)
   - `Status` (Estado de la tarea: *Not started, In progress, Done*)
   - `Priority` (Etiqueta Select: *High, Medium, Low*)
   - `Due date` (Fecha de entrega)
   - `Date Done` (Propiedad tipo **Formula** con la expresión: `prop("Status") == "Done" ? now() : fromTimestamp(0)`)
2. Ve a [Notion Developers](https://www.notion.so/my-integrations) y crea una nueva integración interna. Copia el **Internal Integration Secret**.
3. En tu base de datos de Notion, haz clic en los 3 puntos (arriba a la derecha) -> *Conexiones* -> *Añadir conexión* y selecciona tu nueva integración.

### 2. Configurar el Servidor (Vía Docker)
La forma recomendada de correr este proyecto es mediante Docker, ya que instala Chromium y sus dependencias de sistema automáticamente.

1. Clona el repositorio:
   ```bash
   git clone https://github.com/TU_USUARIO/Life-Calendar.git
   cd Life-Calendar
   ```

2. Configura las variables de entorno:
   Renombra el archivo `.env.example` a `.env` y coloca tus claves:
   ```bash
   NOTION_TOKEN="secret_tunotiontokenaqui12345"
   NOTION_DATABASE_ID="tudatabaseidaqui67890"
   ```

3. Levanta el contenedor:
   ```bash
   docker-compose up -d --build
   ```
   *El servidor ahora estará corriendo en `http://localhost:8000/wallpaper`.*

---

## 📱 Configuración de la Automatización en iOS

Para que tu iPhone descargue y aplique el fondo automáticamente todos los días:

1. Abre la app **Atajos** (Shortcuts) en tu iPhone.
2. Crea un nuevo Atajo y añade la acción **"Obtener contenido de la URL"**.
3. En el campo URL, ingresa la IP pública de tu servidor (ej. `http://tu-ip:8000/wallpaper`).
4. Añade la acción **"Establecer fondo de pantalla"**.
5. Vincula el "Contenido de URL" al fondo de la pantalla de Bloqueo. **Importante:** Apaga la opción de *"Mostrar previsualización"*.
6. Ve a la pestaña **Automatización**, crea una nueva para una hora específica (ej. 6:00 AM) y selecciona el atajo que acabas de crear. ¡Listo!

---

## 📝 Personalización
Si deseas cambiar el calendario base, el tamaño de las tarjetas o los colores, puedes editar las variables globales y el código CSS en el archivo `generator.py`.

## 🤝 Contribuciones
Las contribuciones son bienvenidas. Si tienes ideas para mejorar el algoritmo de ordenamiento o el diseño, siéntete libre de abrir un *Issue* o enviar un *Pull Request*.

## 📄 Licencia
Este proyecto está bajo la Licencia MIT - mira el archivo [LICENSE.md](LICENSE.md) para más detalles.