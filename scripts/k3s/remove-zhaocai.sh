#!/usr/bin/env bash

# 停止或永久删除旧 demo namespace；删除模式会连同 PostgreSQL PVC 一并删除。
set -Eeuo pipefail

source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/common.sh"

usage() {
  echo "用法：$0 stop | delete --confirm-delete-demo" >&2
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

require_cluster_access

if ! k get namespace "${OLD_NAMESPACE}" >/dev/null 2>&1; then
  echo "旧 namespace ${OLD_NAMESPACE} 不存在，无需处理。"
  exit 0
fi

case "$1" in
  stop)
    echo "停止 ${OLD_NAMESPACE} 中的 Deployment 和 StatefulSet，PVC 与 Secret 保留。"
    k scale deployment --all -n "${OLD_NAMESPACE}" --replicas=0
    k scale statefulset --all -n "${OLD_NAMESPACE}" --replicas=0
    k get all,pvc -n "${OLD_NAMESPACE}"
    ;;
  delete)
    if [[ "${OLD_NAMESPACE}" != "demo" || "${2:-}" != "--confirm-delete-demo" ]]; then
      echo "错误：删除会永久移除 demo namespace 及其中的数据库 PVC。" >&2
      usage
      exit 1
    fi
    echo "即将永久删除 demo namespace，当前资源如下："
    k get all,ingress,pvc,secret -n demo
    k delete namespace demo --wait=true
    # 旧项目早期清单还在 default namespace 创建过一组无 namespace 的 nginx 资源。
    k delete ingress nginx-ingress -n default --ignore-not-found
    k delete deployment nginx -n default --ignore-not-found
    k delete service nginx-service -n default --ignore-not-found
    echo "demo namespace 和旧 default/nginx 资源已删除；集群级 Traefik、cert-manager 和 ClusterIssuer 未被删除。"
    ;;
  *)
    usage
    exit 1
    ;;
esac
