#!/usr/bin/env node
// Containment proxy for the Playwright MCP server.
//
// Sits on the stdio JSON-RPC stream between client and upstream server and
// refuses any tools/call whose `filename` argument would resolve outside the
// output directory. Every other message is forwarded byte-for-byte —
// nothing is re-serialized, so key order and number formatting survive.
//
// Usage: playwright-mcp-proxy.mjs <realOutDir> <upstreamCmd> [args...]

import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";

const NL = Buffer.from("\n");

// Largest single JSON-RPC frame we will buffer, in bytes. A full-page 4K
// screenshot base64-encodes to well under this; anything larger is a peer
// streaming without newlines, which would otherwise grow the buffer until
// the process is OOM-killed.
const DEFAULT_MAX_FRAME_BYTES = 33554432;
const configuredMaxFrame = Number(process.env.PLAYWRIGHT_MCP_MAX_FRAME);
const MAX_FRAME_BYTES =
  Number.isInteger(configuredMaxFrame) && configuredMaxFrame > 0
    ? configuredMaxFrame
    : DEFAULT_MAX_FRAME_BYTES;

// Tools that defeat filename containment by construction: they run
// caller-supplied code inside the server process, so no argument gate can
// constrain where they write. @playwright/mcp ships browser_run_code_unsafe
// in the DEFAULT capability set and documents it as RCE-equivalent. Gating
// `filename` while leaving this reachable is not containment, so it is denied
// unless an operator explicitly opts in.
const UNSAFE_TOOLS = new Set(["browser_run_code_unsafe"]);
const ALLOW_UNSAFE = process.env.PLAYWRIGHT_MCP_ALLOW_UNSAFE === "1";

const [outDirArg, upstreamCmd, ...upstreamArgs] = process.argv.slice(2);

if (!outDirArg || !upstreamCmd) {
  process.stderr.write(
    "playwright-mcp-proxy: usage: <realOutDir> <cmd> [args...]\n"
  );
  process.exit(2);
}

// MSYS rewrites a POSIX argument on its way into a native program, and
// `pwd -W` reports the same shape directly: both hand Node "D:/agents/out",
// with forward slashes. path.resolve() renders the candidate filenames below
// as "D:\agents\out\shot.png", so comparing them against an unnormalized
// prefix refused every legitimate name. Normalize slash style and any
// trailing separator once, here, and hand the same value to the upstream
// server so both sides mean one directory.
const realOutDir = path.resolve(outDirArg);

// @playwright/mcp does not resolve a caller-supplied `filename` against
// --output-dir. It resolves it against its "workspace", which it derives from
// the MCP client's roots, falling back to process.cwd() only when there are
// none:  cwd = firstRootPath(clientRoots) ?? process.cwd()
// Claude Code and every other roots-aware client answer that handshake with the
// project directory, so the fallback is never reached and starting the child
// inside the output dir changes nothing -- a plain "shot.png" landed in the
// project root while the gate validated it against the output dir. Gating a
// base path the writer does not use is not containment.
//
// So answer the handshake ourselves: rewrite the client's roots/list response
// to name the output directory. The writer's base path and the gate's then
// agree, and upstream's own checkFile() allowed roots (output dir + workspace)
// collapse to the output dir instead of spanning the whole project.
const OUT_DIR_ROOT_URI = pathToFileURL(realOutDir).href;

// ids of roots/list requests seen heading server -> client, awaiting a reply.
const pendingRootsListIds = new Set();

/**
 * @returns {object|null} the rewritten response, or null to forward unchanged.
 * An error reply (no `result.roots`) is left alone: upstream then treats roots
 * as empty and falls back to process.cwd(), which is already the output dir.
 */
function rewriteRootsResponse(msg) {
  if (msg?.id === undefined || msg?.id === null) return null;
  if (!pendingRootsListIds.delete(msg.id)) return null;
  if (!Array.isArray(msg?.result?.roots)) return null;
  return {
    ...msg,
    result: {
      ...msg.result,
      roots: [{ uri: OUT_DIR_ROOT_URI, name: "playwright-mcp output" }]
    }
  };
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

// Start the writer where the gate points. This covers the fallback branch of
// the workspace derivation above -- a client advertising no roots leaves
// upstream on process.cwd() -- while the roots rewrite covers the branch that
// roots-aware clients actually take. Both must land on the output dir.
// A relative PLAYWRIGHT_MCP_CMD would no longer resolve from the caller's
// directory; it takes an absolute path or a name on PATH.
fs.mkdirSync(realOutDir, { recursive: true });

const child = spawn(
  upstreamCmd,
  [...upstreamArgs, "--output-dir", realOutDir],
  {
    cwd: realOutDir,
    stdio: ["pipe", "pipe", "inherit"]
  }
);

child.on("error", (err) => {
  process.stderr.write(
    `playwright-mcp-proxy: cannot start upstream: ${err.message}\n`
  );
  process.exit(1);
});

let aborted = false;

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
function pumpLines(src, dst, onLine, label) {
  let chunks = [];
  let pending = 0;

  src.on("data", (chunk) => {
    chunks.push(chunk);
    pending += chunk.length;
    if (pending > MAX_FRAME_BYTES) {
      // Fail closed: a frame this large is a malformed or hostile peer.
      process.stderr.write(
        `playwright-mcp-proxy: ${label} frame exceeded ${MAX_FRAME_BYTES} bytes; aborting\n`
      );
      chunks = [];
      pending = 0;
      aborted = true;
      process.exitCode = 1;
      child.kill("SIGTERM");
      src.destroy();
      return;
    }
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

/**
 * Map each frame that must be refused to its reason. A JSON-RPC batch is an
 * array, so a guard on msg.method alone would forward the whole frame
 * uninspected. MCP 2025-06-18 dropped batching and @playwright/mcp ignores
 * such frames, but the gate must not rest on an upstream's incidental
 * behaviour -- PLAYWRIGHT_MCP_CMD can name a server that does honour them.
 *
 * Every filename-bearing tool is gated, not just browser_take_screenshot:
 * browser_pdf_save and friends write through the same argument.
 */
function collectRefusals(frames) {
  const refusals = new Map();
  for (const m of frames) {
    if (m?.method !== "tools/call") continue;

    if (UNSAFE_TOOLS.has(m.params?.name)) {
      if (!ALLOW_UNSAFE) {
        refusals.set(
          m,
          "it runs arbitrary code in the server process, which bypasses " +
            "output-directory containment entirely (set " +
            "PLAYWRIGHT_MCP_ALLOW_UNSAFE=1 to permit it)"
        );
      }
      // Its `filename` names code to LOAD, not a path to write, so the
      // containment check below would be applying the wrong semantics.
      continue;
    }

    const filename = m.params?.arguments?.filename;
    if (filename === undefined || filename === null) continue;
    let reason;
    try {
      reason = containmentError(filename);
    } catch (err) {
      reason = `filename could not be resolved: ${err.code ?? err.message}`;
    }
    if (reason !== null) refusals.set(m, reason);
  }
  return refusals;
}

/**
 * Fail closed: one bad member refuses the whole batch, rather than
 * re-serializing a filtered one and losing byte-fidelity. A notification
 * (no id) gets no response, only suppression.
 */
function refusalResponses(frames, refusals) {
  const responses = [];
  for (const m of frames) {
    if (m?.id === undefined || m?.id === null) continue;
    const reason = refusals.get(m);
    responses.push({
      jsonrpc: "2.0",
      id: m.id,
      result: {
        isError: true,
        content: [
          {
            type: "text",
            text: reason
              ? `Refused ${m.params?.name ?? "tools/call"}: ${reason}.`
              : "Refused: another call in this batch left the output directory."
          }
        ]
      }
    });
  }
  return responses;
}

// client -> server: refuse uncontained writes, forward everything else as-is.
pumpLines(
  process.stdin,
  child.stdin,
  (line) => {
    let msg;
    try {
      msg = JSON.parse(line.toString("utf8"));
    } catch {
      return line; // not our business to police framing errors
    }

    const batched = Array.isArray(msg);
    const frames = batched ? msg : [msg];

    // Point upstream's workspace at the output dir. Only this frame is
    // re-serialized -- it is a short handshake reply, not a payload -- so the
    // byte-fidelity guarantee still holds for every tools/call.
    if (pendingRootsListIds.size > 0) {
      let rewrote = false;
      const patched = frames.map((m) => {
        const r = rewriteRootsResponse(m);
        if (r) rewrote = true;
        return r ?? m;
      });
      if (rewrote) {
        return Buffer.from(JSON.stringify(batched ? patched : patched[0]));
      }
    }

    const refusals = collectRefusals(frames);
    if (refusals.size === 0) return line; // forward original bytes untouched

    const responses = refusalResponses(frames, refusals);
    if (responses.length > 0) {
      writeBackpressured(
        process.stdin,
        process.stdout,
        Buffer.from(`${JSON.stringify(batched ? responses : responses[0])}\n`)
      );
    }
    return null;
  },
  "client"
);

// server -> client: verbatim passthrough, but note the id of any roots/list
// request so its reply can be answered with the output dir on the way back.
pumpLines(
  child.stdout,
  process.stdout,
  (line) => {
    // Substring scan first: a screenshot frame is megabytes of base64 and must
    // not be JSON.parsed just to look for a handshake request.
    if (line.includes("roots/list")) {
      try {
        const msg = JSON.parse(line.toString("utf8"));
        for (const m of Array.isArray(msg) ? msg : [msg]) {
          if (
            m?.method === "roots/list" &&
            m?.id !== undefined &&
            m?.id !== null
          ) {
            pendingRootsListIds.add(m.id);
          }
        }
      } catch {
        // Not a frame we understand; forwarding it unchanged is still correct.
      }
    }
    return line;
  },
  "upstream"
);

process.stdin.on("end", () => child.stdin.end());
for (const sig of ["SIGINT", "SIGTERM", "SIGHUP"]) {
  process.on(sig, () => child.kill(sig));
}
// "close" fires once the child's stdio streams are closed, "exit" can fire
// while child.stdout still holds unread data. In practice process.stdout.end()
// flushes what we already read, so the two are hard to tell apart here — but
// "close" is the one whose documented contract guarantees it.
child.on("close", (code, signal) => {
  // Keep the abort's exit code; the SIGTERM we sent is not the real cause.
  if (aborted) {
    process.stdout.end();
    return;
  }
  process.exitCode = signal
    ? 128 + (os.constants.signals[signal] ?? 15)
    : (code ?? 0);
  process.stdout.end();
});
