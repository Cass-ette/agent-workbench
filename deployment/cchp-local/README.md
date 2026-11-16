# 本机 CCHP 部署

此目录提供朋友自用的 CCHP Compose 部署模板。它拉取 CCH-HQ 发布的多架构镜像，不包含 CCHP 服务端源码，也不包含任何人的上游 Key、下游 Key、数据库或 `.env`。

## 要求

- macOS 14 或更新版本
- Docker Desktop 已安装并运行
- 本目录中的 `compose.yml`、`.env.example` 和启动脚本放在同一个目录

## 首次启动

双击 `setup.command`。脚本会为这台电脑生成全新的数据库口令、Token pepper 和主密钥，保存在权限为 `600` 的 `.env` 文件，然后拉取镜像并启动 CCHP。首次使用时在浏览器打开的控制台完成自己的账户设置。

之后在 CCHP 控制台添加自己的上游供应商 Key，再创建一把下游 LLM 调用 Key。管理器中只填写下游 Key；不要把上游 Key 填入管理器或 Claude/Codex 环境变量。

管理地址为 `http://localhost:8080`，模型网关为 `http://127.0.0.1:8081/v1`。两个端口只绑定本机回环地址，不对局域网或公网开放。

## 日常启停

- 启动：在此目录运行 `docker compose up -d`
- 状态：运行 `docker compose ps --all`
- 日志：运行 `docker compose logs --tail=100 cchp`
- 停止：双击 `stop.command` 或运行 `docker compose down`

停止服务不会删除数据。不要使用 `docker compose down -v`，否则会删除本机数据库卷。

每台电脑都应独立生成 `.env`、建立自己的数据库并填自己的 API Key。不要复制你的 `.env`、Docker 数据卷或完整调用密钥给朋友。`.env` 已被仓库忽略。

容器镜像版本目前沿用官方 Compose 文件中的 `latest` 标签；正式分享安装包前，建议固定到经过验证的版本或 digest，并在升级前备份数据库。
