"""
Hestia — installed application scan (Stage 6 / v0.6)

Prints ready-to-paste apps.yaml lines for what is actually installed on
this machine, so the launch allowlist holds real targets rather than
guessed ones. Two sources:

  Start Menu shortcuts  — most desktop applications
  Steam library         — Steam itself and every installed game

Steam gets its own pass because Steam games usually have no Start Menu
shortcut at all, and because a steam:// URI keeps working wherever the
library lives, which a hard-coded path does not.

This deliberately only prints. Nothing automatic should add entries to
apps.yaml — that list is the fence around what Hestia can open, so it
stays something a person edits on purpose.

    python scripts/find_apps.py           everything
    python scripts/find_apps.py brave     only matches for "brave"
"""

import glob
import os
import re
import sys

START_MENUS = [
    os.path.join(os.environ.get("ProgramData", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
    os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu", "Programs"),
]

COMMON_STEAM_PATHS = [
    r"C:\Program Files (x86)\Steam",
    r"C:\Program Files\Steam",
]


def find_shortcuts(wanted):
    """Start Menu .lnk files, as {label: full path}."""
    found = {}
    for root in START_MENUS:
        if not root or not os.path.isdir(root):
            continue
        for path in glob.glob(os.path.join(root, "**", "*.lnk"), recursive=True):
            label = os.path.splitext(os.path.basename(path))[0]
            if wanted and wanted not in label.lower():
                continue
            found.setdefault(label, path)  # the user's own Start Menu wins ties
    return found


def steam_root():
    """Where Steam is installed, from the registry if possible."""
    try:
        import winreg
        for root, key in [
            (winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
        ]:
            try:
                with winreg.OpenKey(root, key) as handle:
                    for value in ("SteamPath", "InstallPath"):
                        try:
                            path = winreg.QueryValueEx(handle, value)[0]
                            if path and os.path.isdir(path):
                                return path
                        except FileNotFoundError:
                            continue
            except OSError:
                continue
    except ImportError:
        pass  # not Windows
    for path in COMMON_STEAM_PATHS:
        if os.path.isdir(path):
            return path
    return None


def steam_libraries(root):
    """Every library folder, including ones on other drives."""
    libraries = [root]
    vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
    try:
        with open(vdf, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError:
        return libraries
    # The file is Valve's own key-value format; the paths are all we need.
    for path in re.findall(r'"path"\s*"([^"]+)"', text):
        path = path.replace("\\\\", "\\")
        if os.path.isdir(path) and path not in libraries:
            libraries.append(path)
    return libraries


def find_steam_games(wanted):
    """Installed Steam games, as {name: appid}, from the library manifests."""
    root = steam_root()
    if not root:
        return None, {}
    games = {}
    for library in steam_libraries(root):
        pattern = os.path.join(library, "steamapps", "appmanifest_*.acf")
        for manifest in glob.glob(pattern):
            try:
                with open(manifest, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            appid = re.search(r'"appid"\s*"(\d+)"', text)
            name = re.search(r'"name"\s*"([^"]*)"', text)
            if not appid or not name:
                continue
            label = name.group(1).strip()
            if not label or (wanted and wanted not in label.lower()):
                continue
            games[label] = appid.group(1)
    return root, games


def main():
    wanted = " ".join(sys.argv[1:]).strip().lower()
    printed = False

    shortcuts = find_shortcuts(wanted)
    if shortcuts:
        printed = True
        print(f"# {len(shortcuts)} Start Menu shortcut(s). Copy what you want under `apps:`,")
        print("# renaming each to whatever you would actually say out loud.")
        for label in sorted(shortcuts, key=str.lower):
            # Single-quoted: YAML treats backslashes in double quotes as
            # escapes, which would mangle every Windows path.
            print("  {}: '{}'".format(label.lower(), shortcuts[label]))

    root, games = find_steam_games(wanted)
    if root is None:
        if not wanted:
            print()
            print("# Steam not found, so no games were scanned.")
    else:
        if printed:
            print()
        printed = True
        print(f"# Steam at {root}")
        print("# These use steam:// URIs, so they keep working if you move a library.")
        if not wanted:
            print("  steam: 'steam://open/main'")
        for label in sorted(games, key=str.lower):
            print("  {}: 'steam://rungameid/{}'".format(label.lower(), games[label]))
        if not games:
            print("# (no installed games found in the Steam libraries)")

    if not printed:
        print(f"Nothing matching {wanted!r}." if wanted else "Nothing found. Is this Windows?")


if __name__ == "__main__":
    main()
