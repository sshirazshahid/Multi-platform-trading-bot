# Security Policy

## Supported Versions

Only the latest version on `main` is actively supported.

## Reporting a Vulnerability

**Do NOT open a public GitHub issue for security vulnerabilities.**

Please report security issues privately by emailing the maintainers. Include:

1. A description of the vulnerability
2. Steps to reproduce
3. Potential impact
4. Any suggested fixes (optional)

We will respond within 72 hours.

## Security Best Practices for Users

### API Keys
- Use exchange API keys with the **minimum required permissions**
- Enable IP whitelisting on all exchange API keys
- Never enable withdrawal permissions on trading API keys
- Rotate API keys regularly
- Never share API keys or commit them to version control

### Environment File
- Keep `.env` secure and never commit it
- Set appropriate file permissions: `chmod 600 .env` on Linux/macOS
- Use a separate set of API keys for paper trading vs live trading

### Server Security (if deploying remotely)
- Use a dedicated server/VPS for the bot
- Enable a firewall — only expose necessary ports
- Keep Python and all dependencies up to date
- Use a non-root user to run the bot
- Enable fail2ban or similar brute-force protection

### Paper Trading First
- Always test with `DRY_RUN=true` for at least 1–2 weeks
- Validate strategy performance before enabling live trading
- Start with small position sizes when first going live

## What This Software Does NOT Do
- This bot does NOT have withdrawal permissions (and should never be granted them)
- This bot does NOT store your API keys anywhere except your local `.env` file
- This bot does NOT send your credentials to any third-party service
