#!/usr/bin/env python3
"""
Arregla tags de color rotos por traducción automática
<amarillo> → <yellow>
<rojo>     → <red>
<verde>    → <green>
<azul>     → <blue>
etc.
"""

import re
from pathlib import Path

TARGET_FOLDERS = ["Dialogue", "Files", "Misc", "Orders", "Quests", "Story", "Tutorial", "UI"]

# Mapeo de tags rotos → tags correctos de PSO2
TAG_FIXES = {
    "<amarillo>": "<yellow>",
    "</amarillo>": "</yellow>",
    "<rojo>": "<red>",
    "</rojo>": "</red>",
    "<verde>": "<green>",
    "</verde>": "</green>",
    "<azul>": "<blue>",
    "</azul>": "</blue>",
    "<rosa>": "<pink>",
    "</rosa>": "</pink>",
    "<morado>": "<purple>",
    "</morado>": "</purple>",
    "<cyan>": "<cyan>",
    "</cyan>": "</cyan>",
}

def fix_file(path: Path) -> bool:
    try:
        content = path.read_text(encoding="utf-8")
    except Exception:
        return False

    new_content = content
    changes = 0

    for broken, correct in TAG_FIXES.items():
        if broken in new_content:
            count = new_content.count(broken)
            new_content = new_content.replace(broken, correct)
            changes += count

    if changes > 0:
        path.write_text(new_content, encoding="utf-8")
        print(f"✅ {path} → {changes} tags arreglados")
        return True
    return False


def main():
    print("Buscando tags rotos (<amarillo>, <rojo>, etc.)...\n")
    fixed_files = 0

    for folder in TARGET_FOLDERS:
        folder_path = Path(folder)
        if not folder_path.exists():
            continue
        for csv_file in folder_path.rglob("*.csv"):
            if fix_file(csv_file):
                fixed_files += 1

    print(f"\nTerminado. Archivos modificados: {fixed_files}")


if __name__ == "__main__":
    main()
