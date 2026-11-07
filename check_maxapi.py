"""Показывает, как в вашей версии maxapi называются классы для отправки файла по токену.
Запуск:  python check_maxapi.py     Результат скопируйте и пришлите."""
import importlib
import pkgutil

import maxapi

print("maxapi", getattr(maxapi, "__version__", "?"))
words = ("ttach", "pload", "hoto", "mage", "ayload", "Media")
for module_info in pkgutil.walk_packages(maxapi.__path__, "maxapi."):
    name = module_info.name
    if not any(x in name for x in ("types", "enums", "utils")):
        continue
    try:
        module = importlib.import_module(name)
    except Exception:
        continue
    found = [n for n in dir(module) if any(w in n for w in words) and not n.startswith("_")]
    if found:
        print(name, "→", ", ".join(found))
