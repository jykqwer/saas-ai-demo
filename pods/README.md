# k3s 生产部署

本目录面向 `saas-ai-demo` namespace。公网继续使用 `zhaocaidog.space`，复用远端
k3s 已安装的 Traefik、cert-manager 和 `letsencrypt-prod` ClusterIssuer，但应用、
PostgreSQL、TLS Secret 和 Traefik Middleware 都在新 namespace 中独立创建。

发布流程分为四步：构建镜像、创建 Secret、部署并预签发证书、切换公网入口。旧
`demo` namespace 只有在新应用验证成功后才停止或删除。

## 前置条件

- 构建机器已安装 Docker 并登录 Docker Hub；有 Buildx 时自动使用，没有时回退到标准构建。
- 远端服务器已安装 k3s、Traefik 和 cert-manager。
- `zhaocaidog.space` 与 `www.zhaocaidog.space` 仍解析到该服务器。
- 集群中存在并可用的 `letsencrypt-prod` ClusterIssuer。
- 远端服务器已检出本仓库的 `agent/saas-ai-demo` 分支。

脚本在 k3s 服务器上默认使用 `sudo k3s kubectl`。如果使用普通 kubeconfig，可在命令前设置：

```bash
export KUBECTL=kubectl
```

## 1. 构建和推送镜像

在开发机仓库根目录执行：

```bash
docker login
scripts/build-images.sh
```

脚本默认构建 `linux/amd64` 镜像，并生成不可变时间标签，例如
`20260803-231500`。如果服务器不是 amd64：

```bash
PLATFORM=linux/arm64 scripts/build-images.sh
```

也可显式指定标签和镜像仓库用户：

```bash
IMAGE_OWNER=jykqwer scripts/build-images.sh 20260803-231500
```

记下脚本输出的镜像标签，远端部署必须使用同一标签。

## 2. 创建 namespace 和 Secret

SSH 登录远端服务器，进入仓库根目录：

```bash
git fetch origin
git checkout agent/saas-ai-demo
git pull --ff-only
scripts/k3s/create-secrets.sh
```

脚本会：

- 创建 `saas-ai-demo` namespace；
- 生成只含十六进制字符的 PostgreSQL 密码；
- 创建 `postgres-credentials`；
- 交互读取 LLM、Tavily 和初始管理员密码；
- 避免重复运行时自动轮换已有数据库密码。

LLM API Key 可以留空，此时应用进入演示模式。初始管理员用户名在 Deployment 中固定为
`demo-admin`，密码至少 8 位。生产环境应使用强随机密码。

## 3. 部署并预签发 TLS 证书

使用构建脚本输出的标签：

```bash
scripts/k3s/deploy.sh 20260803-231500
```

部署脚本会按以下顺序执行：

1. 检查两个 Secret 和 `letsencrypt-prod`；
2. 创建 PostgreSQL，并等待 StatefulSet 就绪；
3. 使用本次后端镜像创建唯一名称的 Alembic migration Job；
4. 迁移成功后发布后端和前端；
5. 在新 namespace 中预签发包含根域名与 `www` 域名的 TLS 证书；
6. 保留旧 zhaocai Ingress，不自动切换公网流量。

每个镜像标签只能对应一份不可变代码。不要覆盖已推送标签；数据库迁移失败时也不要部署
应用，应先查看脚本打印的 Job 日志并构建新标签。

切流前可在服务器上验证：

```bash
sudo k3s kubectl port-forward \
  -n saas-ai-demo service/frontend-service 8080:80
```

## 4. 切换公网入口

确认新 Pod、数据库迁移和证书均正常后：

```bash
scripts/k3s/cutover.sh
```

脚本删除旧 `demo` namespace 中的 `demo-ingress`、`www-redirect`，并清理旧项目曾在
`default` namespace 创建的同域名 `nginx-ingress`，随后创建新 namespace 的 HTTPS
Middleware、主站 Ingress 和 `www` 跳转。Traefik、cert-manager、ClusterIssuer 和旧数据库
不会在这一步删除。

验证公网：

```bash
curl -I https://zhaocaidog.space
curl -fsS https://zhaocaidog.space/api/v1/health/ready
```

浏览器还应验证登录、管理员审批、聊天流式响应和刷新后的会话持久化。

## 5. 停止或删除旧 zhaocai

仅停止计算资源，保留旧 Secret 与 PostgreSQL PVC 以便回滚：

```bash
scripts/k3s/remove-zhaocai.sh stop
```

确认旧数据不再需要后，永久删除整个 `demo` namespace：

```bash
scripts/k3s/remove-zhaocai.sh delete --confirm-delete-demo
```

删除 namespace 会永久删除旧 PostgreSQL PVC，并删除旧项目残留在 `default` namespace 的
`nginx` Deployment、Service 和 Ingress。该脚本不会删除集群级 Traefik、cert-manager 或
`letsencrypt-prod`。不要应用删除
`../zhaocai/pods/letsencrypt.yaml`，否则会移除新应用仍在使用的 ClusterIssuer。

## 发布新版本

后续发布不需要重新创建数据库 Secret，也不需要重新切换 Ingress：

```bash
# 开发机
scripts/build-images.sh

# 远端服务器，使用上一步输出的新标签
scripts/k3s/deploy.sh <new-tag>
```

部署脚本会创建新的 migration Job 并滚动更新 Deployment。TLS Certificate 已存在时会
保持并由 cert-manager 自动续期。

## 清单说明和限制

- `certificate.yaml`：使用现有 ClusterIssuer，在新 namespace 生成 TLS Secret。
- `ingress.yaml`：根域名入口，`/api` 直达后端，其余路径进入前端。
- `www-redirect.yaml`：把 `www.zhaocaidog.space` 跳转到根域名。
- `backend-migration.yaml`：模板清单；由部署脚本替换镜像标签和 Job 名称。
- `postgres.yaml`：单副本 PostgreSQL 和 5 Gi PVC，无高可用或自动备份。
- 在线修改的知识库文件位于后端容器文件系统，Pod 重建后不会保留；重要知识文档应进入
  镜像或另行挂载持久卷。

真实 LLM Key、数据库密码和管理员密码不得写入清单或提交到 Git。正式业务使用前还应配置
数据库备份、恢复演练、监控告警和 Secret 静态加密。
