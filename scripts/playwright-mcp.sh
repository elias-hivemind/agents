#!/usr/bin/env bash
# Launcher for the Playwright MCP server, wrapped in a containment proxy.
#
# The proxy sits on the stdio JSON-RPC stream and refuses any
# browser_take_screenshot whose `filename` would resolve outside the output
# directory. See scripts/playwright-mcp-proxy.mjs.
#
# Env:
#   PLAYWRIGHT_MCP_OUTPUT_DIR  screenshot/trace output dir (default: <cwd>/.playwright-mcp)
#   PLAYWRIGHT_MCP_CMD         upstream server command    (default: npx -y @playwright/mcp@latest)
set -euo pipefail

OUT_DIR="${PLAYWRIGHT_MCP_OUTPUT_DIR:-$PWD/.playwright-mcp}"
mkdir -p "$OUT_DIR"
REAL_OUT_DIR="$(cd "$OUT_DIR" && pwd -P)"

# Deliberately NOT forwarding "$@" into the upstream command: the wrapper owns
# the upstream flag set so a caller cannot slip in --caps and widen the tool
# surface. Operators who need extra flags set PLAYWRIGHT_MCP_CMD.
UPSTREAM_CMD="${PLAYWRIGHT_MCP_CMD:-npx -y @playwright/mcp@latest}"

PROXY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/playwright-mcp-proxy.mjs"

exec node "$PROXY" "$REAL_OUT_DIR" $UPSTREAM_CMD
