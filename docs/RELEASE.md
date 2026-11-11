# 发布流程

> **当前状态(2026-11-11 核实)。** 已不再整体暂缓,发版按需单通道进行。
>
> 两个 registry 的现状(`npm view foxclaw versions` / PyPI JSON API 实测,别再当"全新包"):
>
> | registry | 已占用版本 | latest |
> | --- | --- | --- |
> | PyPI `foxclaw` | 0.1.0 / 0.1.2 | 0.1.2 |
> | npm `foxclaw` | 0.0.0-stage / 0.1.0 / 0.1.2 / 0.1.3 | 0.1.2 |
>
> - **0.1.3 只发了 npm**(2026-11-11,`publish=npm`)。CI 侧 `npm publish` 正常返回
>   `+ foxclaw@0.1.3`,但 5 分钟后直查 `registry.npmjs.org/foxclaw/0.1.3` 仍是 404,
>   `dist-tags.latest` 仍是 0.1.2 —— 典型的 registry 处理/安全扫描挂起。
>   **号大概率已被占,不要重试**(重发只会拿到 `cannot publish over previously published version`)。
>   等 15~30 分钟再查;仍无则去 npmjs.com 看包页与账号通知(扫描挂起、邮箱验证)。
> - PyPI 停在 **0.1.2**,0.1.3 未上传。
> - 历史:0.1.0(2026-11-06)两通道首版;0.1.1 只在 npm 手工发过、git 里无对应提交,已删;
>   0.1.2(2026-11-09)两通道都发。
>
> 下次发版的可用号:PyPI 需 **> 0.1.2**,npm 需 **> 0.1.3**。

两条发布通道、两种产物,**性质不同,别混着写发布说明**:

| 产物 | 命令 | 里面有什么 |
| --- | --- | --- |
| PyPI `foxclaw` | `pip install foxclaw` | **占位包**:能 import,但 `vendor/`(HKC/HMR)不入包 → 知识/记忆子系统降级为 None |
| npm `foxclaw` | `npm i foxclaw` | **不是占位包**:`build.cjs` 把源码整树(含 `vendor/`)复制进 `python/`;0.1.3 实测 514 文件 / 包 1.7 MB / 解包 4.9 MB |
| GitHub Release | tag 自动 | 上面两份产物 + 自动生成的 release notes |

PyPI 侧 `vendor/` 不入包是设计现状(`packages.find` 只收 `foxclaw*`,HMR/HKC 以 sys.path
内嵌,运行期按 `Path(__file__)/../../vendor` 找)。**PyPI 的发布说明必须写明"这是占位包"**,
不要让人装完以为拿到了完整运行时。npm 包虽然带 `vendor/`,但不带 Python 运行时的第三方
依赖(torch/faiss 等),装完同样跑不起来,说明里要写清"需自备 Python 环境与依赖"。

## 1. 本地预检(发布前必须绿)

```bash
python tools/release_check.py --tag v0.1.4          # 五处版本号一致 + tag 对得上
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

> **tag 必须打在公开仓的导出提交上,不是本地 HEAD。** 本地是私有开发库,公开仓的 HEAD
> 由 `tools/sync_public.ps1` 重新构造(同一份树、不同 SHA)。把 tag 打在本地 HEAD 再 push,
> 会把私有库的完整历史推到公开仓,导出白名单形同虚设。所以先 `git fetch origin`,
> 用 `origin/main` 的 SHA 打 tag —— 前提是这次要发的改动已经 `sync_public.ps1 -Apply` 过。

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

## 4. 发错了怎么办

**PyPI / npm 的版本号不可撤回**。能做的只有:

- PyPI:`yank`(`twine`/网页)→ 该版本不再被新装选中,但**仍然占着号**,不能重发同号;
  修好后必须发一个更高的版本号。
- npm:`npm unpublish foxclaw@x.y.z` 只在发布后 72 小时内、且无其他包依赖时可用;
  过了就只能 `npm deprecate` + 发新号。

所以"tag 先构建、上传再单独触发"不是流程洁癖,是这两个 registry 的规则逼出来的:
把不可逆的那一步单独隔离,前面每一步都能重跑。

## 5. 切到 OIDC(零长期密钥)

当前用 token。想彻底去掉长期密钥时:

- PyPI:项目页 → Publishing → 加 GitHub publisher(owner/repo/workflow/environment
  必须与实际一致),首次发布用 "pending publisher";然后删掉 secret,给 job 加
  `permissions: id-token: write`。
- npm:npm ≥ 11.5 支持 trusted publishing,同样加 `id-token: write` 并去掉 `NODE_AUTH_TOKEN`。

## 仓库名(2026-11-08 已定)

两个仓库,职责不同,别再混:

| 仓库 | 可见性 | 用途 |
|---|---|---|
| `snowfoxHQ/FoxClaw` | public | 对外发布。PyPI/npm 的 `homepage`/`repository` 都指它,OIDC publisher 也填它 |
| 私有开发库(名称不对外) | private | 本地 `git remote origin` 指向的开发备份 |

公开仓库原名 `SnowFoxClaw`,已于 2026-11-08 改名为 `FoxClaw`(GitHub 对旧名自动 301,
老链接不会 404)。切 OIDC 时 publisher 的 repo 必须写成 `FoxClaw`——不是私有开发库的名字,
也不是小写 `foxclaw`(registry 侧对大小写不敏感,GitHub 侧 OIDC 校验按仓库真实名匹配)。
