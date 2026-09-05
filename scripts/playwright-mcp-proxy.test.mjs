#!/usr/bin/env node
// Conformance tests for the screenshot-containment proxy.
// Runs against a stub upstream server, so no browser and no network needed.
//
//   node scripts/playwright-mcp-proxy.test.mjs

import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

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
    const outDir = process.argv[process.argv.indexOf("--output-dir") + 1];
    if (msg.method === "tools/call" && msg.params.name === "browser_take_screenshot") {
      const name = msg.params.arguments?.filename ?? "auto.png";
      // The real @playwright/mcp resolves a caller-supplied filename against
      // its own cwd, not --output-dir. Model that: the proxy is what has to
      // put the child in the right place.
      const target = require("node:path").resolve(name);
      require("node:fs").mkdirSync(require("node:path").dirname(target), { recursive: true });
      require("node:fs").writeFileSync(target, "PNG");
      process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: msg.id, result: { wrote: target } }) + "\\n");
    } else {
      process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: msg.id, result: { echo: msg.method, raw: line, outDir, cwd: process.cwd() } }) + "\\n");
    }
  }
});
`;

const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "pwmcp-"));
const outDirPath = path.join(tmp, "out");
// Deliberately not fs.mkdirSync's return value: on Windows that is an
// extended-length \\?\C:\... path, which fs.realpathSync cannot
// (it lstats the "C:" component and throws EISDIR). On Linux it is a plain
// path, so threading it through realpath happened to work there.
fs.mkdirSync(outDirPath, { recursive: true });
const outDir = fs.realpathSync(outDirPath);
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
let skip = 0;
const skipped = (name, why) => {
  skip++;
  console.log("SKIP  " + name.padEnd(44) + " " + why);
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
  let linked = true;
  let linkKind = "symlink";
  try {
    fs.symlinkSync(escapeTarget, path.join(outDir, "link"));
  } catch (e) {
    // Windows refuses symlink() without Administrator or Developer Mode.
    // An NTFS junction needs no elevation, points at a directory, and is
    // resolved by realpath the same way -- so it is the same escape vector,
    // and it is the one an unprivileged attacker on Windows can actually
    // plant. Fall back to it rather than leaving the vector untested.
    try {
      fs.symlinkSync(escapeTarget, path.join(outDir, "link"), "junction");
      linkKind = "junction";
    } catch (e2) {
      // Neither form available: skip loudly. Never count an assertion
      // that did not run as a pass.
      linked = false;
      skipped(
        "symlink escape refused",
        "symlink() " + e.code + ", junction " + e2.code
      );
    }
  }
  if (linked) {
    const { stdout } = await run([
      shot(5, { filename: "link/via-symlink.png" })
    ]);
    const r = replies(stdout)[0];
    const leaked = fs.existsSync(path.join(escapeTarget, "via-symlink.png"));
    check(
      "symlink escape refused",
      refused(r) && !leaked,
      leaked ? "LEAKED" : "realpath containment held (" + linkKind + ")"
    );
  }
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
  const seen = replies(stdout)[0].result;
  check(
    "upstream receives --output-dir",
    seen?.echo === "initialize" && seen?.outDir === outDir,
    seen?.outDir ?? "(absent)"
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

// 18 — every tool in @playwright/mcp's default set whose `filename` argument
//      means "write the result here" is gated. Enumerated from the live
//      tools/list of @playwright/mcp@0.0.80: gating keys on the argument
//      name, not a tool allowlist, so all six are covered by construction.
//      A tool-name allowlist would have covered only the screenshot.
{
  const WRITERS = [
    "browser_take_screenshot",
    "browser_evaluate",
    "browser_snapshot",
    "browser_console_messages",
    "browser_network_requests",
    "browser_network_request"
  ];
  const calls = WRITERS.map((name, i) => ({
    jsonrpc: "2.0",
    id: 50 + i,
    method: "tools/call",
    params: { name, arguments: { filename: "../out.txt" } }
  }));
  const { stdout } = await run(calls);
  const rs = replies(stdout);
  const allRefused = rs.length === WRITERS.length && rs.every(refused);
  const leaked = fs.existsSync(path.join(path.dirname(outDir), "out.txt"));
  check(
    "every filename-writing tool gated",
    allRefused && !leaked,
    `${rs.filter(refused).length}/${WRITERS.length} refused`
  );
}

// 19 — the shell launcher must hand the proxy a path the proxy can actually
//      compare against. Tests 1-18 spawn the proxy directly with a native
//      path, so none of them can see a launcher that computes the wrong one:
//      on Git Bash `pwd -P` printed /d/agents/out, Node resolved that against
//      the current drive (D:\d\agents\out), and every legitimate filename was
//      refused while the upstream server wrote to the real directory. Drive
//      the launcher end-to-end instead, with the stub standing in for the
//      real server via PLAYWRIGHT_MCP_CMD.
{
  const LAUNCHER = path.join(HERE, "playwright-mcp.sh");
  const launchDir = path.join(tmp, "launched");
  fs.mkdirSync(launchDir, { recursive: true });

  const runLauncher = (lines) =>
    new Promise((resolve) => {
      const p = spawn("bash", [LAUNCHER], {
        stdio: ["pipe", "pipe", "pipe"],
        env: {
          ...process.env,
          PLAYWRIGHT_MCP_OUTPUT_DIR: launchDir,
          PLAYWRIGHT_MCP_CMD: `node ${stubPath}`
        }
      });
      let stdout = "";
      let stderr = "";
      p.stdout.on("data", (c) => (stdout += c));
      p.stderr.on("data", (c) => (stderr += c));
      p.on("error", () => resolve({ stdout, stderr, code: 127 }));
      p.on("close", (code) => resolve({ stdout, stderr, code }));
      for (const l of lines) p.stdin.write(`${JSON.stringify(l)}\n`);
      p.stdin.end();
    });

  // The launcher word-splits PLAYWRIGHT_MCP_CMD deliberately, so a stub path
  // containing a space cannot be expressed as an upstream command at all.
  const unusable = /\s/.test(stubPath) || /\s/.test(launchDir);
  const first = unusable
    ? null
    : await runLauncher([shot(60, { filename: "launched.png" })]);

  if (unusable) {
    skipped("launcher hands the gate a usable path", "tmpdir has whitespace");
    skipped("launcher still refuses traversal", "tmpdir has whitespace");
  } else if (first.code === 127) {
    skipped("launcher hands the gate a usable path", "bash not on PATH");
    skipped("launcher still refuses traversal", "bash not on PATH");
  } else {
    const r = replies(first.stdout)[0];
    check(
      "launcher hands the gate a usable path",
      !refused(r) && fs.existsSync(path.join(launchDir, "launched.png")),
      r?.result?.wrote ?? (r?.result?.content?.[0]?.text ?? "").slice(0, 52)
    );

    // Guard against "fixing" the false refusal by widening the gate.
    const bad = await runLauncher([shot(61, { filename: "../leaked.png" })]);
    const rb = replies(bad.stdout)[0];
    const leaked = fs.existsSync(path.join(tmp, "leaked.png"));
    check(
      "launcher still refuses traversal",
      refused(rb) && !leaked,
      leaked ? "LEAKED" : "gate intact through the launcher"
    );
  }
}

// 20 — the upstream server is started INSIDE the output directory.
//      @playwright/mcp resolves a caller-supplied `filename` against its own
//      cwd rather than --output-dir, so a plain "shot.png" landed in the
//      project root while the gate was busy validating it against
//      .playwright-mcp. Gating a path the writer does not use is not
//      containment, so the writer is started where the gate points.
{
  const { stdout } = await run([
    { jsonrpc: "2.0", id: 70, method: "initialize", params: {} }
  ]);
  const seen = replies(stdout)[0].result;
  check(
    "upstream runs inside the output dir",
    seen?.cwd === outDir,
    seen?.cwd ?? "(absent)"
  );
}

// 21 — the workspace upstream actually writes into follows the output dir.
//      Test 20 pins the child's cwd, but @playwright/mcp only falls back to
//      cwd when the client offers no roots:
//          cwd = firstRootPath(clientRoots) ?? process.cwd()
//      A roots-aware client (Claude Code sends the project directory) takes the
//      other branch, so the cwd fix never applied and a plain "shot.png" still
//      landed in the project root. Model the roots handshake, or the suite
//      cannot see the bug -- which is exactly what happened.
{
  const rootsStub = path.join(tmp, "roots-stub.cjs");
  fs.writeFileSync(
    rootsStub,
    [
      'const fs = require("node:fs");',
      'const path = require("node:path");',
      'const url = require("node:url");',
      'let buf = ""; let root = null;',
      'process.stdin.on("data", (c) => {',
      "  buf += c; let i;",
      '  while ((i = buf.indexOf("\\n")) !== -1) {',
      "    const line = buf.slice(0, i); buf = buf.slice(i + 1);",
      "    if (!line.trim()) continue;",
      "    const msg = JSON.parse(line);",
      "    if (msg.id === 999 && msg.result) {",
      "      const roots = msg.result.roots || [];",
      "      root = roots.length ? url.fileURLToPath(roots[0].uri) : process.cwd();",
      '      process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: "ready", result: { root } }) + "\\n");',
      "      continue;",
      "    }",
      '    if (msg.method === "tools/call") {',
      '      const name = (msg.params.arguments || {}).filename || "auto.png";',
      "      const target = path.resolve(root || process.cwd(), name);",
      "      fs.mkdirSync(path.dirname(target), { recursive: true });",
      '      fs.writeFileSync(target, "PNG");',
      '      process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: msg.id, result: { wrote: target } }) + "\\n");',
      "    }",
      "  }",
      "});",
      'process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: 999, method: "roots/list" }) + "\\n");'
    ].join("\n")
  );

  /** Drive the roots-aware stub; `roots` is what the client answers with. */
  const runRoots = (roots, filename) =>
    new Promise((resolve) => {
      const proc = spawn("node", [PROXY, outDir, "node", rootsStub], {
        stdio: ["pipe", "pipe", "pipe"]
      });
      let result = {};
      let acc = "";
      let seenRoot;
      const finish = (r) => {
        result = r;
        proc.kill();
      };
      const timer = setTimeout(() => finish({}), 15000);
      // Windows keeps the temp dir busy until the child is really gone, so the
      // caller must not proceed to cleanup on "killed", only on "close".
      proc.on("close", () => {
        clearTimeout(timer);
        resolve(result);
      });
      proc.stdout.on("data", (c) => {
        acc += c;
        let i;
        while ((i = acc.indexOf("\n")) !== -1) {
          const line = acc.slice(0, i);
          acc = acc.slice(i + 1);
          if (!line.trim()) continue;
          let m;
          try {
            m = JSON.parse(line);
          } catch {
            continue;
          }
          if (m.method === "roots/list") {
            proc.stdin.write(
              `${JSON.stringify({ jsonrpc: "2.0", id: m.id, result: { roots } })}\n`
            );
          } else if (m.id === "ready") {
            seenRoot = m.result.root;
            proc.stdin.write(`${JSON.stringify(shot(80, { filename }))}\n`);
          } else if (m.id === 80) {
            finish({ wrote: m.result?.wrote, seenRoot, refused: refused(m) });
          }
        }
      });
    });

  const inside = (f) =>
    typeof f === "string" && (f === outDir || f.startsWith(outDir + path.sep));

  // The client names the project directory, as a real MCP client does.
  const projectRoots = [{ uri: pathToFileURL(tmp).href, name: "project" }];
  const withRoots = await runRoots(projectRoots, "bare.png");
  check(
    "bare name contained despite client roots",
    inside(withRoots.wrote) && !fs.existsSync(path.join(tmp, "bare.png")),
    withRoots.wrote ?? "(no reply)"
  );
  check(
    "client roots rewritten to the output dir",
    withRoots.seenRoot === outDir,
    withRoots.seenRoot ?? "(absent)"
  );

  // Fallback branch: a client advertising no roots must still be contained.
  const noRoots = await runRoots([], "bare2.png");
  check(
    "no-roots client falls back inside outDir",
    inside(noRoots.wrote),
    noRoots.wrote ?? "(no reply)"
  );

  // The rewrite must not become a way past the gate.
  const traversal = await runRoots(projectRoots, "../escaped.png");
  check(
    "roots path still refuses traversal",
    traversal.refused === true && !fs.existsSync(path.join(tmp, "escaped.png")),
    traversal.refused ? "gate intact" : "LEAKED"
  );
}

fs.rmSync(tmp, { recursive: true, force: true });
console.log(`\n${pass} passed, ${fail} failed, ${skip} skipped`);

// A skipped assertion is loud but not fatal by default: on a Windows box with
// neither symlink nor junction available, failing the whole suite would punish
// a platform limitation. Automation gating on this suite should set
// PLAYWRIGHT_MCP_TEST_STRICT=1, because a containment vector that did not run
// is not a containment vector that held.
const strict = process.env.PLAYWRIGHT_MCP_TEST_STRICT === "1";
if (skip && strict) {
  console.log("STRICT: skipped assertions count as failures");
}
process.exit(fail || (strict && skip) ? 1 : 0);
