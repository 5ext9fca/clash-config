# Clash 配置分发服务

为每台设备分配独立订阅令牌，通过 HTTPS 拉取完整 YAML：

- `desktop`：读取 `../MySubscription-Common.yaml` 并深度合并
  `../Private-Nodes-Mixin.yaml`、`../Subscription-Mixin.yaml`，将上游 `proxies` 嵌入
  `Airport` inline provider。保留私有节点、健康检查、策略组、DNS 和规则顺序。
- `mobile`：返回订阅服务原始 YAML 字节，不应用 Common、不添加私有节点。
- `router`：在桌面端配置基础上自动深度合并 `../Nikki-Mixin.yaml`，
  下发已关闭 TUN、启用 TPROXY 和 redir-host 的完整配置。

Mixin 合并时递归保留映射中的其他键，列表和标量整体替换；空列表会清空
原列表。自建节点和订阅 Mixin 保持独立，Common 只保留策略及节点名称引用，
不含节点连接参数；单独的 Common 不能直接使用。

Subscription Mixin 将默认 DNS 设为 EIX 官方 DoH，并清空独立的
`proxy-server-nameserver`，使节点域名也使用同一主备解析链。官方解析失败或
没有 IP 答案时，才采用阿里／腾讯 DoH 的结果。备用查询并行预取
（`fallback-lazy-query: false`），避免官方超时耗尽查询预算；官方正常时始终
优先采用官方结果，不按主备响应速度竞选。关闭
GeoIP 回退筛选，避免仅因官方返回境外地址就改用公共入口。此设置影响没有命中
专用 DNS 策略的默认解析；校园、国内域名、订阅下载等已有策略仍优先生效。
不要把公共 DoH 追加到官方 nameserver 列表：列表会并发查询，无法保证官方优先。
此回退针对 DNS 解析失败，不会因节点连接失败而自动切换 DNS。
官方超时时首次解析仍可能等待约 5 秒，再采用备用结果。

设备类型由服务端配置绑定，不推测客户端 User-Agent。移动端需要支持提供商
返回的 Clash YAML 格式；服务不转换为其他客户端格式。规则集仍由客户端按
Common 中的 `rule-providers` 下载。节点改为随完整订阅更新，客户端应开启
订阅自动更新（建议每小时一次）；上游响应中的更新周期不控制本服务。

## 上游访问前提

**现有订阅链接默认拒绝访问，正式启用前必须手动向提供商申请允许服务器访问。**
向提供商确认出口 IP、允许的请求方式及返回 Clash YAML 所需的 User-Agent。
服务只向配置好的 HTTPS 地址发起普通请求，不绕过访问限制，按设备配置决定是否透传
User-Agent 和 flag，不接受客户端传入任意上游 URL。测试全部使用模拟数据。

`upstream_url` 留空时读取 Subscription-Mixin 的 Airport URL；也可以指定提供商批准的
专用地址。设备开启 UA 透传且请求包含非空 User-Agent 时原样透传；否则使用
`upstream_user_agent`，默认 `FlClash/v0.9.2 clash-verge Platform/linux`，仅用于请求 YAML 格式，
不能代替提供商授权。旧配置的 `clash.meta`、`clash-verge/v2.5.8`、空值或未配置会自动使用该默认值；
其他显式自定义 UA 仍保留。直接导入订阅 Mixin 时，其 provider 请求头也使用
相同 UA。格式依据 [FlClash 官方源码](https://github.com/chen08209/FlClash/blob/main/lib/common/package.dart)，
其中 `clash-verge` 是官方兼容标识，平台按 Linux 服务环境填写。上游必须返回带 `proxies`、`proxy-groups`、`rules` 的完整
配置；HTML、空节点、仅节点订阅和 Base64 节点列表会被拒绝。

`upstream_user_agent` 和 `upstream_flag` 分别配置默认 UA 和 flag；后者默认为
`anytls`。每个 `devices` 条目可独立设置两个布尔开关：

| 配置项 | desktop/mobile 默认 | router 默认 |
| --- | --- | --- |
| `forward_user_agent` | `true` | `false` |
| `forward_flag` | `true` | `false` |

开启对应开关且客户端请求头非空时原样透传，否则使用该请求头的服务端默认值。
旧 settings 未填写开关时也按上表生效，router 不会透传 UA 或 flag。
可以为单个设备显式覆盖开关，两个开关互不影响。
修改 settings 后需重建服务容器。直接使用 Subscription-Mixin 的 provider
仍使用该文件内的固定请求头，不读取 server settings。

## 部署

本服务接入独立的 `MyCaddy` 公网入口，使用项目名 `clash-config`，
加入外部网络 `caddy-public`，别名为 `clash-subscription`。8080 不发布到宿主机。
先按 MyCaddy 的 README 创建公共网络；无需先启动 MyCollab。
如修改网络名，两边 `.env` 的 `CADDY_NETWORK` 必须一致。

在服务器 checkout 中运行：

```sh
cd server
cp settings.example.yaml settings.yaml
cp .env.example .env
# 构建应用镜像；构建过程不会读取或访问订阅。
docker compose build app
# 每台设备单独运行一次，将输出填入 settings.yaml。
docker compose run --rm --no-deps app python -c 'import secrets; print(secrets.token_urlsafe(32))'
```

确认 `.env` 中的网络名，并为 `settings.yaml` 中每台设备填写独立令牌；占位令牌会被拒绝。Common、private_nodes_mixin、subscription_mixin 和 router_mixin 路径保留默认相对路径，Compose 会将
settings 和四个 YAML 文件挂载到匹配的容器路径。不要填宿主机绝对路径。

应用固定以 UID/GID `10001:10001` 运行；在 Linux 服务器上设置读取权限：

```sh
sudo chown 10001:10001 settings.yaml
sudo chmod 600 settings.yaml
# Common 和三个 Mixin 默认应可读；若权限收紧，也需允许容器 UID 10001 读取。
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

### 接入公共 Caddy（公网 IPv4）

公网地址、证书、80/443 与 `/sub/*` 路由统一由独立项目 `MyCaddy` 管理。
在 MyCaddy 的 `.env` 设置 `PUBLIC_HOST`，并启用 `conf/routes/clash.caddy`。
此路由保留 `/sub/` 前缀；不再修改 MyCollab 的 Caddyfile，也不共享其默认网络。
迁移旧证书卷、切换入口和重新加载的命令见 MyCaddy README。
本服务 `.env` 只控制网络，不配置公网地址。

验收时先访问 `https://<公网IPv4>/sub/invalid/config.yaml`：应返回应用的纯文本
404，而不是 MyCollab 的 HTML。它不访问订阅提供商。再确认 `/`、API 和静态文件
仍正常；获提供商授权后再用真实令牌验证订阅。证书检查不要跳过校验。

客户端导入地址：

```text
https://<公网IPv4>/sub/<该设备的令牌>/config.yaml
```

路由器使用 `router` 令牌即可订阅合并后的完整配置，无需在 Nikki 再导入 Mixin。
Nikki 中关闭重复的 Mixin File Content，并将对应 GUI 覆写项设为 Unmodified，
避免覆盖服务端生成值；仍需启用路由器/LAN 代理、设置访问控制，并关闭 IPv4/IPv6
DNS 劫持。

Gunicorn 与公共 Caddy 默认配置均不启用访问日志，避免记录令牌；如已有全局代理日志，
也应隐藏订阅路径。响应带 `Cache-Control: private, no-store`，不应由 CDN 公共
缓存。设备令牌能读取完整节点凭证；删除或替换令牌后重建应用容器可撤销下载权限，
但不会撤销设备此前已经下载的节点凭证。

本服务的 `docker compose down` 仅停止订阅服务；外部网络独立于所有业务栈，
MyCollab 的停止和重建不影响订阅入口。

## 更新、缓存与故障

- 更新 Common、任一 Mixin 或 settings 后执行 `docker compose up -d --force-recreate app`。
  单文件 bind mount 可能仍指向原子替换前的 inode，必须重建容器而不只是 restart。
  Common、自建节点和订阅 Mixin 每次生成时读取挂载文件；router 还会读取 Nikki Mixin。
- 更新服务代码或依赖后执行 `docker compose up -d --build`；更新 `.env` 后执行
  `docker compose up -d` 使网络设置生效。公共入口的更新在 MyCaddy 中单独执行。
- 上游按请求触发刷新，按实际发送的 `(User-Agent, flag)` 组合隔离内存缓存，默认 300 秒；
  相同 UA 和 flag 的设备共享缓存，不同组合的响应、流量信息和失败重试互不混用。
  最多保留 32 份，超限淘汰最久未使用的缓存。默认仅一个 Gunicorn worker，
  四个线程共享缓存和锁，避免并发重复拉取。没有定时后台下载任务。
- 403、超时、无效 YAML 等失败后至少等待 60 秒再尝试。首次失败返回 503；
  有缓存时可在缓存过期后最多继续使用 86400 秒，并返回
  `X-Subscription-Cache: stale`；超过时限返回 503，不发布错误页或空配置。
- 缓存不持久化，重启后需要成功拉取一次。服务故障时客户端应保留上次成功配置。
  当前设备所需的 Common 或 Mixin 损坏会返回 503，不自动回滚；用 Git 恢复上一版本。
- 移动端保留上游 `subscription-userinfo` 流量信息头，桌面端/路由器也携带同一
  订阅额度信息。其他上游响应头不会转发。
- 服务检查 YAML 的基本结构，不替代 Mihomo 内核校验。上线前应使用目标版本
  `mihomo -t -f <生成的配置文件>` 检查，并在实际客户端验证；规则集下载需要网络。

## 离线验证

```sh
docker build --target test -t clash-config-test:local .
docker run --rm --network none --read-only --tmpfs /tmp \
  --mount "type=bind,src=$(cd .. && pwd)/MySubscription-Common.yaml,dst=/app/MySubscription-Common.yaml,readonly" \
  --mount "type=bind,src=$(cd .. && pwd)/Subscription-Mixin.yaml,dst=/app/Subscription-Mixin.yaml,readonly" \
  --mount "type=bind,src=$(cd .. && pwd)/Nikki-Mixin.yaml,dst=/app/Nikki-Mixin.yaml,readonly" \
  --mount "type=bind,src=$(cd .. && pwd)/Private-Nodes-Mixin.yaml,dst=/app/Private-Nodes-Mixin.yaml,readonly" \
  clash-config-test:local
```

首次构建需要下载镜像和依赖；测试容器禁用网络。测试覆盖设备分流、移动端字节透传、Common 策略保留、订阅 Mixin 合并、router 自动覆写、桌面端隔离、鉴权、
缓存刷新、拒绝访问与过期回退。不会请求真实订阅，也不需要提供商授权。

Mihomo inline provider 的结构参见
[官方文档](https://wiki.metacubex.one/config/proxy-providers/)。
