#!/usr/bin/env python3
"""Generate Spanish CSV for untranslated Files using machine translation.
   FIXED VERSION - Only translates remaining Japanese text + only writes files that actually change.
"""

import os
import re
import sqlite3
import sys
import time
import subprocess
from pathlib import Path
from io import StringIO
from deep_translator import GoogleTranslator

# --- RUTAS ADAPTADAS PARA RAILWAY / LINUX ---
BASE_DIR = Path(os.getenv("WORK_DIR", "/app/data"))
REPO_DIR = Path("/app")
CACHE_DB = Path("/app/data/translation_cache.db")
LOG = Path("/app/data/translate_missing.log")

TARGET_FOLDERS = ["Dialogue", "Files", "Misc", "Orders", "Quests", "Story", "Tutorial", "UI"]

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
    # Skip placeholder / internal tags (arreglado el syntax error)
    if "((" in text or "intextor" in text.lower():
        return True
    return False

def setup_git():
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        log("ADVERTENCIA: GITHUB_TOKEN no encontrado. No se podrá subir a GitHub.")
        return False
    log("Configurando credenciales de Git y sincronizando...")
    subprocess.run(["git", "config", "--global", "--add", "safe.directory", str(REPO_DIR)], check=False)
    subprocess.run(["git", "config", "--global", "user.email", "railway@bot.com"], check=False)
    subprocess.run(["git", "config", "--global", "user.name", "Railway Traductor"], check=False)
    remote_url = f"https://oauth2:{token}@github.com/rcosven/PSO2ENPatchCSV.git"
    status = subprocess.run(["git", "status"], cwd=REPO_DIR, capture_output=True)
    
    if status.returncode != 0:
        log("No se detectó la carpeta .git (Docker la omitió). Reconstruyendo repositorio internamente...")
        subprocess.run(["git", "init"], cwd=REPO_DIR, check=False)
        subprocess.run(["git", "checkout", "-b", "ES"], cwd=REPO_DIR, check=False)
        subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=REPO_DIR, check=False)
        subprocess.run(["git", "fetch", "origin", "ES"], cwd=REPO_DIR, check=False)
        subprocess.run(["git", "reset", "--mixed", "origin/ES"], cwd=REPO_DIR, check=False)
    else:
        log("Repositorio Git detectado correctamente. Actualizando URL...")
        subprocess.run(["git", "remote", "set-url", "origin", remote_url], cwd=REPO_DIR, check=False)
        subprocess.run(["git", "fetch", "origin", "ES"], cwd=REPO_DIR, check=False)
    return True

def push_to_github():
    log("Iniciando guardado de reparaciones en GitHub...")
    for folder in TARGET_FOLDERS:
        folder_path = REPO_DIR / folder
        if folder_path.exists():
            subprocess.run(["git", "add", f"{folder}/"], cwd=REPO_DIR, check=False)
    
    status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_DIR, capture_output=True, text=True)
    if not status.stdout.strip():
        log("No hay cambios nuevos para subir.")
        return
    subprocess.run(["git", "commit", "-m", "Auto-reparación y traducción parcial desde Railway"], cwd=REPO_DIR, check=False)
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
        
        for attempt in range(max_retries):
            try:
                translated = tr.translate_batch(chunk)
                break
            except Exception as e:
                wait_time = 2 ** attempt
                log(f"Error de red traduciendo lote. Reintento {attempt + 1}/{max_retries} en {wait_time}s...")
                time.sleep(wait_time)
        
        if not translated:
            translated = chunk
        
        if not isinstance(translated, list):
            translated = [translated]
        
        for src, dst in zip(chunk, translated):
            dst = apply_release_sed(dst or src)
            out_map[src] = dst
            conn.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (src, src_lang, dst))
        
        conn.commit()
        time.sleep(0.2)
    
    return out_map

def read_and_repair_rows(path: Path) -> list[list[str]]:
    """Lee el archivo como texto plano, eliminando de raíz cualquier desastre de comillas múltiples."""
    rows = []
    if not path.exists():
        return rows
    
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            if "," in line:
                key, val = line.split(",", 1)
                val = val.strip()
                
                if val.startswith('"') and val.endswith('"'):
                    val = val.strip('"')
                
                val = val.replace('"', "'")
                rows.append([key, val])
            else:
                rows.append([line])
    return rows

def main():
    LOG.write_text("", encoding="utf-8")
    log("Iniciando proceso de traducción PROFUNDA (solo JA pendientes) con auto-reparación condicional...")
    
    git_ready = setup_git()
    conn = init_cache()
    
    files_to_process = []
    for folder in TARGET_FOLDERS:
        folder_dir = REPO_DIR / folder
        if folder_dir.exists():
            for file_path in folder_dir.rglob("*.csv"):
                files_to_process.append(file_path)
    
    log(f"Se encontraron {len(files_to_process)} archivos CSV para analizar.")
    
    files_processed = 0
    files_modified_in_session = 0
    
    for file_path in files_to_process:
        rows = read_and_repair_rows(file_path)
        if not rows:
            continue
        
        # Leemos el contenido original para comparar si realmente cambió algo
        try:
            original_content = file_path.read_text(encoding="utf-8")
        except Exception:
            original_content = ""
        
        texts_to_translate: list[str] = []
        row_meta: list[tuple[list[str], str, str]] = []
        
        for row in rows:
            if len(row) < 2:
                row_meta.append((row, "", ""))
                continue
            
            text = row[1]
            src_lang_row = "ja" if has_cjk(text) else "en"
            row_meta.append((row, text, src_lang_row))
            
            # SOLO traducimos si tiene japonés (CJK). Una vez traducido → no lo tocamos más.
            if has_cjk(text) and not should_skip_translate(text):
                texts_to_translate.append(text)
        
        trans_map: dict[str, str] = {}
        if texts_to_translate:
            unique_ja = sorted(set(texts_to_translate))
            trans_map = batch_translate(conn, unique_ja, "ja")
        
        new_rows = []
        for item in row_meta:
            row, text, lang = item
            if len(row) < 2:
                new_rows.append(row)
                continue
            
            if lang == "ja" and text in trans_map:
                translated = trans_map[text]
            else:
                translated = text
            
            translated_clean = translated.replace('"', "'")
            new_rows.append([row[0], translated_clean])
        
        # Construimos el nuevo contenido exacto
        output = StringIO()
        for row_data in new_rows:
            if len(row_data) == 2:
                key = row_data[0]
                val = row_data[1]
                output.write(f'{key},"""{val}"""\n')
            else:
                output.write(row_data[0] + "\n")
        new_content = output.getvalue()
        
        # SOLO escribimos si hubo cambio real (traducción nueva o reparación de comillas)
        if new_content != original_content:
            with open(file_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(new_content)
            files_modified_in_session += 1
            log(f"  → MODIFICADO: {file_path.relative_to(REPO_DIR)}")
        
        files_processed += 1
        
        if files_processed % 50 == 0:
            log(f"Progreso: {files_processed}/{len(files_to_process)} archivos procesados.")
            if git_ready and files_modified_in_session > 0:
                push_to_github()
                files_modified_in_session = 0
    
    log("Escaneo, reparación y traducción completados.")
    conn.close()
    
    if git_ready:
        push_to_github()

if __name__ == "__main__":
    main()
