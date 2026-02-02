#!/bin/bash
set -e

echo "=========================================="
echo "  WonderWraps Backend Setup for Mac M1"
echo "=========================================="
echo ""

# Кольори
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Перевірка Docker
if ! command -v docker &> /dev/null; then
    echo -e "${RED}❌ Docker не встановлено!${NC}"
    echo "   Встанови Docker Desktop: https://www.docker.com/products/docker-desktop/"
    exit 1
fi

echo -e "${GREEN}✅ Docker знайдено${NC}"

# Перевірка чи Docker запущено
if ! docker info &> /dev/null; then
    echo -e "${RED}❌ Docker не запущено!${NC}"
    echo "   Запусти Docker Desktop"
    exit 1
fi

echo -e "${GREEN}✅ Docker запущено${NC}"
echo ""

# ========================================
# 1. Запуск PostgreSQL
# ========================================
echo "📦 Налаштування PostgreSQL..."

if docker ps -a --format '{{.Names}}' | grep -q '^wonderwraps-postgres$'; then
    if docker ps --format '{{.Names}}' | grep -q '^wonderwraps-postgres$'; then
        echo -e "${GREEN}✅ PostgreSQL вже запущено${NC}"
    else
        echo "   Запускаю існуючий контейнер..."
        docker start wonderwraps-postgres
        echo -e "${GREEN}✅ PostgreSQL запущено${NC}"
    fi
else
    echo "   Створюю новий контейнер..."
    docker run -d \
        --name wonderwraps-postgres \
        -p 5433:5432 \
        -e POSTGRES_USER=user \
        -e POSTGRES_PASSWORD=password \
        -e POSTGRES_DB=dbname \
        -v wonderwraps-postgres-data:/var/lib/postgresql/data \
        postgres:15
    echo -e "${GREEN}✅ PostgreSQL створено та запущено${NC}"
fi

# ========================================
# 2. Запуск Redis
# ========================================
echo ""
echo "📦 Налаштування Redis..."

if docker ps -a --format '{{.Names}}' | grep -q '^wonderwraps-redis$'; then
    if docker ps --format '{{.Names}}' | grep -q '^wonderwraps-redis$'; then
        echo -e "${GREEN}✅ Redis вже запущено${NC}"
    else
        echo "   Запускаю існуючий контейнер..."
        docker start wonderwraps-redis
        echo -e "${GREEN}✅ Redis запущено${NC}"
    fi
else
    echo "   Створюю новий контейнер..."
    docker run -d \
        --name wonderwraps-redis \
        -p 6379:6379 \
        redis:7
    echo -e "${GREEN}✅ Redis створено та запущено${NC}"
fi

# ========================================
# 3. Запуск MinIO
# ========================================
echo ""
echo "📦 Налаштування MinIO (S3)..."

if docker ps -a --format '{{.Names}}' | grep -q '^wonderwraps-minio$'; then
    if docker ps --format '{{.Names}}' | grep -q '^wonderwraps-minio$'; then
        echo -e "${GREEN}✅ MinIO вже запущено${NC}"
    else
        echo "   Запускаю існуючий контейнер..."
        docker start wonderwraps-minio
        echo -e "${GREEN}✅ MinIO запущено${NC}"
    fi
else
    echo "   Створюю новий контейнер..."
    docker run -d \
        --name wonderwraps-minio \
        -p 9000:9000 \
        -p 9001:9001 \
        -e MINIO_ROOT_USER=minioadmin \
        -e MINIO_ROOT_PASSWORD=minioadmin \
        -v wonderwraps-minio-data:/data \
        minio/minio server /data --console-address ":9001"
    echo -e "${GREEN}✅ MinIO створено та запущено${NC}"
fi

# Чекаємо поки сервіси запустяться
echo ""
echo "⏳ Чекаю поки сервіси запустяться..."
sleep 5

# ========================================
# 4. Створення bucket в MinIO
# ========================================
echo ""
echo "📦 Створення S3 bucket..."

# Встановлюємо mc (MinIO Client) якщо потрібно
if ! command -v mc &> /dev/null; then
    echo "   Встановлюю MinIO Client..."
    brew install minio/stable/mc 2>/dev/null || {
        # Fallback - використовуємо curl
        echo "   Створюю bucket через API..."
        sleep 3
        # Створюємо bucket через AWS CLI або просто інформуємо користувача
        echo -e "${YELLOW}⚠️  Створи bucket вручну:${NC}"
        echo "   1. Відкрий http://localhost:9001"
        echo "   2. Залогінься: minioadmin / minioadmin"
        echo "   3. Створи bucket: wonderwraps"
    }
fi

if command -v mc &> /dev/null; then
    mc alias set wonderwraps-local http://localhost:9000 minioadmin minioadmin 2>/dev/null || true
    mc mb wonderwraps-local/wonderwraps 2>/dev/null || echo "   Bucket вже існує"
    echo -e "${GREEN}✅ Bucket 'wonderwraps' готовий${NC}"
fi

# ========================================
# 5. Перевірка сервісів
# ========================================
echo ""
echo "=========================================="
echo "  Перевірка сервісів"
echo "=========================================="

# PostgreSQL
if docker exec wonderwraps-postgres pg_isready -U user -d dbname &> /dev/null; then
    echo -e "${GREEN}✅ PostgreSQL: localhost:5433${NC}"
else
    echo -e "${RED}❌ PostgreSQL не відповідає${NC}"
fi

# Redis
if docker exec wonderwraps-redis redis-cli ping 2>/dev/null | grep -q "PONG"; then
    echo -e "${GREEN}✅ Redis: localhost:6379${NC}"
else
    echo -e "${RED}❌ Redis не відповідає${NC}"
fi

# MinIO
if curl -s http://localhost:9000/minio/health/live &> /dev/null; then
    echo -e "${GREEN}✅ MinIO: localhost:9000 (console: localhost:9001)${NC}"
else
    echo -e "${YELLOW}⚠️  MinIO ще запускається...${NC}"
fi

# ========================================
# 6. Інструкції
# ========================================
echo ""
echo "=========================================="
echo "  Наступні кроки"
echo "=========================================="
echo ""
echo "1. Запусти ComfyUI в Google Colab:"
echo "   - Відкрий colab_comfyui_complete.ipynb"
echo "   - Скопіюй ngrok URL"
echo ""
echo "2. Оновити COMFY_BASE_URL в backend/.env:"
echo "   COMFY_BASE_URL=https://xxx.ngrok.io"
echo ""
echo "3. Встановити Python залежності:"
echo "   cd backend && pip install -r requirements.txt"
echo ""
echo "4. Запустити бекенд:"
echo "   cd backend && python -m app.main"
echo ""
echo "5. В окремому терміналі запустити Celery:"
echo "   cd backend && celery -A app.workers worker -Q gpu,render -l info"
echo ""
echo -e "${GREEN}=========================================="
echo "  Setup завершено!"
echo "==========================================${NC}"
