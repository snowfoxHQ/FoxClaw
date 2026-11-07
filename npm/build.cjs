/**
 * Copies the full project source into npm/python/ before publishing.
 *
 * The npm package ships the same code as the PyPI sdist and the GitHub repo,
 * so `npx foxclaw` works without a separate pip install.
 */
const fs = require("node:fs");
const path = require("node:path");

const root = path.resolve(__dirname, "..");
const dest = path.join(__dirname, "python");

const SKIP_DIRS = new Set([
  ".git",
  ".venv",
  "venv",
  "node_modules",
  "__pycache__",
  ".pytest_cache",
  ".ruff_cache",
  ".mypy_cache",
  "dist",
  "build",
  "foxclaw_data",
  "foxclaw.egg-info",
  "htmlcov",
]);

// Paths relative to the repo root that must never be published.
const SKIP_REL = new Set([
  "npm", // 包自身,不递归进 python/
  path.join("docs", "internal"),
  path.join("frontend_src", "node_modules"),
]);

function copyDir(src, dst, rel) {
  fs.mkdirSync(dst, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const relChild = rel ? path.join(rel, entry.name) : entry.name;
    if (SKIP_REL.has(relChild)) continue;

    const from = path.join(src, entry.name);
    const to = path.join(dst, entry.name);

    if (entry.isDirectory()) {
      if (SKIP_DIRS.has(entry.name)) continue;
      copyDir(from, to, relChild);
    } else if (entry.isFile()) {
      if (/\.(pyc|pyo)$/.test(entry.name)) continue;
      fs.copyFileSync(from, to);
    }
  }
}

fs.rmSync(dest, { recursive: true, force: true });
copyDir(root, dest, "");

function countFiles(dir) {
  let n = 0;
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    n += e.isDirectory() ? countFiles(path.join(dir, e.name)) : 1;
  }
  return n;
}

console.log(`[foxclaw] bundled ${countFiles(dest)} files into npm/python/`);
