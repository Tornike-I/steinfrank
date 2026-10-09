# Образ песочницы для Docker-режима. Собирается автоматически при первом запуске
# (sandbox.py -> _ensure_image). В образе только то, что разрешено сгенерированному коду.
FROM python:3.12-slim
RUN pip install --no-cache-dir pypdf reportlab openpyxl
WORKDIR /work
