# 可选：自建 CCHP 网关

Agent 工作台不依赖 CCHP；任何兼容 Anthropic Messages / OpenAI Responses 的网关都可以。这一页只给想自己跑一套 CCHP 的人一份参考：固定的 Compose 模板和一键脚本。它拉取 CCH-HQ 发布的多架构镜像，不包含 CCHP 服务端源码，也不包含任何人的上游 Key、下游 Key、数据库或 `.env`。

## 要求

- macOS 14 或更新版本
- [Docker Desktop for Mac](https://www.docker.com/products/docker-desktop/) 已安装并运行
- 本目录中的 `compose.yml`、`.env.example` 和启动脚本放在同一个目录

## 首次启动

双击 `setup.command`。脚本会为这台电脑生成全新的数据库口令、Token pepper 和主密钥，保存在权限为 `600` 的 `.env` 文件，然后从 CCHP 官方 GHCR 拉取固定版本的多架构镜像并启动服务。首次使用时在浏览器打开的控制台完成自己的账户设置。镜像不会预先塞入 ZIP，因此首次启动需要网络连接并下载约数百 MB 的镜像。

之后在 CCHP 控制台添加自己的上游供应商 Key，再创建一把下游 LLM 调用 Key。管理器中只填写下游 Key；不要把上游 Key 填入管理器或 Claude/Codex 环境变量。

推荐按这个顺序操作：

1. 打开 `setup.command` 显示的管理地址（默认 `http://localhost:8080`），完成本机 CCHP 的首次管理员初始化。
2. 在供应商管理中创建供应商、配置端点，并把自己的上游 API Key 保存在 CCHP 中。
3. 在供应商分组和路由中限制模型范围；免费分组不要意外包含付费路由。
4. 在下游 LLM 密钥页面创建一把客户端调用 Key，按需要绑定分组。
5. 打开 Agent 工作台，新建调用配置：网关填 `http://127.0.0.1:<网关端口>`，Key 填那把下游调用 Key；选择模型和工作目录后启动 Claude 或 Codex。

官方文档：

- [Docker Compose 安装](https://cch-plus.com/en/docs/installation/docker-compose)
- [供应商端点](https://cch-plus.com/en/docs/providers/endpoints)
- [供应商密钥](https://cch-plus.com/en/docs/providers/keys)
- [供应商分组](https://cch-plus.com/en/docs/providers/groups)
- [下游 LLM 密钥](https://cch-plus.com/en/docs/guide/llm-keys)
- [Claude Code 客户端](https://cch-plus.com/en/docs/guide/claude-code)
- [Codex 客户端](https://cch-plus.com/en/docs/guide/codex)

默认管理地址为 `http://localhost:8080`，模型网关为 `http://127.0.0.1:8081/v1`。如果这两个宿主端口被占用，`setup.command` 会自动选择可用端口并写入 `.env`；容器内部端口仍固定为 8080/8081。两个宿主端口只绑定本机回环地址，不对局域网或公网开放。

## 日常启停

- 启动：在此目录运行 `docker compose up -d`
- 状态：运行 `docker compose ps --all`
- 日志：运行 `docker compose logs --tail=100 cchp`
- 停止：双击 `stop.command` 或运行 `docker compose down`

停止服务不会删除数据。不要使用 `docker compose down -v`，否则会删除本机数据库卷。

每台电脑都应独立生成 `.env`、建立自己的数据库并填自己的 API Key。不要复制你的 `.env`、Docker 数据卷或完整调用密钥给朋友。`.env` 已被仓库忽略。

容器镜像已固定到当前核对的多架构 digest，对应 CCHP Plus `1.0.14+build.53`。升级镜像前请先备份数据库，并重新核对兼容性。
