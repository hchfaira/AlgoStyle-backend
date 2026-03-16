#!/bin/bash
# ─────────────────────────────────────────────────────────────
# AlgoStyle Backend — Local development startup script
# Usage: bash start_backend.sh
# ─────────────────────────────────────────────────────────────
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "🐘  Starting PostgreSQL (Docker)..."
if docker ps | grep -q algostyle_pg; then
  echo "    Already running — OK"
else
  docker start algostyle_pg 2>/dev/null || \
    docker run -d \
      --name algostyle_pg \
      -e POSTGRES_USER=postgres \
      -e POSTGRES_PASSWORD=postgres \
      -e POSTGRES_DB=algostyle \
      -p 5432:5432 \
      postgres:16-alpine
fi

echo "⏳  Waiting for PostgreSQL to be ready..."
until docker exec algostyle_pg pg_isready -U postgres > /dev/null 2>&1; do
  printf "."
  sleep 1
done
echo " ✅"

# Create .env if it doesn't exist
if [ ! -f "$SCRIPT_DIR/.env" ]; then
  echo "📝  Creating .env from example..."
  cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
fi

# Create venv if it doesn't exist
if [ ! -d "$SCRIPT_DIR/.venv" ]; then
  echo "🐍  Creating Python virtual environment..."
  python3 -m venv "$SCRIPT_DIR/.venv"
fi

echo "📦  Installing / checking dependencies..."
"$SCRIPT_DIR/.venv/bin/pip" install -q -r "$SCRIPT_DIR/requirements.txt"

echo ""
echo "🚀  Starting FastAPI on http://0.0.0.0:8000"
echo "    Swagger UI → http://localhost:8000/docs"
echo "    Press Ctrl+C to stop"
echo ""
cd "$SCRIPT_DIR"
"$SCRIPT_DIR/.venv/bin/uvicorn" main:app --host 0.0.0.0 --port 8000 --reload
