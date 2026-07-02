#!/usr/bin/env python3
"""Generate Spanish CSV for untranslated Files/ using EN patch + machine translation."""
import csv
import json
import os
import re
import sqlite3
import sys
import time
import shutil
import subprocess
from pathlib import Path

from deep_translator import GoogleTranslator

# --- RUTAS ADAPTADAS PARA RAILWAY / LINUX ---
BASE_DIR = Path(os.getenv("WORK_DIR", "/app/data"))
ES_REPO = Path("/app")
OUT_DIR = Path("/app/data/Translated/Auto")
CACHE_DB = Path("/app/data/translation_cache.db")
LOG = Path("/app/data/translate_missing.log")
EN_REPO = Path("/app/data/csv-repo-en") 
SKIP = {".git", ".circleci", "_py", "_sh", "_tools", "_fonts", "_aspell", "_misc", "Files", "data"}
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

def translate_cached(conn, text: str, src_lang: str, translator: GoogleTranslator | None = None) -> str:
    if should_skip_translate(text):
        return text
    row = conn.execute("SELECT dst FROM cache WHERE src=? AND lang=?", (text, src_lang)).fetchone()
    if row:
        return row[0]
    tr = translator or GoogleTranslator(source=src_lang, target="es")
    try:
        out = tr.translate(text[:4500])
        if not out:
            out = text
    except Exception:
        time.sleep(0.5)
        try:
            out = tr.translate(text[:4500])
        except Exception:
            out = text
    out = apply_release_sed(out)
    conn.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (text, src_lang, out))
    return out

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
        try:
            translated = tr.translate_batch(chunk)
        except Exception:
            time.sleep(0.5)
            translated = [translate_cached(conn, t, src_lang, tr) for t in chunk]
        if not isinstance(translated, list):
            translated = [translated]
        for src, dst in zip(chunk, translated):
            dst = apply_release_sed(dst or src)
            out_map[src] = dst
            conn.execute("INSERT OR REPLACE INTO cache VALUES (?,?,?)", (src, src_lang, dst))
        conn.commit()
        time.sleep(0.1)
    return out_map

def index_csv(repo: Path) -> dict[str, Path]:
    out = {}
    for root, _, files in os.walk(repo):
        parts = Path(root).parts
        if any(s in parts for s in SKIP):
            continue
        for fn in files:
            if fn.endswith(".csv"):
                out[fn] = Path(root) / fn
    return out

def read_rows(path: Path) -> list[list[str]]:
    with open(path, encoding="utf-8") as f:
        return list(csv.reader(f, strict=True))

def push_to_github():
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        log("GITHUB_TOKEN no encontrado. Saltando subida automática.")
        return

    log("Preparando archivos para subir a GitHub...")
    repo_dir = "/app"
    dest_dir = Path("/app/Translated/Auto")
    dest_dir.mkdir(parents=True, exist_ok=True)
    src_dir = Path("/app/data/Translated/Auto")
    
    if not src_dir.exists():
        log("No hay traducciones nuevas en el disco persistente.")
        return

    for file_path in src_dir.glob("*.csv"):
        shutil.copy2(file_path, dest_dir / file_path.name)

    subprocess.run(["git", "config", "--global", "user.email", "railway@bot.com"], cwd=repo_dir)
    subprocess.run(["git", "config", "--global", "user.name", "Railway Traductor"], cwd=repo_dir)

    remote_url = f"https://{token}@github.com/rcosven/PSO2ENPatchCSV.git"
    subprocess.run(["git", "remote", "set-url", "origin", remote_url], cwd=repo_dir)

    subprocess.run(["git", "add", "Translated/Auto/"], cwd=repo_dir)
    
    status = subprocess.run(["git", "status", "--porcelain"], cwd=repo_dir, capture_output=True, text=True)
    if not status.stdout.strip():
        log("No hay cambios nuevos para subir. Todo está al día.")
        return

    subprocess.run(["git", "commit", "-m", "Auto-traducción desde Railway"], cwd=repo_dir)
    
    log("Subiendo cambios a GitHub...")
    push = subprocess.run(["git", "push", "origin", "HEAD:ES"], cwd=repo_dir, capture_output=True, text=True)
    
    if push.returncode == 0:
        log("¡Subida a GitHub exitosa! Los archivos están en tu repositorio.")
    else:
        log(f"Error al subir: {push.stderr}")

def main():
    LOG.write_text("", encoding="utf-8")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    conn = init_cache()
    log("Indexando repositorios CSV...")
    es_index = index_csv(ES_REPO)
    en_index = index_csv(EN_REPO)
    files_dir = ES_REPO / "Files"
    
    if not files_dir.exists():
        log(f"Error: La carpeta {files_dir} no existe. Verifica que los repositorios estén clonados.")
        sys.exit(1)

    pending = sorted(f.name for f in files_dir.glob("*.csv"))
    log(f"CSV en Files/: {len(pending)} | ya traducidos: {len(es_index)} | EN: {len(en_index)}")
    done = 0
    skipped = 0

    for i, fn in enumerate(pending):
        if fn in es_index:
            skipped += 1
            continue
        out_path = OUT_DIR / fn
        if out_path.exists():
            done += 1
            continue

        en_path = en_index.get(fn)
        es_path = files_dir / fn
        src_path = en_path if en_path else es_path
        rows = read_rows(src_path)
        src_lang = "ja" if has_cjk(unquote(rows[0][1]) if rows else "") else "en"
        if en_path:
            en_rows = read_rows(en_path)
            if not has_cjk(unquote(en_rows[0][1]) if en_rows else ""):
                rows = en_rows
                src_lang = "en"

        texts_to_translate: list[str] = []
        row_meta: list[tuple[list[str], str, str]] = []
        for row in rows:
            if len(row) < 2:
                row_meta.append((row, "", ""))
                continue
            text = unquote(row[1])
            if has_cjk(text):
                src_lang_row = "ja"
            else:
                src_lang_row = "en"
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
        for item in row_meta:
            row, text, lang = item
            if len(row) < 2:
                new_rows.append(row)
                continue
            if should_skip_translate(text) or lang not in trans_maps:
                translated = text
            else:
                translated = trans_maps[lang].get(text, text)
            new_rows.append([row[0], quote(translated)])

        with open(out_path, "w", encoding="utf-8", newline="\n") as f:
            csv.writer(f, lineterminator="\n").writerows(new_rows)
        done += 1
        if done % 25 == 0:
            total_auto = len(list(OUT_DIR.glob("*.csv")))
            log(f"Traducidos: {done}/{len(pending)} | Auto total: {total_auto} | omitidos: {skipped}")

    total_auto = len(list(OUT_DIR.glob("*.csv")))
    log(f"Final: {done} generados, {skipped} ya existian, total Auto: {total_auto}")
    conn.close()

    # Ejecutar la subida automática al finalizar todas las traducciones
    push_to_github()

    if "--no-rebuild" not in sys.argv:
        build_script = Path(__file__).with_name("build_spanish_patch.py")
        if build_script.exists():
            subprocess.Popen([sys.executable, str(build_script)], cwd=build_script.parent)

if __name__ == "__main__":
    main()
