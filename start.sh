#!/bin/bash
# 德州扑克服务器启动脚本

# 默认配置 (可通过环境变量覆盖)
export REDIS_HOST=${REDIS_HOST:-"127.0.0.1"}
export REDIS_PORT=${REDIS_PORT:-6379}
export SERVER_HOST=${SERVER_HOST:-"0.0.0.0"}
export SERVER_PORT=${SERVER_PORT:-8888}
export ADMIN_USERNAME=${ADMIN_USERNAME:-"admin"}
export ADMIN_PASSWORD=${ADMIN_PASSWORD:-"admin123"}

echo "============================================"
echo "  Texas Hold'em Poker Server"
echo "============================================"
echo "  Server:  http://${SERVER_HOST}:${SERVER_PORT}"
echo "  Admin:   http://${SERVER_HOST}:${SERVER_PORT}/admin"
echo "  Redis:   ${REDIS_HOST}:${REDIS_PORT}"
echo "============================================"

python -m uvicorn main:app --host $SERVER_HOST --port $SERVER_PORT --reload
