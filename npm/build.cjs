/**
 * Copies the full project source into npm/python/ before publishing.
 *
 * The npm package ships the same code as the PyPI sdist and the GitHub repo,
 * so `npx foxclaw` works without a separate pip install.
 *
 * 排除规则是**红线**,不是优化:0.1.1 因为只排了 docs/internal,npm 上公开的
 * 包里带着 docs/superpowers 设计稿和 docs/reports 审计报告,任何人都能下载。
 * 所以这里用「相对仓库根的正则白名单式排除」+ 复制后自检,自检命中直接中止
 * publish(fail closed),宁可不出包也不外发内部文档。
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

// 相对仓库根的路径(统一用 / 分隔):绝对不进包的条目。
const SKIP_PATTERNS = [
  /^npm(\/|$)/,                                  // 包自身,不递归进 python/
  /^frontend_src\/node_modules(\/|$)/,
  /^docs\/internal(\/|$)/,                       // 内部文档
  /^docs\/reports(\/|$)/,                        // 审计报告
  /^docs\/superpowers(\/|$)/,                    // 设计稿/计划
  /^docs\/(FIX_EXECUTION_REPORT|REFACTOR_PLAN)\.md$/,
  /^docs\/SYSTEM_REVIEW_[^/]*\.md$/,
  /(^|\/)\.tmp_srv\./,                           // 本地调试残留
  /(^|\/)\.env$/,                                // 真实凭据
  /(^|\/)\.env\.local$/,
];

const isSkipped = (rel) => SKIP_PATTERNS.some((re) => re.test(rel));

function copyDir(src, dst, rel) {
  fs.mkdirSync(dst, { recursive: true });
  for (const entry of fs.readdirSync(src, { withFileTypes: true })) {
    const relChild = rel ? `${rel}/${entry.name}` : entry.name;
    if (isSkipped(relChild)) continue;

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

// 落盘自检:python/ 里残留任何禁止条目就整体删掉并让 npm publish 失败。
const violations = [];
(function scan(dir, rel) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const relChild = rel ? `${rel}/${e.name}` : e.name;
    if (isSkipped(relChild)) {
      violations.push(relChild);
    } else if (e.isDirectory()) {
      scan(path.join(dir, e.name), relChild);
    }
  }
})(dest, "");

if (violations.length) {
  fs.rmSync(dest, { recursive: true, force: true });
  console.error(
    `[foxclaw] 打包中止:python/ 含 ${violations.length} 个禁止条目:\n  ` +
      violations.slice(0, 10).join("\n  "),
  );
  process.exit(1);
}

function countFiles(dir) {
  let n = 0;
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    n += e.isDirectory() ? countFiles(path.join(dir, e.name)) : 1;
  }
  return n;
}

console.log(`[foxclaw] bundled ${countFiles(dest)} files into npm/python/`);
