#!/usr/bin/env bash
# Launcher for the Playwright MCP server, wrapped in a containment proxy.
#
# The proxy sits on the stdio JSON-RPC stream and refuses any tools/call whose
# `filename` argument would resolve outside the output directory.
# See scripts/playwright-mcp-proxy.mjs.
#
# Env:
#   PLAYWRIGHT_MCP_OUTPUT_DIR  screenshot/trace output dir (default: <cwd>/.playwright-mcp)
#   PLAYWRIGHT_MCP_CMD         upstream server command    (default: the pin below)
set -euo pipefail

OUT_DIR="${PLAYWRIGHT_MCP_OUTPUT_DIR:-$PWD/.playwright-mcp}"
mkdir -p "$OUT_DIR"
REAL_OUT_DIR="$(cd "$OUT_DIR" && pwd -P)"

# Deliberately NOT forwarding "$@" into the upstream command: the wrapper owns
# the upstream flag set so a caller cannot slip in --caps and widen the tool
# surface. Operators who need extra flags set PLAYWRIGHT_MCP_CMD.
# Pinned: @playwright/mcp@0.0.80 depends on playwright 1.63.0-alpha-2026-08-31,
# the build the conformance run was recorded against. Bump both together --
# `npm view @playwright/mcp@<v> dependencies` reports the Playwright it carries.
PLAYWRIGHT_MCP_PIN="npx -y @playwright/mcp@0.0.80"

# Split on whitespace into an array with globbing off, so a command containing
# a shell metacharacter is passed through as literal argv rather than expanded.
set -f
# shellcheck disable=SC2206  # word-splitting is the intent here
UPSTREAM_CMD=(${PLAYWRIGHT_MCP_CMD:-$PLAYWRIGHT_MCP_PIN})
set +f

PROXY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/playwright-mcp-proxy.mjs"

exec node "$PROXY" "$REAL_OUT_DIR" "${UPSTREAM_CMD[@]}"
