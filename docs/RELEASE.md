# 发布流程

> **当前状态(2026-11-12 核实)。** 已不再整体暂缓,发版按需单通道进行。
>
> 两个 registry 的现状(`npm view foxclaw versions` / PyPI JSON API 实测,别再当"全新包"):
>
> | registry | 已占用版本 | latest |
> | --- | --- | --- |
> | PyPI `foxclaw` | 0.1.0 / 0.1.2 / 0.1.3 / 0.1.4 | 0.1.4 |
> | npm `foxclaw` | 0.0.0-stage / 0.1.0 / 0.1.2 / 0.1.3 / 0.1.4 | 0.1.4 |
>
> - **0.1.4 两通道都已发**(2026-11-12 05:42 CST = 2026-11-11T21:42Z,PyPI 与 npm 同一次
>   `workflow_dispatch` 的 `publish=both` 发出):tag 打在 `d61a8f2`,Release 带 3 个附件;
>   PyPI wheel 1.33 MB / 298 条目,`foxclaw/web/` 24 个文件 —— **前端首次进 wheel**,
>   `pip install foxclaw` 后 `foxclaw serve` 首页可用。npm 侧 530 文件 / 4.85 MB。
> - **0.1.3 两通道都已发**(2026-11-11:npm 15:08 UTC、PyPI 15:28 UTC),用的是同一个
>   `v0.1.3` tag:先 tag run 构建 + GitHub Release,再两次 `workflow_dispatch`
>   (`publish=npm` / `publish=pypi`)。PyPI 上传即时可见;npm 有**数分钟传播延迟**:
>   发布后 5 分钟直查 `registry.npmjs.org/foxclaw/0.1.3` 仍是 404、`latest` 仍是 0.1.2,
>   约 6 分钟后才转 200。
>   **所以 npm 发布后立刻查不到是正常现象,等 10 分钟再看**,不要在这期间重试——
>   重发只会拿到 `cannot publish over previously published version`。
>   若 30 分钟后仍 404,才需要去 npmjs.com 查包页与账号通知(扫描挂起、邮箱验证)。
> - 历史:0.1.0(2026-11-06)两通道首版;0.1.1 只在 npm 手工发过、git 里无对应提交,已删;
>   0.1.2(2026-11-09)、0.1.3(2026-11-11)两通道都发。
>
> 下次发版的可用号:两个 registry 都需 **> 0.1.4**。

两条发布通道、两种产物。**后端运行时是同一份代码**,差别只在打包方式:

| 产物 | 命令 | 里面有什么 |
| --- | --- | --- |
| PyPI `foxclaw` | `pip install foxclaw` | 0.1.4 起:后端(`foxclaw` 181 + `hmr` 21 + `hkc_*` 60)+ 前端 `foxclaw/web/` 24 文件(约 1.7 MB 原始 / wheel 内约 1.3 MB);**0.1.3 及之前的 wheel 不含前端 UI** |
| npm `foxclaw` | `npm i foxclaw` | 530 文件(0.1.4 实测):整棵源码树,`python/foxclaw` + `python/vendor`(hmr/hkc 原布局)+ `python/frontend` 23 个构建产物 + tests/tools/bench/docs |
| GitHub Release | tag 自动 | 上面两份产物 + 自动生成的 release notes |

差异来自打包机制,不是"一个是残包":

- setuptools 只能收 import 单元,于是 `pyproject.toml` 用 `where = [".", "vendor", "vendor/hkc"]` +
  `include = ["foxclaw*", "hmr*", "hkc_*"]`,把 vendor 内的包**提升为顶层包**(`hmr`、`hkc_ace`…)
  装到 site-packages 根下;wheel 里因此**没有** `vendor/` 目录,`_ensure_hmr_importable` /
  `_ensure_hkc_importable` 对 wheel 布局走"顶层包直接可导入"的分支。
- npm 侧 `build.cjs` 把仓库根 `copyDir` 到 `npm/python/`,保留 `vendor/` 原布局,运行期按
  `Path(__file__)/../../vendor` 注入 sys.path。多出来的 252 个文件是 tests(113)、frontend_src(43)、
  frontend(23)、tools/bench/evals/examples/skills/docs,运行时不依赖。

**前端 UI 的来路(0.1.4 起已修好)**:`frontend/` 在仓库根、不属于任何包,setuptools 收不到,
所以构建前必须先跑 `tools/bundle_web.py`,把它复制成 `foxclaw/web/`(已 gitignore),再由
`[tool.setuptools.package-data]` 带进 wheel。`_mount_frontend` 优先用源码仓的 `frontend/`
(改前端即时生效),回退包内 `foxclaw/web/`(wheel 安装)。漏跑脚本不会静默出错:下一步的
`release_check.py --dist` 会以 `missing_web_ui` 报 error,流水线停下,发不出首页 404 的包。

**0.1.3 及之前的 PyPI 包没有前端**:`pip install` 后 `foxclaw serve` 首页 404,只有 API/CLI。
0.1.4 起已修并实测:wheel 内含 `foxclaw/web/` 24 个文件。
这类版本不要再说成"知识/记忆子系统降级为 None"—— HMR/HKC 从 0.1.2 起就已完整在 wheel 里
(实测 hmr 21 + hkc_* 60)。npm 包虽然带前端,但不带 Python 第三方依赖(torch/faiss 等),
装完同样要自备运行环境。

## 1. 本地预检(发布前必须绿)

```bash
python tools/release_check.py --tag v0.1.4          # 五处版本号一致 + tag 对得上
python tools/bundle_web.py                           # frontend/ → foxclaw/web/(不跑则 wheel 无 UI)
python -m build                                      # 出 dist/*.whl + *.tar.gz
cd npm && node build.cjs && npm pack --pack-destination ../dist && cd ..
python tools/release_check.py --dist dist --npm-tgz dist/*.tgz
```

`npm pack` 前必须先跑 `node build.cjs`:它负责把源码复制进 `npm/python/`,
漏了这步打出来的是空壳(`npm pack` 不触发 `prepublishOnly`)。CI 里已经内置这一步。

第二道检查会做这些事,任一 `[ERROR]` 都不许发布:

- `dist/` 里恰好一个 wheel + 一个 sdist,文件名与版本号一致;
- wheel 的 `METADATA` 里 `Name` / `Version` 与 `pyproject.toml` 一致;
- 归档内**不得**出现 `.env`(除 `.env.example`)、`docs/internal/`、`foxclaw_data/`、
  `__pycache__`、嵌套压缩包、`.venv`;
- npm tgz 文件名与 `package/package.json` 的 `name@version` 一致;
- 缺 `vendor/` 只报 `[WARN]`(见上表),不会拦发布。

版本号的唯一来源是 `pyproject.toml`,另外四处(`foxclaw/__init__.py`、
`npm/package.json`、`npm/index.js`、`frontend_src/package.json`)由
`tests/test_version_sync.py` 盯着必须相等。
改版本时**五处一起改**,只改一处会在第一道检查里挂掉。

## 2. 打 tag(自动构建 + 挂 Release,不外发)

```bash
git fetch origin
git tag -a v0.1.4 "$(git rev-parse origin/main)" -m "v0.1.4"
git push origin v0.1.4
```

> **tag 必须打在 `origin/main` 上,不是本地 HEAD。** 本地 `personal` 分支是开发分支,
> 含未导出的内部文档与中间提交;`origin/main` 由 `tools/sync_public.ps1` 从 HEAD 重新构造
> (同一份树、不同 SHA)。把 tag 打在本地 HEAD 再 push,会把内部历史一起推到公开仓,
> 导出白名单形同虚设。所以先 `git fetch origin`,用 `origin/main` 的 SHA 打 tag ——
> 前提是这次要发的改动已经 `sync_public.ps1 -Apply` 过。

`.github/workflows/release.yml` 的 `build` job 会:校验版本/tag → 构建 → `build.cjs` +
npm pack → 产物结构校验 → 上传 `python-dist` / `npm-tgz` 两个 artifact;
`github-release` job 把它们挂成 Release 附件(重跑用 `--clobber` 覆盖附件,不重建 Release)。

这一步**不会**上传到 PyPI / npm,打错了 `git push --delete origin vX.Y.Z` 再重来即可。

## 3. 上传到 PyPI / npm

Actions → Release → Run workflow:

- `publish`:选 `pypi` / `npm` / `both`
- `ref`:填**同一个 tag**(如 `v0.1.4`;不填会直接失败——不允许发分支 HEAD)

| environment | 执行 | 用的 secret |
| --- | --- | --- |
| `pypi` | `pypa/gh-action-pypi-publish` | `PYPI_API_TOKEN`(项目级 `pypi-`/`npt_` token) |
| `npm` | `npm publish` | `NPM_TOKEN`(granular,仅 `foxclaw` 的 publish 权限) |

> **审批门当前不存在(2026-11-11 核实)。** `gh api repos/snowfoxHQ/FoxClaw/environments/npm`
> 返回的 `protection_rules` 是 `[]`,`pypi` 同理:两个环境只建了、没配 Required reviewers。
> 也就是说 `publish` 一旦触发就**直接外发**,中间没有任何人工停顿 —— 0.1.3 的 npm 就是
> 这么出去的。想要真正的审门,先去 Settings → Environments 给 `pypi` / `npm` 各加
> Required reviewers,再触发发布。

## 4. 发版后回写现状并同步(每次都做)

发布成功后,把实测结果回写本文开头的现状表(已占版本、latest、产物条目数、传播延迟),
提交后**立刻**跑 `tools/sync_public.ps1 -Apply` 推到 `origin/main`。不要攒到下次发版——
攒着只会让公开仓的文档持续失真,而且差异越积越大,下次同步越容易出错。

tag 是不可变快照,`origin/main` 是不断前进的公开状态,两者 SHA 不同是**正常语义**,
不是"不一致"。唯一的判断标准是「公开仓里的文档与事实是否相符」;为了"让 tag 等于 main"
而推迟同步是错的方向。

## 5. 发错了怎么办

**PyPI / npm 的版本号不可撤回**。能做的只有:

- PyPI:`yank`(`twine`/网页)→ 该版本不再被新装选中,但**仍然占着号**,不能重发同号;
  修好后必须发一个更高的版本号。
- npm:`npm unpublish foxclaw@x.y.z` 只在发布后 72 小时内、且无其他包依赖时可用;
  过了就只能 `npm deprecate` + 发新号。

所以"tag 先构建、上传再单独触发"不是流程洁癖,是这两个 registry 的规则逼出来的:
把不可逆的那一步单独隔离,前面每一步都能重跑。

## 6. 切到 OIDC(零长期密钥)

当前用 token。想彻底去掉长期密钥时:

- PyPI:项目页 → Publishing → 加 GitHub publisher(owner/repo/workflow/environment
  必须与实际一致),首次发布用 "pending publisher";然后删掉 secret,给 job 加
  `permissions: id-token: write`。
- npm:npm ≥ 11.5 支持 trusted publishing,同样加 `id-token: write` 并去掉 `NODE_AUTH_TOKEN`。

## 仓库(2026-11-12 订正)

只有一个仓库:`snowfoxHQ/FoxClaw`(public)。`origin` 就指向它,没有第二个开发库。

| 位置 | 用途 |
|---|---|
| `origin/main` | 唯一对外分支。由 `tools/sync_public.ps1` 从本地 HEAD 构造后推送,只含导出白名单 |
| 本地 `personal` | 开发分支。内部文档与中间提交都在这里,不直接 push 到 `origin` |

两者的 SHA 天然不同(同一份树、不同父历史),所以 tag 只认 `origin/main`。

旧名 `SnowFoxClaw` 已不存在——GitHub 上查无此仓,也没有改名跳转。切 OIDC 时 publisher
的 repo 必须写 `FoxClaw`:registry 侧对大小写不敏感,GitHub 侧 OIDC 校验按仓库真实名匹配,
`foxclaw` 全小写能打开网页,校验却会失败。
