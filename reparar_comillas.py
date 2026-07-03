import os
from pathlib import Path

# Las carpetas donde están tus CSV
TARGET_FOLDERS = ["Dialogue", "Files", "Misc", "Orders", "Quests", "Story", "Tutorial", "UI"]
REPO_DIR = Path(".") 

archivos_reparados = 0

for folder in TARGET_FOLDERS:
    folder_dir = REPO_DIR / folder
    if not folder_dir.exists():
        continue

    for file_path in folder_dir.rglob("*.csv"):
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()

        new_lines = []
        modified = False
        
        for line in content.splitlines():
            # Si la línea tiene una coma, separamos la ID del texto
            if "," in line:
                key, val = line.split(",", 1)
                val = val.strip()
                
                # Si el valor está entre comillas (aunque sean 7 comillas)
                if val.startswith('"') and val.endswith('"'):
                    # 1. Quitamos TODAS las comillas de los bordes
                    clean_val = val.strip('"')
                    # 2. Si quedaron comillas dobles DENTRO del texto, las pasamos a simples
                    clean_val = clean_val.replace('"', "'")
                    
                    # 3. Volvemos a armar la línea con exactamente 3 comillas
                    new_line = f'{key},"""{clean_val}"""'
                    
                    new_lines.append(new_line)
                    if new_line != line:
                        modified = True
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)
                
        # Guardamos el archivo solo si lo reparamos
        if modified:
            with open(file_path, "w", encoding="utf-8", newline="\n") as f:
                f.write("\n".join(new_lines) + "\n")
            archivos_reparados += 1
            print(f"Reparado: {file_path.name}")

print(f"\n¡Listo! Se repararon {archivos_reparados} archivos. Ya puedes subirlos a GitHub.")
