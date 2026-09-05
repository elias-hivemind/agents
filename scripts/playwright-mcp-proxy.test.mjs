#!/usr/bin/env node
// Conformance tests for the screenshot-containment proxy.
// Runs against a stub upstream server, so no browser and no network needed.
//
//   node scripts/playwright-mcp-proxy.test.mjs

import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const PROXY = path.join(HERE, "playwright-mcp-proxy.mjs");

const STUB = `
let buf = "";
process.stdin.on("data", (c) => {
  buf += c;
  let i;
  while ((i = buf.indexOf("\\n")) !== -1) {
    const line = buf.slice(0, i);
    buf = buf.slice(i + 1);
    if (!line.trim()) continue;
    const msg = JSON.parse(line);
    if (msg.method === "tools/call" && msg.params.name === "browser_take_screenshot") {
      const outDir = process.argv[process.argv.indexOf("--output-dir") + 1];
      const name = msg.params.arguments?.filename ?? "auto.png";
      const target = require("node:path").resolve(outDir, name);
      require("node:fs").mkdirSync(require("node:path").dirname(target), { recursive: true });
      require("node:fs").writeFileSync(target, "PNG");
      process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: msg.id, result: { wrote: target } }) + "\\n");
    } else {
      process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: msg.id, result: { echo: msg.method, raw: line } }) + "\\n");
    }
  }
});
`;

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "pwmcp-"));
const outDir = fs.realpathSync(
  fs.mkdirSync(path.join(tmp, "out"), { recursive: true }) ??
    path.join(tmp, "out")
);
const stubPath = path.join(tmp, "stub.cjs");
fs.writeFileSync(stubPath, STUB);

/** Drive the proxy with `lines`, resolve with { stdout, stderr, code }. */
function run(lines, { env, raw, stub } = {}) {
  return new Promise((resolve) => {
    const p = spawn("node", [PROXY, outDir, "node", stub ?? stubPath], {
      stdio: ["pipe", "pipe", "pipe"],
      env: { ...process.env, ...env }
    });
    let stdout = "";
    let stderr = "";
    p.stdout.on("data", (c) => (stdout += c));
    p.stderr.on("data", (c) => (stderr += c));
    p.on("close", (code) => resolve({ stdout, stderr, code }));
    if (raw) p.stdin.write(raw);
    for (const l of lines) p.stdin.write(`${JSON.stringify(l)}\n`);
    p.stdin.end();
  });
}

const shot = (id, args) => ({
  jsonrpc: "2.0",
  id,
  method: "tools/call",
  params: { name: "browser_take_screenshot", arguments: args }
});

let pass = 0;
let fail = 0;
const check = (name, ok, detail = "") => {
  if (ok) {
    pass++;
    console.log(`PASS  ${name.padEnd(44)} ${detail}`);
  } else {
    fail++;
    console.log(`FAIL  ${name.padEnd(44)} ${detail}`);
  }
};
const replies = (out) =>
  out
    .trim()
    .split("\n")
    .filter(Boolean)
    .map((l) => JSON.parse(l));
const refused = (r) => r.result?.isError === true;

// 1 — plain basename is allowed through and lands inside the output dir.
{
  const { stdout } = await run([shot(1, { filename: "ok.png" })]);
  const r = replies(stdout)[0];
  check(
    "safe basename forwarded",
    !refused(r) && fs.existsSync(path.join(outDir, "ok.png")),
    r.result?.wrote ?? ""
  );
}

// 2 — parent traversal is refused, and nothing is written outside.
{
  const { stdout } = await run([shot(2, { filename: "../escaped.png" })]);
  const r = replies(stdout)[0];
  const leaked = fs.existsSync(path.join(path.dirname(outDir), "escaped.png"));
  check(
    "../ traversal refused",
    refused(r) && !leaked,
    leaked ? "LEAKED" : "no file outside outDir"
  );
}

// 3 — absolute path is refused.
{
  const abs = path.join(tmp, "abs.png");
  const { stdout } = await run([shot(3, { filename: abs })]);
  const r = replies(stdout)[0];
  check("absolute path refused", refused(r) && !fs.existsSync(abs));
}

// 4 — a legitimate name containing ".." is NOT refused (regression: the naive
//     substring check rejected this).
{
  const { stdout } = await run([shot(4, { filename: "shot..png" })]);
  const r = replies(stdout)[0];
  check(
    "dots in basename allowed",
    !refused(r) && fs.existsSync(path.join(outDir, "shot..png"))
  );
}

// 5 — symlink planted inside outDir cannot be used to escape.
{
  const escapeTarget = fs.mkdtempSync(path.join(os.tmpdir(), "pwmcp-esc-"));
  fs.symlinkSync(escapeTarget, path.join(outDir, "link"));
  const { stdout } = await run([shot(5, { filename: "link/via-symlink.png" })]);
  const r = replies(stdout)[0];
  const leaked = fs.existsSync(path.join(escapeTarget, "via-symlink.png"));
  check(
    "symlink escape refused",
    refused(r) && !leaked,
    leaked ? "LEAKED" : "realpath containment held"
  );
}

// 6 — omitted filename is allowed; the server names it inside --output-dir.
{
  const { stdout } = await run([shot(6, {})]);
  const r = replies(stdout)[0];
  check(
    "omitted filename allowed",
    !refused(r) && String(r.result?.wrote ?? "").startsWith(outDir + path.sep)
  );
}

// 7 — non-screenshot calls pass through byte-for-byte.
{
  const msg = {
    jsonrpc: "2.0",
    id: 7,
    method: "tools/call",
    params: { name: "browser_navigate", zz: 1, aa: 2 }
  };
  const { stdout } = await run([msg]);
  const r = replies(stdout)[0];
  check(
    "other tools passthrough verbatim",
    r.result?.raw === JSON.stringify(msg),
    "key order preserved"
  );
}

// 8 — stdout carries only JSON-RPC; stderr is clean on the success path.
{
  const { stdout, stderr, code } = await run([
    shot(8, { filename: "a.png" }),
    shot(9, { filename: "../b.png" })
  ]);
  const nonJson = stdout
    .trim()
    .split("\n")
    .filter((l) => {
      try {
        JSON.parse(l);
        return false;
      } catch {
        return true;
      }
    });
  check(
    "stdout carried only JSON-RPC",
    nonJson.length === 0,
    `${nonJson.length} non-JSON lines`
  );
  check(
    "stderr clean on success path",
    stderr === "",
    stderr ? JSON.stringify(stderr.slice(0, 80)) : "clean"
  );
  check("exit code propagated", code === 0, `code ${code}`);
}

// 9 — --output-dir reaches the upstream server.
{
  const { stdout } = await run([
    { jsonrpc: "2.0", id: 10, method: "initialize", params: {} }
  ]);
  check(
    "upstream receives --output-dir",
    replies(stdout)[0].result?.echo === "initialize",
    outDir
  );
}

// 10 — the gate covers every filename-bearing tool, not just screenshots.
{
  const call = {
    jsonrpc: "2.0",
    id: 11,
    method: "tools/call",
    params: { name: "browser_pdf_save", arguments: { filename: "../out.pdf" } }
  };
  const { stdout } = await run([call]);
  const r = replies(stdout)[0];
  const leaked = fs.existsSync(path.join(path.dirname(outDir), "out.pdf"));
  check(
    "non-screenshot filename gated",
    refused(r) && !leaked,
    "browser_pdf_save refused too"
  );
}

// 11 — an unterminated frame is bounded rather than buffered until OOM.
{
  const { stderr, code } = await run([], {
    env: { PLAYWRIGHT_MCP_MAX_FRAME: "1024" },
    raw: "x".repeat(4096) // no newline: would grow without bound
  });
  check(
    "oversized frame aborts",
    code === 1 && /exceeded 1024 bytes/.test(stderr),
    `code ${code}`
  );
}

// 12 — a large final response survives the upstream exiting immediately after
//      writing it. Note this passes under both child "exit" and "close", so it
//      asserts the payload arrives intact, not the choice between those events.
{
  const bigStub = path.join(tmp, "big.cjs");
  fs.writeFileSync(
    bigStub,
    `let b="";process.stdin.on("data",c=>{b+=c;let i;while((i=b.indexOf("\\n"))!==-1){const l=b.slice(0,i);b=b.slice(i+1);if(!l.trim())continue;const m=JSON.parse(l);process.stdout.write(JSON.stringify({jsonrpc:"2.0",id:m.id,result:{blob:"z".repeat(2000000)}})+"\\n",()=>process.exit(0));}});`
  );
  const { stdout } = await run(
    [{ jsonrpc: "2.0", id: 12, method: "initialize", params: {} }],
    { stub: bigStub }
  );
  let parsed = null;
  try {
    parsed = replies(stdout)[0];
  } catch {
    /* truncated -> unparseable */
  }
  check(
    "large final frame arrives intact",
    parsed?.result?.blob?.length === 2000000,
    `${stdout.length} bytes received`
  );
}

// 13 — a batched tools/call cannot smuggle a traversal past the gate.
{
  const batch = [
    { jsonrpc: "2.0", id: 20, method: "initialize", params: {} },
    {
      jsonrpc: "2.0",
      id: 21,
      method: "tools/call",
      params: {
        name: "browser_take_screenshot",
        arguments: { filename: "../batch-escape.png" }
      }
    }
  ];
  const { stdout } = await run([batch]);
  const out = JSON.parse(stdout.trim().split("\n")[0]);
  const leaked = fs.existsSync(
    path.join(path.dirname(outDir), "batch-escape.png")
  );
  check(
    "batched traversal refused",
    Array.isArray(out) &&
      out.every((r) => r.result?.isError === true) &&
      !leaked,
    leaked ? "LEAKED" : "whole batch refused, fail-closed"
  );
}

// 14 — a clean batch is still forwarded byte-for-byte.
{
  const batch = [
    {
      jsonrpc: "2.0",
      id: 22,
      method: "tools/call",
      params: { name: "browser_navigate", zz: 1 }
    }
  ];
  const { stdout } = await run([batch]);
  const r = replies(stdout)[0];
  check(
    "clean batch forwarded verbatim",
    r.result?.raw === JSON.stringify(batch),
    "bytes unchanged"
  );
}

// 15 — one response per id, exactly once. A locally-synthesised refusal must
//      not leave the client's pending-request map desynchronised: refused
//      frames never reach the upstream, so no id can be answered twice.
{
  const stream = [
    shot(30, { filename: "keep.png" }),
    shot(31, { filename: "../drop.png" }),
    {
      jsonrpc: "2.0",
      id: 32,
      method: "tools/call",
      params: { name: "browser_navigate" }
    },
    {
      jsonrpc: "2.0",
      id: 33,
      method: "tools/call",
      params: {
        name: "browser_pdf_save",
        arguments: { filename: "/tmp/drop.pdf" }
      }
    }
  ];
  const { stdout } = await run(stream);
  const ids = replies(stdout)
    .map((r) => r.id)
    .sort((a, b) => a - b);
  const unique = new Set(ids);
  check(
    "one response per id, exactly once",
    ids.length === 4 && unique.size === 4 && ids.join() === "30,31,32,33",
    `ids ${ids.join(",")}`
  );
}

// 16 — an RCE-equivalent tool is denied: it writes through `code`, not
//      `filename`, so no argument gate can contain it. @playwright/mcp ships
//      browser_run_code_unsafe in the DEFAULT capability set.
{
  const rce = {
    jsonrpc: "2.0",
    id: 40,
    method: "tools/call",
    params: {
      name: "browser_run_code_unsafe",
      arguments: {
        code: 'async () => { require("fs").writeFileSync("/tmp/pwned", "x"); }'
      }
    }
  };
  const { stdout } = await run([rce]);
  const r = replies(stdout)[0];
  check(
    "RCE-equivalent tool denied",
    refused(r) && !/REACHED/.test(stdout),
    "browser_run_code_unsafe blocked at the gate"
  );
}

// 17 — the denial is an operator decision, not a hard block.
{
  const rce = {
    jsonrpc: "2.0",
    id: 41,
    method: "tools/call",
    params: {
      name: "browser_run_code_unsafe",
      arguments: { code: "async () => 1" }
    }
  };
  const { stdout } = await run([rce], {
    env: { PLAYWRIGHT_MCP_ALLOW_UNSAFE: "1" }
  });
  const r = replies(stdout)[0];
  check(
    "opt-in re-enables the unsafe tool",
    !refused(r) && r.result?.echo === "tools/call",
    "PLAYWRIGHT_MCP_ALLOW_UNSAFE=1 forwards it"
  );
}

fs.rmSync(tmp, { recursive: true, force: true });
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
