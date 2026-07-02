FROM python:3.11-slim

# Instalar git (por si necesitamos clonar algo)
RUN apt-get update && apt-get install -y git && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Instalar dependencias de Python
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copiar todo el código del repo
COPY . .

# Crear carpetas que usaremos
RUN mkdir -p /app/data

# Comando por defecto (luego lo podemos cambiar)
CMD ["python", "translate_missing.py", "--no-rebuild"]
