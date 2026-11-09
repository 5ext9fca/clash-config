# Clash Config

Private Mihomo configuration and device-specific subscription distribution service.

`MySubscription-Common.yaml` owns shared DNS, routing and strategy groups.
Private Vultr node definitions live in `Private-Nodes-Mixin.yaml`; merge
that file into Common first. Common retains node-name references to preserve
exit preferences, but contains no node credentials or airport subscription settings.
To include airport nodes, deep-merge `Subscription-Mixin.yaml` into Common,
including its `proxy-providers` and subscription-domain DNS policy. Preserve
existing DNS map entries. The PROXY, AUTO and FALLBACK groups automatically
include all configured providers; their explicit private-node order is retained.
Without the subscription overlay, Common plus the private-node mixin uses only
private nodes. Common alone is a policy template, not a complete profile.

The subscription server merges the private-node and subscription mixins for desktop and
router output, and automatically applies `Nikki-Mixin.yaml` for router
devices only. See `server/README.md` for deployment. When importing these
files without the server, compose the mappings yourself before import.
Use the remote subscription only as a proxy provider, not as the main profile.

## Layout

- `*.yaml`: shared policy and private-node, subscription, and router overlays.
- `server/`: Python WSGI service, tests, dependencies, and Docker Compose deployment.

See [server/README.md](server/README.md) for setup, upstream authorization, testing, and operations.

## Migration from dotfiles

This repository was extracted from `dotfiles/clash`, retaining the relevant commit history.
Run deployment commands from `server/` (previously `clash/server/`).
Copy any existing `server/settings.yaml` and `server/.env` from the old checkout; these are untracked deployment files.
Keep their permissions intact. Relative configuration paths remain unchanged.
The Compose project name `clash-config`, network alias `clash-subscription`, external Caddy network, and subscription URLs remain unchanged.
Stop the old deployment before starting from the new checkout, then recreate the application container to update bind mounts.
Retain the old deployment files until the new service has been verified. MyCaddy is managed separately.
