"""
Furniture Upgrade - PySide6 save editor for Mewgenics
------------------------------------------------------
v19 changes:
  * Preserve-excess merge mode: consume only the minimum members needed
    to reach the target tier; leftovers stay in the furniture table.
  * Compact tooltips via STAT_ABBREV (c/a/s/h/m).
  * target_plus_from_value uses bit_length (no math import needed).
  * NOTIFY_SOUND_ENABLED syncs from config on startup.

v18 changes:
  * _data_search_roots() - walks up from the exe dir looking for the mod
    root, so the frozen exe can find data/text/combined.csv.append and
    data/furniture_effects.gon (fixes "0 display names" in the exe).
  * Full UI localization: load translations.json, auto-detect system
    language, Settings -> Language menu, tr() wraps UI strings.
  * Config remembers ui_language choice across restarts.

v17 changes:
  * Tools -> "Rebuild stats (.gon)..." dialog with buff/debuff multipliers
    (integer 1-10), unlink toggle, mode dropdown, and tooltip toggle.
  * AutoFitTreeWidget. Log font 1.25x body.

v16 changes:
  * Portable backups + config next to exe/script.
  * Cross-platform save detection (Windows, macOS, Linux, Steam Deck).
  * Wide-grab column headers.

--- HOW TO BUILD FOR EACH OS ---
PyInstaller does NOT cross-compile. Build each on its own OS.

Windows:  --add-data "fonts;fonts"
macOS:    --add-data "fonts:fonts"
Linux:    --add-data "fonts:fonts"

--- LINUX / MAC USERS ---
They can run the .py directly:
    pip install -r requirements.txt
    python FurnitureUpgrade.py

Run BEFORE starting Mewgenics. Close the game first.
"""

import csv
import glob
import json
import os
import re
import shutil
import sqlite3
import struct
import sys
import time
import datetime
import traceback

try:
    import winsound
except ImportError:
    winsound = None

from PySide6.QtCore import Qt, QObject, QEvent, QLocale
from PySide6.QtGui import (
    QFont, QFontDatabase, QPainter, QLinearGradient,
    QColor, QBrush, QKeySequence, QShortcut,
    QAction, QActionGroup
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QLabel, QPushButton,
    QLineEdit, QComboBox, QTreeWidget, QTreeWidgetItem, QPlainTextEdit,
    QFileDialog, QDialog, QDialogButtonBox,
    QVBoxLayout, QHBoxLayout,
    QCheckBox, QSplitter, QFrame, QSizePolicy, QHeaderView,
    QAbstractItemView, QInputDialog
)


# =============================================================================
# CONFIG
# =============================================================================
APP_TITLE = "Furniture Upgrade"
CONFIG_FILENAME = "FurnitureUpgrade-config.json"
TRANSLATIONS_FILENAME = "translations.json"
BACKUP_SUBDIR = "backups"

BASE_EXCLUDE = {"special_foodbox"}
STATUE_HINTS = ()
IDOL_HINTS = ("idol",)

DEFAULT_BACKUP_KEEP = 5
STORAGE_LOC = ""
SYSTEM_FALLBACK_FONT = "Segoe UI"
NOTIFY_SOUND_ENABLED = False

DEFAULT_FONT_SIZE_MEWGENICS = 8
DEFAULT_FONT_SIZE_SYSTEM = 10
FONT_SIZE_CHOICES = list(range(6, 15))
_CURRENT_FONT_SIZE = DEFAULT_FONT_SIZE_SYSTEM

DEFAULT_BUFF_MULT = 2
DEFAULT_DEBUFF_MULT = 2
DEFAULT_DEBUFF_MODE = "flip"
SCALING_MODE_LABELS = {
    "flip":       "Flip (debuff -> buff at tier 3+)",
    "neutralize": "Neutralize (debuff -> 0 at tier 2+)",
    "leave":      "Leave debuffs alone",
}

SCALABLE_STATS = ("Comfort", "Appeal", "Stimulation", "Health", "Evolution")
STAT_ABBREV = {
    "Comfort":     "c",
    "Appeal":      "a",
    "Stimulation": "s",
    "Health":      "h",
    "Evolution":   "m",
}

_BLOCK_OPEN_RE = re.compile(r'^\s*([A-Za-z0-9_]+)\s*\{\s*(?://.*)?$')
_BLOCK_CLOSE_RE = re.compile(r'^\s*\}\s*(?://.*)?$')
_STAT_LINE_RE = re.compile(r'^(\s*)([A-Z][A-Za-z_]*)\s+(-?\d+)\s*$')


def set_current_font_size(pt):
    global _CURRENT_FONT_SIZE
    _CURRENT_FONT_SIZE = int(pt)


def _fs(mult=1.0, min_pt=6):
    return max(min_pt, int(round(_CURRENT_FONT_SIZE * mult)))


FONT_ROLE_KEYWORDS = {
    "body":   ("tikafont",),
    "title":  ("edmund",),
    "log":    ("organgrinder",),
    "error":  ("theend",),
    "warn":   ("peralta",),
    "button": ("frank",),
    "notif":  ("jack",),
    "accent": ("swanky", "tikaswank"),
}
ACTIVE_FONT_ROLES = {}


def set_active_font_roles(roles):
    global ACTIVE_FONT_ROLES
    ACTIVE_FONT_ROLES = dict(roles)


def font_for(role, fallback=None):
    if role in ACTIVE_FONT_ROLES:
        return ACTIVE_FONT_ROLES[role]
    return fallback or SYSTEM_FALLBACK_FONT


def play_notify_sound():
    if not NOTIFY_SOUND_ENABLED:
        return
    if winsound is not None:
        try:
            winsound.PlaySound("SystemAsterisk",
                               winsound.SND_ALIAS | winsound.SND_ASYNC)
            return
        except Exception:
            pass
    try:
        QApplication.beep()
    except Exception:
        pass


# =============================================================================
# LOCALIZATION
# =============================================================================
TRANSLATIONS = {}
CURRENT_LANGUAGE = "en"

# UI language codes we support (matching translations.json top-level keys)
SUPPORTED_LANGUAGES = ("en", "fr", "de", "es", "it", "pt-br", "ru", "ko", "ja", "zh")

LANGUAGE_LABELS = {
    "auto": "(auto)",
    "en":   "English",
    "fr":   "Français",
    "de":   "Deutsch",
    "es":   "Español",
    "it":   "Italiano",
    "pt-br": "Português (BR)",
    "ru":   "Русский",
    "ko":   "한국어",
    "ja":   "日本語",
    "zh":   "简体中文",
}


def _writable_base_dir():
    """Return a WRITABLE directory: exe dir for frozen builds, script dir
    otherwise. Never MEIPASS (temp, wipes on exit)."""
    if getattr(sys, "frozen", False):
        exe_dir = os.path.dirname(sys.executable)
        if exe_dir and os.path.isdir(exe_dir):
            return exe_dir
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return os.getcwd()


def config_file_path():
    return os.path.join(_writable_base_dir(), CONFIG_FILENAME)


def portable_backup_dir():
    d = os.path.join(_writable_base_dir(), BACKUP_SUBDIR)
    try:
        os.makedirs(d, exist_ok=True)
        if os.path.isdir(d):
            return d
    except Exception:
        pass
    return None


def load_config():
    p = config_file_path()
    if not p or not os.path.isfile(p):
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except Exception:
        pass
    return {}


def save_config(data):
    p = config_file_path()
    if not p:
        return
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


def _translation_candidates():
    """Where we look for translations.json. Includes bundled dirs and
    walks up from writable base so frozen exe finds it in the mod root."""
    out = []
    seen = set()
    for base in _base_dirs():
        p = os.path.join(base, TRANSLATIONS_FILENAME)
        if p not in seen:
            seen.add(p); out.append(p)
    cur = _writable_base_dir()
    for _ in range(4):
        p = os.path.join(cur, TRANSLATIONS_FILENAME)
        if p not in seen:
            seen.add(p); out.append(p)
        parent = os.path.dirname(cur)
        if not parent or parent == cur:
            break
        cur = parent
    return out


def load_translations():
    """Load translations.json from the first candidate that exists.
    Returns the loaded path or None."""
    global TRANSLATIONS
    for p in _translation_candidates():
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    TRANSLATIONS = data
                    return p
            except Exception:
                pass
    TRANSLATIONS = {}
    return None


def detect_system_language():
    """Return the 2-letter system locale, lowercased. Falls back to 'en'."""
    try:
        name = QLocale.system().name()   # e.g. 'fr_FR'
        code = name.split("_")[0].lower()
        if code:
            return code
    except Exception:
        pass
    for var in ("LANG", "LC_ALL", "LC_MESSAGES", "LANGUAGE"):
        v = os.environ.get(var, "").strip()
        if v:
            return v.split(".")[0].split("_")[0].lower()
    return "en"


def set_language(lang):
    """Set current UI language. 'auto' -> detect system. Normalizes to a
    supported key, or falls back to 'en'."""
    global CURRENT_LANGUAGE
    if not lang or lang == "auto":
        lang = detect_system_language()
    if lang not in TRANSLATIONS:
        base = lang.split("-")[0].lower()
        if base in TRANSLATIONS:
            lang = base
        elif "en" in TRANSLATIONS:
            lang = "en"
        else:
            lang = "en"
    CURRENT_LANGUAGE = lang


def tr(key):
    """Look up a UI string. Order: current lang, en, then the key itself."""
    if CURRENT_LANGUAGE and CURRENT_LANGUAGE != "en":
        v = TRANSLATIONS.get(CURRENT_LANGUAGE, {}).get(key)
        if v:
            return v
    v = TRANSLATIONS.get("en", {}).get(key)
    if v:
        return v
    return key


# =============================================================================
# SAVE DETECTION
# =============================================================================
def _candidate_save_roots():
    home = os.path.expanduser("~")
    roots = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(os.path.join(appdata, "Glaiel Games", "Mewgenics"))
    xdg_data = os.environ.get("XDG_DATA_HOME")
    if xdg_data:
        roots.append(os.path.join(xdg_data, "Glaiel Games", "Mewgenics"))
    roots.append(os.path.join(home, ".local", "share",
                              "Glaiel Games", "Mewgenics"))
    roots.append(os.path.join(home, ".config",
                              "Glaiel Games", "Mewgenics"))
    roots.append(os.path.join(home, "Library", "Application Support",
                              "Glaiel Games", "Mewgenics"))
    steam_roots = [
        os.path.join(home, ".steam", "steam"),
        os.path.join(home, ".steam", "root"),
        os.path.join(home, ".local", "share", "Steam"),
        os.path.join(home, ".var", "app", "com.valvesoftware.Steam",
                     ".local", "share", "Steam"),
    ]
    for steam in steam_roots:
        compatdata = os.path.join(steam, "steamapps", "compatdata")
        if not os.path.isdir(compatdata):
            continue
        try:
            for appid in os.listdir(compatdata):
                pfx_root = os.path.join(
                    compatdata, appid, "pfx", "drive_c",
                    "users", "steamuser", "AppData", "Roaming",
                    "Glaiel Games", "Mewgenics")
                if os.path.isdir(pfx_root):
                    roots.append(pfx_root)
        except OSError:
            pass
    seen, out = set(), []
    for r in roots:
        if not r:
            continue
        r = os.path.abspath(r)
        if r in seen:
            continue
        seen.add(r)
        if os.path.isdir(r):
            out.append(r)
    return out


def default_save_dir():
    roots = _candidate_save_roots()
    return roots[0] if roots else None


def find_saves():
    diag = []
    roots = _candidate_save_roots()
    if not roots:
        diag.append("  no Mewgenics data folder found on this system")
        return [], diag
    diag.append(f"  searching {len(roots)} candidate root(s):")
    saves = set()
    for root in roots:
        diag.append(f"    {root}")
        try:
            pattern = os.path.join(root, "*", "saves", "*.sav")
            for p in glob.glob(pattern):
                saves.add(os.path.abspath(p))
        except Exception:
            pass
    saves = sorted(saves)
    diag.append(f"  matches: {len(saves)}")
    return saves, diag


def _best_save(saves):
    if not saves:
        return None
    campaigns = [s for s in saves
                 if "steamcampaign" in os.path.basename(s).lower()]
    pool = campaigns if campaigns else saves
    def _mtime(p):
        try:
            return os.path.getmtime(p)
        except OSError:
            return 0
    return max(pool, key=_mtime)


def make_backup_path(save_path, kind):
    bdir = portable_backup_dir()
    base = os.path.basename(save_path)
    ts = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    name = f"{base}.backup_{kind}_{ts}"
    candidate = os.path.join(bdir, name) if bdir else save_path + f".backup_{kind}_{ts}"
    if not os.path.exists(candidate):
        return candidate
    i = 1
    while True:
        c = (os.path.join(bdir, f"{name}_{i}") if bdir
             else save_path + f".backup_{kind}_{ts}_{i}")
        if not os.path.exists(c):
            return c
        i += 1


def find_backups(save_path):
    prefix = os.path.basename(save_path) + ".backup"
    seen, out = set(), []
    bdir = portable_backup_dir()
    if bdir:
        try:
            for entry in os.listdir(bdir):
                if entry.startswith(prefix):
                    full = os.path.join(bdir, entry)
                    if os.path.isfile(full) and full not in seen:
                        seen.add(full); out.append(full)
        except OSError:
            pass
    directory = os.path.dirname(os.path.abspath(save_path))
    try:
        for entry in os.listdir(directory):
            if entry.startswith(prefix):
                full = os.path.join(directory, entry)
                if os.path.isfile(full) and full not in seen:
                    seen.add(full); out.append(full)
    except OSError:
        pass
    return sorted(out)


def get_backup_info(save_path):
    out = []
    for p in find_backups(save_path):
        try:
            st = os.stat(p)
        except OSError:
            continue
        out.append({
            'path': p, 'name': os.path.basename(p),
            'mtime': st.st_mtime,
            'dt': datetime.datetime.fromtimestamp(st.st_mtime),
            'size': st.st_size,
        })
    out.sort(key=lambda x: x['mtime'], reverse=True)
    return out


def humanize_age(seconds):
    seconds = max(0, seconds)
    if seconds < 60:
        return f"{int(seconds)}s ago"
    if seconds < 3600:
        return f"{int(seconds // 60)}m ago"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h ago"
    if seconds < 86400 * 30:
        return f"{int(seconds // 86400)}d ago"
    if seconds < 86400 * 365:
        return f"{int(seconds // (86400 * 30))}mo ago"
    return f"{seconds / (86400 * 365):.1f}y ago"


def prune_backups_keep_n(save_path, keep_n):
    infos = get_backup_info(save_path)
    to_delete = infos[keep_n:]
    deleted, freed = 0, 0
    for info in to_delete:
        try:
            os.remove(info['path'])
            deleted += 1
            freed += info['size']
        except OSError:
            pass
    return deleted, freed


# =============================================================================
# EMBEDDED .GON PATCHER ENGINE
# =============================================================================
def _gon_parse_blocks(lines):
    blocks = []
    i = 0
    n = len(lines)
    while i < n:
        m = _BLOCK_OPEN_RE.match(lines[i])
        if not m:
            i += 1
            continue
        name = m.group(1)
        start = i
        depth = 1
        i += 1
        while i < n and depth > 0:
            if _BLOCK_OPEN_RE.match(lines[i]):
                depth += 1
            elif _BLOCK_CLOSE_RE.match(lines[i]):
                depth -= 1
                if depth == 0:
                    break
            i += 1
        end = i + 1
        blocks.append((start, end, name, list(lines[start:end])))
        i += 1
    return blocks


def _gon_scale_stat(value, tier, buff, debuff, mode):
    if value == 0:
        return 0
    if value > 0:
        return value * (buff ** tier)
    mag = -value
    if mode == "leave":
        return value
    if mode == "neutralize":
        if tier <= 0:
            return value
        if tier == 1:
            return -(mag // debuff)
        return 0
    if tier <= 0:
        return value
    if tier == 1:
        return -(mag // debuff)
    if tier == 2:
        return 0
    if tier == 3:
        return mag // debuff
    if tier == 4:
        return mag
    return mag * (debuff ** (tier - 4))


def _gon_extract_stats(body):
    d = {}
    for line in body:
        m = _STAT_LINE_RE.match(line)
        if m:
            _, stat_name, value_s = m.groups()
            d[stat_name] = int(value_s)
    return d


def _gon_fmt_signed(v):
    return f"+{v}" if v > 0 else str(v)


def _gon_build_tooltip(base_stats, new_stats):
    """Compact tooltip: base stat values only, e.g. 'c 1 a 2'.
    new_stats kept for signature compatibility but ignored."""
    parts = []
    for stat in SCALABLE_STATS:
        bv = base_stats.get(stat)
        if not bv:
            continue
        parts.append(f"{STAT_ABBREV.get(stat, stat[0].lower())} {bv}")
    return " ".join(parts)


def _gon_build_plus_block(base_name, base_body, suffix, tier, buff, debuff,
                          mode, base_stats_map, write_desc):
    new_id = base_name + suffix
    new_stats = {}
    for line in base_body[1:-1]:
        m = _STAT_LINE_RE.match(line)
        if not m:
            continue
        _, stat_name, value_s = m.groups()
        v = int(value_s)
        if stat_name in SCALABLE_STATS:
            new_stats[stat_name] = _gon_scale_stat(v, tier, buff, debuff, mode)
        else:
            new_stats[stat_name] = v
    tooltip = _gon_build_tooltip(base_stats_map, new_stats) if write_desc else ""
    out = [f"{new_id} {{"]
    for line in base_body[1:-1]:
        stripped = line.strip()
        if not stripped:
            out.append(line); continue
        if stripped.startswith("desc "):
            continue
        if stripped.startswith("name "):
            out.append(f"    name FURNITURE_NAME_{new_id.upper()}")
            if write_desc and tooltip:
                out.append(f"    desc FURNITURE_DESC_{new_id.upper()}")
            continue
        m_stat = _STAT_LINE_RE.match(line)
        if m_stat:
            indent, stat_name, _ = m_stat.groups()
            if stat_name in SCALABLE_STATS:
                out.append(f"{indent}{stat_name} {new_stats[stat_name]}")
            else:
                out.append(line)
            continue
        out.append(line)
    out.append("}")
    return out, tooltip


_CSV_CONTENT_COLUMNS = 11


def _csv_escape(value):
    if value is None:
        return ""
    s = str(value)
    if any(c in s for c in (",", '"', "\n", "\r")):
        s = s.replace('"', '""')
        return f'"{s}"'
    return s


def _csv_row(fields):
    return ",".join(_csv_escape(f) for f in fields) + "\n"


def _csv_desc_row(key, tooltip):
    fields = [key, tooltip, ""]
    for _ in range(_CSV_CONTENT_COLUMNS - 2):
        fields.append(tooltip)
    return _csv_row(fields)


def _csv_name_row(base_id, base_fields, tier):
    suffix = f" +{tier}"
    key = f"FURNITURE_NAME_{base_id.upper()}_PLUS{tier}"
    fields = list(base_fields) if base_fields else []
    while len(fields) < _CSV_CONTENT_COLUMNS:
        fields.append("")
    en = fields[0].strip() or base_id
    row = [key, en + suffix, ""]
    for i in range(2, _CSV_CONTENT_COLUMNS):
        v = fields[i].strip() or en
        row.append(v + suffix)
    return _csv_row(row)


def _normalize_key(line):
    if not line:
        return ""
    i = 0
    n = len(line)
    in_quotes = False
    while i < n:
        c = line[i]
        if c == '"':
            in_quotes = not in_quotes
        elif c == "," and not in_quotes:
            break
        i += 1
    key_part = line[:i].strip()
    if key_part.startswith('"') and key_part.endswith('"'):
        key_part = key_part[1:-1]
    key_part = key_part.replace('""', '"')
    return key_part.upper()


def _gon_load_base_name_translations(csv_paths):
    result = {}
    plus_suffixes = tuple(f"_PLUS{i}" for i in range(1, 6))
    for path in csv_paths:
        if not path or not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8-sig", newline="") as f:
                reader = csv.reader(f)
                for row in reader:
                    if not row:
                        continue
                    key = (row[0] or "").strip()
                    if not key or key.startswith("#") or key.startswith("//"):
                        continue
                    if not key.startswith("FURNITURE_NAME_"):
                        continue
                    base_id = key[len("FURNITURE_NAME_"):]
                    if base_id.upper().endswith(plus_suffixes):
                        continue
                    base_id_lc = base_id.lower()
                    if base_id_lc in result:
                        continue
                    fields = [f.strip() for f in row[1:]]
                    while len(fields) < _CSV_CONTENT_COLUMNS:
                        fields.append("")
                    result[base_id_lc] = fields[:_CSV_CONTENT_COLUMNS]
        except Exception:
            pass
    return result


def _gon_update_csv(csv_path, new_desc_rows, new_name_rows):
    if os.path.isfile(csv_path):
        with open(csv_path, "r", encoding="utf-8-sig") as f:
            existing = f.readlines()
    else:
        existing = []

    plus_suffixes = tuple(f"_PLUS{i}" for i in range(1, 6))

    filtered = []
    for line in existing:
        key = _normalize_key(line.strip())
        if key.startswith("FURNITURE_DESC_") or key.startswith("FURNITURE_NAME_"):
            if key.endswith(plus_suffixes):
                continue
        filtered.append(line)

    if filtered and not filtered[-1].endswith("\n"):
        filtered[-1] += "\n"

    for key, tooltip in new_desc_rows:
        filtered.append(_csv_desc_row(key, tooltip))
    for base_id, base_fields, tier in new_name_rows:
        filtered.append(_csv_name_row(base_id, base_fields, tier))

    parent = os.path.dirname(csv_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(csv_path, "w", encoding="utf-8") as f:
        f.writelines(filtered)


def rebuild_effects_gon(gon_path, buff=2, debuff=2, mode="flip",
                        write_desc=True, write_names=True, dry_run=False):
    """Rebuild _plusN blocks in furniture_effects.gon.
    Returns dict with keys: base_count, plus_count, desc_rows, name_rows,
    preview_lines, bytes_written, backup_path."""
    gon_path = os.path.abspath(gon_path)
    if not os.path.isfile(gon_path):
        raise FileNotFoundError(gon_path)
    if not (1 <= buff <= 10):
        raise ValueError(f"buff must be 1..10, got {buff}")
    if not (1 <= debuff <= 10):
        raise ValueError(f"debuff must be 1..10, got {debuff}")
    if mode not in ("flip", "neutralize", "leave"):
        raise ValueError(f"mode must be flip|neutralize|leave, got {mode}")

    text = open(gon_path, "r", encoding="utf-8").read()
    lines = text.splitlines()
    blocks = _gon_parse_blocks(lines)
    base_blocks = [b for b in blocks if "_plus" not in b[2]]
    if not base_blocks:
        raise RuntimeError("No base blocks found in .gon")

    suffixes = [(f"_plus{i}", i) for i in range(1, 6)]
    base_stats_maps = {name: _gon_extract_stats(body)
                       for _, _, name, body in base_blocks}

    csv_dir = os.path.join(os.path.dirname(gon_path), "text")
    candidate_csv_paths = [
        os.path.join(csv_dir, "combined.csv.append"),
        os.path.join(csv_dir, "combined.csv"),
    ]
    base_names = _gon_load_base_name_translations(candidate_csv_paths)

    preamble = lines[:blocks[0][0]]
    last_end = blocks[-1][1]
    postamble = lines[last_end:]

    out_lines = list(preamble)
    for _, _, name, body in base_blocks:
        out_lines.extend(body); out_lines.append("")

    desc_rows = []
    name_rows = []
    preview = []
    for _, _, name, body in base_blocks:
        bmap = base_stats_maps.get(name, {})
        if write_names:
            base_fields = base_names.get(name.lower())
            for suffix, tier in suffixes:
                name_rows.append((name, base_fields, tier))
        for suffix, tier in suffixes:
            blk, tip = _gon_build_plus_block(
                name, body, suffix, tier, buff, debuff, mode,
                bmap, write_desc)
            out_lines.extend(blk); out_lines.append("")
            if write_desc and tip:
                desc_rows.append(
                    (f"FURNITURE_DESC_{name.upper()}{suffix.upper()}", tip))
            if name == "object_cinderblock1":
                preview.append((suffix, blk, tip,
                                _csv_name_row(name, base_names.get(name.lower()), tier).strip()))
    out_lines.extend(postamble)

    result = {
        "base_count":    len(base_blocks),
        "plus_count":    len(base_blocks) * 5,
        "desc_rows":     len(desc_rows),
        "name_rows":     len(name_rows),
        "preview_lines": preview,
        "bytes_written": 0,
        "backup_path":   None,
        "name_count":    len(base_names),
    }

    if dry_run:
        return result

    backup = gon_path + ".bak_scaling"
    shutil.copy2(gon_path, backup)
    with open(gon_path, "w", encoding="utf-8") as f:
        f.write("\n".join(out_lines) + "\n")
    result["bytes_written"] = os.path.getsize(gon_path)
    result["backup_path"] = backup

    if write_desc or write_names:
        csv_path = os.path.join(csv_dir, "combined.csv.append")
        _gon_update_csv(csv_path, desc_rows, name_rows)

    return result


# =============================================================================
# THEME SYSTEM
# =============================================================================
def _theme(**overrides):
    base = {
        "style": "aero",
        "bg": "#150a24", "bg_panel": "#241542",
        "bg_glass": "#30205a", "bg_glass_hi": "#3d2b6e",
        "fg": "#f2e9ff", "fg_dim": "#b8a8d8",
        "accent": "#c77dff", "accent_hi": "#e0aaff",
        "accent_dim": "#5a3a7a", "accent_fg": "#101018",
        "good": "#7fe8c8", "warn": "#ffd68a", "bad": "#ff8a8a",
        "banner_top": "#3a1466", "banner_bottom": "#8a3ad8",
        "banner_title_fg": "#ffffff", "banner_sub_fg": "#e8d5ff",
        "radius": 8, "input_bg": None, "input_fg": None,
    }
    base.update(overrides)
    return base


THEMES = {
    "Frutiger Aero (Purple)": _theme(**{
        "style": "aero", "banner_top": "#2b0d52",
        "banner_bottom": "#9a4ae0", "radius": 10}),
    "Frutiger Aero (Blue)": _theme(**{
        "style": "aero",
        "bg": "#0a1a2e", "bg_panel": "#12263f", "bg_glass": "#1a3557",
        "bg_glass_hi": "#25548a", "fg": "#e8f4ff", "fg_dim": "#8fb0d0",
        "accent": "#5ec8ff", "accent_hi": "#a4e2ff", "accent_dim": "#2b5a85",
        "accent_fg": "#062035", "banner_top": "#082348",
        "banner_bottom": "#3a9ee0", "banner_sub_fg": "#c8e4ff", "radius": 10}),
    "Sakura": _theme(**{
        "style": "modern",
        "bg": "#1a1218", "bg_panel": "#241a20", "bg_glass": "#2e2028",
        "bg_glass_hi": "#3e2c38", "fg": "#f8e8f0", "fg_dim": "#b8a0ac",
        "accent": "#ff8ab8", "accent_hi": "#ffb8d4", "accent_dim": "#4a2c38",
        "accent_fg": "#1a0a10", "banner_top": "#2e1824",
        "banner_bottom": "#c8558a", "banner_sub_fg": "#f0c8d8", "radius": 6}),
    "Mint": _theme(**{
        "style": "modern",
        "bg": "#0f1a15", "bg_panel": "#16241c", "bg_glass": "#1c2e24",
        "bg_glass_hi": "#274234", "fg": "#e8f4ec", "fg_dim": "#90a89a",
        "accent": "#5effa0", "accent_hi": "#a0ffc8", "accent_dim": "#1e3a2a",
        "accent_fg": "#0a1f14", "banner_top": "#0a2a1a",
        "banner_bottom": "#28a060", "banner_sub_fg": "#c8ffd8", "radius": 6}),
    "Standard Dark": _theme(**{
        "style": "modern",
        "bg": "#1a1a1c", "bg_panel": "#232326", "bg_glass": "#2b2b2e",
        "bg_glass_hi": "#3a3a3e", "fg": "#e6e6e8", "fg_dim": "#8a8a8e",
        "accent": "#4a9eff", "accent_hi": "#7bb8ff", "accent_dim": "#2a2a2e",
        "accent_fg": "#0a1525", "good": "#6cc78f", "warn": "#e0b060",
        "bad": "#e06060", "banner_top": "#1a1a1c",
        "banner_bottom": "#1a1a1c", "banner_title_fg": "#e6e6e8",
        "banner_sub_fg": "#8a8a8e", "radius": 6}),
    "Standard Light": _theme(**{
        "style": "modern",
        "bg": "#f7f7f8", "bg_panel": "#ffffff", "bg_glass": "#fafafa",
        "bg_glass_hi": "#efeff1", "fg": "#1a1a1c", "fg_dim": "#6a6a6e",
        "accent": "#0a84ff", "accent_hi": "#4aa8ff", "accent_dim": "#e0e0e3",
        "accent_fg": "#ffffff", "good": "#1a8a5a", "warn": "#a07000",
        "bad": "#c03030", "banner_top": "#ffffff",
        "banner_bottom": "#ffffff", "banner_title_fg": "#1a1a1c",
        "banner_sub_fg": "#6a6a6e", "radius": 6,
        "input_bg": "#ffffff", "input_fg": "#1a1a1c"}),
    "Windows 98": _theme(**{
        "style": "classic",
        "bg": "#c0c0c0", "bg_panel": "#d4d0c8", "bg_glass": "#ffffff",
        "bg_glass_hi": "#e8e8e8", "fg": "#000000", "fg_dim": "#404040",
        "accent": "#000080", "accent_hi": "#0000c0", "accent_dim": "#808080",
        "accent_fg": "#ffffff", "good": "#008000", "warn": "#808000",
        "bad": "#800000", "banner_top": "#000080",
        "banner_bottom": "#000080", "banner_title_fg": "#ffffff",
        "banner_sub_fg": "#c0c0ff", "radius": 0,
        "input_bg": "#ffffff", "input_fg": "#000000"}),
}


# =============================================================================
# QSS
# =============================================================================
def _font_block():
    return {r: font_for(r) for r in FONT_ROLE_KEYWORDS}


def _menu_qss(c, fam):
    return f"""
QMenuBar {{ background-color: {c['bg_panel']}; color: {c['fg']};
    border-bottom: 1px solid {c['accent_dim']}; font-family: "{fam}"; }}
QMenuBar::item {{ background: transparent; padding: 5px 10px; color: {c['fg']}; }}
QMenuBar::item:selected {{ background: {c['accent_dim']}; color: {c['fg']}; }}
QMenuBar::item:pressed {{ background: {c['accent']}; color: {c['accent_fg']}; }}
QMenu {{ background-color: {c['bg_panel']}; color: {c['fg']};
    border: 1px solid {c['accent_dim']}; padding: 4px; font-family: "{fam}"; }}
QMenu::item {{ background: transparent; padding: 5px 26px 5px 22px; color: {c['fg']}; }}
QMenu::item:selected {{ background: {c['accent']}; color: {c['accent_fg']}; }}
QMenu::item:disabled {{ color: {c['fg_dim']}; }}
QMenu::separator {{ height: 1px; background: {c['accent_dim']}; margin: 4px 8px; }}
QMenu::indicator {{ width: 12px; height: 12px; margin-left: 6px; }}
QMenu::indicator:checked {{ background: {c['accent']}; border: 1px solid {c['accent_hi']}; }}
QMenu::indicator:unchecked {{ background: transparent; border: 1px solid {c['accent_dim']}; }}
"""


def _role_qss():
    f = _font_block()
    return f"""
QLabel#Title, QLabel#StatusLabel,
QGroupBox::title, QHeaderView::section, QLabel#NotifyTitle {{
    font-family: "{f['title']}"; }}
QPushButton {{ font-family: "{f['button']}"; }}
QPushButton#AccentBtn {{ font-family: "{f['title']}"; }}
QPlainTextEdit {{ font-family: "{f['log']}"; }}
QLabel#WarnLabel {{ font-family: "{f['warn']}"; }}
QLabel#SectionLabel {{ font-family: "{f['accent']}"; }}
"""


def _qss_aero(c):
    r = int(c["radius"]); input_bg = c["input_bg"] or c["bg_glass"]
    input_fg = c["input_fg"] or c["fg"]; f = _font_block()
    pt_body = _fs(1.0); pt_title = _fs(2.2, 11); pt_section = _fs(0.9, 6)
    pt_status = _fs(1.0); pt_warn = _fs(0.8, 6); pt_hint = _fs(0.8, 6)
    pt_notify = _fs(1.0); pt_log = _fs(1.25, 8)
    btn_grad = (f"qlineargradient(x1:0, y1:0, x2:0, y2:1, "
                f"stop:0 {c['bg_glass_hi']}, stop:0.5 {c['accent_dim']}, "
                f"stop:0.51 {c['accent_dim']}, stop:1 {c['accent_dim']})")
    btn_hover = (f"qlineargradient(x1:0, y1:0, x2:0, y2:1, "
                 f"stop:0 {c['accent']}, stop:1 {c['accent_dim']})")
    return f"""
* {{ font-family: "{f['body']}"; font-size: {pt_body}pt; color: {c['fg']}; }}
QMainWindow, QDialog {{ background-color: {c['bg']}; }}
QWidget {{ background-color: {c['bg']}; }}
{_menu_qss(c, f['body'])}
{_role_qss()}
QToolTip {{ background-color: {c['bg_panel']}; color: {c['fg']};
    border: 1px solid {c['accent_hi']}; padding: 5px 7px; border-radius: 4px; }}
QFrame#Panel {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
    stop:0 {c['bg_glass_hi']}, stop:0.06 {c['bg_glass']}, stop:1 {c['bg_glass']});
    border: 1px solid {c['accent_dim']}; border-radius: {r}px; }}
QLabel {{ background: transparent; }}
QLabel#Title {{ font-size: {pt_title}pt; font-weight: 800; color: {c['banner_title_fg']}; }}
QLabel#Subtitle {{ font-size: {pt_body}pt; color: {c['fg_dim']}; }}
QLabel#SectionLabel {{ font-size: {pt_section}pt; font-weight: 700; color: {c['accent_hi']};
    padding: 2px 4px; background: transparent; letter-spacing: 0.5px; }}
QLabel#StatusLabel {{ font-size: {pt_status}pt; font-weight: 700; color: {c['warn']};
    background: transparent; }}
QLabel#WarnLabel {{ font-size: {pt_warn}pt; font-style: italic; color: {c['warn']};
    background: transparent; }}
QLabel#HintLabel {{ font-size: {pt_hint}pt; color: {c['fg_dim']};
    background: transparent; padding-left: 4px; }}
QLabel#NotifyMessage {{ font-size: {pt_notify}pt; background: transparent; }}
QPushButton {{ background: {btn_grad}; color: {c['fg']};
    border: 1px solid {c['accent_dim']}; border-top: 1px solid {c['accent_hi']};
    border-radius: {r}px; padding: 8px 16px; font-weight: 600; }}
QPushButton:hover {{ background: {btn_hover}; border: 1px solid {c['accent_hi']}; }}
QPushButton:pressed {{ background: {c['accent']}; color: {c['accent_fg']};
    border: 1px solid {c['accent_hi']}; }}
QPushButton#AccentBtn {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
    stop:0 {c['accent_hi']}, stop:0.5 {c['accent']},
    stop:0.51 {c['accent']}, stop:1 {c['accent_dim']});
    color: {c['accent_fg']}; font-weight: 800;
    border: 1px solid {c['accent_hi']}; padding: 9px 20px; }}
QPushButton#AccentBtn:hover {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
    stop:0 {c['accent_hi']}, stop:1 {c['accent']}); }}
QPushButton:disabled {{ color: {c['fg_dim']};
    border: 1px solid {c['accent_dim']}; background: {c['bg_panel']}; }}
QLineEdit {{ background-color: {input_bg}; color: {input_fg};
    border: 1px solid {c['accent_dim']}; border-radius: {r}px;
    padding: 6px 10px; min-height: 22px;
    selection-background-color: {c['accent']}; selection-color: {c['accent_fg']}; }}
QLineEdit:focus {{ border: 1px solid {c['accent_hi']}; }}
QComboBox {{ background-color: {input_bg}; color: {input_fg};
    border: 1px solid {c['accent_dim']}; border-radius: {r}px;
    padding: 6px 10px; min-width: 100px; min-height: 22px; }}
QComboBox:hover {{ border: 1px solid {c['accent_hi']}; }}
QComboBox::drop-down {{ border: 0; width: 22px; }}
QComboBox QAbstractItemView {{ background-color: {c['bg_panel']};
    color: {c['fg']}; border: 1px solid {c['accent_dim']};
    selection-background-color: {c['accent']}; selection-color: {c['accent_fg']};
    outline: 0; }}
QCheckBox {{ color: {c['fg']}; spacing: 8px; padding: 3px 0; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px;
    border: 2px solid {c['accent_dim']}; border-radius: 8px; background: {input_bg}; }}
QCheckBox::indicator:hover {{ border: 2px solid {c['accent_hi']}; }}
QCheckBox::indicator:checked {{ background: qradialgradient(cx:0.5, cy:0.45,
    radius:0.75, stop:0 {c['accent_hi']}, stop:0.7 {c['accent']}, stop:1 {c['accent']});
    border: 2px solid {c['accent_hi']}; }}
QCheckBox::indicator:disabled {{ border: 2px solid {c['bg_panel']};
    background: {c['bg_panel']}; }}
QGroupBox {{ border: 1px solid {c['accent_dim']}; border-radius: {r}px;
    margin-top: 14px; padding: 12px 10px 10px 10px;
    background: {c['bg_panel']}; font-weight: 700; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px;
    color: {c['accent_hi']}; background: {c['bg_panel']}; }}
QTreeWidget {{ background-color: {c['bg_glass']};
    alternate-background-color: {c['bg_panel']}; color: {c['fg']};
    border: 1px solid {c['accent_dim']}; border-radius: {r}px;
    outline: 0; gridline-color: {c['accent_dim']}; }}
QTreeWidget::item {{ padding: 4px 6px; border: 0; }}
QTreeWidget::item:selected {{ background: {c['accent']}; color: {c['accent_fg']}; }}
QTreeWidget::item:hover {{ background: {c['bg_glass_hi']}; }}
QHeaderView::section {{ background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
    stop:0 {c['accent_dim']}, stop:1 {c['bg_panel']});
    color: {c['fg']}; padding: 6px 8px; border: 0; font-weight: 700; }}
QPlainTextEdit {{ background-color: {c['bg_panel']}; color: {c['fg']};
    border: 1px solid {c['accent_dim']}; border-radius: {r}px; padding: 6px;
    selection-background-color: {c['accent']}; selection-color: {c['accent_fg']};
    font-size: {pt_log}pt; }}
QSplitter::handle {{ background: {c['bg_panel']}; }}
QSplitter::handle:vertical {{ height: 6px; }}
QSplitter::handle:horizontal {{ width: 6px; }}
QSplitter::handle:hover {{ background: {c['accent_dim']}; }}
QScrollBar:vertical {{ background: {c['bg_panel']}; width: 12px; border-radius: 6px; }}
QScrollBar::handle:vertical {{ background: {c['accent_dim']};
    border-radius: 6px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {c['accent']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: {c['bg_panel']}; height: 12px; border-radius: 6px; }}
QScrollBar::handle:horizontal {{ background: {c['accent_dim']};
    border-radius: 6px; min-width: 24px; }}
QDialogButtonBox QPushButton {{ min-width: 100px; }}
"""


def _qss_modern(c):
    r = int(c["radius"]); input_bg = c["input_bg"] or c["bg_panel"]
    input_fg = c["input_fg"] or c["fg"]; f = _font_block()
    pt_body = _fs(1.0); pt_title = _fs(2.2, 11); pt_section = _fs(0.9, 6)
    pt_status = _fs(1.0); pt_warn = _fs(0.8, 6); pt_hint = _fs(0.8, 6)
    pt_notify = _fs(1.0); pt_log = _fs(1.25, 8)
    return f"""
* {{ font-family: "{f['body']}"; font-size: {pt_body}pt; color: {c['fg']}; }}
QMainWindow, QDialog {{ background-color: {c['bg']}; }}
QWidget {{ background-color: {c['bg']}; }}
{_menu_qss(c, f['body'])}
{_role_qss()}
QToolTip {{ background-color: {c['bg_panel']}; color: {c['fg']};
    border: 1px solid {c['accent']}; padding: 5px 7px; border-radius: 4px; }}
QFrame#Panel {{ background-color: {c['bg_panel']};
    border: 1px solid {c['accent_dim']}; border-radius: {r}px; }}
QLabel {{ background: transparent; }}
QLabel#Title {{ font-size: {pt_title}pt; font-weight: 700; color: {c['banner_title_fg']}; }}
QLabel#Subtitle {{ font-size: {pt_body}pt; color: {c['banner_sub_fg']}; }}
QLabel#SectionLabel {{ font-size: {pt_section}pt; font-weight: 700; color: {c['accent']};
    padding: 2px 4px; background: transparent; letter-spacing: 0.6px; }}
QLabel#StatusLabel {{ font-size: {pt_status}pt; font-weight: 700; color: {c['warn']};
    background: transparent; }}
QLabel#WarnLabel {{ font-size: {pt_warn}pt; font-style: italic; color: {c['warn']};
    background: transparent; }}
QLabel#HintLabel {{ font-size: {pt_hint}pt; color: {c['fg_dim']};
    background: transparent; padding-left: 4px; }}
QLabel#NotifyMessage {{ font-size: {pt_notify}pt; background: transparent; }}
QPushButton {{ background-color: {c['bg_glass']}; color: {c['fg']};
    border: 1px solid {c['accent_dim']}; border-radius: {r}px;
    padding: 8px 16px; font-weight: 600; }}
QPushButton:hover {{ background-color: {c['bg_glass_hi']}; border: 1px solid {c['accent']}; }}
QPushButton:pressed {{ background-color: {c['accent_dim']}; }}
QPushButton#AccentBtn {{ background-color: {c['accent']};
    color: {c['accent_fg']}; font-weight: 800;
    border: 1px solid {c['accent_hi']}; padding: 9px 20px; }}
QPushButton#AccentBtn:hover {{ background-color: {c['accent_hi']}; }}
QPushButton:disabled {{ color: {c['fg_dim']};
    border: 1px solid {c['accent_dim']}; background-color: {c['bg_panel']}; }}
QLineEdit {{ background-color: {input_bg}; color: {input_fg};
    border: 1px solid {c['accent_dim']}; border-radius: {r}px;
    padding: 6px 10px; min-height: 22px;
    selection-background-color: {c['accent']}; selection-color: {c['accent_fg']}; }}
QLineEdit:focus {{ border: 2px solid {c['accent']}; padding: 5px 9px; }}
QComboBox {{ background-color: {input_bg}; color: {input_fg};
    border: 1px solid {c['accent_dim']}; border-radius: {r}px;
    padding: 6px 10px; min-width: 100px; min-height: 22px; }}
QComboBox:hover {{ border: 1px solid {c['accent']}; }}
QComboBox::drop-down {{ border: 0; width: 22px; }}
QComboBox QAbstractItemView {{ background-color: {c['bg_panel']};
    color: {c['fg']}; border: 1px solid {c['accent_dim']};
    selection-background-color: {c['accent']}; selection-color: {c['accent_fg']};
    outline: 0; }}
QCheckBox {{ color: {c['fg']}; spacing: 8px; padding: 3px 0; background: transparent; }}
QCheckBox::indicator {{ width: 16px; height: 16px;
    border: 2px solid {c['accent_dim']}; border-radius: 8px; background: {input_bg}; }}
QCheckBox::indicator:hover {{ border: 2px solid {c['accent']}; }}
QCheckBox::indicator:checked {{ background: {c['accent']};
    border: 2px solid {c['accent']}; }}
QCheckBox::indicator:disabled {{ border: 2px solid {c['accent_dim']};
    background: {c['bg_panel']}; }}
QGroupBox {{ border: 1px solid {c['accent_dim']}; border-radius: {r}px;
    margin-top: 14px; padding: 12px 10px 10px 10px;
    background: {c['bg_panel']}; font-weight: 700; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 12px; padding: 0 6px;
    color: {c['accent']}; background: {c['bg_panel']}; }}
QTreeWidget {{ background-color: {c['bg_panel']};
    alternate-background-color: {c['bg_glass']}; color: {c['fg']};
    border: 1px solid {c['accent_dim']}; border-radius: {r}px;
    outline: 0; gridline-color: {c['accent_dim']}; }}
QTreeWidget::item {{ padding: 5px 6px; border: 0; }}
QTreeWidget::item:selected {{ background: {c['accent']}; color: {c['accent_fg']}; }}
QTreeWidget::item:hover {{ background: {c['bg_glass_hi']}; }}
QHeaderView::section {{ background: {c['bg_glass']}; color: {c['fg_dim']};
    padding: 7px 8px; border: 0;
    border-bottom: 1px solid {c['accent_dim']}; font-weight: 700; }}
QPlainTextEdit {{ background-color: {c['bg_glass']}; color: {c['fg']};
    border: 1px solid {c['accent_dim']}; border-radius: {r}px; padding: 6px;
    selection-background-color: {c['accent']}; selection-color: {c['accent_fg']};
    font-size: {pt_log}pt; }}
QSplitter::handle {{ background: {c['bg']}; }}
QSplitter::handle:vertical {{ height: 6px; }}
QSplitter::handle:horizontal {{ width: 6px; }}
QSplitter::handle:hover {{ background: {c['accent_dim']}; }}
QScrollBar:vertical {{ background: {c['bg']}; width: 10px; border-radius: 5px; }}
QScrollBar::handle:vertical {{ background: {c['accent_dim']};
    border-radius: 5px; min-height: 24px; }}
QScrollBar::handle:vertical:hover {{ background: {c['accent']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; width: 0; }}
QScrollBar:horizontal {{ background: {c['bg']}; height: 10px; border-radius: 5px; }}
QScrollBar::handle:horizontal {{ background: {c['accent_dim']};
    border-radius: 5px; min-width: 24px; }}
QDialogButtonBox QPushButton {{ min-width: 100px; }}
"""


def _qss_classic(c):
    input_bg = c["input_bg"] or "#ffffff"
    input_fg = c["input_fg"] or "#000000"; f = _font_block()
    pt_body = max(6, _fs(0.9, 6)); pt_title = _fs(1.6, 9)
    pt_section = max(6, _fs(0.9, 6)); pt_status = pt_body
    pt_warn = max(6, _fs(0.8, 6)); pt_hint = max(6, _fs(0.8, 6))
    pt_notify = pt_body; pt_log = max(8, _fs(1.15, 8))
    btn_up = (f"background: {c['bg']}; border-top: 2px solid #ffffff; "
              f"border-left: 2px solid #ffffff; border-right: 2px solid #000000; "
              f"border-bottom: 2px solid #000000;")
    btn_down = (f"background: {c['bg']}; border-top: 2px solid #000000; "
                f"border-left: 2px solid #000000; border-right: 2px solid #ffffff; "
                f"border-bottom: 2px solid #ffffff;")
    sunken = ("background: #ffffff; border-top: 2px solid #808080; "
              "border-left: 2px solid #808080; border-right: 2px solid #ffffff; "
              "border-bottom: 2px solid #ffffff;")
    return f"""
* {{ font-family: "{f['body']}"; font-size: {pt_body}pt; color: {c['fg']}; }}
QMainWindow, QDialog {{ background-color: {c['bg']}; }}
QWidget {{ background-color: {c['bg']}; }}
{_menu_qss(c, f['body'])}
{_role_qss()}
QToolTip {{ background-color: #ffffe1; color: #000000;
    border: 1px solid #000000; padding: 3px; }}
QFrame#Panel {{ background-color: {c['bg']}; border: 1px solid #808080; }}
QLabel {{ background: transparent; color: {c['fg']}; }}
QLabel#Title {{ font-size: {pt_title}pt; font-weight: 700; color: {c['banner_title_fg']}; }}
QLabel#Subtitle {{ font-size: {pt_body}pt; color: {c['banner_sub_fg']}; }}
QLabel#SectionLabel {{ font-size: {pt_section}pt; font-weight: 700; color: {c['accent']};
    padding: 2px 0; background: transparent; }}
QLabel#StatusLabel {{ font-size: {pt_status}pt; font-weight: 700; color: {c['warn']};
    background: transparent; }}
QLabel#WarnLabel {{ font-size: {pt_warn}pt; font-style: italic; color: {c['bad']};
    background: transparent; }}
QLabel#HintLabel {{ font-size: {pt_hint}pt; color: {c['fg_dim']}; background: transparent; }}
QLabel#NotifyMessage {{ font-size: {pt_notify}pt; background: transparent; }}
QPushButton {{ {btn_up} padding: 4px 12px; min-width: 75px; font-weight: 400; }}
QPushButton:pressed {{ {btn_down} padding: 5px 11px 3px 13px; }}
QPushButton:disabled {{ color: #808080; background: {c['bg']};
    border-top: 2px solid #ffffff; border-left: 2px solid #ffffff;
    border-right: 2px solid #808080; border-bottom: 2px solid #808080; }}
QPushButton#AccentBtn {{ {btn_up} padding: 4px 12px; font-weight: 700; }}
QPushButton#AccentBtn:pressed {{ {btn_down} }}
QLineEdit {{ {sunken} color: {input_fg}; padding: 4px 6px; min-height: 20px;
    selection-background-color: {c['accent']}; selection-color: #ffffff; }}
QLineEdit:focus {{ {sunken} }}
QComboBox {{ {sunken} color: {input_fg}; padding: 4px 6px;
    min-width: 100px; min-height: 20px; }}
QComboBox::drop-down {{ border: 0; width: 18px; background: {c['bg']};
    border-left: 1px solid #808080; }}
QComboBox QAbstractItemView {{ background-color: #ffffff; color: #000000;
    border: 1px solid #000000; selection-background-color: {c['accent']};
    selection-color: #ffffff; outline: 0; }}
QCheckBox {{ color: {c['fg']}; spacing: 6px; padding: 2px 0; background: transparent; }}
QCheckBox::indicator {{ width: 13px; height: 13px; {sunken} }}
QCheckBox::indicator:checked {{ background: #000000;
    border-top: 2px solid #808080; border-left: 2px solid #808080;
    border-right: 2px solid #ffffff; border-bottom: 2px solid #ffffff; }}
QGroupBox {{ border: 1px solid #808080; margin-top: 10px;
    padding: 8px 6px 6px 6px; background: {c['bg']}; font-weight: 700; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 8px; padding: 0 4px;
    color: {c['fg']}; background: {c['bg']}; }}
QTreeWidget {{ background-color: #ffffff; alternate-background-color: #ffffff;
    color: #000000; border: 2px inset #808080; outline: 0;
    gridline-color: #c0c0c0; }}
QTreeWidget::item {{ padding: 2px 4px; border: 0; }}
QTreeWidget::item:selected {{ background: {c['accent']}; color: #ffffff; }}
QTreeWidget::item:hover {{ background: #e8e8e8; }}
QHeaderView::section {{ {btn_up} padding: 3px 6px; font-weight: 700; color: #000000; }}
QPlainTextEdit {{ background-color: #ffffff; color: #000000;
    border: 2px inset #808080; padding: 4px;
    selection-background-color: {c['accent']}; selection-color: #ffffff;
    font-size: {pt_log}pt; }}
QSplitter::handle {{ background: {c['bg']}; }}
QSplitter::handle:vertical {{ height: 4px; }}
QSplitter::handle:horizontal {{ width: 4px; }}
QScrollBar:vertical {{ background: #d4d0c8; width: 16px; border: 0; }}
QScrollBar::handle:vertical {{ {btn_up} min-height: 20px; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ {btn_up} height: 16px; }}
QScrollBar:horizontal {{ background: #d4d0c8; height: 16px; border: 0; }}
QScrollBar::handle:horizontal {{ {btn_up} min-width: 20px; }}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    {btn_up} width: 16px; }}
QDialogButtonBox QPushButton {{ min-width: 75px; }}
"""


def build_qss(c):
    style = c.get("style", "aero")
    if style == "classic":
        return _qss_classic(c)
    if style == "modern":
        return _qss_modern(c)
    return _qss_aero(c)


# =============================================================================
# WIDGETS
# =============================================================================
class AutoFitTreeWidget(QTreeWidget):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.header().setStretchLastSection(False)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._refit()

    def _refit(self):
        header = self.header()
        viewport = self.viewport().width()
        if viewport <= 0:
            return
        n = self.columnCount()
        if n == 0:
            return
        widths = [header.sectionSize(i) for i in range(n)]
        total = sum(widths)
        if total <= 0:
            return
        delta = viewport - total
        if abs(delta) < 5:
            return
        widest = max(range(n), key=lambda i: widths[i])
        new_w = max(80, widths[widest] + delta)
        header.resizeSection(widest, new_w)


class WideGrabHeaderFilter(QObject):
    GRAB_PADDING = 6

    def __init__(self, header):
        super().__init__(header)
        self.header = header
        header.setMouseTracking(True)
        self._drag_section = -1
        self._drag_start_x = 0
        self._drag_start_width = 0

    def _near_boundary(self, x):
        h = self.header
        for i in range(1, h.count()):
            boundary = h.sectionViewportPosition(i)
            if boundary < 0:
                continue
            if abs(x - boundary) <= self.GRAB_PADDING:
                return i
        return -1

    def eventFilter(self, obj, event):
        if obj is not self.header:
            return super().eventFilter(obj, event)
        et = event.type()
        if et == QEvent.MouseMove:
            try:
                pos = event.position().toPoint()
            except AttributeError:
                pos = event.pos()
            if self._drag_section >= 0:
                delta = pos.x() - self._drag_start_x
                new_size = max(self.header.minimumSectionSize(),
                               self._drag_start_width + delta)
                self.header.resizeSection(self._drag_section, new_size)
                return True
            if self._near_boundary(pos.x()) >= 0:
                self.header.setCursor(Qt.SplitHCursor)
            else:
                self.header.unsetCursor()
            return True
        if et == QEvent.MouseButtonPress:
            if event.button() == Qt.LeftButton:
                try:
                    pos = event.position().toPoint()
                except AttributeError:
                    pos = event.pos()
                idx = self._near_boundary(pos.x())
                if idx >= 0:
                    self._drag_section = idx - 1
                    self._drag_start_x = pos.x()
                    self._drag_start_width = self.header.sectionSize(idx - 1)
                    return True
        if et == QEvent.MouseButtonRelease:
            if self._drag_section >= 0:
                self._drag_section = -1
                self.header.unsetCursor()
                return True
        if et == QEvent.Leave:
            self.header.unsetCursor()
        return super().eventFilter(obj, event)


# =============================================================================
# FONT LOADING
# =============================================================================
def _base_dirs():
    dirs = []
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass and os.path.isdir(meipass):
            dirs.append(meipass)
        exe_dir = os.path.dirname(sys.executable)
        if exe_dir and os.path.isdir(exe_dir):
            dirs.append(exe_dir)
    try:
        dirs.append(os.path.dirname(os.path.abspath(__file__)))
    except NameError:
        pass
    seen, out = set(), []
    for d in dirs:
        d = os.path.abspath(d)
        if d not in seen and os.path.isdir(d):
            seen.add(d); out.append(d)
    return out


def _data_search_roots():
    """Like _base_dirs(), but ALSO walks up from the writable base dir so
    the frozen exe can find data/ in the mod root 1-4 levels up."""
    roots = list(_base_dirs())
    seen = set(os.path.abspath(r) for r in roots)
    cur = _writable_base_dir()
    for _ in range(4):
        parent = os.path.dirname(cur)
        if not parent or parent == cur:
            break
        a = os.path.abspath(parent)
        if a not in seen:
            seen.add(a); roots.append(a)
        cur = parent
    return roots


def load_bundled_fonts():
    patterns = ["fonts/*.ttf", "fonts/*.otf",
                "fonts/**/*.ttf", "fonts/**/*.otf"]
    all_paths = set()
    for base in _base_dirs():
        for pat in patterns:
            for p in glob.glob(os.path.join(base, pat), recursive=True):
                all_paths.add(p)
    all_families, loaded_paths, path_to_families = [], [], {}
    for p in sorted(all_paths):
        fid = QFontDatabase.addApplicationFont(p)
        if fid == -1:
            continue
        fams = list(QFontDatabase.applicationFontFamilies(fid))
        for fam in fams:
            if fam not in all_families:
                all_families.append(fam)
        loaded_paths.append(p)
        path_to_families[p] = fams
    return all_families, loaded_paths, path_to_families


def map_font_roles(loaded_families, loaded_paths, path_to_families):
    roles = {}
    for path in loaded_paths:
        basename = os.path.basename(path).lower()
        fams = path_to_families.get(path, [])
        if not fams:
            continue
        fam = fams[0]
        for role, keywords in FONT_ROLE_KEYWORDS.items():
            if role in roles:
                continue
            if any(k in basename for k in keywords):
                roles[role] = fam
    default_fam = loaded_families[0] if loaded_families else SYSTEM_FALLBACK_FONT
    for role in FONT_ROLE_KEYWORDS:
        if role not in roles:
            roles[role] = default_fam
    return roles


# =============================================================================
# DISPLAY NAMES
# =============================================================================
def load_display_names():
    mapping = {}
    for base in _data_search_roots():
        for rel in (os.path.join("data", "text", "combined.csv.append"),
                    os.path.join("data", "text", "combined.csv")):
            path = os.path.join(base, rel)
            if not os.path.isfile(path):
                continue
            try:
                with open(path, "r", encoding="utf-8-sig") as fh:
                    for line in fh:
                        line = line.rstrip("\r\n")
                        if not line or line.startswith("#"):
                            continue
                        parts = line.split(",")
                        if len(parts) < 2:
                            continue
                        key = parts[0].strip().strip('"')
                        val = parts[1].strip().strip('"')
                        if not key.startswith("FURNITURE_NAME_"):
                            continue
                        base_id = key[len("FURNITURE_NAME_"):].lower()
                        if "_plus" in base_id or not val:
                            continue
                        mapping.setdefault(val.lower(), [])
                        if base_id not in mapping[val.lower()]:
                            mapping[val.lower()].append(base_id)
            except Exception:
                pass
    return mapping


def display_name_for(base_id, display_map):
    low = base_id.lower()
    for disp, ids in display_map.items():
        if low in ids:
            return disp
    return None


def expand_excludes(raw_set, display_map):
    expanded = set(raw_set); added = set()
    for entry in raw_set:
        low = entry.lower().strip()
        if not low:
            continue
        if low in display_map:
            for bid in display_map[low]:
                if bid not in expanded:
                    expanded.add(bid); added.add(bid)
            continue
        for disp_low, ids in display_map.items():
            if low in disp_low:
                for bid in ids:
                    if bid not in expanded:
                        expanded.add(bid); added.add(bid)
    return expanded, added


# =============================================================================
# BLOB PARSING
# =============================================================================
def parse_blob(blob):
    off = 0
    magic, = struct.unpack_from('<I', blob, off); off += 4
    id_len, = struct.unpack_from('<I', blob, off); off += 4
    off += 4
    item_id = blob[off:off + id_len].decode('ascii', 'replace'); off += id_len
    off += 4; off += 4
    loc_len, = struct.unpack_from('<I', blob, off); off += 4
    off += 4
    loc = blob[off:off + loc_len].decode('ascii', 'replace'); off += loc_len
    stats_blob = blob[off:]
    n = len(stats_blob) // 4
    stats = list(struct.unpack_from('<' + 'i' * n, stats_blob, 0)) if n else []
    return magic, item_id, loc, stats, off


def rewrite_item_id(blob, new_item_id):
    magic, old_id, loc, stats, _ = parse_blob(blob)
    new_id_bytes = new_item_id.encode('ascii')
    old_id_len = len(old_id)
    out = bytearray()
    out += struct.pack('<I', magic)
    out += struct.pack('<I', len(new_id_bytes))
    out += blob[8:12]
    out += new_id_bytes
    out += blob[12 + old_id_len:]
    _, check_id, check_loc, check_stats, _ = parse_blob(bytes(out))
    if check_id != new_item_id:
        raise RuntimeError(f"id mismatch: {check_id!r} != {new_item_id!r}")
    if check_loc != loc:
        raise RuntimeError("loc changed unexpectedly")
    if check_stats != stats:
        raise RuntimeError("stats changed unexpectedly")
    return bytes(out)


def rewrite_loc(blob, new_loc):
    magic, item_id, old_loc, stats, _ = parse_blob(blob)
    new_bytes = new_loc.encode('ascii')
    old_len = len(old_loc); id_len = len(item_id)
    loc_len_off = 20 + id_len; loc_data_off = 28 + id_len
    out = bytearray()
    out += blob[:loc_len_off]
    out += struct.pack('<I', len(new_bytes))
    out += blob[loc_len_off + 4:loc_data_off]
    out += new_bytes
    out += blob[loc_data_off + old_len:]
    _, check_id, check_loc, check_stats, _ = parse_blob(bytes(out))
    if check_id != item_id:
        raise RuntimeError("id changed unexpectedly")
    if check_loc != new_loc:
        raise RuntimeError(f"loc mismatch: {check_loc!r} != {new_loc!r}")
    if check_stats != stats:
        raise RuntimeError("stats changed unexpectedly")
    return bytes(out)


# =============================================================================
# PLUS-TIER MATH
# =============================================================================
def split_plus_suffix(item_id):
    total, remaining = 0, item_id
    while True:
        idx = remaining.rfind("_plus")
        if idx <= 0:
            break
        suffix = remaining[idx + 5:]
        if not suffix.isdigit():
            break
        total += int(suffix)
        remaining = remaining[:idx]
    return remaining, total


def item_value(plus_n):
    return 1 << plus_n


def target_plus_from_value(total_value):
    if total_value < 2:
        return 0
    return total_value.bit_length() - 1


def is_statue_or_idol(base_id):
    b = base_id.lower()
    if any(h in b for h in STATUE_HINTS):
        return True
    if any(h in b for h in IDOL_HINTS):
        return True
    return False


def parse_custom_excludes(text):
    out = set()
    for chunk in text.replace("\n", ",").split(","):
        chunk = chunk.strip()
        if chunk:
            out.add(chunk)
    return out


def build_plan(rows, include_special=True, custom_excludes=None,
               preserve_excess=True):
    exclude = set(BASE_EXCLUDE)
    if custom_excludes:
        exclude |= custom_excludes
    parsed = []
    for key, blob in rows:
        try:
            magic, item_id, loc, stats, _ = parse_blob(blob)
        except Exception:
            continue
        base, plus_n = split_plus_suffix(item_id)
        if not base:
            continue
        parsed.append({
            'key': key, 'blob': blob, 'item_id': item_id,
            'loc': loc, 'stats': stats,
            'base': base, 'plus_n': plus_n,
            'value': item_value(plus_n)})
    groups = {}
    for p in parsed:
        groups.setdefault(p['base'], []).append(p)
    plan, skipped = [], []
    for base, members in groups.items():
        total_value = sum(m['value'] for m in members)
        if total_value < 2:
            continue
        if base in exclude:
            skipped.append((base, len(members), 'excluded')); continue
        if not include_special and is_statue_or_idol(base):
            skipped.append((base, len(members),
                            'idol (check the box to include)')); continue
        target = target_plus_from_value(total_value)
        if target < 1:
            continue
        best_current = max(m['plus_n'] for m in members)
        if target <= best_current:
            skipped.append((base, len(members),
                f'already at plus{best_current} (total value {total_value})'))
            continue
        new_id = f"{base}_plus{target}"
        members_sorted = sorted(members, key=lambda m: (-m['plus_n'], m['key']))
        keep = members_sorted[0]
        target_value = 1 << target
        consumed_value = keep['value']
        parks = []
        leftovers = []
        if preserve_excess:
            # Greedy desc: consume highest-plus items first so we hit the
            # target tier with the fewest rows. Once we're at target value,
            # everything else stays in the furniture table untouched.
            for m in members_sorted[1:]:
                if consumed_value >= target_value:
                    leftovers.append(m)
                else:
                    parks.append(m)
                    consumed_value += m['value']
        else:
            parks = members_sorted[1:]
        try:
            new_blob = rewrite_item_id(keep['blob'], new_id)
        except Exception as e:
            skipped.append((base, len(members), f'rewrite failed: {e}')); continue
        plan.append({
            'base': base, 'keep_key': keep['key'],
            'keep_loc': keep['loc'], 'keep_old_id': keep['item_id'],
            'park_keys': [p['key'] for p in parks],
            'park_old_ids': [p['item_id'] for p in parks],
            'leftover_keys': [m['key'] for m in leftovers],
            'leftover_ids': [m['item_id'] for m in leftovers],
            'old_blob': keep['blob'], 'new_blob': new_blob,
            'new_id': new_id, 'target_plus': target,
            'best_current': best_current, 'count': len(members),
            'total_value': total_value})
    return plan, skipped, groups


def find_merged_items(rows):
    merged = []
    for key, blob in rows:
        try:
            magic, item_id, loc, stats, _ = parse_blob(blob)
        except Exception:
            continue
        base, plus_n = split_plus_suffix(item_id)
        if plus_n < 1 or not base:
            continue
        merged.append({
            'key': key, 'blob': blob, 'item_id': item_id,
            'base': base, 'plus_n': plus_n, 'copies': 1 << plus_n})
    return merged


def ensure_side_tables(con):
    cur = con.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS furniture_parked (
        key INTEGER PRIMARY KEY, data BLOB) STRICT""")
    cur.execute("""CREATE TABLE IF NOT EXISTS furniture_merge_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT, merged_at TEXT, kept_key INTEGER,
        old_data BLOB, deleted_keys TEXT, plus_level INTEGER,
        new_item_id TEXT) STRICT""")
    con.commit()


# =============================================================================
# DIALOGS
# =============================================================================
class FUNotify(QDialog):
    def __init__(self, parent, title, message, icon="info", buttons="ok"):
        super().__init__(parent)
        self.setWindowTitle(title); self.setModal(True); self.setMinimumWidth(420)
        play_notify_sound()
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 18, 20, 14); outer.setSpacing(12)
        row = QHBoxLayout(); row.setSpacing(14)
        icon_char, icon_color_key = {
            "info": ("ⓘ", "accent"), "question": ("?", "accent"),
            "warn": ("⚠", "warn"), "error": ("✖", "bad"),
        }.get(icon, ("ⓘ", "accent"))
        colors = THEMES.get(getattr(parent, "theme_name", None),
                            THEMES["Frutiger Aero (Purple)"])
        icon_color = colors.get(icon_color_key, colors["accent"])
        icon_lbl = QLabel(icon_char)
        icon_lbl.setStyleSheet(
            f"font-size: {_fs(2.6, 16)}pt; color: {icon_color}; "
            f"background: transparent;")
        icon_lbl.setFixedWidth(48)
        icon_lbl.setAlignment(Qt.AlignTop | Qt.AlignHCenter)
        row.addWidget(icon_lbl)
        msg = QLabel(message); msg.setWordWrap(True)
        msg.setObjectName("NotifyMessage")
        if icon == "error":
            fam = font_for("error")
        elif icon == "warn":
            fam = font_for("warn")
        else:
            fam = font_for("notif")
        msg.setFont(QFont(fam, _fs(1.0)))
        row.addWidget(msg, 1); outer.addLayout(row)
        btn_box = QDialogButtonBox()
        if buttons == "ok":
            ok = btn_box.addButton(tr("ok"), QDialogButtonBox.AcceptRole)
            ok.setObjectName("AccentBtn")
            btn_box.accepted.connect(self.accept)
        elif buttons == "yesno":
            yes = btn_box.addButton(tr("yes"), QDialogButtonBox.YesRole)
            yes.setObjectName("AccentBtn")
            no = btn_box.addButton(tr("no"), QDialogButtonBox.NoRole)
            btn_box.accepted.connect(self.accept)
            btn_box.rejected.connect(self.reject)
        outer.addWidget(btn_box)


def fu_info(parent, title, msg):
    return FUNotify(parent, title, msg, icon="info", buttons="ok").exec()


def fu_warn(parent, title, msg):
    return FUNotify(parent, title, msg, icon="warn", buttons="ok").exec()


def fu_error(parent, title, msg):
    return FUNotify(parent, title, msg, icon="error", buttons="ok").exec()


def fu_question(parent, title, msg):
    return FUNotify(parent, title, msg, icon="question",
                    buttons="yesno").exec() == QDialog.Accepted


class Banner(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self._colors = THEMES["Frutiger Aero (Purple)"]
        self.setFixedHeight(96)

    def set_style(self, colors):
        self._colors = colors
        style = colors.get("style", "aero")
        base = _CURRENT_FONT_SIZE
        if style == "classic":
            self.setFixedHeight(max(40, base * 5))
        elif style == "modern":
            self.setFixedHeight(max(56, base * 7))
        else:
            self.setFixedHeight(max(72, base * 9))
        self.update()

    def paintEvent(self, event):
        c = self._colors
        style = c.get("style", "aero")
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, style != "classic")
        rect = self.rect()
        title_fam = font_for("title")
        body_fam = font_for("body")
        if style == "classic":
            p.fillRect(rect, QColor("#000080"))
            p.setPen(QColor("#ffffff"))
            f = QFont(title_fam); f.setPointSize(_fs(1.2, 8)); f.setBold(True); p.setFont(f)
            p.drawText(12, 0, rect.width() - 24, rect.height() // 2,
                       Qt.AlignLeft | Qt.AlignBottom, APP_TITLE)
            p.setPen(QColor("#c0c0ff"))
            f2 = QFont(body_fam); f2.setPointSize(_fs(0.8, 6)); p.setFont(f2)
            p.drawText(12, rect.height() // 2, rect.width() - 24,
                       rect.height() // 2, Qt.AlignLeft | Qt.AlignTop,
                       tr("banner_subtitle"))
            return
        if style == "modern":
            p.fillRect(rect, QColor(c["bg_panel"]))
            p.fillRect(0, rect.height() - 2, rect.width(), 2, QColor(c["accent"]))
            p.setPen(QColor(c["banner_title_fg"]))
            f = QFont(title_fam); f.setPointSize(_fs(1.6, 10)); f.setBold(True); p.setFont(f)
            p.drawText(20, 0, rect.width() - 40, rect.height() - 22,
                       Qt.AlignLeft | Qt.AlignBottom, APP_TITLE)
            p.setPen(QColor(c["banner_sub_fg"]))
            f2 = QFont(body_fam); f2.setPointSize(_fs(0.9, 6)); p.setFont(f2)
            p.drawText(20, rect.height() - 22, rect.width() - 40, 20,
                       Qt.AlignLeft | Qt.AlignVCenter,
                       tr("banner_subtitle"))
            return
        grad = QLinearGradient(0, 0, 0, rect.height())
        grad.setColorAt(0.0, QColor(c["banner_top"]))
        grad.setColorAt(1.0, QColor(c["banner_bottom"]))
        p.fillRect(rect, QBrush(grad))
        top_gloss = QLinearGradient(0, 0, 0, max(2, rect.height() // 5))
        top_gloss.setColorAt(0.0, QColor(255, 255, 255, 110))
        top_gloss.setColorAt(1.0, QColor(255, 255, 255, 0))
        p.fillRect(0, 0, rect.width(), max(2, rect.height() // 5), QBrush(top_gloss))
        p.setPen(QColor(c["banner_sub_fg"]))
        p.drawLine(0, 1, rect.width(), 1)
        p.setPen(QColor(0, 0, 0, 120))
        p.drawLine(0, rect.height() - 1, rect.width(), rect.height() - 1)
        p.setPen(QColor(c["banner_title_fg"]))
        tf = QFont(title_fam); tf.setPointSize(_fs(2.0, 11)); tf.setBold(True); p.setFont(tf)
        p.drawText(24, 0, rect.width() - 48, rect.height() // 2,
                   Qt.AlignLeft | Qt.AlignBottom, APP_TITLE)
        p.setPen(QColor(c["banner_sub_fg"]))
        sf = QFont(body_fam); sf.setPointSize(_fs(1.0)); p.setFont(sf)
        p.drawText(24, rect.height() // 2, rect.width() - 48, rect.height() // 2,
                   Qt.AlignLeft | Qt.AlignTop,
                   tr("banner_subtitle"))


class ConfirmDialog(QDialog):
    def __init__(self, parent, summary_lines, backup_path, detail_lines,
                 confirm_text=None):
        super().__init__(parent)
        self.setWindowTitle(APP_TITLE)
        self.setMinimumSize(600, 360); self.resize(720, 480)
        outer = QVBoxLayout(self)
        for line in summary_lines:
            lbl = QLabel(line); lbl.setWordWrap(True); outer.addWidget(lbl)
        backup_lbl = QLabel(f"{tr('backup_will_be_written_to')}\n{backup_path}")
        backup_lbl.setWordWrap(True)
        outer.addSpacing(8); outer.addWidget(backup_lbl); outer.addSpacing(8)
        self._toggle_btn = QPushButton(tr("show_queued_changes"))
        self._toggle_btn.clicked.connect(self._toggle_details)
        outer.addWidget(self._toggle_btn, 0, Qt.AlignLeft)
        self._detail = QPlainTextEdit()
        self._detail.setReadOnly(True)
        self._detail.setPlainText("\n".join(detail_lines) or "(no changes)")
        self._detail.setVisible(False)
        outer.addWidget(self._detail, 1)
        btns = QDialogButtonBox()
        ok = btns.addButton(confirm_text or tr("apply"),
                            QDialogButtonBox.AcceptRole)
        btns.addButton(tr("cancel"), QDialogButtonBox.RejectRole)
        ok.setObjectName("AccentBtn")
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        outer.addWidget(btns)

    def _toggle_details(self):
        vis = self._detail.isVisible()
        self._detail.setVisible(not vis)
        self._toggle_btn.setText(
            tr("hide_queued_changes") if not vis else tr("show_queued_changes"))


class BackupsDialog(QDialog):
    def __init__(self, parent, save_path):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_TITLE} — {tr('menu_backups')}")
        self.setMinimumSize(680, 480); self.resize(800, 560)
        self.save_path = save_path
        self._restored = False
        outer = QVBoxLayout(self)
        try:
            st = os.stat(save_path)
            save_dt = datetime.datetime.fromtimestamp(st.st_mtime)
            header = QLabel(
                f"<b>{tr('current_save')}</b> {os.path.basename(save_path)}<br>"
                f"{tr('modified')}: {save_dt:%Y-%m-%d %H:%M:%S} "
                f"({humanize_age(time.time() - st.st_mtime)}) "
                f"&nbsp;·&nbsp; {st.st_size / 1024:.0f} KB<br>"
                f"<b>{tr('backup_folder')}</b> "
                f"{portable_backup_dir() or tr('next_to_save_file')}")
        except OSError:
            header = QLabel(f"<b>{tr('current_save')}</b> {os.path.basename(save_path)}")
        header.setWordWrap(True)
        outer.addWidget(header); outer.addSpacing(6)
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels([
            tr("col_backup_file"), tr("col_date_time"),
            tr("col_age"), tr("col_size")])
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.SingleSelection)
        self.tree.setAlternatingRowColors(True)
        h = self.tree.header()
        h.setStretchLastSection(False)
        for i in range(4):
            h.setSectionResizeMode(i, QHeaderView.Interactive)
        h.setSectionResizeMode(0, QHeaderView.Stretch)
        h.setSectionResizeMode(1, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(2, QHeaderView.ResizeToContents)
        h.setSectionResizeMode(3, QHeaderView.ResizeToContents)
        outer.addWidget(self.tree, 1)
        self._populate()
        btn_row = QHBoxLayout()
        self.restore_btn = QPushButton(tr("restore_selected"))
        self.restore_btn.clicked.connect(self._restore)
        btn_row.addWidget(self.restore_btn)
        self.delete_btn = QPushButton(tr("delete_selected"))
        self.delete_btn.clicked.connect(self._delete)
        btn_row.addWidget(self.delete_btn)
        btn_row.addStretch(1)
        close_btn = QPushButton(tr("close"))
        close_btn.clicked.connect(self.reject)
        btn_row.addWidget(close_btn)
        outer.addLayout(btn_row)
        self.tree.itemSelectionChanged.connect(self._update_button_state)
        self._update_button_state()

    def _populate(self):
        self.tree.clear()
        self._infos = get_backup_info(self.save_path)
        now = time.time()
        for info in self._infos:
            it = QTreeWidgetItem([
                info['name'], info['dt'].strftime("%Y-%m-%d %H:%M:%S"),
                humanize_age(now - info['mtime']),
                f"{info['size'] / 1024:.0f} KB"])
            it.setData(0, Qt.UserRole, info['path'])
            self.tree.addTopLevelItem(it)
        if not self._infos:
            self.tree.addTopLevelItem(
                QTreeWidgetItem([tr("no_backups_found"), "", "", ""]))

    def _update_button_state(self):
        has = bool(self.tree.selectedItems()) and bool(self._infos)
        self.restore_btn.setEnabled(has)
        self.delete_btn.setEnabled(has)

    def _selected_path(self):
        items = self.tree.selectedItems()
        if not items or not self._infos:
            return None
        return items[0].data(0, Qt.UserRole)

    def _restore(self):
        path = self._selected_path()
        if not path:
            return
        name = os.path.basename(path)
        if not fu_question(
            self, APP_TITLE,
            f"{tr('restore_confirm')}\n\n"
            f"{tr('backup_label')} {name}\n"
            f"{tr('current_label')} {os.path.basename(self.save_path)}\n\n"
            f"{tr('safety_copy_note')}"):
            return
        safety = make_backup_path(self.save_path, "prerestore")
        try:
            shutil.copy2(self.save_path, safety)
            shutil.copy2(path, self.save_path)
        except Exception as e:
            fu_error(self, APP_TITLE, f"{tr('restore_failed')}\n{e}")
            return
        self._restored = True
        fu_info(self, APP_TITLE,
                f"{tr('restore_success')}\n\n"
                f"{tr('safety_copy')}\n{os.path.basename(safety)}")
        self.accept()

    def _delete(self):
        path = self._selected_path()
        if not path:
            return
        name = os.path.basename(path)
        if not fu_question(self, APP_TITLE,
                f"{tr('delete_backup_confirm')}\n\n{name}\n\n{tr('cannot_be_undone')}"):
            return
        try:
            os.remove(path)
        except OSError as e:
            fu_error(self, APP_TITLE, f"{tr('delete_failed')}\n{e}")
            return
        self._populate()
        self._update_button_state()


class ScalingDialog(QDialog):
    def __init__(self, parent, gon_path, config):
        super().__init__(parent)
        self.setWindowTitle(f"{APP_TITLE} — {tr('rebuild_stats')}")
        self.setMinimumSize(640, 480)
        self.resize(720, 560)
        self.gon_path = gon_path
        self.config = config
        self._last_result = None

        outer = QVBoxLayout(self)
        intro = QLabel(tr("scaling_intro"))
        intro.setWordWrap(True)
        outer.addWidget(intro); outer.addSpacing(6)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel(tr("buff_multiplier")))
        self.buff_combo = QComboBox()
        for i in range(1, 11):
            self.buff_combo.addItem(f"{i}x")
        self.buff_combo.setCurrentText(
            f"{int(config.get('scaling_buff', DEFAULT_BUFF_MULT))}x")
        self.buff_combo.currentTextChanged.connect(self._refresh_preview)
        row1.addWidget(self.buff_combo); row1.addStretch(1)
        outer.addLayout(row1)

        self.chk_unlink = QCheckBox(tr("unlink_debuff"))
        self.chk_unlink.setChecked(
            bool(config.get("scaling_unlink_debuff", False)))
        self.chk_unlink.toggled.connect(self._on_unlink_toggled)
        outer.addWidget(self.chk_unlink)

        self.debuff_widget = QWidget()
        row2 = QHBoxLayout(self.debuff_widget)
        row2.setContentsMargins(0, 0, 0, 0)
        row2.addWidget(QLabel(tr("debuff_multiplier")))
        self.debuff_combo = QComboBox()
        for i in range(1, 11):
            self.debuff_combo.addItem(f"{i}x")
        self.debuff_combo.setCurrentText(
            f"{int(config.get('scaling_debuff', DEFAULT_DEBUFF_MULT))}x")
        self.debuff_combo.currentTextChanged.connect(self._refresh_preview)
        row2.addWidget(self.debuff_combo); row2.addStretch(1)
        self.debuff_widget.setVisible(self.chk_unlink.isChecked())
        outer.addWidget(self.debuff_widget)

        row3 = QHBoxLayout()
        row3.addWidget(QLabel(tr("debuff_behavior")))
        self.mode_combo = QComboBox()
        self._mode_keys = list(SCALING_MODE_LABELS.keys())
        for key in self._mode_keys:
            self.mode_combo.addItem(tr(f"mode_{key}"))
        current_mode = config.get("scaling_mode", DEFAULT_DEBUFF_MODE)
        if current_mode not in self._mode_keys:
            current_mode = DEFAULT_DEBUFF_MODE
        self.mode_combo.setCurrentIndex(self._mode_keys.index(current_mode))
        self.mode_combo.currentIndexChanged.connect(self._refresh_preview)
        row3.addWidget(self.mode_combo, 1)
        outer.addLayout(row3)

        self.chk_tooltips = QCheckBox(tr("write_tooltip_desc"))
        self.chk_tooltips.setChecked(
            bool(config.get("scaling_tooltips", True)))
        self.chk_tooltips.toggled.connect(self._refresh_preview)
        outer.addWidget(self.chk_tooltips)

        outer.addSpacing(6)
        self.preview_label = QLabel(tr("preview"))
        self.preview_label.setObjectName("SectionLabel")
        outer.addWidget(self.preview_label)
        self.preview = QPlainTextEdit()
        self.preview.setReadOnly(True)
        self.preview.setMinimumHeight(160)
        outer.addWidget(self.preview, 1)

        btns = QDialogButtonBox()
        self.apply_btn = btns.addButton(tr("rebuild_gon"),
                                        QDialogButtonBox.AcceptRole)
        self.apply_btn.setObjectName("AccentBtn")
        btns.addButton(tr("cancel"), QDialogButtonBox.RejectRole)
        btns.accepted.connect(self._apply)
        btns.rejected.connect(self.reject)
        outer.addWidget(btns)
        self._refresh_preview()

    def _on_unlink_toggled(self, checked):
        self.debuff_widget.setVisible(bool(checked))
        self._refresh_preview()

    def _current_config(self):
        buff = int(self.buff_combo.currentText().replace("x", ""))
        debuff = int(self.debuff_combo.currentText().replace("x", ""))
        if not self.chk_unlink.isChecked():
            debuff = buff
        mode = self._mode_keys[self.mode_combo.currentIndex()]
        return buff, debuff, mode, self.chk_tooltips.isChecked()

    def _refresh_preview(self):
        buff, debuff, mode, tooltips = self._current_config()
        try:
            res = rebuild_effects_gon(self.gon_path, buff=buff, debuff=debuff,
                                       mode=mode, write_desc=tooltips,
                                       write_names=True, dry_run=True)
        except Exception as e:
            self.preview.setPlainText(f"ERROR: {e}")
            self.apply_btn.setEnabled(False)
            return
        self.apply_btn.setEnabled(True)
        lines = []
        lines.append(f"Buff: {buff}x   Debuff: {debuff}x   "
                     f"Mode: {mode}   Tooltips: {'on' if tooltips else 'off'}")
        lines.append(f"Base blocks: {res['base_count']}   "
                     f"Plus variants: {res['plus_count']}   "
                     f"DESC rows: {res['desc_rows']}   "
                     f"NAME rows: {res['name_rows']}")
        lines.append("")
        lines.append("Example: object_cinderblock1")
        for _, blk, tip, name_csv in res["preview_lines"]:
            for ln in blk:
                lines.append(f"  {ln}")
            if tip:
                lines.append(f"  [tooltip] {tip}")
            lines.append(f"  [name CSV] {name_csv}")
            lines.append("")
        self.preview.setPlainText("\n".join(lines))

    def _apply(self):
        buff, debuff, mode, tooltips = self._current_config()
        if not fu_question(
            self, APP_TITLE,
            f"{tr('rebuild_confirm')}\n\n"
            f"Buff: {buff}x   Debuff: {debuff}x   Mode: {mode}\n"
            f"Tooltips: {'on' if tooltips else 'off'}\n\n"
            f"{tr('backup_will_be_made')}"):
            return
        try:
            res = rebuild_effects_gon(self.gon_path, buff=buff, debuff=debuff,
                                       mode=mode, write_desc=tooltips,
                                       write_names=True, dry_run=False)
        except Exception as e:
            fu_error(self, APP_TITLE, f"{tr('rebuild_failed')}\n{e}")
            return
        self._last_result = res
        self.config["scaling_buff"] = buff
        self.config["scaling_debuff"] = debuff
        self.config["scaling_unlink_debuff"] = self.chk_unlink.isChecked()
        self.config["scaling_mode"] = mode
        self.config["scaling_tooltips"] = tooltips
        fu_info(self, APP_TITLE,
                f"{tr('rebuild_success')}\n\n"
                f"Plus variants: {res['plus_count']}\n"
                f"DESC rows: {res['desc_rows']}   NAME rows: {res['name_rows']}\n"
                f"Backup: {os.path.basename(res['backup_path'] or '')}")
        self.accept()


# =============================================================================
# MAIN WINDOW
# =============================================================================
class FurnitureUpgradeWindow(QMainWindow):
    def __init__(self, font_roles, loaded_families, loaded_paths,
                 path_to_families, display_names):
        super().__init__()
        self.font_roles = font_roles
        self.loaded_families = loaded_families
        self.loaded_paths = loaded_paths
        self.path_to_families = path_to_families
        self.display_names = display_names
        self.system_family = SYSTEM_FALLBACK_FONT
        self.config = load_config()

        global NOTIFY_SOUND_ENABLED
        NOTIFY_SOUND_ENABLED = bool(self.config.get("notify_sound", False))

        # Apply saved UI language before building any UI.
        set_language(self.config.get("ui_language", "auto"))

        self.use_mewgenics_font = bool(
            self.config.get("use_mewgenics_font", True))
        legacy = self.config.get("font_size")
        self.font_size_mewgenics = int(self.config.get(
            "font_size_mewgenics",
            legacy if legacy is not None else DEFAULT_FONT_SIZE_MEWGENICS))
        self.font_size_system = int(self.config.get(
            "font_size_system",
            legacy if legacy is not None else DEFAULT_FONT_SIZE_SYSTEM))
        self.font_size_mewgenics = max(6, min(14, self.font_size_mewgenics))
        self.font_size_system = max(6, min(14, self.font_size_system))
        self.theme_name = self.config.get(
            "theme_name", "Frutiger Aero (Purple)")
        if self.theme_name not in THEMES:
            self.theme_name = "Frutiger Aero (Purple)"
        self.show_appearance_row = bool(
            self.config.get("show_appearance_row", False))
        self.show_backups_row = bool(
            self.config.get("show_backups_row", False))
        self.show_diagnostic_tools = bool(
            self.config.get("show_diagnostic_tools", False))
        self.setWindowTitle(APP_TITLE)
        self.resize(1100, 850)
        self.setMinimumSize(800, 600)
        self.setAcceptDrops(True)
        self.plan = []; self.skipped = []; self.groups = {}
        set_current_font_size(self._active_font_size())
        self._build_ui()
        self._build_menus()
        self._apply_theme()
        self._autodetect()

    def _active_font_size(self):
        return (self.font_size_mewgenics if self.use_mewgenics_font
                else self.font_size_system)

    def _save_config(self):
        self.config["last_save_path"] = self.path_edit.text().strip()
        self.config["theme_name"] = self.theme_name
        self.config["use_mewgenics_font"] = bool(self.use_mewgenics_font)
        self.config["notify_sound"] = bool(NOTIFY_SOUND_ENABLED)
        self.config["custom_excludes"] = self.exclude_edit.text().strip()
        self.config["merge_idols"] = bool(self.chk_special.isChecked())
        self.config["preserve_excess"] = bool(self.chk_preserve.isChecked())
        self.config["font_size_mewgenics"] = int(self.font_size_mewgenics)
        self.config["font_size_system"] = int(self.font_size_system)
        self.config["show_appearance_row"] = bool(self.show_appearance_row)
        self.config["show_backups_row"] = bool(self.show_backups_row)
        self.config["show_diagnostic_tools"] = bool(self.show_diagnostic_tools)
        save_config(self.config)

    def closeEvent(self, event):
        try:
            self._save_config()
        except Exception:
            pass
        super().closeEvent(event)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            for url in event.mimeData().urls():
                if url.toLocalFile().lower().endswith(".sav"):
                    event.acceptProposedAction(); return
        event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            path = url.toLocalFile()
            if path.lower().endswith(".sav"):
                self.path_edit.setText(path)
                self._save_config()
                self._preview()
                event.acceptProposedAction()
                self._log(f"Dropped save: {path}")
                return
        event.ignore()

    def _active_font_roles(self):
        if self.use_mewgenics_font:
            return dict(self.font_roles)
        return {r: SYSTEM_FALLBACK_FONT for r in FONT_ROLE_KEYWORDS}

    def _build_menus(self):
        mb = self.menuBar()
        file_menu = mb.addMenu("&" + tr("menu_file"))
        a = QAction(tr("browse_save"), self); a.triggered.connect(self._browse)
        file_menu.addAction(a)
        a = QAction(tr("refresh_save"), self); a.setShortcut(QKeySequence("F5"))
        a.triggered.connect(self._preview); file_menu.addAction(a)
        file_menu.addSeparator()
        a = QAction(tr("exit"), self); a.triggered.connect(self.close)
        file_menu.addAction(a)

        sm = mb.addMenu("&" + tr("menu_settings"))
        self.act_fonts = QAction(tr("use_mewgenics_fonts"), self)
        self.act_fonts.setCheckable(True)
        self.act_fonts.setChecked(self.use_mewgenics_font)
        self.act_fonts.toggled.connect(self._on_font_toggled)
        sm.addAction(self.act_fonts)

        self.font_size_menu = sm.addMenu(tr("menu_font_size"))
        self.font_size_group = QActionGroup(self); self.font_size_group.setExclusive(True)
        self.font_size_actions = {}
        for pt in FONT_SIZE_CHOICES:
            a = QAction(f"{pt}pt", self); a.setCheckable(True)
            a.setChecked(pt == self._active_font_size())
            a.triggered.connect(lambda _c=False, p=pt: self._set_font_size(p))
            self.font_size_group.addAction(a); self.font_size_menu.addAction(a)
            self.font_size_actions[pt] = a

        self.theme_menu = sm.addMenu(tr("menu_theme"))
        self.theme_group = QActionGroup(self); self.theme_group.setExclusive(True)
        self.theme_actions = {}
        for name in THEMES.keys():
            a = QAction(name, self); a.setCheckable(True)
            a.setChecked(name == self.theme_name)
            a.triggered.connect(lambda _c=False, n=name: self._on_theme_changed(n))
            self.theme_group.addAction(a); self.theme_menu.addAction(a)
            self.theme_actions[name] = a

        # Language submenu
        self.lang_menu = sm.addMenu(tr("menu_language"))
        self.lang_group = QActionGroup(self); self.lang_group.setExclusive(True)
        self.lang_actions = {}
        current_lang = self.config.get("ui_language", "auto")
        a_auto = QAction(tr("menu_auto"), self); a_auto.setCheckable(True)
        a_auto.setChecked(current_lang == "auto")
        a_auto.triggered.connect(lambda _c=False: self._set_ui_language("auto"))
        self.lang_group.addAction(a_auto); self.lang_menu.addAction(a_auto)
        self.lang_actions["auto"] = a_auto
        for code in SUPPORTED_LANGUAGES:
            label = LANGUAGE_LABELS.get(code, code)
            a = QAction(label, self); a.setCheckable(True)
            a.setChecked(current_lang == code)
            a.triggered.connect(lambda _c=False, c=code: self._set_ui_language(c))
            self.lang_group.addAction(a); self.lang_menu.addAction(a)
            self.lang_actions[code] = a

        self.act_sound = QAction(tr("notification_sound"), self)
        self.act_sound.setCheckable(True)
        self.act_sound.setChecked(bool(self.config.get("notify_sound", False)))
        self.act_sound.toggled.connect(self._on_sound_toggled)
        sm.addAction(self.act_sound)

        bm = mb.addMenu("&" + tr("menu_backups"))
        a = QAction(tr("undo_last_op"), self)
        a.setShortcut(QKeySequence("Ctrl+Z"))
        a.triggered.connect(self._undo_last); bm.addAction(a)
        bm.addSeparator()
        a = QAction(tr("manage_backups"), self); a.triggered.connect(self._open_backups)
        bm.addAction(a)
        a = QAction(tr("prune_backups"), self); a.triggered.connect(self._prune_backups)
        bm.addAction(a)

        tm = mb.addMenu("&" + tr("menu_tools"))
        self.act_rebuild_stats = QAction(tr("rebuild_stats_menu"), self)
        self.act_rebuild_stats.triggered.connect(self._rebuild_stats)
        tm.addAction(self.act_rebuild_stats)
        tm.addSeparator()
        self.act_diag = QAction(tr("show_diagnostic_tools"), self)
        self.act_diag.setCheckable(True)
        self.act_diag.setChecked(self.show_diagnostic_tools)
        self.act_diag.toggled.connect(self._on_diagnostic_toggled)
        tm.addAction(self.act_diag)
        tm.addSeparator()
        a = QAction(tr("dump_fonts"), self); a.triggered.connect(self._dump_fonts)
        tm.addAction(a)
        a = QAction(tr("dump_debug"), self); a.triggered.connect(self._dump_debug)
        tm.addAction(a)

        vm = mb.addMenu("&" + tr("menu_view"))
        self.act_show_appearance = QAction(tr("show_appearance_row"), self)
        self.act_show_appearance.setCheckable(True)
        self.act_show_appearance.setChecked(self.show_appearance_row)
        self.act_show_appearance.toggled.connect(self._on_show_appearance_toggled)
        vm.addAction(self.act_show_appearance)
        self.act_show_backups = QAction(tr("show_backups_buttons"), self)
        self.act_show_backups.setCheckable(True)
        self.act_show_backups.setChecked(self.show_backups_row)
        self.act_show_backups.toggled.connect(self._on_show_backups_toggled)
        vm.addAction(self.act_show_backups)

    def _set_font_size(self, pt):
        if self.use_mewgenics_font:
            self.font_size_mewgenics = int(pt)
        else:
            self.font_size_system = int(pt)
        self._refresh_font_size_menu_checks()
        if hasattr(self, "font_size_combo"):
            self.font_size_combo.blockSignals(True)
            self.font_size_combo.setCurrentText(f"{pt}pt")
            self.font_size_combo.blockSignals(False)
        set_current_font_size(pt)
        self._apply_theme(); self._save_config()

    def _refresh_font_size_menu_checks(self):
        active = self._active_font_size()
        for pt, a in self.font_size_actions.items():
            a.setChecked(pt == active)

    def _set_ui_language(self, code):
        self.config["ui_language"] = code
        save_config(self.config)
        set_language(code)
        fu_info(self, APP_TITLE, tr("language_restart_notice"))

    def _on_show_appearance_toggled(self, checked):
        self.show_appearance_row = bool(checked)
        self.appearance_row_widget.setVisible(self.show_appearance_row)
        self._save_config()

    def _on_show_backups_toggled(self, checked):
        self.show_backups_row = bool(checked)
        self.backups_btn.setVisible(self.show_backups_row)
        self.prune_btn.setVisible(self.show_backups_row)
        self._save_config()

    def _on_diagnostic_toggled(self, checked):
        self.show_diagnostic_tools = bool(checked)
        self.fonts_dump_btn.setVisible(self.show_diagnostic_tools)
        self.debug_dump_btn.setVisible(self.show_diagnostic_tools)
        self._save_config()

    def _build_ui(self):
        central = QWidget(); self.setCentralWidget(central)
        root = QVBoxLayout(central); root.setContentsMargins(0, 0, 0, 0); root.setSpacing(0)
        self.banner = Banner(); root.addWidget(self.banner)
        inner = QWidget(); root.addWidget(inner, 1)
        outer = QVBoxLayout(inner)
        outer.setContentsMargins(18, 10, 18, 14); outer.setSpacing(8)

        self.appearance_row_widget = QWidget()
        ar = QHBoxLayout(self.appearance_row_widget)
        ar.setContentsMargins(0, 0, 0, 0)
        ar.addWidget(QLabel(tr("theme")))
        self.theme_combo = QComboBox()
        for name in THEMES.keys():
            self.theme_combo.addItem(name)
        self.theme_combo.setCurrentText(self.theme_name)
        self.theme_combo.currentTextChanged.connect(self._on_theme_changed)
        ar.addWidget(self.theme_combo)
        self.font_chk = QCheckBox(tr("mewgenics_fonts"))
        self.font_chk.setChecked(self.use_mewgenics_font)
        self.font_chk.toggled.connect(self._on_font_toggled)
        ar.addWidget(self.font_chk)
        ar.addWidget(QLabel(tr("size")))
        self.font_size_combo = QComboBox()
        for pt in FONT_SIZE_CHOICES:
            self.font_size_combo.addItem(f"{pt}pt")
        self.font_size_combo.setCurrentText(f"{self._active_font_size()}pt")
        self.font_size_combo.currentTextChanged.connect(
            lambda s: self._set_font_size(int(s.replace("pt", ""))))
        self.font_size_combo.setFixedWidth(80)
        ar.addWidget(self.font_size_combo)
        self.sound_chk = QCheckBox(tr("notification_sound"))
        self.sound_chk.setChecked(bool(self.config.get("notify_sound", False)))
        self.sound_chk.toggled.connect(self._on_sound_toggled)
        ar.addWidget(self.sound_chk)
        ar.addStretch(1)
        self.appearance_row_widget.setVisible(self.show_appearance_row)
        outer.addWidget(self.appearance_row_widget)

        self.status_label = QLabel(tr("starting_up"))
        self.status_label.setObjectName("StatusLabel")
        outer.addWidget(self.status_label)

        path_row = QHBoxLayout()
        path_row.addWidget(QLabel(tr("save_file")))
        self.path_edit = QLineEdit()
        self.path_edit.setPlaceholderText(tr("save_file_placeholder"))
        path_row.addWidget(self.path_edit, 1)
        b = QPushButton(tr("browse")); b.clicked.connect(self._browse)
        path_row.addWidget(b)
        r = QPushButton("↻"); r.setToolTip(tr("refresh_save_tooltip")); r.setFixedWidth(40)
        r.clicked.connect(self._preview); path_row.addWidget(r)
        outer.addLayout(path_row)

        splitter = QSplitter(Qt.Vertical); splitter.setChildrenCollapsible(False)

        tree_panel = QFrame(); tree_panel.setObjectName("Panel")
        tl = QVBoxLayout(tree_panel); tl.setContentsMargins(10, 8, 10, 10); tl.setSpacing(4)
        lbl = QLabel(tr("preview_title"))
        lbl.setObjectName("SectionLabel"); tl.addWidget(lbl)
        self.tree = AutoFitTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels([
            tr("col_base"), tr("col_copies"),
            tr("col_becomes"), tr("col_parked")])
        self.tree.setAlternatingRowColors(True)
        self.tree.setRootIsDecorated(False)
        self.tree.setUniformRowHeights(True)
        self.tree.setSelectionMode(QAbstractItemView.NoSelection)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.AscendingOrder)
        header = self.tree.header()
        for i in range(4):
            header.setSectionResizeMode(i, QHeaderView.Interactive)
        header.setSectionsClickable(True)
        self.tree.setColumnWidth(0, 300); self.tree.setColumnWidth(1, 90)
        self.tree.setColumnWidth(2, 340); self.tree.setColumnWidth(3, 90)
        self.tree.setMinimumHeight(140)
        self._wide_header = WideGrabHeaderFilter(header)
        header.installEventFilter(self._wide_header)
        tl.addWidget(self.tree, 1); splitter.addWidget(tree_panel)

        opts_panel = QFrame(); opts_panel.setObjectName("Panel")
        ol = QVBoxLayout(opts_panel); ol.setContentsMargins(10, 8, 10, 10); ol.setSpacing(6)
        lbl = QLabel(tr("merge_options")); lbl.setObjectName("SectionLabel"); ol.addWidget(lbl)
        self.chk_special = QCheckBox(tr("merge_idols"))
        self.chk_special.setChecked(bool(self.config.get("merge_idols", True)))
        self.chk_special.stateChanged.connect(self._preview); ol.addWidget(self.chk_special)
        self.chk_preserve = QCheckBox(tr("preserve_excess"))
        self.chk_preserve.setChecked(
            bool(self.config.get("preserve_excess", True)))
        self.chk_preserve.stateChanged.connect(self._preview)
        ol.addWidget(self.chk_preserve)
        ol.addWidget(QLabel(tr("custom_exclusions")))
        self.exclude_edit = QLineEdit()
        self.exclude_edit.setText(self.config.get("custom_excludes", ""))
        self.exclude_edit.setPlaceholderText(tr("exclude_placeholder"))
        self.exclude_edit.textChanged.connect(self._preview); ol.addWidget(self.exclude_edit)
        h = QLabel(tr("exclude_hint"))
        h.setObjectName("HintLabel"); h.setWordWrap(True); ol.addWidget(h)
        h = QLabel(tr("unmerged_hint"))
        h.setObjectName("HintLabel"); ol.addWidget(h)
        ol.addStretch(1); splitter.addWidget(opts_panel)

        log_panel = QFrame(); log_panel.setObjectName("Panel")
        ll = QVBoxLayout(log_panel); ll.setContentsMargins(10, 8, 10, 10); ll.setSpacing(4)
        lbl = QLabel(tr("log")); lbl.setObjectName("SectionLabel"); ll.addWidget(lbl)
        self.log = QPlainTextEdit(); self.log.setReadOnly(True)
        self.log.setMinimumHeight(90); ll.addWidget(self.log, 1)
        splitter.addWidget(log_panel)

        splitter.setSizes([360, 240, 150]); outer.addWidget(splitter, 1)

        btn_row = QHBoxLayout()
        undo = QPushButton(tr("undo"))
        undo.setToolTip(tr("undo_tooltip"))
        undo.clicked.connect(self._undo_last); btn_row.addWidget(undo)
        unm = QPushButton(tr("unmerge_all")); unm.clicked.connect(self._unmerge)
        btn_row.addWidget(unm)
        self.backups_btn = QPushButton(tr("backups_btn"))
        self.backups_btn.clicked.connect(self._open_backups)
        self.backups_btn.setVisible(self.show_backups_row)
        btn_row.addWidget(self.backups_btn)
        self.prune_btn = QPushButton(tr("prune"))
        self.prune_btn.clicked.connect(self._prune_backups)
        self.prune_btn.setVisible(self.show_backups_row)
        btn_row.addWidget(self.prune_btn)
        self.fonts_dump_btn = QPushButton(tr("dump_fonts"))
        self.fonts_dump_btn.clicked.connect(self._dump_fonts)
        self.fonts_dump_btn.setVisible(self.show_diagnostic_tools)
        btn_row.addWidget(self.fonts_dump_btn)
        self.debug_dump_btn = QPushButton(tr("dump_debug"))
        self.debug_dump_btn.clicked.connect(self._dump_debug)
        self.debug_dump_btn.setVisible(self.show_diagnostic_tools)
        btn_row.addWidget(self.debug_dump_btn)
        btn_row.addStretch(1)
        merge = QPushButton(tr("merge_dupes")); merge.setObjectName("AccentBtn")
        merge.clicked.connect(self._apply); btn_row.addWidget(merge)
        outer.addLayout(btn_row)

        try:
            self._undo_shortcut = QShortcut(QKeySequence("Ctrl+Z"), self)
            self._undo_shortcut.activated.connect(self._undo_last)
        except Exception:
            pass

    def _on_theme_changed(self, name):
        self.theme_name = name
        if hasattr(self, "theme_combo") and self.theme_combo.currentText() != name:
            self.theme_combo.blockSignals(True)
            self.theme_combo.setCurrentText(name)
            self.theme_combo.blockSignals(False)
        if hasattr(self, "theme_actions") and name in self.theme_actions:
            self.theme_actions[name].setChecked(True)
        self._apply_theme(); self._save_config()

    def _on_font_toggled(self, checked):
        self.use_mewgenics_font = bool(checked)
        new_size = self._active_font_size()
        if hasattr(self, "font_size_combo"):
            self.font_size_combo.blockSignals(True)
            self.font_size_combo.setCurrentText(f"{new_size}pt")
            self.font_size_combo.blockSignals(False)
        if hasattr(self, "font_chk") and self.font_chk.isChecked() != bool(checked):
            self.font_chk.blockSignals(True); self.font_chk.setChecked(bool(checked))
            self.font_chk.blockSignals(False)
        if hasattr(self, "act_fonts") and self.act_fonts.isChecked() != bool(checked):
            self.act_fonts.blockSignals(True); self.act_fonts.setChecked(bool(checked))
            self.act_fonts.blockSignals(False)
        self._refresh_font_size_menu_checks()
        self._log(f"Font toggle: "
                  f"{'mewgenics' if self.use_mewgenics_font else 'system'} "
                  f"({new_size}pt)")
        self._apply_theme(); self._save_config()

    def _on_sound_toggled(self, checked):
        global NOTIFY_SOUND_ENABLED
        NOTIFY_SOUND_ENABLED = bool(checked)
        if hasattr(self, "sound_chk") and self.sound_chk.isChecked() != bool(checked):
            self.sound_chk.blockSignals(True); self.sound_chk.setChecked(bool(checked))
            self.sound_chk.blockSignals(False)
        if hasattr(self, "act_sound") and self.act_sound.isChecked() != bool(checked):
            self.act_sound.blockSignals(True); self.act_sound.setChecked(bool(checked))
            self.act_sound.blockSignals(False)
        self._log(f"Notification sound: "
                  f"{'on' if NOTIFY_SOUND_ENABLED else 'off'}")
        if NOTIFY_SOUND_ENABLED:
            play_notify_sound()
        self._save_config()

    def _apply_theme(self):
        colors = THEMES.get(self.theme_name, THEMES["Frutiger Aero (Purple)"])
        set_active_font_roles(self._active_font_roles())
        set_current_font_size(self._active_font_size())
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(build_qss(colors))
            app.setFont(QFont(font_for("body"), _fs(1.0)))
        self.banner.set_style(colors)

    def _log(self, msg):
        self.log.appendPlainText(msg)

    def _set_status(self, text, color=None):
        if color is None:
            color = THEMES[self.theme_name]["warn"]
        self.status_label.setText(text)
        self.status_label.setStyleSheet(
            f"font-size: {_fs(1.0)}pt; font-weight: 700; color: {color};"
            f"background: transparent;")

    def _blob_for_new_row(self, source_blob, mode):
        return rewrite_loc(source_blob, STORAGE_LOC)

    def _load_rows(self, path):
        con = sqlite3.connect(path)
        try:
            rows = con.execute(
                "SELECT key, data FROM furniture ORDER BY key").fetchall()
        finally:
            con.close()
        return rows

    def _find_gon_path(self):
        for base in _data_search_roots():
            p = os.path.join(base, "data", "furniture_effects.gon")
            if os.path.isfile(p):
                return p
        return None

    def _rebuild_stats(self):
        gon_path = self._find_gon_path()
        if not gon_path:
            fu_error(self, APP_TITLE, tr("gon_not_found"))
            return
        dlg = ScalingDialog(self, gon_path, self.config)
        dlg.exec()
        self._save_config()

    def _autodetect(self):
        self._log(f"Writable base dir: {_writable_base_dir()}")
        self._log(f"Config file: {config_file_path()}")
        bdir = portable_backup_dir()
        self._log(f"Backup folder: {bdir if bdir else '(fallback: next to save)'}")
        gon_path = self._find_gon_path()
        self._log(f"GON: {gon_path if gon_path else '(not found!)'}")
        trans_path = None
        for p in _translation_candidates():
            if os.path.isfile(p):
                trans_path = p; break
        self._log(f"Translations: {trans_path if trans_path else '(not found)'}")
        self._log(f"UI language: {CURRENT_LANGUAGE}")
        saves, diag = find_saves()
        for line in diag:
            self._log(line)
        self._log(f"Loaded {len(self.loaded_paths)} font file(s).")
        self._log(f"Font families: {self.loaded_families}")
        if self.display_names:
            self._log(f"Display names loaded: {len(self.display_names)} entries")
        else:
            self._log("⚠ No display names loaded.")
        self._log(f"Theme: {self.theme_name}    "
                  f"Font: {'mewgenics' if self.use_mewgenics_font else 'system'} "
                  f"@ {self._active_font_size()}pt")
        last = self.config.get("last_save_path", "").strip()
        if last and os.path.isfile(last):
            self.path_edit.setText(last)
            self._log(f"Restored last save from config: {last}")
            self._preview(); return
        best = _best_save(saves)
        if best:
            self.path_edit.setText(best)
            self._log(f"Auto-detected save: {best}")
            self._preview()
        else:
            self._log("No save auto-detected. Click Browse.")
            self._set_status(tr("no_save_autodetected"),
                             THEMES[self.theme_name]["bad"])

    def _dump_fonts(self):
        self._log("=== FONT DUMP ===")
        self._log(f"Base dirs: {_base_dirs()}")
        for p in self.loaded_paths:
            self._log(f"  {p}")
        self._log(f"Loaded families ({len(self.loaded_families)}):")
        for fam in self.loaded_families:
            self._log(f"  '{fam}'")
        for role in ("body", "title", "log", "error", "warn",
                     "button", "notif", "accent"):
            self._log(f"  font[{role:6}] = {self.font_roles.get(role)}")
        self._log(f"Font size (mewgenics): {self.font_size_mewgenics}pt")
        self._log(f"Font size (system):    {self.font_size_system}pt")
        self._log("=== END FONT DUMP ===")

    def _dump_debug(self):
        path = self.path_edit.text().strip()
        out_path = os.path.join(_writable_base_dir(), "FurnitureUpgrade-debug.txt")
        lines = []
        lines.append(f"FurnitureUpgrade v19 debug dump")
        lines.append(f"Timestamp: {datetime.datetime.now().isoformat()}")
        lines.append(f"Platform: {sys.platform}")
        lines.append(f"Python: {sys.version.split()[0]}")
        lines.append(f"Frozen: {getattr(sys, 'frozen', False)}")
        lines.append(f"Writable base dir: {_writable_base_dir()}")
        lines.append(f"Backup folder:     {portable_backup_dir()}")
        lines.append(f"Config path:       {config_file_path()}")
        lines.append(f"GON path:          {self._find_gon_path()}")
        lines.append(f"UI language:       {CURRENT_LANGUAGE}")
        lines.append(f"Data search roots: {_data_search_roots()}")
        lines.append(f"Save path:         {path}")
        lines.append(f"Config: {json.dumps(self.config, indent=2)}")
        if path and os.path.isfile(path):
            try:
                con = sqlite3.connect(path)
                cur = con.cursor()
                total = cur.execute("SELECT COUNT(*) FROM furniture").fetchone()[0]
                lines.append(f"\nTotal furniture rows: {total}")
                for key, data in cur.execute(
                    "SELECT key, data FROM furniture ORDER BY key LIMIT 10"):
                    try:
                        _, iid, loc, stats, _ = parse_blob(data)
                        lines.append(
                            f"  key={key} id={iid!r} loc={loc!r} stats={stats}")
                    except Exception as e:
                        lines.append(f"  key={key} PARSE FAILED: {e}")
                con.close()
            except Exception as e:
                lines.append(f"  DB ERROR: {e}")
        lines.append("\n=== LOG PANEL ===")
        lines.append(self.log.toPlainText())
        try:
            with open(out_path, "w", encoding="utf-8") as fh:
                fh.write("\n".join(lines))
            self._log(f"Debug dump written: {out_path}")
            fu_info(self, APP_TITLE, f"{tr('debug_dump_written')}\n{out_path}")
        except Exception as e:
            fu_error(self, APP_TITLE, f"{tr('debug_dump_failed')}\n{e}")

    def _browse(self):
        start = default_save_dir() or os.path.expanduser("~")
        path, _ = QFileDialog.getOpenFileName(
            self, tr("browse_dialog_title"), start,
            "Mewgenics save (*.sav);;All files (*)")
        if path:
            self.path_edit.setText(path); self._save_config(); self._preview()

    def _preview(self):
        path = self.path_edit.text().strip()
        self.tree.setSortingEnabled(False); self.tree.clear()
        if not path:
            self._set_status(tr("no_save_selected"),
                             THEMES[self.theme_name]["bad"]); return
        if not os.path.isfile(path):
            self._set_status(f"{tr('file_not_found')}: {path}",
                             THEMES[self.theme_name]["bad"])
            self._log(f"Save path does not exist: {path}"); return
        try:
            rows = self._load_rows(path)
        except Exception as e:
            self._set_status(tr("read_failed"),
                             THEMES[self.theme_name]["bad"])
            self._log(f"  error: {e}"); self._log(traceback.format_exc())
            fu_error(self, APP_TITLE, f"{tr('read_failed')}\n{e}"); return
        if len(rows) == 0:
            self._log("⚠ This save has 0 furniture rows.")
        include_special = self.chk_special.isChecked()
        raw_excludes = parse_custom_excludes(self.exclude_edit.text())
        expanded, extra = expand_excludes(raw_excludes, self.display_names)
        if raw_excludes:
            self._log(f"Custom exclusions raw: {sorted(raw_excludes)}")
            if extra:
                self._log(f"  expanded: {sorted(extra)}")
        try:
            self.plan, self.skipped, self.groups = build_plan(
                rows, include_special=include_special,
                custom_excludes=expanded,
                preserve_excess=self.chk_preserve.isChecked())
        except Exception as e:
            self._set_status(tr("parse_failed"),
                             THEMES[self.theme_name]["bad"])
            self._log(f"  build_plan error: {e}"); fu_error(self, APP_TITLE, str(e)); return
        self._log(f"Read {len(rows)} furniture rows; "
                  f"{len(self.groups)} unique base items.")
        dup_groups = sum(1 for members in self.groups.values()
                         if sum(m['value'] for m in members) >= 2)
        if not self.plan:
            self.tree.addTopLevelItem(
                QTreeWidgetItem([tr("no_mergeable_groups"), "", "", ""]))
            if dup_groups == 0:
                self._set_status(tr("no_duplicates"),
                                 THEMES[self.theme_name]["good"])
            else:
                self._set_status(tr("all_skipped"),
                                 THEMES[self.theme_name]["warn"])
        else:
            self._set_status(tr("groups_ready").format(n=len(self.plan)),
                             THEMES[self.theme_name]["good"])
        for p in self.plan:
            base = p['base']
            disp = display_name_for(base, self.display_names)
            item_col = f"{disp}  —  {base}" if disp else base
            if p['best_current'] > 0:
                item_col += f"  (best: plus{p['best_current']})"
            new_disp = display_name_for(p['new_id'], self.display_names)
            new_col = (f"{new_disp}  —  {p['new_id']}" if new_disp else p['new_id'])
            it = QTreeWidgetItem([item_col, str(p['count']),
                                  new_col, str(len(p['park_keys']))])
            it.setData(1, Qt.UserRole, int(p['count']))
            it.setData(3, Qt.UserRole, int(len(p['park_keys'])))
            it.setToolTip(0, item_col); it.setToolTip(2, new_col)
            self.tree.addTopLevelItem(it)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(0, Qt.AscendingOrder)
        for base, n, reason in self.skipped:
            self._log(f"  skipped: {base} ({n} copies) - {reason}")

    def _plan_to_lines(self):
        lines = []
        for p in self.plan:
            lines.append(
                f"MERGE  {p['base']}  ({p['count']} rows, "
                f"total value {p['total_value']})  ->  {p['new_id']}")
            lines.append(f"       keep : key={p['keep_key']}  "
                         f"loc={p['keep_loc']!r}  ({p['keep_old_id']})")
            for k, old in zip(p['park_keys'], p['park_old_ids']):
                lines.append(f"       park : key={k}  ({old})")
            for k, old in zip(p.get('leftover_keys', []),
                              p.get('leftover_ids', [])):
                lines.append(f"       keep in place : key={k}  ({old})")
        return lines

    def _apply(self):
        path = self.path_edit.text().strip()
        if not path or not os.path.isfile(path):
            fu_error(self, APP_TITLE, tr("pick_valid_save")); return
        if not self.plan:
            self._preview()
        if not self.plan:
            fu_info(self, APP_TITLE, tr("nothing_to_merge")); return
        backup = make_backup_path(path, "merge")
        dlg = ConfirmDialog(
            self,
            summary_lines=[
                tr("merge_summary").format(n=len(self.plan)),
                tr("close_game_notice"),
                f"{tr('backup_will_be_written_to')} "
                f"{os.path.basename(backup)}"],
            backup_path=backup, detail_lines=self._plan_to_lines(),
            confirm_text=tr("merge_now"))
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            shutil.copy2(path, backup)
        except Exception as e:
            fu_error(self, APP_TITLE, f"{tr('backup_failed')}\n{e}"); return
        self._log(f"Backup written: {backup}")
        try:
            con = sqlite3.connect(path)
        except Exception as e:
            fu_error(self, APP_TITLE, f"{tr('open_save_failed')}\n{e}"); return
        try:
            ensure_side_tables(con)
            cur = con.cursor()
            now = datetime.datetime.now().isoformat(timespec='seconds')
            for p in self.plan:
                for dk in p['park_keys']:
                    cur.execute(
                        "INSERT OR REPLACE INTO furniture_parked (key, data) "
                        "SELECT key, data FROM furniture WHERE key=?", (dk,))
                    cur.execute("DELETE FROM furniture WHERE key=?", (dk,))
                cur.execute("UPDATE furniture SET data=? WHERE key=?",
                            (p['new_blob'], p['keep_key']))
                cur.execute("""INSERT INTO furniture_merge_log
                    (merged_at, kept_key, old_data, deleted_keys, plus_level, new_item_id)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (now, p['keep_key'], p['old_blob'],
                     ",".join(str(k) for k in p['park_keys']),
                     p['target_plus'], p['new_id']))
            con.commit()
        except Exception as e:
            con.close(); self._log(f"Merge failed: {e}")
            self._log(traceback.format_exc())
            fu_error(self, APP_TITLE, f"{tr('merge_failed_restoring')}\n{e}")
            try:
                shutil.copy2(backup, path)
            except Exception as e2:
                fu_error(self, APP_TITLE, f"{tr('restore_failed')}\n{e2}")
            return
        con.close()
        self._log(f"Applied {len(self.plan)} merge(s).")
        fu_info(self, APP_TITLE,
                f"{tr('merge_complete')}\n{tr('backup_label')} {backup}")
        self._preview()

    def _undo_last(self):
        path = self.path_edit.text().strip()
        if not path or not os.path.isfile(path):
            fu_error(self, APP_TITLE, tr("pick_valid_save")); return
        infos = get_backup_info(path)
        if not infos:
            fu_info(self, APP_TITLE, tr("no_backups_to_undo")); return
        newest = infos[0]
        age = humanize_age(time.time() - newest['mtime'])
        if not fu_question(
            self, APP_TITLE,
            f"{tr('undo_confirm')}\n\n"
            f"  {tr('backup_label')} {newest['name']}\n"
            f"  {tr('when_label')} {newest['dt']:%Y-%m-%d %H:%M:%S} ({age})\n"
            f"  {tr('size_label')} {newest['size'] / 1024:.0f} KB\n\n"
            f"{tr('safety_copy_note')}"):
            return
        safety = make_backup_path(path, "prerestore")
        try:
            shutil.copy2(path, safety)
            shutil.copy2(newest['path'], path)
        except Exception as e:
            fu_error(self, APP_TITLE, f"{tr('undo_failed')}\n{e}"); return
        self._log(f"Undo: restored {newest['name']} (safety: {os.path.basename(safety)})")
        fu_info(self, APP_TITLE,
                f"{tr('restored_label')}\n{tr('from_label')} {newest['name']}\n"
                f"{tr('safety_copy')} {os.path.basename(safety)}")
        self._preview()

    def _unmerge(self):
        path = self.path_edit.text().strip()
        if not path or not os.path.isfile(path):
            fu_error(self, APP_TITLE, tr("pick_valid_save")); return
        try:
            rows = self._load_rows(path)
        except Exception as e:
            fu_error(self, APP_TITLE, f"{tr('read_failed')}\n{e}"); return
        merged_items = find_merged_items(rows)
        if not merged_items:
            fu_error(self, APP_TITLE, tr("nothing_to_unmerge")); return
        total_new_rows = sum(m['copies'] - 1 for m in merged_items)
        detail_lines = []
        for m in merged_items:
            detail_lines.append(
                f"UNMERGE  {m['item_id']}  ->  {m['copies']} x {m['base']}"
                f"   (source key={m['key']})")
        backup = make_backup_path(path, "unmerge")
        dlg = ConfirmDialog(
            self,
            summary_lines=[
                tr("unmerge_summary").format(n=len(merged_items)),
                tr("unmerge_add_rows").format(n=total_new_rows),
                tr("storage_note"),
                tr("close_game_notice")],
            backup_path=backup, detail_lines=detail_lines,
            confirm_text=tr("unmerge_now"))
        if dlg.exec() != QDialog.Accepted:
            return
        try:
            shutil.copy2(path, backup)
        except Exception as e:
            fu_error(self, APP_TITLE, f"{tr('backup_failed')}\n{e}"); return
        self._log(f"Pre-undo backup: {backup}")
        try:
            con = sqlite3.connect(path)
            cur = con.cursor()
            max_key = cur.execute(
                "SELECT COALESCE(MAX(key), 0) FROM furniture").fetchone()[0]
            next_key = max_key + 1
            for m in merged_items:
                base_blob = rewrite_item_id(m['blob'], m['base'])
                cur.execute("UPDATE furniture SET data=? WHERE key=?",
                            (base_blob, m['key']))
                new_blob = self._blob_for_new_row(base_blob, "storage")
                for _ in range(m['copies'] - 1):
                    cur.execute("INSERT INTO furniture (key, data) VALUES (?, ?)",
                                (next_key, new_blob))
                    next_key += 1
            con.commit(); con.close()
        except Exception as e:
            self._log(f"Unmerge failed: {e}")
            self._log(traceback.format_exc())
            fu_error(self, APP_TITLE, f"{tr('unmerge_failed_restoring')}\n{e}")
            try:
                shutil.copy2(backup, path)
            except Exception as e2:
                fu_error(self, APP_TITLE, f"{tr('restore_failed')}\n{e2}")
            return
        self._log(f"Unmerged {len(merged_items)} item(s); added {total_new_rows} row(s).")
        fu_info(self, APP_TITLE,
            f"{tr('unmerge_complete')}\n{tr('backup_label')} {backup}")
        self._preview()

    def _open_backups(self):
        path = self.path_edit.text().strip()
        if not path or not os.path.isfile(path):
            fu_error(self, APP_TITLE, tr("pick_valid_save")); return
        dlg = BackupsDialog(self, path); dlg.exec()
        if dlg._restored:
            self._log("Save was restored; re-previewing."); self._preview()

    def _prune_backups(self):
        path = self.path_edit.text().strip()
        if not path or not os.path.isfile(path):
            fu_error(self, APP_TITLE, tr("pick_valid_save")); return
        infos = get_backup_info(path)
        if not infos:
            fu_info(self, APP_TITLE, tr("no_backups_found")); return
        try:
            st = os.stat(path)
            save_dt = datetime.datetime.fromtimestamp(st.st_mtime)
            save_line = (f"{tr('modified')}: {save_dt:%Y-%m-%d %H:%M:%S} "
                         f"({humanize_age(time.time() - st.st_mtime)})")
        except OSError:
            save_line = ""
        total_size = sum(i['size'] for i in infos)
        preview = []
        for i in infos[:8]:
            preview.append(f"  {i['dt']:%Y-%m-%d %H:%M:%S}  "
                           f"({humanize_age(time.time() - i['mtime'])})  "
                           f"{i['size'] / 1024:.0f} KB")
        if len(infos) > 8:
            preview.append(f"  … and {len(infos) - 8} more")
        keep_n, ok = QInputDialog.getInt(
            self, f"{APP_TITLE} — {tr('prune')}",
            f"{save_line}\n\n"
            f"{tr('found_backups').format(n=len(infos), mb=total_size / (1024 * 1024))}\n\n"
            f"{tr('recent_backups')}\n" + "\n".join(preview) + "\n\n"
            f"{tr('keep_how_many')}",
            DEFAULT_BACKUP_KEEP, 0, len(infos), 1)
        if not ok:
            return
        to_delete = len(infos) - keep_n
        if to_delete <= 0:
            fu_info(self, APP_TITLE, tr("nothing_to_delete")); return
        going = infos[keep_n:]
        going_lines = [f"  {i['dt']:%Y-%m-%d %H:%M:%S}  "
                       f"({humanize_age(time.time() - i['mtime'])})  {i['name']}"
                       for i in going[:15]]
        if len(going) > 15:
            going_lines.append(f"  … and {len(going) - 15} more")
        if not fu_question(self, APP_TITLE,
                f"{tr('delete_backups_confirm').format(n=to_delete)}\n\n" +
                "\n".join(going_lines)):
            return
        deleted, freed = prune_backups_keep_n(path, keep_n)
        mb = freed / (1024 * 1024)
        self._log(f"Pruned {deleted} backup(s), freed {mb:.2f} MB.")
        fu_info(self, APP_TITLE,
                tr("deleted_backups").format(n=deleted, mb=mb))


# =============================================================================
# ENTRY POINT
# =============================================================================
def main():
    app = QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    app.setOrganizationName("FurnitureUpgrade")
    load_translations()
    loaded_families, loaded_paths, path_to_families = load_bundled_fonts()
    font_roles = map_font_roles(loaded_families, loaded_paths, path_to_families)
    display_names = load_display_names()
    win = FurnitureUpgradeWindow(font_roles, loaded_families,
                                 loaded_paths, path_to_families,
                                 display_names)
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        try:
            base = _writable_base_dir()
        except Exception:
            base = os.getcwd()
        crash_path = os.path.join(base, "FurnitureUpgrade-crash.log")
        try:
            with open(crash_path, "w", encoding="utf-8") as f:
                f.write(traceback.format_exc())
        except Exception:
            crash_path = "(couldn't write crash log)"
        try:
            _app = QApplication.instance() or QApplication(sys.argv)
            from PySide6.QtWidgets import QMessageBox as _QMB
            _QMB.critical(None, APP_TITLE,
                f"Furniture Upgrade crashed.\n\n"
                f"Details written to:\n{crash_path}")
        except Exception:
            pass
        raise