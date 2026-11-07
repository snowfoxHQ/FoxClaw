#!/usr/bin/env node
/**
 * foxclaw CLI - runs the full Python project bundled in python/.
 *
 * Resolves a Python interpreter, points PYTHONPATH at the bundled source tree,
 * and forwards every argument to `python -m foxclaw.cli`.
 */
const { spawn } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const pkgRoot = path.resolve(__dirname, "..");
const bundled = path.join(pkgRoot, "python");
const hasBundled = fs.existsSync(path.join(bundled, "foxclaw", "cli.py"));

const env = { ...process.env };
if (hasBundled) {
  env.PYTHONPATH = env.PYTHONPATH
    ? `${bundled}${path.delimiter}${env.PYTHONPATH}`
    : bundled;
}

const candidates = [
  process.env.FOXCLAW_PYTHON,
  process.platform === "win32" ? "python" : "python3",
  "python",
].filter(Boolean);

function run(python) {
  return new Promise((resolve) => {
    const child = spawn(python, ["-m", "foxclaw.cli", ...process.argv.slice(2)], {
      stdio: "inherit",
      env,
      cwd: process.cwd(),
    });
    child.on("error", () => resolve(false));
    child.on("exit", (code, signal) => resolve({ code, signal }));
  });
}

(async () => {
  for (const python of candidates) {
    const result = await run(python);
    if (result === false) continue; // interpreter missing, try next
    process.exit(typeof result.code === "number" ? result.code : 1);
  }
  console.error(
    "[foxclaw] Python 3.11+ not found. Install it, or set FOXCLAW_PYTHON to the interpreter path."
  );
  process.exit(127);
})();
