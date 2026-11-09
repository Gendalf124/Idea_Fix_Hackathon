import sys
from importlib.metadata import PackageNotFoundError, version

PACKAGES = ["maxapi", "openai", "httpx", "python-dotenv", "Pillow"]  # библиотеки, которые мы используем напрямую

lines = []
for name in PACKAGES:
    try:
        lines.append(f"{name}=={version(name)}")
    except PackageNotFoundError:
        sys.exit(f"Не найдена библиотека {name}. Запустите скрипт в том же окружении, где работает бот.")

with open("requirements.txt", "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")

print("Готово! requirements.txt:")
print("\n".join(lines))
print(f"\nУ вас Python {sys.version.split()[0]}, а в Docker образ python:3.12-slim. Если у вас другая ветка (например 3.13), в Dockerfile поменяйте 3.12 на свою.")