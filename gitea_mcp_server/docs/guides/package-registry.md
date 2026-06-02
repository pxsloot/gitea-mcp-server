---
title: Package Registry
description: Package registry in Gitea/Forgejo -- supported formats, authentication, publishing, and consuming packages across 20+ types.
tags: [packages, registry, npm, PyPI, Docker, Maven, Go, publishing]
source: Forgejo Docs -- Package Registry (CC-BY-SA-4.0)
---

# Package Registry

Gitea/Forgejo includes a built-in package registry supporting 20+ formats.

## Supported Package Types

| Type | Registry | Publish | Install |
|------|----------|---------|---------|
| Alpine | `.apk` | ✓ | ✓ |
| Cargo | Rust crates | ✓ | ✓ |
| Chef | Cookbooks | ✓ | ✓ |
| Composer | PHP | ✓ | ✓ |
| Conan | C/C++ | ✓ | ✓ |
| Conda | Python | ✓ | ✓ |
| Container | OCI/Docker | ✓ | ✓ |
| Debian | `.deb` | ✓ | ✓ |
| Generic | Any file | ✓ | ✓ |
| Go | Go modules | ✓ | ✓ |
| Helm | Charts | ✓ | ✓ |
| Maven | Java | ✓ | ✓ |
| npm | Node.js | ✓ | ✓ |
| NuGet | .NET | ✓ | ✓ |
| Pub | Dart/Flutter | ✓ | ✓ |
| PyPI | Python | ✓ | ✓ |
| RPM | `.rpm` | ✓ | ✓ |
| RubyGems | Ruby | ✓ | ✓ |
| Swift | Swift | ✓ | ✓ |
| Vagrant | Boxes | ✓ | ✓ |

## Authentication

Publishing requires authentication with a token that has `read:package` and/or `write:package` scope. Each package type uses its own auth method:

- **npm:** `.npmrc` with `//gitea.example.com/packages/{owner}/npm/:_authToken={token}`
- **PyPI:** `~/.pypirc` with username `__token__` and password being the token
- **Docker/OCI:** `docker login gitea.example.com` with token as password
- **Maven:** `settings.xml` with token in server password
- **Go:** `GOPROXY=https://gitea.example.com/api/packages/{owner}/go`

## Managing Packages

- `gitea://packages/{owner}` -- list all packages for an owner (user or org)
- `gitea://packages/{owner}/{type}/{name}/{version}` -- get package details
- `gitea://packages/{owner}/{type}/{name}/{version}/files` -- list package files

## Relevant Tools

- `gitea://packages/{owner}` -- list packages resource
- `gitea//packages/{owner}/{type}/{name}/{version}` -- package detail resource
- `gitea//packages/{owner}/{type}/{name}/{version}/files` -- files resource
