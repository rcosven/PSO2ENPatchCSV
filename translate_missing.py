#!/usr/bin/env python3
"""
PSO2 CSV Spanish Translator - Todo en un solo script
- Arregla tags rotos automaticamente (<amarillo>, <rojo>, etc.)
- Protege lineas con tags para no traducirlas
- Solo traduce lo que no esta en espanol
"""

import os
import re
import sqlite3
import time
import subprocess
from pathlib import Path
from io import StringIO
from deep_translator import GoogleTranslator

try:
    from langdetect import detect, DetectorFactory
    DetectorFactory.seed = 0
    HAS_LANGDETECT = True
except ImportError:
    HAS_LANGDETECT = False

BASE_DIR = Path(os.getenv("WORK_DIR", "/app/data"))
REPO_DIR = Path("/app")
CACHE_DB = Path("/app/data/translation_cache.db")
LOG = Path("/app/data/translate_missing.log")

TARGET_FOLDERS = ["Dialogue", "Files", "Misc", "Orders", "Quests", "Story", "Tutorial", "UI"]

# === TAGS ROTOS QUE VAMOS A ARREGLAR AUTOMATICAMENTE ===
TAG_FIXES = {
    "<amarillo>": "<yellow>", "</amarillo>": "</yellow>",
    "<rojo>": "<red>", "</rojo>": "</red>",
    "<verde>": "<green>", "</verde>": "</green>",
    "<azul>": "<blue>", "</azul>": "</blue>",
    "<rosa>": "<pink>", "</rosa>": "</pink>",
    "<morado>": "<purple>", "</morado>": "</purple>",
}


def log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def fix_broken_tags():
    """Arregla tags de color rotos por traducciones anteriores"""
    log("Revisando y arreglando tags rotos (<amarillo>, <rojo>, etc.)...")
    fixed_count = 0
    for folder in TARGET_FOLDERS:
        folder_path = REPO_DIR / folder
        if not folder_path.exists():
            continue
        for csv_file in folder_path.rglob("*.csv"):
            try:
                content = csv_file.read_text(encoding="utf-8")
                new_content = content
                for bad, good in TAG_FIXES.items():
                    if bad in new_content:
                        new_content = new_content.replace(bad, good)
                        fixed_count += 1
                if new_content != content:
                    csv_file.write_text(new_content, encoding="utf-8")
            except:
                pass
    if fixed_count > 0:
        log(f"Se arreglaron {fixed_count} tags rotos.")
    else:
        log("No se encontraron tags rotos.")


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
    # Protege cualquier linea que tenga tags < >
    if "<" in text and ">" in text:
        return True
    if text.startswith("<&"):
        return True
    if "((" in text or "intextor" in text.lower():
        return True
    return False


def detect_language(text: str) -> str:
    if has_cjk(text):
        return "ja"
    if not HAS_LANGDETECT:
        return "en"
    try:
        return detect(text)
    except Exception:
        return "en"


def setup_git():
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        log("ADVERTENCIA: GITHUB_TOKEN no encontrado.")
        return False
    log("Configurando Git...")
    subprocess.run(["git", "config", "--global", "--add", "safe.directory", str(REPO_DIR)], check=False)
    subprocess.run(["git", "config", "--global", "user.email", "railway@bot.com"], check=False)
    subprocess.run(["git", "config", "--global", "user.name", "Railway Traductor"], check=False)
    remote_url = f"https://oauth2:{token}@github.com/rcosven/PSO2ENPatchCSV.git"
    status = subprocess.run(["git", "status"], cwd=REPO_DIR, capture_output=True)
    if status.returncode != 0:
        log("Reconstruyendo repo Git...")
        subprocess.run(["git", "init"], cwd=REPO_DIR, check=False)
        subprocess.run(["git", "checkout", "-b", "ES"], cwd=REPO_DIR, check=False)
        subprocess.run(["git", "remote", "add", "origin", remote_url], cwd=REPO_DIR, check=False)
        subprocess.run(["git", "fetch", "origin", "ES"], cwd=REPO_DIR, check=False)
        subprocess.run(["git", "reset", "--mixed", "origin/ES"], cwd=REPO_DIR, check=False)
    else:
        subprocess.run(["git", "remote", "set-url", "origin", remote_url], cwd=REPO_DIR, check=False)
        subprocess.run(["git", "fetch", "origin", "ES"], cwd=REPO_DIR, check=False)
    return True


def push_to_github():
    log("Guardando cambios en GitHub...")
    for folder in TARGET_FOLDERS:
        if (REPO_DIR / folder).exists():
            subprocess.run(["git", "add", f"{folder}/"], cwd=REPO_DIR, check=False)
    status = subprocess.run(["git", "status", "--porcelain"], cwd=REPO_DIR, capture_output=True, text=True)
    if not status.stdout.strip():
        log("No hay cambios nuevos para subir.")
        return
    subprocess.run(["git", "commit", "-m", "Auto-traduccion CSV ES"], cwd=REPO_DIR, check=False)
    push = subprocess.run(["git", "push", "origin", "HEAD:ES"], cwd=REPO_DIR, capture_output=True, text=True)
    if push.returncode == 0:
        log("Cambios subidos OK")
    else:
        log(f"Error push: {push.stderr[:150]}")


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
        chunk = pending[i:i + chunk_size]
        translated = None
        for attempt in range(5):
            try:
                translated = tr.translate_batch(chunk)
                break
            except Exception:
                time.sleep(2 ** attempt)
        if not translated:
            translated = chunk
        if not isinstance(translated, list):
            translated = [translated]
        for src, dst in zip(chunk, translated):
            dst = apply_release_sed(dst or src)
            out_map[src] = dst
            conn.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (src, src_lang, dst))
        conn.commit()
        time.sleep(0.12)
    return out_map


def read_and_repair_rows(path: Path) -> list[list[str]]:
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
    log("=== Traductor PSO2 ES (Todo en un solo script) ===")
    
    if not HAS_LANGDETECT:
        log("ERROR: Falta langdetect. Ejecuta en Railway: pip install langdetect")
        return

    # === PASO 1: Arreglar tags rotos automaticamente ===
    fix_broken_tags()

    git_ready = setup_git()
    conn = init_cache()

    files_to_process = []
    for folder in TARGET_FOLDERS:
        folder_dir = REPO_DIR / folder
        if folder_dir.exists():
            for p in folder_dir.rglob("*.csv"):
                files_to_process.append(p)

    log(f"Archivos encontrados: {len(files_to_process)}")

    files_processed = 0
    files_modified = 0

    for file_path in files_to_process:
        rows = read_and_repair_rows(file_path)
        if not rows:
            continue

        original_content = ""
        try:
            original_content = file_path.read_text(encoding="utf-8")
        except:
            pass

        texts_ja = []
        texts_en = []
        row_meta = []

        for row in rows:
            if len(row) < 2:
                row_meta.append((row, "", "", False))
                continue
            text = row[1]
            lang = detect_language(text)
            needs = False
            if lang == "ja":
                needs = True
                texts_ja.append(text)
            elif lang != "es" and not should_skip_translate(text):
                needs = True
                texts_en.append(text)
            row_meta.append((row, text, lang, needs))

        trans_ja = batch_translate(conn, sorted(set(texts_ja)), "ja") if texts_ja else {}
        trans_en = batch_translate(conn, sorted(set(texts_en)), "en") if texts_en else {}

        new_rows = []
        for item in row_meta:
            row, text, lang, needs = item
            if len(row) < 2:
                new_rows.append(row)
                continue
            if needs:
                translated = trans_ja.get(text, text) if lang == "ja" else trans_en.get(text, text)
            else:
                translated = text
            clean = translated.replace('"', "'")
            new_rows.append([row[0], clean])

        output = StringIO()
        for r in new_rows:
            if len(r) == 2:
                output.write(f'{r[0]},"""{r[1]}"""\n')
            else:
                output.write(r[0] + "\n")
        new_content = output.getvalue()

        if new_content != original_content:
            with open(file_path, "w", encoding="utf-8", newline="\n") as f:
                f.write(new_content)
            files_modified += 1
            log(f"MODIFICADO: {file_path.relative_to(REPO_DIR)}")

        files_processed += 1
        if files_processed % 50 == 0:
            log(f"Progreso: {files_processed}/{len(files_to_process)} | Modificados: {files_modified}")
            if git_ready and files_modified > 0:
                push_to_github()
                files_modified = 0

    log("Proceso terminado.")
    conn.close()
    if git_ready:
        push_to_github()


if __name__ == "__main__":
    main()
