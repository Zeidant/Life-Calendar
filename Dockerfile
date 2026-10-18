FROM mcr.microsoft.com/playwright/python:v1.42.0-jammy

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 2211

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "2211"]