# 发布流程

> **当前状态(2026-11-07):发布暂缓。** 开发全部完成后再一次性发,现在只往 GitHub 推代码。
> 因此**不要**打 `v*` tag(`.github/workflows/release.yml` 由 tag 触发),也**不要**跑
> `workflow_dispatch` 的 `publish` 选项。流水线已经就位,等要发时按下面走。
>
> 另外两个 registry 的现状(查过,别再当"全新包"处理):
>
> - PyPI `foxclaw` 已有 **0.1.0**(2026-11-06 上传,不能重发,只能 yank);
> - npm `foxclaw` 已有 `0.0.0-stage` / `0.1.0` / **0.1.1**(latest),而仓库代码是 0.1.0
>   —— 那个 0.1.1 是手工 `npm publish` 后没提交,git 里找不到对应版本。
>
> 所以下次真正发布时,四处版本号必须先跳到 **≥ 0.1.2**(PyPI 只需 >0.1.0,npm 需 >0.1.1,取大)。

两条发布通道、两种产物,性质完全不同,先说清楚免得误会:

| 产物 | 命令 | 里面有什么 |
| --- | --- | --- |
| PyPI `foxclaw` | `pip install foxclaw` | **占位包**:能 import,但 `vendor/`(HKC/HMR)不入包 → 知识/记忆子系统降级为 None |
| npm `foxclaw` | `npm i foxclaw` | 占位包:`index.js` 只导出 `name` / `version` |
| GitHub Release | tag 自动 | 上面两个包的文件 + 自动生成的 release notes |

`vendor/` 不入包是设计现状(`packages.find` 只收 `foxclaw*`,HMR/HKC 以 sys.path
内嵌,运行期按 `Path(__file__)/../../vendor` 找)。发布说明里必须写明"这是占位包",
不要让人装完以为拿到了完整运行时。

## 1. 本地预检(发布前必须绿)

```bash
python tools/release_check.py --tag v0.1.0          # 四处版本号一致 + tag 对得上
python -m build                                      # 出 dist/*.whl + *.tar.gz
cd npm && npm pack --pack-destination ../dist && cd ..
python tools/release_check.py --dist dist --npm-tgz dist/*.tgz
```

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
git tag v0.1.0 && git push origin v0.1.0
```

`.github/workflows/release.yml` 的 `build` job 会:校验版本/tag → 构建 → npm pack →
产物结构校验 → 上传 `python-dist` / `npm-tgz` 两个 artifact;`github-release` job 把它们
挂成 Release 附件(重跑用 `--clobber` 覆盖附件,不重建 Release)。

这一步**不会**上传到 PyPI / npm,打错了删 tag 重来即可。

## 3. 上传到 PyPI / npm(需要人工审批)

Actions → Release → Run workflow:

- `publish`:选 `pypi` / `npm` / `both`
- `ref`:填**同一个 tag**(如 `v0.1.0`;不填会直接失败——不允许发分支 HEAD)

然后经由 environment 审批:

| environment | 审批后执行 | 用的 secret |
| --- | --- | --- |
| `pypi` | `pypa/gh-action-pypi-publish` | `PYPI_API_TOKEN`(项目级 `pypi-`/`npt_` token) |
| `npm` | `npm publish` | `NPM_TOKEN`(granular,仅 `foxclaw` 的 publish 权限) |

需要在仓库 Settings → Environments 里先建 `pypi`、`npm` 两个环境并配 Required reviewers,
否则"审批门"不存在,勾了就直接发。

## 4. 发错了怎么办

**PyPI / npm 的版本号不可撤回**。能做的只有:

- PyPI:`yank`(`twine`/网页)→ 该版本不再被新装选中,但**仍然占着号**,不能重发同号;
  修好后必须 `0.1.1`。
- npm:`npm unpublish foxclaw@0.1.0` 只在发布后 72 小时内、且无其他包依赖时可用;
  过了就只能 `npm deprecate` + 发新号。

所以"先构建、再审批上传"不是流程洁癖,是这两个 registry 的规则逼出来的。

## 5. 切到 OIDC(零长期密钥)

当前用 token,因为上传已有人工审批兜底。想彻底去掉长期密钥时:

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
