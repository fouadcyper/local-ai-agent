# Security policy

## Reporting

Do not open a public issue for a credential leak, command-execution bypass, SSRF bypass, or authentication flaw. Use GitHub's private security advisory flow.

## Trust boundaries

- The HTTP server is intended to bind only to <code>127.0.0.1</code>.
- Connected MCP servers are separate programs with their own permissions.
- Remote MCP, web, GitHub, and Telegram operations leave the device.
- Enabling auto approval increases risk. Pattern matching is not a sandbox.
- Only install MCP servers and model files from sources you trust.

Never commit the settings file, environment files, tokens, or authorization headers.
