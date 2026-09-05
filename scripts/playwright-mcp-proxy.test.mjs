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
const outDir = fs.realpathSync(fs.mkdirSync(path.join(tmp, "out"), { recursive: true }) ?? path.join(tmp, "out"));
const stubPath = path.join(tmp, "stub.cjs");
fs.writeFileSync(stubPath, STUB);

/** Drive the proxy with `lines`, resolve with { stdout, stderr, code }. */
function run(lines) {
  return new Promise((resolve) => {
    const p = spawn("node", [PROXY, outDir, "node", stubPath], { stdio: ["pipe", "pipe", "pipe"] });
    let stdout = "";
    let stderr = "";
    p.stdout.on("data", (c) => (stdout += c));
    p.stderr.on("data", (c) => (stderr += c));
    p.on("close", (code) => resolve({ stdout, stderr, code }));
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
const replies = (out) => out.trim().split("\n").filter(Boolean).map((l) => JSON.parse(l));
const refused = (r) => r.result?.isError === true;

// 1 — plain basename is allowed through and lands inside the output dir.
{
  const { stdout } = await run([shot(1, { filename: "ok.png" })]);
  const r = replies(stdout)[0];
  check("safe basename forwarded", !refused(r) && fs.existsSync(path.join(outDir, "ok.png")), r.result?.wrote ?? "");
}

// 2 — parent traversal is refused, and nothing is written outside.
{
  const { stdout } = await run([shot(2, { filename: "../escaped.png" })]);
  const r = replies(stdout)[0];
  const leaked = fs.existsSync(path.join(path.dirname(outDir), "escaped.png"));
  check("../ traversal refused", refused(r) && !leaked, leaked ? "LEAKED" : "no file outside outDir");
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
  check("dots in basename allowed", !refused(r) && fs.existsSync(path.join(outDir, "shot..png")));
}

// 5 — symlink planted inside outDir cannot be used to escape.
{
  const escapeTarget = fs.mkdtempSync(path.join(os.tmpdir(), "pwmcp-esc-"));
  fs.symlinkSync(escapeTarget, path.join(outDir, "link"));
  const { stdout } = await run([shot(5, { filename: "link/via-symlink.png" })]);
  const r = replies(stdout)[0];
  const leaked = fs.existsSync(path.join(escapeTarget, "via-symlink.png"));
  check("symlink escape refused", refused(r) && !leaked, leaked ? "LEAKED" : "realpath containment held");
}

// 6 — omitted filename is allowed; the server names it inside --output-dir.
{
  const { stdout } = await run([shot(6, {})]);
  const r = replies(stdout)[0];
  check("omitted filename allowed", !refused(r) && String(r.result?.wrote ?? "").startsWith(outDir + path.sep));
}

// 7 — non-screenshot calls pass through byte-for-byte.
{
  const msg = { jsonrpc: "2.0", id: 7, method: "tools/call", params: { name: "browser_navigate", zz: 1, aa: 2 } };
  const { stdout } = await run([msg]);
  const r = replies(stdout)[0];
  check("other tools passthrough verbatim", r.result?.raw === JSON.stringify(msg), "key order preserved");
}

// 8 — stdout carries only JSON-RPC; stderr is clean on the success path.
{
  const { stdout, stderr, code } = await run([shot(8, { filename: "a.png" }), shot(9, { filename: "../b.png" })]);
  const nonJson = stdout.trim().split("\n").filter((l) => {
    try { JSON.parse(l); return false; } catch { return true; }
  });
  check("stdout carried only JSON-RPC", nonJson.length === 0, `${nonJson.length} non-JSON lines`);
  check("stderr clean on success path", stderr === "", stderr ? JSON.stringify(stderr.slice(0, 80)) : "clean");
  check("exit code propagated", code === 0, `code ${code}`);
}

// 9 — --output-dir reaches the upstream server.
{
  const { stdout } = await run([{ jsonrpc: "2.0", id: 10, method: "initialize", params: {} }]);
  check("upstream receives --output-dir", replies(stdout)[0].result?.echo === "initialize", outDir);
}

fs.rmSync(tmp, { recursive: true, force: true });
console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail ? 1 : 0);
