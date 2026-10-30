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

服务器需要系统安装的 Python 3.11+（示例为 `/usr/bin/python3`）、uv 和 HTTPS 反向代理。在服务器 checkout 中运行：

```sh
cd clash/server
uv sync --locked --python /usr/bin/python3
cp settings.example.yaml settings.yaml
chmod 600 settings.yaml
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

为每台设备分别生成令牌并替换 `settings.yaml` 中的占位值。占位令牌会被拒绝。
服务账号必须能读取配置文件和 Common。不要将仓库目录设为 Web 静态根目录。

```sh
uv run --locked gunicorn --config gunicorn.conf.py 'app:create_app()'
```

默认仅监听 `127.0.0.1:8080`。将 `Caddyfile.example` 的域名替换为真实域名并加入
服务器的 Caddy 配置，配置 DNS 和 HTTPS 所需端口。`clash-config.service.example`
提供 systemd 模板：创建专用 `clash-config` 系统账号，按实际 checkout 位置调整
路径，确保账号有读取权限后安装并启用；不要使用个人 home 目录中的 checkout，
模板启用了 `ProtectHome=true`。本仓库的平台安装器不会自动部署此服务。

客户端导入地址：

```text
https://config.example.com/sub/<该设备的令牌>/config.yaml
```

路由器使用 `router` 令牌订阅主配置，再按 `Nikki-Mixin.yaml` 文件头说明导入
Mixin 并配置 Nikki。主配置单独使用时仍启用 TUN，不能跳过路由器 Mixin 步骤。

Gunicorn 与示例 Caddy 均不启用访问日志，避免记录令牌；如已有全局代理日志，
也应隐藏订阅路径。响应带 `Cache-Control: private, no-store`，不应由 CDN 公共
缓存。设备令牌能读取完整节点凭证；删除或替换令牌后重启服务可撤销下载权限，
但不会撤销设备此前已经下载的节点凭证。

## 更新、缓存与故障

- Common 每次生成时重新读取；更新文件后下次订阅请求生效。请先验证再原子替换。
  Mixin 仍需在路由器手动更新。修改设备令牌、上游地址或其他服务设置后需重启。
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
uv run --locked python -m unittest discover -s . -p 'test_*.py' -v
```

测试覆盖设备分流、移动端字节透传、Common 策略保留、Mixin 分离、鉴权、
缓存刷新、拒绝访问与过期回退。不会请求真实订阅，也不需要提供商授权。

Mihomo inline provider 的结构参见
[官方文档](https://wiki.metacubex.one/config/proxy-providers/)。
