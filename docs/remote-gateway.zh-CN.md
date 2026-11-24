# 三端连接远程网关

Mac、Windows 和 Android 都可以直接使用同一个 HTTPS 模型网关。工作台在当前设备运行终端和 Agent，网关负责转发模型请求；远程网关不需要另一台电脑开机，也不要求设备位于同一局域网。

## 地址与凭据

在工作台的调用配置中填写 API 基础地址，例如 `https://gateway.example.com`、`https://gateway.example.com/proxy/v1` 或 `https://gateway.example.com/api/paas/v4`。自定义路径会保留，Codex 直接在该前缀下使用 Responses；裸域名默认使用 `/v1`。Claude 的 SDK 自行附加 `/v1/messages`，因此填写末尾 `/v1` 时会去掉这一段再交给 SDK，避免重复。Anthropic 网关若要求其他前缀，应填写它文档中的 Anthropic 基础地址。填写控制台域名或钱包页面不能替代 API 地址。

三端均可手动输入模型 ID；模型目录查询先尝试 `x-api-key`，收到 401/403 时会在同一地址安全重试为纯 Bearer，不把 Key 转发到重定向地址。添加时默认只查询模型列表，不调用模型；接口返回 404/405/501 会提示目录不可用并允许保存，成功但空列表也会提示手填。401/403、网络、重定向和无效 JSON 不会当作验证成功；如需先保存，可取消「保存时读取模型列表」，界面会注明未验证。启动时由客户端向已选网关确认 Key、模型与协议，模型目录不作为启动白名单。Claude 需要 Messages，Codex 需要 Responses；仅提供 Chat Completions 的服务需要额外的协议转换，本次不包含转换网关。

使用该网关签发的**模型调用 Key**。控制台的账号密码、管理 Token、上游供应商 Key 是不同的凭据。网页登录成功不等于工作台已完成配置。

`127.0.0.1` 和 `localhost` 始终指当前运行工作台的设备。例如，Windows 或 Android 上的 `http://127.0.0.1:18081` 只有在这台设备的对应端口确实有网关或 SSH 转发时才可用。迁移到公网网关后，应填写公网 HTTPS 地址。

## 各平台操作

| 平台 | 配置入口 | HTTPS 实现 |
| --- | --- | --- |
| macOS | 选择调用配置 → 网关设置；也可添加新配置 | 本机 Python 标准库 |
| Windows | 左侧调用配置 → 添加配置，填写新地址和对应 Key | Rust `reqwest` / Rustls |
| Android | 调用配置 → 添加配置，填写新地址和对应 Key | Android 系统 `HttpURLConnection` |

验证新地址时先保留旧配置，成功后再选择新配置启动任务。正在运行的终端不会自动切换网关。不要为了换地址先删除旧配置：配置 ID 参与会话隔离，重新添加配置可能不会自动显示旧配置下的会话历史。

迁移同一套网关数据库后，原调用 Key 可以继续使用；两套独立网关的账本不会自动同步。模型列表验证只查询元数据，不产生模型推理。

## 连接失败时

- **连接拒绝或超时**：先检查地址是否还指向旧隧道、局域网 IP 或其他设备的 `localhost`。
- **HTTP 401/403**：服务已响应，检查 Key 是否属于当前网关、是否有效、是否具有所需权限。
- **TLS 握手失败但浏览器能访问**：浏览器、工作台元数据请求和 Agent CLI 使用的 TLS/代理实现可能不同，不能只凭浏览器成功判断全部路径正常。还需对照同服务器的其他域名、查看服务端握手记录，以区分客户端兼容性和网络路径重置。不要关闭证书校验。
- **模型列表成功但 Agent 失败**：列表查询与实际推理是两个接口；继续核对 Claude 的 Messages 或 Codex 的 Responses 支持、模型权限和 Agent 自身的代理设置。

macOS 工作台优先使用已安装的 Homebrew/Miniconda Python，最后回退到系统 Python。系统 Python 的旧 LibreSSL 曾在远程网关握手时失败；可通过启动环境中的 `CCHP_MANAGER_PYTHON` 显式指定 Python 路径。这个设置不改变网关配置，也不改变 Agent CLI 的网络实现。Windows 和 Android 不需要套用这个 Mac 专用修复。

## Android 真机元数据检查（开发用）

安装调试版应用及其测试 APK 后，可使用已保存在设备上的 Key 验证 HTTPS。测试只读取模型列表，不输出 Key、不启动 Agent、不修改配置：

```sh
adb shell am instrument -w -r \
  -e gateway_smoke true \
  -e gateway_url https://gateway.example.com \
  com.cassette.workbench.test/com.cassette.workbench.TerminalInputInstrumentation
```

不填 `gateway_url` 时使用当前选中配置的地址；`profile_id` 可指定其他已有配置。成功输出模型数量和耗时。这个检查使用应用自己的 Android HTTPS 实现，无需把设备中的 Key 导出到电脑。
