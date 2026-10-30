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

本服务复用 `MyCollabAdvanced/deploy` 的 Caddy，不再启动第二个 Caddy。
现有 Compose 项目名为 `mycollab-prod`，默认网络为 `mycollab-prod_default`；
本服务使用独立项目名 `clash-config`，加入该外部网络，别名为 `clash-subscription`。
8080 不发布到宿主机，公网 80/443 仍由原 Caddy 管理。

先确保 MyCollab 栈已启动，再确认网络存在：

```sh
docker network inspect mycollab-prod_default
```

如果服务器通过 `-p` 或 `COMPOSE_PROJECT_NAME` 改过项目名，以实际网络为准，
将 `.env` 中的 `CADDY_NETWORK` 改为该网络名。不要额外创建同名空网络来掩盖
原 Caddy 未连接的问题。宿主机不需要安装 Python 或 uv。

在服务器 checkout 中运行：

```sh
cd clash/server
cp settings.example.yaml settings.yaml
cp .env.example .env
# 构建应用镜像；构建过程不会读取或访问订阅。
docker compose build app
# 每台设备单独运行一次，将输出填入 settings.yaml。
docker compose run --rm --no-deps app python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

确认 `.env` 中的网络名，并为 `settings.yaml` 中每台设备填写独立令牌；占位令牌会被拒绝。Common 路径保留默认相对路径，Compose 会将
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

先向提供商申请允许服务器访问订阅，再启动应用：

```sh
docker compose up -d --build
docker compose ps
docker compose logs --tail=50 app
```

健康检查仅请求应用 `/` 并验证 404，不访问提供商；健康状态不代表上游已授权。

### 接入现有 Caddy（公网 IPv4）

在服务器 `MyCollabAdvanced/deploy/.env` 中，`DOMAIN` 填真实公网 IPv4，
不带协议或路径；现有 Django 的 `DJANGO_ALLOWED_HOSTS` 与
`DJANGO_CSRF_TRUSTED_ORIGINS` 继续按 MyCollab 的公网 IP 部署说明配置。
本服务 `.env` 只控制 Docker 网络，不能修改另一 Compose 栈的 `DOMAIN`。

本目录的 `Caddyfile` 是基于已查看的 MyCollab 配置制作的**合并示例**，不会被
本服务自动加载。它保留 `/api/*`、`/admin/*`、`/static/*` 与 SPA 回退，加入：

```caddyfile
handle /sub/* {
    reverse_proxy clash-subscription:8080
}
```

将该段合并进服务器原 `deploy/Caddyfile` 的同一 IP 站点，在无匹配条件的
SPA `handle` 前放置；使用 `handle` 而不是 `handle_path`，以保留 `/sub/` 前缀。
如果已有 `/sub/` 业务路由，需要先处理路径冲突。保留服务器其他自定义设置。

若现有 Caddy 尚未按公网 IP 配置证书，合并示例还提供：全局
`default_sni {$DOMAIN}`、Let's Encrypt 生产 ACME 地址及 `profile shortlived`。
已有全局块时将 `default_sni` 合并进去，不添加第二个全局块。公网 IP 已支持
受信任证书，无需域名；IP 证书寿命短，需要保留原 `caddy_data` 卷并持续自动续期。
不要保留 `tls internal` 或 `local_certs` 与此公网方案混用。
[Let's Encrypt 公告](https://letsencrypt.org/2026/01/15/6day-and-ip-general-availability/)、
[Caddy ACME 配置](https://caddyserver.com/docs/caddyfile/directives/tls#issuers)。

原 Caddy 的 `Dockerfile.caddy` 会将配置复制进镜像，修改文件后必须重建。
以下命令在服务器 **MyCollabAdvanced/deploy** 中运行：

```sh
docker compose build caddy
docker compose run --rm --no-deps caddy caddy version
docker compose run --rm --no-deps caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker compose up -d --no-deps caddy
docker compose logs --tail=50 caddy
```

示例在 Caddy 2.11.7 上校验；以服务器实际版本为准，旧版本需先按 MyCollab
部署说明升级。保持原 TCP 80/443 对公网验证可达，无需增加端口或 DNS 记录。
不要停止已有 MyCollab 服务或删除证书卷来启动订阅服务。

验收时先访问 `https://<公网IPv4>/sub/invalid/config.yaml`：应返回应用的纯文本
404，而不是 MyCollab 的 HTML。它不访问订阅提供商。再确认 `/`、API 和静态文件
仍正常；获提供商授权后再用真实令牌验证订阅。证书检查不要跳过校验。

客户端导入地址：

```text
https://<公网IPv4>/sub/<该设备的令牌>/config.yaml
```

路由器使用 `router` 令牌订阅主配置，再按 `Nikki-Mixin.yaml` 文件头说明导入
Mixin 并配置 Nikki。主配置单独使用时仍启用 TUN，不能跳过路由器 Mixin 步骤。

Gunicorn 与合并示例均不启用访问日志，避免记录令牌；如已有全局代理日志，
也应隐藏订阅路径。响应带 `Cache-Control: private, no-store`，不应由 CDN 公共
缓存。设备令牌能读取完整节点凭证；删除或替换令牌后重建应用容器可撤销下载权限，
但不会撤销设备此前已经下载的节点凭证。

本服务的 `docker compose down` 仅停止订阅服务，外部网络由 MyCollab 栈管理。
MyCollab 若需整体 down，先停止订阅服务，否则共享网络可能因仍有容器使用而无法删除。

## 更新、缓存与故障

- 更新 Common 或 settings 后执行 `docker compose up -d --force-recreate app`。
  单文件 bind mount 可能仍指向原子替换前的 inode，必须重建容器而不只是 restart。
  Common 每次生成时读取挂载文件；Mixin 仍需在路由器手动更新。
- 更新服务代码或依赖后执行 `docker compose up -d --build`；更新 `.env` 后执行
  `docker compose up -d` 使网络设置生效。原 Caddy 的更新在 MyCollab 栈单独执行。
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
