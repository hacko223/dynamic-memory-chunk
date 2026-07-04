# Dynamic Memory Chunk (dmc)

DMC is a dynamic library that lets you install specialized packages based on your needs as a developer. Every package in the DMC ecosystem follows the same philosophy of [amcx](https://github.com/hacko223/adaptive-memory-chunk-eXtended) a Python library for adaptive memory chunking with compression and recovery.

**Author:** hacko223  
**License:** LGPL-3.0  See the [LICENSE](https://github.com/hacko223/dynamic-memory-chunk/blob/4d9b5226d4a4f7d098e7d8fe1f1aae49791e5814/LICENSE) and [LICENSE.LESSER](https://github.com/hacko223/dynamic-memory-chunk/blob/4d9b5226d4a4f7d098e7d8fe1f1aae49791e5814/LICENSE.LESSER) file for details 

**Repo:** https://github.com/hacko223/dynamic-memory-chunk

---
### *read [wiki](https://github.com/hacko223/dynamic-memory-chunk/wiki) for more info*
---

## Installation

```bash
pip install dmc
```

---

## How it works

DMC downloads packages directly from GitHub and integrates them into `import dmc`. Packages are only loaded into memory when you actually use them — if you have 10 packages installed and only use one, the other 9 are never loaded.

```python
import dmc

# Loads only when accessed
dmc.amcx.SmartMemory("chat.amcx")

# Or import directly
from dmc import amcx
amcx.SmartMemory("chat.amcx")
```

---

## Commands

```bash
dmc install --package "name"    # install a package
dmc list                         # installed and available packages
dmc info --package "name"       # package details
dmc search keyword               # search the registry
dmc update --package "name"     # update to latest
dmc update --all                 # update all
dmc reinstall --package "name"  # remove and reinstall
dmc remove --package "name"     # remove a package
dmc edit --package "name"       # remove individual modules
dmc freeze                       # list installed as name==version
dmc export --output file.txt     # export to a file
dmc import --file file.txt       # install from a file
dmc download --package "name"   # download .zip without installing
```
