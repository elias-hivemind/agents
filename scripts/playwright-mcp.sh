#!/usr/bin/env bash
# Launcher for the Playwright MCP server, wrapped in a containment proxy.
#
# The proxy sits on the stdio JSON-RPC stream and refuses any tools/call whose
# `filename` argument would resolve outside the output directory.
# See scripts/playwright-mcp-proxy.mjs.
#
# Scope: this contains WRITES. Six tools in the pinned server's default set
# take a `filename` meaning "save the result here" -- browser_take_screenshot,
# browser_evaluate, browser_snapshot, browser_console_messages,
# browser_network_requests, browser_network_request -- and all six are gated,
# because the proxy keys on the argument name rather than a tool allowlist.
# browser_run_code_unsafe is denied outright (see the proxy). NOT contained:
# browser_file_upload reads caller-named absolute paths and can post them to
# the loaded page; that is read-exfiltration, out of scope for this control.
#
# Env:
#   PLAYWRIGHT_MCP_OUTPUT_DIR    output dir      (default: <cwd>/.playwright-mcp)
#   PLAYWRIGHT_MCP_CMD           upstream cmd    (default: the pin below)
#   PLAYWRIGHT_MCP_MAX_FRAME     frame cap bytes (default: 33554432)
#   PLAYWRIGHT_MCP_ALLOW_UNSAFE  =1 permits browser_run_code_unsafe
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
