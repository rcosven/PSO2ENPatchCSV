#!/usr/bin/env python3
"""Generate Spanish CSV for untranslated Files using machine translation."""
import csv
import os
import re
import sqlite3
import sys
import time
import subprocess
from pathlib import Path

from deep_translator import GoogleTranslator

# --- RUTAS ADAPTADAS PARA RAILWAY / LINUX ---
BASE_DIR = Path(os.getenv("WORK_DIR", "/app/data"))
REPO_DIR = Path("/app")
CACHE_DB = Path("/app/data/translation_cache.db")
LOG = Path("/app/data/translate_missing.log")
EN_REPO = Path("/app/data/csv-repo-en") # Opcional, por si lo sigues usando

# Las carpetas que queremos revisar y traducir in-place
TARGET_FOLDERS = ["Dialogue", "Files", "Misc", "Orders", "Quests", "Story", "Tutorial", "UI"]
# --------------------------------------------

def log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def apply_release_sed(text: str) -> str:
    repl = {
        "á": "a", "à": "a", "è": "e", "é": "e", "ì": "i", "í": "i",
        "ò": "o", "ó": "o", "ö": "o", "ō": "o", "ù": "u", "ú": "u", "ü": "u",
        "ñ": "й", "Á": "A", "À": "A", "È": "E", "É": "E", "Ì": "I", "Í": "I",
        "Ò": "O", "Ó": "O", "Ö": "O", "Ō": "O", "Ù": "U", "Ú": "U", "Ü": "U", "Ñ": "Й",
    }
    for k, v in repl.items():
        text = text.replace(k, v)
    return text

def unquote(val: str) -> str:
    if val.startswith('"""') and val.endswith('"""'):
        return val[3:-3]
    if val.startswith('"') and val.endswith('"'):
        return val[1:-1]
    return val

def quote(val: str) -> str:
    return f'"""{val}"""'

def has_cjk(text: str) -> bool:
    return bool(re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", text))

def init_cache():
    CACHE_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(CACHE_DB)
    conn.execute("CREATE TABLE IF NOT EXISTS cache (src TEXT, lang TEXT, dst TEXT, PRIMARY KEY(src, lang))")
    conn.commit()
    return conn

def should_skip_translate(text: str) -> bool:
    if not text or not text.strip():
        return True
    if text.startswith("<&") or (text.startswith("<") and ">" in text[:20]):
        return True
    return "$((" in text or "${" in text

def setup_git():
    """Prepara el repositorio y asegura que Git esté inicializado, incluso si Docker borró la carpeta .git."""
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        log("ADVERTENCIA: GITHUB_TOKEN no encontrado. No se podrá subir a GitHub.")
        return False

    log("Configurando credenciales de Git y sincronizando...")
    
    # Previene errores de seguridad de Git al correr como usuario "root" en contenedores
    subprocess.run(["git", "config", "--global", "--add", "safe.directory", str(REPO_DIR)])
    subprocess.run(["git", "config", "--global", "user.email", "railway@bot.com"])
    subprocess.run(["git", "config", "--global", "user.name", "Railway Traductor"])

    remote_url = f"https://oauth2:{token}@github.com/rcosven/PSO2ENPatchCSV.git"

    # Revisar si la carpeta actual ya es un repositorio de Git válido
    status = subprocess.run(["git", "status"], cwd=REPO_DIR, capture_output=True)
    
    if status.returncode != 0:
        log("No se detectó la carpeta .git (Docker la omitió). Reconstruyendo repositorio internamente...")
        subprocess.run(["git", "init"], cwd=REPO_DIR)
        subprocess.run(["git", "checkout", "-b", "ES"], cwd=REPO_DIR)
        subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=REPO_DIR)
        
        log("Descargando historial de tu rama ES desde GitHub...")
        subprocess.run(["git", "fetch", "origin", "ES"], cwd=REPO_DIR)
        
        # El modo --mixed le dice a Git que asocie la historia descargada con los 
        # archivos que ya existen en tu disco, SIN sobrescribirlos ni borrarlos.
        subprocess.run(["git", "reset", "--mixed", "origin/ES"], cwd=REPO_DIR)
    else:
        log("Repositorio Git detectado correctamente. Actualizando URL...")
        subprocess.run(["git", "remote", "set-url", "origin", remote_url], cwd=REPO_DIR)
        subprocess.run(["git", "fetch", "origin", "ES"], cwd=REPO_DIR)

    return True

def push_to_github():
    log("Iniciando guardado en GitHub...")
    # Añadimos solo las carpetas objetivo
    for folder in TARGET_FOLDERS:
        folder_path = REPO_DIR / folder
        if folder_path.exists():
            subprocess.run(["git", "add", f"{folder}/"], cwd=REPO_DIR)
    
    status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_DIR, capture_output=True, text=True)
    if not status.stdout.strip():
        log("No hay cambios nuevos para subir.")
        return

    subprocess.run(["git", "commit", "-m", "Auto-traducción: Actualización parcial desde Railway"], cwd=REPO_DIR)
    push = subprocess.run(["git", "push", "origin", "HEAD:ES"], cwd=REPO_DIR, capture_output=True, text=True)
    
    if push.returncode == 0:
        log("¡Progreso guardado exitosamente en GitHub!")
    else:
        log(f"Error al subir: {push.stderr}")

def batch_translate(conn, texts: list[str], src_lang: str) -> dict[str, str]:
    out_map: dict[str, str] = {}
    pending: list[str] = []
    
    for text in texts:
        if should_skip_translate(text):
            out_map[text] = text
            continue
        row = conn.execute("SELECT dst FROM cache WHERE src=? AND lang=?", (text, src_lang)).fetchone()
        if row:
            out_map[text] = row[0]
        else:
            pending.append(text)
            
    if not pending:
        return out_map

    tr = GoogleTranslator(source=src_lang, target="es")
    chunk_size = 40
    
    for i in range(0, len(pending), chunk_size):
        chunk = pending[i : i + chunk_size]
        max_retries = 5
        translated = None
        
        # Sistema de reintentos en caso de que Google bloquee la IP temporalmente
        for attempt in range(max_retries):
            try:
                translated = tr.translate_batch(chunk)
                break
            except Exception as e:
                wait_time = 2 ** attempt # Espera 1s, 2s, 4s, 8s, 16s...
                log(f"Error de red/API traduciendo lote. Reintento {attempt + 1}/{max_retries} en {wait_time}s... Error: {str(e)[:50]}")
                time.sleep(wait_time)
                
        if not translated:
            log("Lote fallido tras múltiples intentos. Se mantendrán las líneas originales por ahora.")
            translated = chunk # Fallback a original
            
        if not isinstance(translated, list):
            translated = [translated]
            
        for src, dst in zip(chunk, translated):
            dst = apply_release_sed(dst or src)
            out_map[src] = dst
            conn.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (src, src_lang, dst))
            
        conn.commit()
        time.sleep(0.2) # Pausa amigable para no saturar la API
        
    return out_map

def read_rows(path: Path) -> list[list[str]]:
    with open(path, encoding="utf-8") as f:
        return list(csv.reader(f, strict=True))

def main():
    LOG.write_text("", encoding="utf-8")
    log("Iniciando proceso de traducción profunda...")
    
    git_ready = setup_git()
    conn = init_cache()
    
    files_to_process = []
    
    # Escanear todas las carpetas objetivo buscando CSVs
    for folder in TARGET_FOLDERS:
        folder_dir = REPO_DIR / folder
        if folder_dir.exists():
            for file_path in folder_dir.rglob("*.csv"):
                files_to_process.append(file_path)

    log(f"Se encontraron {len(files_to_process)} archivos CSV en las carpetas seleccionadas.")
    
    files_processed = 0
    files_modified_in_session = 0

    for file_path in files_to_process:
        rows = read_rows(file_path)
        if not rows:
            continue

        # Detectar idioma base aproximado del archivo
        src_lang_file = "ja" if has_cjk(unquote(rows[0][1]) if len(rows[0]) > 1 else "") else "en"

        texts_to_translate: list[str] = []
        row_meta: list[tuple[list[str], str, str]] = []
        
        for row in rows:
            if len(row) < 2:
                row_meta.append((row, "", ""))
                continue
                
            text = unquote(row[1])
            # Forzamos idioma de origen basándonos en si tiene caracteres japoneses
            src_lang_row = "ja" if has_cjk(text) else "en"
            row_meta.append((row, text, src_lang_row))
            
            if not should_skip_translate(text):
                texts_to_translate.append(text)

        by_lang: dict[str, set[str]] = {"en": set(), "ja": set()}
        for _, text, lang in row_meta:
            if text and not should_skip_translate(text):
                by_lang[lang].add(text)
                
        trans_maps: dict[str, dict[str, str]] = {}
        for lang, unique in by_lang.items():
            if unique:
                trans_maps[lang] = batch_translate(conn, sorted(unique), lang)

        new_rows = []
        needs_update = False
        
        for item in row_meta:
            row, text, lang = item
            if len(row) < 2:
                new_rows.append(row)
                continue
                
            if should_skip_translate(text) or lang not in trans_maps:
                translated = text
            else:
                translated = trans_maps[lang].get(text, text)
                
            if translated != text:
                needs_update = True
                
            new_rows.append([row[0], quote(translated)])

        # Sobrescribir el archivo SOLO si hubo traducciones nuevas (evita gastar disco innecesariamente)
        if needs_update:
            with open(file_path, "w", encoding="utf-8", newline="\n") as f:
                csv.writer(f, lineterminator="\n").writerows(new_rows)
            files_modified_in_session += 1
            
        files_processed += 1
        
        if files_processed % 50 == 0:
            log(f"Progreso: {files_processed}/{len(files_to_process)} archivos escaneados. ({files_modified_in_session} modificados)")
            
            # Subir a GitHub cada 50 archivos revisados para asegurar progreso
            if git_ready and files_modified_in_session > 0:
                push_to_github()
                files_modified_in_session = 0 # Reiniciar contador tras la subida

    log("Escaneo y traducción completados.")
    conn.close()

    # Subida final de lo que haya quedado pendiente
    if git_ready:
        push_to_github()

if __name__ == "__main__":
    main()
