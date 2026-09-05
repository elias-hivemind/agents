#!/usr/bin/env node
// Containment proxy for the Playwright MCP server.
//
// Sits on the stdio JSON-RPC stream between client and upstream server and
// refuses any browser_take_screenshot whose `filename` would resolve outside
// the output directory. Every other message is forwarded byte-for-byte —
// nothing is re-serialized, so key order and number formatting survive.
//
// Usage: playwright-mcp-proxy.mjs <realOutDir> <upstreamCmd> [args...]

import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const NL = Buffer.from("\n");
const SCREENSHOT_TOOL = "browser_take_screenshot";

const [realOutDir, upstreamCmd, ...upstreamArgs] = process.argv.slice(2);

if (!realOutDir || !upstreamCmd) {
  process.stderr.write("playwright-mcp-proxy: usage: <realOutDir> <cmd> [args...]\n");
  process.exit(2);
}

/**
 * Resolve `p` through symlinks as far as it exists on disk, then re-append the
 * segments that do not exist yet. Lets us contain a path whose leaf has not
 * been written, while still defeating a symlink planted inside the output dir.
 */
function realpathNearest(p) {
  const tail = [];
  let cur = path.resolve(p);
  for (;;) {
    try {
      return path.join(fs.realpathSync(cur), ...tail.slice().reverse());
    } catch (err) {
      if (err.code !== "ENOENT" && err.code !== "ENOTDIR") throw err;
      const parent = path.dirname(cur);
      if (parent === cur) return path.resolve(p);
      tail.push(path.basename(cur));
      cur = parent;
    }
  }
}

/**
 * @returns {string|null} rejection reason, or null when the filename is safe.
 * A missing/empty filename is safe: the server picks its own name inside
 * --output-dir.
 */
function containmentError(filename) {
  if (filename === undefined || filename === null) return null;
  if (typeof filename !== "string") return "filename must be a string";
  if (filename === "") return null;
  if (filename.includes("\0")) return "filename contains a NUL byte";
  if (path.isAbsolute(filename) || path.win32.isAbsolute(filename)) {
    return "filename must be relative to the output directory";
  }

  // Segment-wise so a legitimate name like "shot..png" is not rejected.
  const normalized = path.normalize(filename);
  if (normalized.split(/[\\/]/).includes("..")) {
    return "filename must not traverse above the output directory";
  }

  const resolved = realpathNearest(path.resolve(realOutDir, normalized));
  if (resolved !== realOutDir && !resolved.startsWith(realOutDir + path.sep)) {
    return "filename resolves outside the output directory";
  }
  return null;
}

const child = spawn(upstreamCmd, [...upstreamArgs, "--output-dir", realOutDir], {
  stdio: ["pipe", "pipe", "inherit"]
});

child.on("error", (err) => {
  process.stderr.write(`playwright-mcp-proxy: cannot start upstream: ${err.message}\n`);
  process.exit(1);
});

/** Write `buf` to `dst`, pausing `src` until drained. */
function writeBackpressured(src, dst, buf) {
  if (!dst.write(buf)) {
    src.pause();
    dst.once("drain", () => src.resume());
  }
}

/**
 * Split `src` into newline-delimited frames and hand each to `onLine`.
 * `onLine` returns the Buffer to forward, or null to swallow the frame.
 */
function pumpLines(src, dst, onLine) {
  let chunks = [];
  let pending = 0;

  src.on("data", (chunk) => {
    chunks.push(chunk);
    pending += chunk.length;
    if (!chunk.includes(0x0a)) return;

    let buf = chunks.length === 1 ? chunks[0] : Buffer.concat(chunks, pending);
    let idx;
    while ((idx = buf.indexOf(0x0a)) !== -1) {
      const line = buf.subarray(0, idx);
      buf = buf.subarray(idx + 1);
      const out = onLine(line);
      if (out !== null) writeBackpressured(src, dst, Buffer.concat([out, NL]));
    }
    chunks = buf.length ? [buf] : [];
    pending = buf.length;
  });
}

// client -> server: intercept screenshot calls, forward everything else as-is.
pumpLines(process.stdin, child.stdin, (line) => {
  let msg;
  try {
    msg = JSON.parse(line.toString("utf8"));
  } catch {
    return line; // not our business to police framing errors
  }

  if (msg?.method !== "tools/call" || msg?.params?.name !== SCREENSHOT_TOOL) return line;

  let reason;
  try {
    reason = containmentError(msg.params?.arguments?.filename);
  } catch (err) {
    reason = `filename could not be resolved: ${err.code ?? err.message}`;
  }
  if (reason === null) return line;

  // Refuse locally. A notification (no id) gets no response, only suppression.
  if (msg.id !== undefined && msg.id !== null) {
    const refusal = {
      jsonrpc: "2.0",
      id: msg.id,
      result: {
        isError: true,
        content: [{ type: "text", text: `Refused ${SCREENSHOT_TOOL}: ${reason}.` }]
      }
    };
    writeBackpressured(process.stdin, process.stdout, Buffer.from(`${JSON.stringify(refusal)}\n`));
  }
  return null;
});

// server -> client: pure passthrough.
pumpLines(child.stdout, process.stdout, (line) => line);

process.stdin.on("end", () => child.stdin.end());
for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(sig, () => child.kill(sig));
}
child.on("exit", (code, signal) => {
  process.exitCode = signal ? 128 + (os.constants.signals[signal] ?? 15) : (code ?? 0);
  process.stdout.end();
});
