#!/usr/bin/env bash

# 构建不可变版本镜像并推送到镜像仓库，供远端 k3s 拉取。
set -Eeuo pipefail

readonly SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly REPO_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
readonly IMAGE_OWNER="${IMAGE_OWNER:-jykqwer}"
readonly PLATFORM="${PLATFORM:-linux/amd64}"
readonly TAG="${1:-$(date +%Y%m%d-%H%M%S)}"

if [[ ! "${TAG}" =~ ^[a-z0-9][a-z0-9.-]{0,40}$ ]]; then
  echo "错误：镜像标签只能包含小写字母、数字、点和连字符，且不能超过 41 个字符。" >&2
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "错误：未安装 Docker。" >&2
  exit 1
fi

backend_image="docker.io/${IMAGE_OWNER}/saas-ai-demo-backend:${TAG}"
frontend_image="docker.io/${IMAGE_OWNER}/saas-ai-demo-frontend:${TAG}"

if docker buildx version >/dev/null 2>&1; then
  docker buildx build \
    --platform "${PLATFORM}" \
    --tag "${backend_image}" \
    --push \
    "${REPO_ROOT}/backend"

  docker buildx build \
    --platform "${PLATFORM}" \
    --tag "${frontend_image}" \
    --push \
    "${REPO_ROOT}/frontend"
else
  # 单架构服务器可能未安装 Buildx；标准构建完成后再分别推送。
  docker build --platform "${PLATFORM}" --tag "${backend_image}" "${REPO_ROOT}/backend"
  docker push "${backend_image}"
  docker build --platform "${PLATFORM}" --tag "${frontend_image}" "${REPO_ROOT}/frontend"
  docker push "${frontend_image}"
fi

echo
echo "镜像已推送："
echo "  ${backend_image}"
echo "  ${frontend_image}"
echo
echo "在远端服务器执行：IMAGE_OWNER=${IMAGE_OWNER} scripts/k3s/deploy.sh ${TAG}"
