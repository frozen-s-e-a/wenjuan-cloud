# 部署、备份与迁移

以下命令均从仓库根目录执行，除非明确写出进入 backend。正式环境按 Linux 2 vCPU / 2 GB RAM 设计；目前没有目标机器实测结果。仅运行一个 FastAPI worker，后台重算也仅一个任务。

## 1. 原生运行与离线维护

Windows 启动入口为 `start.cmd`。以下维护命令以 PowerShell 为例；Linux/macOS 将 `..\.venv\Scripts\python.exe` 替换为 `../.venv/bin/python`。

```powershell
cd backend
# 首次完整初始化；start.cmd 会自动执行
..\.venv\Scripts\python.exe -m app.manage init
# 升级数据库（先停止网页服务）
..\.venv\Scripts\python.exe -m app.manage migrate
# 重置管理员密码（先停止网页服务，会使该管理员旧会话失效）
..\.venv\Scripts\python.exe -m app.manage admin --username admin --reset
# 一致性备份，可在线运行，目标文件必须不存在
..\.venv\Scripts\python.exe -m app.manage backup ..\backups\before-upgrade.db
# 检查完整性、外键、版本和记录数量
..\.venv\Scripts\python.exe -m app.manage verify ..\backups\before-upgrade.db
# 恢复：先停止网页服务
..\.venv\Scripts\python.exe -m app.manage restore ..\backups\before-upgrade.db --app-stopped
```

应用与离线维护命令共用进程锁；服务未停止时不能进行迁移、恢复、管理员修改和配置导入，避免替换正在使用的数据库。请勿自行删除锁文件尝试绕过正在运行的进程；锁文件存在不代表仍有进程，操作系统会在进程退出时释放锁。

备份使用 SQLite Backup API，另生成 `.db.json` 清单，包含版本、表记录数与 SHA-256，不包含原始回答。恢复会验证备份；有清单时核对 SHA-256。目标已有数据库时先生成 `data/before-restore-时间.db`，再替换；恢复完成后使全部管理会话失效，并将中断重算标记为失败。恢复只能恢复到备份时刻，之后新增数据不会自动合并。

## 2. 本机 Docker 验收

Windows 需要 Docker Desktop 的 Linux 容器模式。本机原生运行正常后，仍应使用发布镜像完成验收。

1. 将 `.env.example` 复制为 `.env`（已有 `.env` 时直接补充，不要覆盖现有密钥）。
2. 设置随机 `APP_SECRET`，至少 32 字符。可在本机执行：

```powershell
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

3. 停止原生服务，避免它与容器使用同一个数据库。需要隔离测试时，将 `DATA_DIR` 改成 `./docker-data` 并加入自己的忽略规则。
4. 构建、初始化、运行：

```powershell
docker compose -f compose.yaml -f compose.local.yaml build app
docker compose -f compose.yaml -f compose.local.yaml run --rm app python -m app.manage init
docker compose -f compose.yaml -f compose.local.yaml up -d --no-build
```

默认访问 `http://localhost:8000`。`PUBLIC_ORIGIN` 必须与浏览器使用的地址相同。生产与本机共用 `/app/data` 挂载目录，前端只使用 `/api/...` 相对地址。

在 Linux 上先准备目录权限（容器运行用户 UID/GID 为 10001）：

```bash
sudo install -d -o 10001 -g 10001 -m 750 data backups
```

Windows Docker Desktop 文件共享语义不同，请确认挂载可写。不要通过移除持久化挂载解决权限问题。

## 3. 在构建机准备发布镜像

使用已测试代码构建，给镜像打独立版本标签，生产配置不要使用 `latest`。同一次本机验收和云端部署使用同一个镜像。

```bash
docker build -t wenjuan-cloud:0.1.0 .
docker image inspect wenjuan-cloud:0.1.0 --format '{{.Id}}'
docker save -o wenjuan-cloud-0.1.0.tar wenjuan-cloud:0.1.0
```

记录 Git 提交、镜像 ID 与归档 SHA-256。在构建机 / CI 构建；2 核 2G 云服务器只导入镜像或拉取固定摘要，不在上面执行前端打包。镜像也可以推送到用户自己的镜像仓库，本项目不会自动将回答或备份上传到任何仓库。

本机与服务器架构必须一致；不一致时用 buildx 指定目标平台并在目标架构验证，不能假设 amd64 镜像可原生运行于 arm64。

## 4. 云服务器启动

准备 Docker Engine / Compose 插件、域名、DNS、80/443 入站端口及充足磁盘。服务器不需要安装 Python、Node、MySQL 或 Redis。

将发布镜像、`compose.yaml`、`compose.prod.yaml`、`deploy/Caddyfile` 与 `.env.prod.example` 传到服务器的项目目录。将示例复制为 `.env.prod`，填写真实域名、HTTPS `PUBLIC_ORIGIN`、独立随机 `APP_SECRET`，确认 `APP_IMAGE` 指向验收的镜像。

```bash
docker load -i wenjuan-cloud-0.1.0.tar
sudo install -d -o 10001 -g 10001 -m 750 data backups
chmod 600 .env.prod
# 干净生产库不创建示例题，之后从后台创建，或先按下节导入配置
docker compose --env-file .env.prod -f compose.yaml -f compose.prod.yaml run --rm app python -m app.manage init --no-sample
docker compose --env-file .env.prod -f compose.yaml -f compose.prod.yaml up -d --no-build
```

Caddy 自动处理指定域名的证书；HTTPS 能否签发取决于 DNS 和公网端口是否正确配置。只有 Caddy 公开端口，app 端口留在容器网络。`APP_ENV=production` 要求 HTTPS 来源和 Secure Cookie。

初始内存上限为 app 1024 MiB、Caddy 128 MiB，为系统和突发留余量；这只是预算，不是性能保证。记录服务器实际峰值内存、CPU、延迟、失败率后再调整。

## 5. 默认迁移：仅题目与规则

这种方式不会把本机测试回答带入生产。源端导出：

```powershell
cd backend
..\.venv\Scripts\python.exe -m app.manage export-config ..\questions.json
```

导出文件仅包含题目、提示和当前规则，仍可能包含用户自定义的文字，按业务资料保管，不自动提交 Git。把文件传到服务器 `backups/questions.json`，目标题目库必须为空：

```bash
docker compose --env-file .env.prod -f compose.yaml -f compose.prod.yaml stop app
docker compose --env-file .env.prod -f compose.yaml -f compose.prod.yaml run --rm app python -m app.manage import-config /app/backups/questions.json
docker compose --env-file .env.prod -f compose.yaml -f compose.prod.yaml run --rm app python -m app.manage init --no-sample
docker compose --env-file .env.prod -f compose.yaml -f compose.prod.yaml up -d --no-build
```

导入轮次默认全部暂停。管理员登录后核对并开启目标轮次。目标已有题目时导入会拒绝，不清空或覆盖现有数据。

## 6. 完整迁移：保留已有回答

1. 停止源端服务，等在途写入与重算结束；源端保持停止，避免双端继续收集。
2. 用 `backup` 生成快照，保存其 `.json` 清单和已验证的镜像版本。
3. 传输数据库与清单到服务器 `backups`，不要上传 GitHub。保留源 `.env` 的安全副本；生产一般使用新的密钥。浏览器去重 Cookie 不保证跨域或跨密钥延续。
4. 目标服务停止后恢复、迁移，再启动：

```bash
docker compose --env-file .env.prod -f compose.yaml -f compose.prod.yaml stop app
docker compose --env-file .env.prod -f compose.yaml -f compose.prod.yaml run --rm app python -m app.manage restore /app/backups/transfer.db --app-stopped
docker compose --env-file .env.prod -f compose.yaml -f compose.prod.yaml run --rm app python -m app.manage migrate
docker compose --env-file .env.prod -f compose.yaml -f compose.prod.yaml up -d --no-build
```

5. 核对轮次、有效回答数、词语排名与原文，测试提交；重启容器后再次核对。

此版恢复工具仅接受迁移版本 `0001`。后续数据库升级必须同步提供新兼容策略；不能用此限制以外的备份强行替换。

## 7. 升级、回滚与日常备份

升级前停止 app，备份数据库，记下镜像 ID、Git 提交和迁移版本。导入新镜像、更改 `APP_IMAGE` 后，用一次性维护命令执行迁移，成功后 `up -d --no-build`。迁移失败时保持服务停止。

回滚先判断旧代码与现有数据库是否兼容；不兼容时恢复升级前快照和旧镜像。若升级后已重新收集回答，先保留新数据并制定合并方式，不能直接恢复旧备份丢弃新增回答。

在线备份示例：

```bash
docker compose --env-file .env.prod -f compose.yaml -f compose.prod.yaml exec app python -m app.manage backup /app/backups/backup-20260910.db
```

用操作系统计划任务定期运行，每次使用不同文件名。当前程序提供备份命令，不内置调度、备份自动轮换或异地上传；需要自行安排并保留一份异地副本。Docker 日志已配置轮换。勿执行会清空业务目录的清理命令；站点应用源码重新下载不能恢复业务回答。
