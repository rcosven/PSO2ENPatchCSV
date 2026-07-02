#!/usr/bin/env python3
"""Build complete PSO2 Spanish patch from Arks-Layer ES CSV + Auto translations."""
import argparse
import csv
import os
import re
import shutil
import struct
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

GAME_DATA = Path(r"C:\Program Files (x86)\Steam\steamapps\common\PHANTASYSTARONLINE2_NA_STEAM\pso2_bin\data\win32_na")
CSV_REPO = Path(r"C:\Users\raiko\pso2-spanish-mod\csv-repo")
ZAMBONI_DIR = Path(r"C:\Users\raiko\pso2-spanish-mod\Zamboni\Release")
ZAMBONI = ZAMBONI_DIR / "Zamboni.exe"
WORK = Path(r"C:\Users\raiko\pso2-spanish-mod\build_work")
OUTPUT_MOD = Path(r"C:\Users\raiko\pso2-spanish-mod\PSO2_Parche_Espanol\data\win32_na")
LOG = Path(r"C:\Users\raiko\pso2-spanish-mod\build_patch_log.txt")

SKIP_DIRS = {".git", ".circleci", "_py", "_sh", "_tools", "_fonts", "_aspell", "_misc"}
PREFER_SKIP = {"Files"}


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


def load_filelist() -> dict[str, str]:
    mapping = {}
    with open(CSV_REPO / "filelist.txt", encoding="utf-8") as f:
        for line in f:
            h, name = line.strip().split(",", 1)
            mapping[name] = h
    return mapping


def collect_csv_files() -> tuple[dict[str, Path], dict[str, int]]:
    files: dict[str, Path] = {}
    priorities: dict[str, int] = {}
    for root, _, filenames in os.walk(CSV_REPO):
        parts = set(Path(root).parts)
        if parts & SKIP_DIRS:
            continue
        prio = 0
        if "Files" in parts:
            prio = 1
        elif "Translated" in parts and "Auto" in parts:
            prio = 2
        elif "Translated" in parts:
            prio = 3
        else:
            prio = 4
        for fn in filenames:
            if not fn.endswith(".csv"):
                continue
            text_name = fn[:-4] + ".text"
            if text_name not in files or prio > priorities[text_name]:
                files[text_name] = Path(root) / fn
                priorities[text_name] = prio
    return files, priorities


def ice_has_spanish_source(text_names: set[str], csv_files: dict[str, Path], priorities: dict[str, int]) -> bool:
    """Solo parchear ICE con CSV traducido (no japones crudo en Files/)."""
    for text_name in text_names:
        if text_name in priorities and priorities[text_name] >= 2:
            return True
        path = csv_files.get(text_name)
        if path and "Files" not in path.parts:
            return True
    return False


def read_csv_translations(csv_path: Path) -> dict[str, str]:
    out = {}
    with open(csv_path, encoding="utf-8") as f:
        for row in csv.reader(f, strict=True):
            if len(row) < 2:
                continue
            val = row[1]
            if val.startswith('"""') and val.endswith('"""'):
                val = val[3:-3]
            elif val.startswith('"') and val.endswith('"'):
                val = val[1:-1]
            out[row[0]] = apply_release_sed(val)
    return out


def read_cstring(data: bytes, offset: int) -> str:
    end = data.index(0, offset)
    return data[offset:end].decode("utf-8")


def read_utf16_string(data: bytes, offset: int) -> str:
    chars = []
    i = offset
    while i + 1 < len(data):
        ch = struct.unpack_from("<H", data, i)[0]
        if ch == 0:
            break
        chars.append(chr(ch))
        i += 2
    return "".join(chars)


def parse_nifl_text(data: bytes) -> dict:
    if data[:4] != b"NIFL":
        raise ValueError("Not NIFL")
    base = 0x20
    rel0_data_start = struct.unpack_from("<I", data, 0x28)[0]
    data_start = base + rel0_data_start
    category_ptr, category_count = struct.unpack_from("<ii", data, data_start)

    cat_names = []
    texts = []
    pos = base + category_ptr
    for _ in range(category_count):
        name_off, data_off, sub_count = struct.unpack_from("<iii", data, pos)
        pos += 12
        cat_names.append(read_cstring(data, base + name_off))
        subcats = []
        dpos = base + data_off
        for _ in range(sub_count):
            idx_off, sub_id, idx_count = struct.unpack_from("<iii", data, dpos)
            dpos += 12
            pairs = []
            ipos = base + idx_off
            for _ in range(idx_count):
                name_loc, text_loc = struct.unpack_from("<ii", data, ipos)
                ipos += 8
                pairs.append({
                    "name": read_cstring(data, base + name_loc),
                    "str": read_utf16_string(data, base + text_loc),
                })
            while len(subcats) <= sub_id:
                subcats.append([])
            subcats[sub_id] = pairs
        texts.append(subcats)
    return {"category_names": cat_names, "text": texts}


def align_up(n: int, a: int = 4) -> int:
    return (n + a - 1) & ~(a - 1)


def build_nifl_text(cat_names: list[str], text_groups: list[list[list[dict]]]) -> bytes:
    """Rebuild NIFL text (matches Aqua PSO2Text.GetBytesNIFL layout)."""
    out = bytearray()
    nof0_locs: list[int] = []

    def nof0(pos: int):
        nof0_locs.append(pos)

    out += b"REL0"
    rel0_size_off = 4
    out += struct.pack("<III", 0, 0, 0)
    out += struct.pack("<i", -1)

    text_addrs: list[list[int]] = []
    pair_slots: list[tuple[int, int, dict]] = []

    for cat_idx, subcats in enumerate(text_groups):
        text_addrs.append([])
        for sub_idx, pairs in enumerate(subcats):
            text_addrs[cat_idx].append(len(out))
            for pair in pairs:
                nof0(len(out))
                nof0(len(out) + 4)
                pair_slots.append((len(out), cat_idx, pair))
                out += b"\x00" * 8

    subcat_addrs = []
    sub_nulls: dict[int, list[int]] = defaultdict(list)
    for cat_idx, subcats in enumerate(text_groups):
        subcat_addrs.append(len(out))
        for sub_idx, pairs in enumerate(subcats):
            nof0(len(out))
            if pairs:
                out += struct.pack("<i", text_addrs[cat_idx][sub_idx])
            else:
                sub_nulls[sub_idx].append(len(out))
                out += struct.pack("<i", 0)
            out += struct.pack("<ii", sub_idx, len(pairs))

    cat_off = len(out)
    cat_slots = []
    for cat_idx, _ in enumerate(cat_names):
        cat_slots.append(len(out))
        nof0(len(out))
        nof0(len(out) + 4)
        out += b"\x00" * 4
        out += struct.pack("<i", subcat_addrs[cat_idx])
        out += struct.pack("<i", len(text_groups[cat_idx]))

    # REL0DataStart -> offset to (categoryOffset, categoryCount) header
    struct.pack_into("<I", out, rel0_size_off + 4, len(out))
    nof0(len(out))
    if text_groups:
        out += struct.pack("<ii", cat_off, len(text_groups))
    else:
        out += struct.pack("<III", 0x24, 0, 1)
        out += struct.pack("<i", 0)

    name_ptrs: dict[int, dict[str, int]] = defaultdict(dict)
    for slot, cat_idx, pair in pair_slots:
        nm = pair["name"]
        if nm in name_ptrs[cat_idx]:
            struct.pack_into("<I", out, slot, name_ptrs[cat_idx][nm])
        else:
            struct.pack_into("<I", out, slot, len(out))
            name_ptrs[cat_idx][nm] = len(out)
            out += nm.encode("utf-8") + b"\x00"
            while len(out) % 4:
                out.append(0)
        struct.pack_into("<I", out, slot + 4, len(out))
        out += pair["str"].encode("utf-16-le") + b"\x00\x00"
        while len(out) % 4:
            out.append(0)

    for slot, name in zip(cat_slots, cat_names):
        struct.pack_into("<I", out, slot, len(out))
        out += name.encode("utf-8") + b"\x00"
        while len(out) % 4:
            out.append(0)

    if text_groups:
        for sub_idx in range(len(text_groups[0])):
            if text_groups[0][sub_idx]:
                out += struct.pack("<i", sub_idx)
            else:
                for addr in sub_nulls.get(sub_idx, []):
                    struct.pack_into("<I", out, addr, len(out))
                out += struct.pack("<i", 0)
    while len(out) % 0x10:
        out.append(0)

    struct.pack_into("<I", out, rel0_size_off, len(out) - 8)

    nof0_off = len(out)
    nof0_size = (len(nof0_locs) + 2) * 4
    out += b"NOF0"
    out += struct.pack("<III", nof0_size, len(nof0_locs), 0x10)
    for loc in nof0_locs:
        out += struct.pack("<I", loc)
    while len(out) % 0x10:
        out.append(0)
    out += b"NEND" + struct.pack("<III", 8, 0, 0)

    nof0_block = align_up(len(out) - nof0_off) + 0x10
    nifl = struct.pack(
        "<8I",
        0x4C46494E,
        0x18,
        1,
        0x20,
        nof0_off,
        nof0_off + 0x20,
        nof0_block,
        0,
    )
    return nifl + bytes(out)


def csv_key_map(translations: dict[str, str]) -> dict[tuple[str, int], str]:
    ordered: dict[str, list[str]] = defaultdict(list)
    for key, value in translations.items():
        m = re.match(r"^(.*)#(\d+)$", key)
        base = m.group(1) if m else key
        ordered[base].append(value)
    return {(b, i): v for b, vals in ordered.items() for i, v in enumerate(vals)}


def patch_text_file(original: bytes, translations: dict[str, str]) -> bytes:
    parsed = parse_nifl_text(original)
    key_map = csv_key_map(translations)
    seen: dict[str, int] = defaultdict(int)
    target_group = 1 if parsed["text"] and len(parsed["text"][0]) > 1 else 0
    changed = 0

    for cat in parsed["text"]:
        if target_group >= len(cat):
            continue
        for pair in cat[target_group]:
            base = pair["name"]
            occ = seen[base]
            seen[base] += 1
            new_val = key_map.get((base, occ))
            if new_val is not None and new_val != pair["str"]:
                pair["str"] = new_val
                changed += 1

    if changed == 0:
        return original
    return build_nifl_text(parsed["category_names"], parsed["text"])


def zamboni_unpack(ice_hash: str, ice_path: Path) -> Path | None:
    ext = ZAMBONI_DIR / f"{ice_hash}_ext"
    if (ext / "group2").exists() or (ext / "group1").exists():
        return ext
    for proc in ("Zamboni.exe",):
        try:
            subprocess.run(
                [str(ZAMBONI), "-unpack", "-outName", str(ext), str(ice_path)],
                cwd=ZAMBONI_DIR, capture_output=True, timeout=180,
            )
        except subprocess.TimeoutExpired:
            pass
    time.sleep(0.15)
    if (ext / "group2").exists() or (ext / "group1").exists():
        return ext
    return None


def zamboni_pack(pack_parent: Path, out_ice: Path) -> bool:
    import shutil

    out_ice.parent.mkdir(parents=True, exist_ok=True)
    if out_ice.exists():
        out_ice.unlink()
    subprocess.run(
        [str(ZAMBONI), "-pack", "-outName", str(out_ice), str(pack_parent)],
        cwd=ZAMBONI_DIR, capture_output=True, timeout=120,
    )
    time.sleep(0.2)
    candidates = [
        out_ice,
        WORK / f"{pack_parent.name}.ice",
        Path(str(pack_parent) + ".ice"),
        pack_parent.parent / f"{pack_parent.name}.ice",
        pack_parent / "group2.ice",
    ]
    for src in candidates:
        if src.exists() and src.stat().st_size > 100:
            if src.resolve() != out_ice.resolve():
                shutil.copy2(src, out_ice)
            return True
    return False


def patch_is_stale(ice_hash: str) -> bool:
    out_ice = OUTPUT_MOD / ice_hash
    ice_src = GAME_DATA / ice_hash
    if not out_ice.exists() or out_ice.stat().st_size <= 100:
        return True
    return ice_src.stat().st_mtime > out_ice.stat().st_mtime


def clear_unpack_cache(ice_hash: str):
    for p in (ZAMBONI_DIR / f"{ice_hash}_ext", WORK / f"{ice_hash}_ext"):
        if p.exists():
            shutil.rmtree(p, ignore_errors=True)


def sync_orphan_ice() -> int:
    synced = 0
    for ice in WORK.glob("pack_*.ice"):
        if ice.stat().st_size <= 100:
            continue
        dest = OUTPUT_MOD / ice.stem.removeprefix("pack_")
        if not dest.exists() or dest.stat().st_size <= 100:
            shutil.copy2(ice, dest)
            synced += 1
    return synced


def main(force_rebuild: bool = False):
    LOG.write_text("", encoding="utf-8")
    WORK.mkdir(parents=True, exist_ok=True)
    OUTPUT_MOD.mkdir(parents=True, exist_ok=True)

    filelist = load_filelist()
    csv_files, priorities = collect_csv_files()
    ice_to_texts: dict[str, set[str]] = defaultdict(set)
    for text_name in csv_files:
        if text_name in filelist:
            ice_to_texts[filelist[text_name]].add(text_name)

    # Solo ICE que existen en el cliente instalado y tienen traduccion al espanol
    ice_to_texts = {
        h: t for h, t in ice_to_texts.items()
        if (GAME_DATA / h).exists() and ice_has_spanish_source(t, csv_files, priorities)
    }

    synced = sync_orphan_ice()
    existing = sum(1 for f in OUTPUT_MOD.iterdir() if f.is_file() and f.stat().st_size > 100)
    log(f"CSV fuentes: {len(csv_files)} | ICE a parchear: {len(ice_to_texts)} | ya listos: {existing} | sincronizados: {synced}")

    built = existing
    skipped = errors = 0
    total = len(ice_to_texts)

    for i, (ice_hash, text_names) in enumerate(sorted(ice_to_texts.items()), 1):
        ice_src = GAME_DATA / ice_hash
        out_ice = OUTPUT_MOD / ice_hash
        if not force_rebuild and not patch_is_stale(ice_hash):
            built += 1
            continue
        if patch_is_stale(ice_hash):
            clear_unpack_cache(ice_hash)
        if not ice_src.exists():
            skipped += 1
            continue
        try:
            ext = zamboni_unpack(ice_hash, ice_src)
            if not ext:
                errors += 1
                continue
            group = ext / "group2" if (ext / "group2").exists() else ext / "group1"
            if not group.exists():
                errors += 1
                continue

            modified = False
            for text_name in text_names:
                text_path = group / text_name
                if not text_path.exists() or text_name not in csv_files:
                    continue
                trans = read_csv_translations(csv_files[text_name])
                original = text_path.read_bytes()
                patched = patch_text_file(original, trans)
                if patched != original:
                    text_path.write_bytes(patched)
                    modified = True

            if modified:
                pack_dir = WORK / f"pack_{ice_hash}"
                if pack_dir.exists():
                    import shutil
                    shutil.rmtree(pack_dir)
                pack_dir.mkdir(parents=True)
                import shutil
                tgt = pack_dir / group.name
                shutil.copytree(group, tgt)
                out_ice = OUTPUT_MOD / ice_hash
                if zamboni_pack(pack_dir, out_ice):
                    built += 1
                else:
                    errors += 1
        except Exception as e:
            errors += 1
            log(f"ERROR {ice_hash}: {e}")

        if i % 25 == 0 or i == total:
            log(f"Progreso {i}/{total} | parcheados: {built} | omitidos: {skipped} | errores: {errors}")

    log(f"FIN — ICE generados: {built}")
    return built


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force-rebuild", action="store_true", help="Reparchear todos los ICE")
    args = parser.parse_args()
    main(force_rebuild=args.force_rebuild)
