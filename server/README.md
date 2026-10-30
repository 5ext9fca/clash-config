# Clash 配置分发服务

为每台设备分配独立订阅令牌，通过 HTTPS 拉取完整 YAML：

- `desktop`：读取 `../MySubscription-Common.yaml`，将上游 `proxies` 嵌入
  `Airport` inline provider。保留私有节点、健康检查、策略组、DNS 和规则顺序。
- `mobile`：返回订阅服务原始 YAML 字节，不应用 Common、不添加私有节点。
- `router`：返回与桌面端相同的主配置；在 Nikki 中单独手动导入
  `../Nikki-Mixin.yaml`，服务不合并 Mixin。

设备类型由服务端配置绑定，不推测客户端 User-Agent。移动端需要支持提供商
返回的 Clash YAML 格式；服务不转换为其他客户端格式。规则集仍由客户端按
Common 中的 `rule-providers` 下载。节点改为随完整订阅更新，客户端应开启
订阅自动更新（建议每小时一次）；上游响应中的更新周期不控制本服务。

## 上游访问前提

**现有订阅链接默认拒绝访问，正式启用前必须手动向提供商申请允许服务器访问。**
向提供商确认出口 IP、允许的请求方式及返回 Clash YAML 所需的 User-Agent。
服务只向配置好的 HTTPS 地址发起普通请求，不绕过访问限制，不转发设备的
User-Agent，也不接受客户端传入任意上游 URL。测试全部使用模拟数据。

`upstream_url` 留空时读取 Common 的 Airport URL；也可以指定提供商批准的
专用地址。`upstream_user_agent` 默认 `clash.meta`，仅用于请求 YAML 格式，
不能代替提供商授权。上游必须返回带 `proxies`、`proxy-groups`、`rules` 的完整
配置；HTML、空节点、仅节点订阅和 Base64 节点列表会被拒绝。

## 部署

服务器只需 Docker Engine 和 Docker Compose 插件，不需要在宿主机安装 Python、
uv 或 systemd 服务。Compose 启动应用与 Caddy，只有 Caddy 发布公网端口；
应用通过容器网络提供服务，8080 不映射到宿主机。

在服务器 checkout 中运行：

```sh
cd clash/server
cp settings.example.yaml settings.yaml
cp .env.example .env
# 构建应用镜像；构建过程不会读取或访问订阅。
CONFIG_DOMAIN=config.example.com docker compose build app
# 每台设备单独运行一次，将输出填入 settings.yaml。
CONFIG_DOMAIN=config.example.com docker compose run --rm --no-deps app python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

编辑 `.env` 中的 `CONFIG_DOMAIN` 为真实域名，并为 `settings.yaml` 中每台设备
填写独立令牌；占位令牌会被拒绝。Common 路径保留默认相对路径，Compose 会将
两个文件挂载到匹配的容器路径。不要填宿主机绝对路径。

应用固定以 UID/GID `10001:10001` 运行；在 Linux 服务器上设置读取权限：

```sh
sudo chown 10001:10001 settings.yaml
sudo chmod 600 settings.yaml
# Common 默认应可读；若权限收紧，也需允许容器 UID 10001 读取。
docker compose config --quiet
docker compose run --rm --no-deps app python -c 'from app import create_app; create_app(); print("Configuration OK")'
```

配置检查只读取本地文件，不访问上游。配置文件以只读方式挂载，镜像中不包含
订阅地址、节点凭证或设备令牌；`.dockerignore` 只允许构建所需代码与依赖文件。
应用根文件系统只读，Gunicorn 临时文件放在 `/tmp` tmpfs。

先向提供商申请允许服务器访问订阅，再将域名 A/AAAA 记录指向服务器，放行
TCP 80/443（UDP 443 用于 HTTP/3），确保这些端口未被其他服务占用：

```sh
docker compose up -d
docker compose ps
docker compose logs --tail=50 app caddy
```

Caddy 自动管理 HTTPS，证书与配置保存在命名卷中；正常升级或停止使用
`docker compose down`，不要加 `-v`，以保留证书。健康检查只请求应用 `/` 并验证
404，不使用订阅令牌、不访问提供商；健康状态不代表上游已获授权或可用。

若已在宿主机部署上一版服务，先停用旧 Gunicorn/systemd 服务，并迁移原有
Caddy 配置或停止旧反向代理，避免占用 80/443；确认新服务可用后移除旧的
systemd 单元。仓库不再提供 systemd 部署模板，平台安装器也不会自动部署此服务。

客户端导入地址：

```text
https://config.example.com/sub/<该设备的令牌>/config.yaml
```

路由器使用 `router` 令牌订阅主配置，再按 `Nikki-Mixin.yaml` 文件头说明导入
Mixin 并配置 Nikki。主配置单独使用时仍启用 TUN，不能跳过路由器 Mixin 步骤。

Gunicorn 与 Caddy 均不启用访问日志，避免记录令牌；如已有全局代理日志，
也应隐藏订阅路径。响应带 `Cache-Control: private, no-store`，不应由 CDN 公共
缓存。设备令牌能读取完整节点凭证；删除或替换令牌后重建应用容器可撤销下载权限，
但不会撤销设备此前已经下载的节点凭证。

## 更新、缓存与故障

- 更新 Common 或 settings 后执行 `docker compose up -d --force-recreate app`。
  单文件 bind mount 可能仍指向原子替换前的 inode，必须重建容器而不只是 restart。
  Common 每次生成时读取挂载文件；Mixin 仍需在路由器手动更新。
- 更新服务代码或依赖后执行 `docker compose up -d --build`；更新 `.env` 后执行
  `docker compose up -d` 使域名环境变量生效。
- 上游按请求触发刷新，共享内存缓存默认 300 秒；默认仅一个 Gunicorn worker，
  四个线程共享缓存和锁，避免并发重复拉取。没有定时后台下载任务。
- 403、超时、无效 YAML 等失败后至少等待 60 秒再尝试。首次失败返回 503；
  有缓存时可在缓存过期后最多继续使用 86400 秒，并返回
  `X-Subscription-Cache: stale`；超过时限返回 503，不发布错误页或空配置。
- 缓存不持久化，重启后需要成功拉取一次。服务故障时客户端应保留上次成功配置。
  Common 本身损坏会返回 503，不自动回滚；用 Git 恢复上一版本。
- 移动端保留上游 `subscription-userinfo` 流量信息头，桌面端/路由器也携带同一
  订阅额度信息。其他上游响应头不会转发。
- 服务检查 YAML 的基本结构，不替代 Mihomo 内核校验。上线前应使用目标版本
  `mihomo -t -f <生成的配置文件>` 检查，并在实际客户端验证；规则集下载需要网络。

## 离线验证

```sh
docker build --target test -t clash-config-test:local .
docker run --rm --network none --read-only --tmpfs /tmp \
  --mount "type=bind,src=$(cd .. && pwd)/MySubscription-Common.yaml,dst=/app/MySubscription-Common.yaml,readonly" \
  clash-config-test:local
```

首次构建需要下载镜像和依赖；测试容器禁用网络。测试覆盖设备分流、移动端字节透传、Common 策略保留、Mixin 分离、鉴权、
缓存刷新、拒绝访问与过期回退。不会请求真实订阅，也不需要提供商授权。

Mihomo inline provider 的结构参见
[官方文档](https://wiki.metacubex.one/config/proxy-providers/)。
