# Security Policy

## Supported Versions

Only the latest release receives security updates.
Older versions are not maintained.

## Reporting a Vulnerability

This server handles API tokens that grant access to your Gitea/Forgejo
instance. If you discover a security vulnerability, please report it
privately — **do not open a public issue**.

**Report via email**: gitea-mcp-server@pxsloot.nl

You can also use GitHub's private vulnerability reporting if the repo is
on GitHub: go to the repository's "Security" tab → "Report a vulnerability".

### What to include

- A clear description of the issue
- Steps to reproduce (if applicable)
- Potential impact
- Any suggested fix (if you have one)

### Response time

I aim to acknowledge receipt within 48 hours and provide an initial
assessment within 5 business days.

### Disclosure

I will coordinate disclosure with you. Once a fix is released, the
vulnerability will be documented in the release notes with credit
to the reporter (unless you prefer to remain anonymous).

## Scope

This policy covers the `gitea-mcp-server` Python package and its
contained Python code. The Gitea/Forgejo server itself, the FastMCP
framework, and any other third-party dependencies are out of scope —
report issues there to their respective projects.
