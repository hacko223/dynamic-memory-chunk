#!/usr/bin/env python3
# dmc/_cli.py
# Dynamic Memory Chunk — CLI

import argparse
import ast
import json
import os
import re
import shutil
import sys
import tempfile
import urllib.request
import zipfile

# ─── Paths ────────────────────────────────────────────────────────────────────

_DMC_DIR = os.path.dirname(__file__)


def _resolve_packages_dir() -> str:
    """
    Always uses site-packages/dmc/packages/ as the install target.
    Falls back to the local packages/ folder in editable/dev mode.
    """
    import site
    for sp in site.getsitepackages():
        candidate = os.path.join(sp, "dmc", "packages")
        if os.path.isdir(os.path.join(sp, "dmc")):
            try:
                os.makedirs(candidate, exist_ok=True)
                return candidate
            except OSError:
                continue
    candidate = os.path.join(_DMC_DIR, "packages")
    os.makedirs(candidate, exist_ok=True)
    return candidate


_PACKAGES_DIR = _resolve_packages_dir()

# ─── GitHub registry ──────────────────────────────────────────────────────────

REGISTRY_USER   = "hacko223"
REGISTRY_REPO   = "dynamic-memory-chunk"
REGISTRY_BRANCH = "main"

_RAW_BASE      = f"https://raw.githubusercontent.com/{REGISTRY_USER}/{REGISTRY_REPO}/{REGISTRY_BRANCH}"
_API_BASE      = f"https://api.github.com/repos/{REGISTRY_USER}/{REGISTRY_REPO}/contents"
_PACKAGES_JSON = f"{_RAW_BASE}/DMC-registry/Packages.json"
_ZIP_BASE      = f"{_RAW_BASE}/DMC-registry"


# ─── GitHub helpers ───────────────────────────────────────────────────────────

def _github_list() -> list[dict]:
    """Reads DMC-registry/Packages.json and returns the package list."""
    req = urllib.request.Request(
        _PACKAGES_JSON,
        headers={"User-Agent": "dmc-package-manager"},
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        _die(f"Could not read Packages.json (HTTP {e.code}).")
    except Exception as e:
        _die(f"Could not connect to registry: {e}")


def _github_info(package: str) -> dict:
    """
    Looks inside source-code/<package>/ for any .json file and returns its contents.
    Does not depend on the json filename.
    """
    url = f"{_API_BASE}/source-code/{package}"
    req = urllib.request.Request(url, headers={"User-Agent": "dmc-package-manager"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            items = json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            _die(f"Folder '{package}' not found in source-code/.")
        _die(f"HTTP {e.code} while accessing source-code/{package}/.")
    except Exception as e:
        _die(f"Could not access source-code/{package}/: {e}")

    json_files = [
        item for item in items
        if isinstance(item, dict)
        and item.get("name", "").endswith(".json")
        and item.get("type") == "file"
    ]
    if not json_files:
        _die(f"No .json found in source-code/{package}/.")

    json_url = json_files[0]["download_url"]
    req = urllib.request.Request(json_url, headers={"User-Agent": "dmc-package-manager"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        _die(f"Error reading json for '{package}': {e}")


def _github_download(package: str, dest: str) -> None:
    url = f"{_ZIP_BASE}/{package}.zip"
    print(f"  → Downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "dmc-package-manager"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r, open(dest, "wb") as f:
            shutil.copyfileobj(r, f)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            _die(f"Package '{package}' not found in DMC-registry/.\n"
                 f"      Run 'dmc list' to see available packages.")
        _die(f"HTTP {e.code} while downloading '{package}'.")
    except Exception as e:
        _die(f"Download error: {e}")


# ─── Utilities ────────────────────────────────────────────────────────────────

def _die(msg: str) -> None:
    print(f"[dmc] {msg}")
    sys.exit(1)


def _ok(msg: str) -> None:
    print(f"[dmc] ✓ {msg}")


def _find_package_root(tmp: str, name: str) -> str:
    """
    Finds the folder containing __init__.py inside the extracted zip.
    Prefers an exact name match; falls back to the first one found.
    """
    best = ""
    for root, dirs, files in os.walk(tmp):
        if "__init__.py" not in files:
            continue
        if os.path.basename(root) == name:
            return root
        if not best:
            best = root
    return best


def _get_modules(pkg_dir: str) -> list[str]:
    """Returns the names of .py modules in the package (excluding __init__)."""
    return sorted(
        f[:-3]
        for f in os.listdir(pkg_dir)
        if f.endswith(".py") and f != "__init__.py"
    )


def _build_dependency_map(pkg_dir: str) -> dict[str, set[str]]:
    """
    For each module, returns the set of other modules in the same package
    that import it (i.e. who depends on it).

    dep_map["writer"] = {"smart", "mirror"}  →  smart and mirror import writer
    """
    modules = _get_modules(pkg_dir)
    imports_of: dict[str, set[str]] = {m: set() for m in modules}

    for mod in modules:
        path = os.path.join(pkg_dir, f"{mod}.py")
        try:
            source = open(path, encoding="utf-8").read()
            tree   = ast.parse(source)
        except Exception:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level and node.level > 0:
                if node.module:
                    dep = node.module.split(".")[0]
                    if dep in imports_of:
                        imports_of[mod].add(dep)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    dep = alias.name.split(".")[0]
                    if dep in imports_of:
                        imports_of[mod].add(dep)

    # Invert: who imports each module
    dep_map: dict[str, set[str]] = {m: set() for m in modules}
    for mod, deps in imports_of.items():
        for dep in deps:
            dep_map[dep].add(mod)

    return dep_map


# ─── Commands ─────────────────────────────────────────────────────────────────

def cmd_install(args):
    name       = args.package
    target_dir = os.path.join(_PACKAGES_DIR, name)

    if os.path.exists(target_dir):
        _die(f"'{name}' is already installed at:\n      {target_dir}\n"
             f"      Run 'dmc remove --package \"{name}\"' first to reinstall.")

    os.makedirs(_PACKAGES_DIR, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = os.path.join(tmp, f"{name}.zip")
        _github_download(name, zip_path)

        print("  → Extracting ...")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp)

        src = _find_package_root(tmp, name)
        if not src:
            _die("No valid Python package found inside the zip.\n"
                 "      The zip must contain a folder with __init__.py.")

        shutil.copytree(src, target_dir)

    _ok(f"'{name}' installed at:\n      {target_dir}")
    print(f"\n  Usage:\n"
          f"    import dmc\n"
          f"    dmc.{name}.<class>()\n"
          f"\n"
          f"    # or direct import\n"
          f"    from dmc import {name}")


def cmd_list(args):
    installed: list[str] = []
    if os.path.isdir(_PACKAGES_DIR):
        installed = sorted(
            d for d in os.listdir(_PACKAGES_DIR)
            if os.path.isdir(os.path.join(_PACKAGES_DIR, d))
            and os.path.exists(os.path.join(_PACKAGES_DIR, d, "__init__.py"))
        )

    print(f"Packages directory: {_PACKAGES_DIR}\n")
    print("Installed:")
    if not installed:
        print("  (none)")
    else:
        for name in installed:
            mods = _get_modules(os.path.join(_PACKAGES_DIR, name))
            print(f"  • {name}  [{', '.join(mods)}]")

    print()
    print(f"Available in {REGISTRY_USER}/{REGISTRY_REPO}:")
    try:
        packages = _github_list()
    except SystemExit:
        return
    if not packages:
        print("  (registry empty or no connection)")
        return
    for pkg in packages:
        if isinstance(pkg, dict):
            name = pkg.get("name", "?")
            desc = pkg.get("description", "")
        else:
            name = pkg
            desc = ""
        status = "✓" if name in installed else " "
        line   = f"  {status} {name}"
        if desc:
            line += f"  —  {desc}"
        print(line)


def cmd_remove(args):
    name   = args.package
    target = os.path.join(_PACKAGES_DIR, name)
    if not os.path.exists(target):
        _die(f"'{name}' is not installed.")
    shutil.rmtree(target)
    _ok(f"'{name}' removed.")


def cmd_search(args):
    term = args.term.lower()
    print(f"Searching '{args.term}' in {REGISTRY_USER}/{REGISTRY_REPO} ...")
    try:
        available = _github_list()
    except SystemExit:
        return
    results = [
        p for p in available
        if term in (p.get("name", "") if isinstance(p, dict) else p).lower()
    ]
    if not results:
        print(f"  No results for '{args.term}'.")
    else:
        for pkg in results:
            if isinstance(pkg, dict):
                name = pkg.get("name", "?")
                desc = pkg.get("description", "")
            else:
                name = pkg
                desc = ""
            installed = os.path.exists(os.path.join(_PACKAGES_DIR, name))
            status = "✓ installed" if installed else "  available"
            print(f"  {status}  {name}" + (f"  —  {desc}" if desc else ""))


def cmd_info(args):
    name = args.package

    # If installed, look for any .json in the local package folder first
    local_dir = os.path.join(_PACKAGES_DIR, name)
    if os.path.exists(local_dir):
        json_files = [f for f in os.listdir(local_dir) if f.endswith(".json")]
        if json_files:
            try:
                info = json.loads(open(os.path.join(local_dir, json_files[0]), encoding="utf-8").read())
                _print_info(name, info, source="local")
                return
            except Exception:
                pass

    print(f"  → Looking up source-code/{name}/ ...")
    try:
        info = _github_info(name)
    except SystemExit:
        return
    _print_info(name, info, source="registry")


def _print_info(name: str, info: dict, source: str) -> None:
    print(f"\nPackage info '{name}' [{source}]:\n")
    fields = [
        ("Name",        info.get("name",        name)),
        ("Version",     info.get("version",      "?")),
        ("Description", info.get("description",  "—")),
        ("Author",      info.get("author",       "—")),
        ("License",     info.get("license",      "—")),
    ]
    for label, value in fields:
        if value and value != "—":
            print(f"  {label:<12} {value}")
    known = {"name", "version", "description", "author", "license"}
    for key, value in info.items():
        if key not in known:
            print(f"  {key:<12} {value}")
    print()


def cmd_edit(args):
    name    = args.package
    pkg_dir = os.path.join(_PACKAGES_DIR, name)

    if not os.path.exists(pkg_dir):
        _die(f"'{name}' is not installed.\n"
             f"      Run 'dmc install --package \"{name}\"' first.")

    modules = _get_modules(pkg_dir)
    if not modules:
        _die(f"'{name}' has no editable modules (only __init__.py).")

    dep_map = _build_dependency_map(pkg_dir)

    print(f"\nModules in '{name}':\n")
    print(f"  {'Module':<20} {'Used by'}")
    print(f"  {'-'*20} {'-'*30}")
    for mod in modules:
        dependents = dep_map.get(mod, set())
        used_by    = ", ".join(sorted(dependents)) if dependents else "—"
        lock       = "🔒" if dependents else "  "
        print(f"  {lock} {mod:<18} {used_by}")

    print()
    print("Modules marked with 🔒 cannot be removed because others depend on them.")
    print("Type a module name to remove it, or 'cancel' to exit.\n")

    deletable = [m for m in modules if not dep_map.get(m)]
    if not deletable:
        print("No removable modules in this package.")
        return

    while True:
        try:
            choice = input("Module > ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return

        if choice.lower() in ("cancel", "q", ""):
            print("Cancelled.")
            return

        if choice not in modules:
            print(f"  '{choice}' not found. Options: {', '.join(modules)}")
            continue

        if dep_map.get(choice):
            deps = ", ".join(sorted(dep_map[choice]))
            print(f"  🔒 Cannot remove '{choice}': required by [{deps}].")
            continue

        try:
            confirm = input(f"  Remove '{choice}.py' from '{name}'? [y/N] > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled.")
            return

        if confirm not in ("y", "yes"):
            print("  Cancelled.")
            continue

        os.remove(os.path.join(pkg_dir, f"{choice}.py"))
        _ok(f"'{choice}.py' removed from '{name}'.")

        modules   = _get_modules(pkg_dir)
        dep_map   = _build_dependency_map(pkg_dir)
        deletable = [m for m in modules if not dep_map.get(m)]

        if not modules:
            print("No modules left in the package.")
            return

        print(f"\nRemaining modules: {', '.join(modules)}")
        if not deletable:
            print("No removable modules left.")
            return
        print(f"Removable: {', '.join(deletable)}")
        print("Type another module to remove, or 'cancel' to exit.\n")


def cmd_update(args):
    all_ = getattr(args, "all", False)

    if all_:
        if not os.path.isdir(_PACKAGES_DIR):
            _die("No packages installed.")
        packages = [
            d for d in os.listdir(_PACKAGES_DIR)
            if os.path.isdir(os.path.join(_PACKAGES_DIR, d))
            and os.path.exists(os.path.join(_PACKAGES_DIR, d, "__init__.py"))
        ]
        if not packages:
            _die("No packages installed.")
        for pkg in packages:
            print(f"\nUpdating '{pkg}' ...")
            _do_reinstall(pkg)
        return

    name = getattr(args, "package", None)
    if not name:
        _die("Specify a package with --package or use --all.")
    _do_reinstall(name)


def cmd_reinstall(args):
    _do_reinstall(args.package)


def _do_reinstall(name: str) -> None:
    target = os.path.join(_PACKAGES_DIR, name)
    if os.path.exists(target):
        shutil.rmtree(target)
        print(f"  → '{name}' removed.")

    os.makedirs(_PACKAGES_DIR, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        zip_path = os.path.join(tmp, f"{name}.zip")
        _github_download(name, zip_path)
        print("  → Extracting ...")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(tmp)
        src = _find_package_root(tmp, name)
        if not src:
            _die("No valid Python package found inside the zip.")
        shutil.copytree(src, target)

    _ok(f"'{name}' reinstalled at:\n      {target}")


def cmd_freeze(args):
    if not os.path.isdir(_PACKAGES_DIR):
        return
    packages = sorted(
        d for d in os.listdir(_PACKAGES_DIR)
        if os.path.isdir(os.path.join(_PACKAGES_DIR, d))
        and os.path.exists(os.path.join(_PACKAGES_DIR, d, "__init__.py"))
    )
    if not packages:
        print("# No packages installed")
        return
    for name in packages:
        version = _read_version(name)
        print(f"{name}=={version}")


def cmd_export(args):
    if not os.path.isdir(_PACKAGES_DIR):
        _die("No packages installed.")
    packages = sorted(
        d for d in os.listdir(_PACKAGES_DIR)
        if os.path.isdir(os.path.join(_PACKAGES_DIR, d))
        and os.path.exists(os.path.join(_PACKAGES_DIR, d, "__init__.py"))
    )
    if not packages:
        _die("No packages installed.")

    out_path = getattr(args, "output", None) or "dmc-requirements.txt"
    with open(out_path, "w", encoding="utf-8") as f:
        for name in packages:
            version = _read_version(name)
            f.write(f"{name}=={version}\n")
    _ok(f"Requirements exported to: {out_path}")


def cmd_import_reqs(args):
    path = args.file
    if not os.path.exists(path):
        _die(f"File not found: {path}")
    with open(path, encoding="utf-8") as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
    if not lines:
        _die("File is empty.")
    for line in lines:
        name = line.split("==")[0].strip()
        print(f"\nInstalling '{name}' ...")
        target = os.path.join(_PACKAGES_DIR, name)
        if os.path.exists(target):
            print(f"  '{name}' is already installed, skipping.")
            continue
        _do_reinstall(name)


def cmd_download(args):
    name   = args.package
    export = getattr(args, "export", None)

    if export:
        dest_dir = os.path.expanduser(export)
        os.makedirs(dest_dir, exist_ok=True)
    else:
        dest_dir = os.getcwd()

    dest_path = os.path.join(dest_dir, f"{name}.zip")

    if os.path.exists(dest_path):
        _die(f"File already exists at: {dest_path}")

    _github_download(name, dest_path)
    _ok(f"'{name}.zip' saved to:\n      {dest_path}")


def _read_version(name: str) -> str:
    """Tries to read __version__ from the package's __init__.py."""
    init_path = os.path.join(_PACKAGES_DIR, name, "__init__.py")
    try:
        source = open(init_path, encoding="utf-8").read()
        tree   = ast.parse(source)
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Assign)
                and any(
                    isinstance(t, ast.Name) and t.id == "__version__"
                    for t in node.targets
                )
                and isinstance(node.value, ast.Constant)
            ):
                return str(node.value.value)
    except Exception:
        pass
    return "0.0.0"


# ─── Entry point ──────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="dmc",
        description="Dynamic Memory Chunk — core ecosystem for AI memory and agent packages",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  install    Download and install a package from the registry
  list       Show installed and available packages
  remove     Remove an installed package
  search     Search packages in the registry
  info       Show detailed info about a package
  edit       Remove individual modules from an installed package

  update     Update a package or all with --all
  reinstall  Remove and reinstall a package

  freeze     List installed packages as name==version
  export     Export package list to a file
  import     Install packages from a requirements file
  download   Download the .zip without installing (--export is optional)

Examples:
  dmc install --package "amcx"
  dmc list
  dmc search "memory"
  dmc info --package "amcx"
  dmc edit --package "amcx"
  dmc remove --package "amcx"
  dmc update --package "amcx"
  dmc update --all
  dmc reinstall --package "amcx"
  dmc freeze
  dmc export --output my-packages.txt
  dmc import --file dmc-requirements.txt
  dmc download --package "amcx"
  dmc download --package "amcx" --export "/home/user/downloads/"
        """,
    )

    sub = parser.add_subparsers(dest="command", metavar="command")

    p_i = sub.add_parser("install", help='Install a package  e.g. dmc install --package "amcx"')
    p_i.add_argument("--package", "-p", required=True, metavar='"name"')

    sub.add_parser("list", help="List installed and available packages")

    p_r = sub.add_parser("remove", help='Remove a package  e.g. dmc remove --package "amcx"')
    p_r.add_argument("--package", "-p", required=True, metavar='"name"')

    p_s = sub.add_parser("search", help="Search the registry  e.g. dmc search memory")
    p_s.add_argument("term", metavar="term")

    p_e = sub.add_parser("edit", help='Edit modules of a package  e.g. dmc edit --package "amcx"')
    p_e.add_argument("--package", "-p", required=True, metavar='"name"')

    p_inf = sub.add_parser("info", help='Package info  e.g. dmc info --package "amcx"')
    p_inf.add_argument("--package", "-p", required=True, metavar='"name"')

    p_u = sub.add_parser("update", help='Update a package  e.g. dmc update --package "amcx"  or  dmc update --all')
    p_u.add_argument("--package", "-p", default=None, metavar='"name"')
    p_u.add_argument("--all", "-a", action="store_true", help="Update all installed packages")

    p_ri = sub.add_parser("reinstall", help='Reinstall a package  e.g. dmc reinstall --package "amcx"')
    p_ri.add_argument("--package", "-p", required=True, metavar='"name"')

    sub.add_parser("freeze", help="List installed packages as name==version")

    p_ex = sub.add_parser("export", help="Export installed packages to a requirements file")
    p_ex.add_argument("--output", "-o", default="dmc-requirements.txt", metavar="path")

    p_im = sub.add_parser("import", help='Install from a requirements file  e.g. dmc import -f dmc-requirements.txt')
    p_im.add_argument("--file", "-f", required=True, metavar="path")

    p_dl = sub.add_parser("download", help='Download .zip without installing  e.g. dmc download --package "amcx" --export "/path/"')
    p_dl.add_argument("--package", "-p", required=True, metavar='"name"')
    p_dl.add_argument("--export", "-e", default=None, metavar="path", help="Folder to save the .zip (optional, default: current directory)")

    args = parser.parse_args()

    commands = {
        "install":   cmd_install,
        "list":      cmd_list,
        "remove":    cmd_remove,
        "search":    cmd_search,
        "edit":      cmd_edit,
        "info":      cmd_info,
        "update":    cmd_update,
        "reinstall": cmd_reinstall,
        "freeze":    cmd_freeze,
        "export":    cmd_export,
        "import":    cmd_import_reqs,
        "download":  cmd_download,
    }

    if args.command in commands:
        commands[args.command](args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
