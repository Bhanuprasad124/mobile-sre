FROM node:22-alpine AS frontend-build
WORKDIR /ui
COPY frontend/package.json ./
RUN npm install
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim
WORKDIR /app
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./backend/
COPY --from=frontend-build /ui/dist ./backend/static/
COPY frontend/public/alerts.html ./backend/static/alerts.html
EXPOSE 8080
CMD ["uvicorn", "backend.alerts_app:app", "--host", "0.0.0.0", "--port", "8080"]
