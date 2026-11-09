# Repository Guidelines

## Language

Conduct all conversational interaction with the user in Chinese. Keep code, commands, and identifiers in their original form.

## Structure

Root YAML files own Mihomo policy and overlays. `server/` owns distribution code, dependencies, tests, and deployment. Keep configuration and server changes together when their behavior depends on each other.

## Clash Routing Policy

In `MySubscription-Common.yaml`, the high priority of `RULE-SET,dev,Development` is intentional. Keep it ahead of the Google, Microsoft, Apple, and other service rules that currently follow it, so overlapping development domains use Development. Preserve the preceding GitHub Copilot and GitHub exceptions. Do not move Development into the category fallback section or treat this precedence as a routing defect unless the user explicitly requests a policy change. Alphabetical sorting of strategy groups or rule-provider definitions must not change the matching order in `rules`.

## Validation

Run the offline regression tests documented in `server/README.md`; never use real subscriptions for tests. Preserve device-specific output, authentication, cache behavior, and relative Compose mount paths. Validate generated profiles with the target Mihomo version before deployment.

## Commits

Commit focused, completed and verified changes with short imperative subjects.

## Private Configuration

This is a private repository and may intentionally contain node credentials. Preserve ignored runtime settings and device tokens.
