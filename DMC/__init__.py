# Dynamic Memory Chunk (dmc)
# Core ecosystem for AI memory and agent packages
# https://github.com/hacko223/dynamic-memory-chunk

import importlib
import sys
import os

__version__     = "0.3.2"
__author__      = "hacko223"
__description__ = "Dynamic Memory Chunk — core ecosystem for AI memory chunk"

_DMC_DIR      = os.path.dirname(__file__)
_PACKAGES_DIR = os.path.join(_DMC_DIR, "packages")

# Registry of available packages (name -> path) — populated on first access
_available: dict[str, str] = {}


def _scan_packages() -> None:
    """Scans packages/ and registers available packages without loading them."""
    if not os.path.isdir(_PACKAGES_DIR):
        return
    if _PACKAGES_DIR not in sys.path:
        sys.path.insert(0, _PACKAGES_DIR)
    for name in os.listdir(_PACKAGES_DIR):
        pkg_path = os.path.join(_PACKAGES_DIR, name)
        if not os.path.isdir(pkg_path):
            continue
        if not os.path.exists(os.path.join(pkg_path, "__init__.py")):
            continue
        _available[name] = pkg_path


def _load(name: str):
    """Loads a package and caches it as an attribute of this module."""
    try:
        module = importlib.import_module(name)
        setattr(sys.modules[__name__], name, module)
        sys.modules[f"dmc.{name}"] = module
        return module
    except Exception as e:
        raise ImportError(f"[dmc] Could not load package '{name}': {e}") from e


def __getattr__(name: str):
    """
    Lazy loader — triggered by dmc.name access.
    Only loads the requested package, leaving all others untouched.
    """
    if name in _available:
        return _load(name)
    raise AttributeError(f"[dmc] Package '{name}' is not installed. "
                         f"Run: dmc install --package \"{name}\"")


def __dir__():
    """
    Exposes available packages so 'from dmc import name' works correctly.
    """
    base = list(globals().keys())
    return base + [k for k in _available if k not in base]


# Scan on import — fast, just reads directory names
_scan_packages()
