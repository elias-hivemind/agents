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

# Git Bash/MSYS `pwd` prints a POSIX path (/d/agents/.playwright-mcp). MSYS
# does rewrite such an argument on the way into a native program -- but as
# "D:/agents/..." with forward slashes, while Node renders the filenames it
# is compared against as "D:\agents\...". That mismatch refused every
# legitimate name. Convert here so the handoff does not rest on MSYS's
# rewriting at all; the proxy normalizes slash style either way.
case "$(uname -s)" in
  MINGW* | MSYS* | CYGWIN*)
    if NATIVE_OUT_DIR="$(cygpath -w "$REAL_OUT_DIR" 2>/dev/null)"; then
      REAL_OUT_DIR="$NATIVE_OUT_DIR"
    else
      REAL_OUT_DIR="$(cd "$OUT_DIR" && pwd -W)"
    fi
    ;;
esac

# Deliberately NOT forwarding "$@" into the upstream command: the wrapper owns
# the upstream flag set so a caller cannot slip in --caps and widen the tool
# surface. Operators who need extra flags set PLAYWRIGHT_MCP_CMD.
# Pinned: @playwright/mcp@0.0.80 depends on playwright 1.63.0-alpha-2026-08-31,
# the build the conformance run was recorded against. Bump both together --
# `npm view @playwright/mcp@<v> dependencies` reports the Playwright it carries.
# --prefer-offline keeps a warm cache from touching the registry on every
# launch; only the first run fetches. The exact pin is what makes that safe --
# a cached entry can only ever be this one version.
PLAYWRIGHT_MCP_PIN_PKG="@playwright/mcp@0.0.80"

if [ -n "${PLAYWRIGHT_MCP_CMD:-}" ]; then
  # Split on whitespace into an array with globbing off, so a command
  # containing a shell metacharacter is passed through as literal argv
  # rather than expanded.
  set -f
  # shellcheck disable=SC2206  # word-splitting is the intent here
  UPSTREAM_CMD=(${PLAYWRIGHT_MCP_CMD})
  set +f
else
  case "$(uname -s)" in
    MINGW* | MSYS* | CYGWIN*)
      # On Windows "npx" is npx.cmd, and node spawn() cannot execute a .cmd
      # without a shell -- it fails with ENOENT. Handing it to a shell would
      # reintroduce the metacharacter interpretation that the array form
      # exists to prevent, so drive npm's own CLI with node instead. Built as
      # a quoted array, so a prefix path containing spaces still works.
      NPX_CLI="$(npm prefix -g)/node_modules/npm/bin/npx-cli.js"
      NPX_CLI="$(cygpath -u "$NPX_CLI" 2>/dev/null || printf "%s" "$NPX_CLI")"
      if [ -f "$NPX_CLI" ]; then
        UPSTREAM_CMD=(node "$NPX_CLI" --prefer-offline -y "$PLAYWRIGHT_MCP_PIN_PKG")
      else
        echo "playwright-mcp: npx-cli.js missing at $NPX_CLI; trying npx" >&2
        UPSTREAM_CMD=(npx --prefer-offline -y "$PLAYWRIGHT_MCP_PIN_PKG")
      fi
      ;;
    *)
      UPSTREAM_CMD=(npx --prefer-offline -y "$PLAYWRIGHT_MCP_PIN_PKG")
      ;;
  esac
fi

PROXY="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/playwright-mcp-proxy.mjs"

exec node "$PROXY" "$REAL_OUT_DIR" "${UPSTREAM_CMD[@]}"
