# 内容闸门与敏感词表

内容闸门在**用户请求**和**模型输出**两端做确定性检查，命中时以固定话术结束回应。它不调用模型、不做语义理解，也不替代平台审核或人工审核——提示词负责让模型自己不越线，这一层负责在越线时守住下限。

内置规则默认开启，只覆盖明确请求型的违法有害内容。判定按「动作词 + 对象」组合进行，正常提及（如一本书、一篇论文中的相关名词）不会触发。

仓库不附带任何政治 / 色情 / 暴力等大类词表。是否挂载、挂哪几类，由部署方自行决定。

## 三种模式

```bash
FOXCLAW_CONTENT_GUARD_MODE=enforce   # 默认：拦截
FOXCLAW_CONTENT_GUARD_MODE=audit     # 只记录不拦截，用于观察误伤
FOXCLAW_CONTENT_GUARD_MODE=off       # 关闭
```

写错的值会回退到 `enforce`，不会静默失效。`audit` 档的命中记录输出到进程 stdout，包含类别与命中的词。

## 配置写在哪

- **`.env`（推荐）**：服务启动时读取，改完需重启。
- **系统环境变量**：直接以 SDK 方式使用时需在进程启动前设置。

`.env` 加载可通过 `FOXCLAW_SKIP_DOTENV=1` 关闭。

## 词表格式

一行一条，UTF-8，`#` 开头为注释：

```
# 词<TAB>类别，类别可省略
某词	politics
另一个词	sexual
不带类别的词
```

加载规则：

| 项 | 行为 |
|---|---|
| 分隔符 | `词<TAB>类别`；没有 TAB 时整行是词，类别记为 `platform` |
| 注释 | `#` 开头的整行跳过 |
| 归一化 | 去空白、去中英标点、全半角归一、转小写 |
| 长度 | 归一化后少于 2 个字符的词丢弃 |
| 同词多类别 | 允许，命中时按第一个类别记录 |
| 坏行 / 读不到的文件 | 静默跳过，不影响服务启动 |
| 生效时机 | 进程启动时建索引，改词表需重启 |

建议使用绝对路径；相对路径按进程工作目录解析。

## 从第三方词库转换

`tools/build_content_lexicon.py` 用于把现成词库转换成所需格式。脚本在仓库中，不随 wheel 分发。

```bash
python tools/build_content_lexicon.py sensitive_word_dict.txt -o content_lexicon.txt
python tools/build_content_lexicon.py dict/*.txt -o content_lexicon.txt
python tools/build_content_lexicon.py dict/*.txt --exclude 政治类,色情类 -o content_lexicon.txt
python tools/build_content_lexicon.py ads.txt --category ads -o content_lexicon.txt
```

它会检测编码（UTF-8 / GB18030）、按类别筛选、归一化重复写法，并在转换后回读验证。

| 参数 | 默认 | 说明 |
|---|---|---|
| `-o, --output` | `content_lexicon.txt` | 输出路径 |
| `--category` | `platform` | 文件名不含中文时使用的类别名 |
| `--include` / `--exclude` | 无 | 类别筛选，支持 `通配*` |
| `--encoding` | `auto` | 可强制指定 |
| `--min-len` | `2` | 短于该长度丢弃 |
| `--max-len` | `0` | 长于该长度丢弃，0 为不限 |
| `--sample` | `5` | 验证抽样条数 |
| `--no-verify` | 关 | 跳过载入验证 |

转换产物已在 `.gitignore` 中排除，不要提交进仓库。

## 挂载前先跑 audit

大词表中通常含有大量双字词，直接启用拦截可能切断正常的订单讨论、书稿审阅或医学问答。建议流程：

1. 挂上词表，设为 `audit`。
2. 正常使用几天，收集 stdout 中的命中记录。
3. 查看哪些是该拦的，哪些是误伤。
4. 误伤较多的类别用 `--exclude` 摘掉，或换更长的词条重新生成。
5. 确认可接受后再切到 `enforce`。

没有白名单机制；降低误伤的手段是少挂类别、使用更长的词条，或调高 `--min-len`。

## 排查清单

| 现象 | 检查 |
|---|---|
| 完全不拦 | 模式是否为 `off`；环境变量是否真的进入进程 |
| 内置规则生效、词表不生效 | 加载条数是否为 0；路径是相对还是绝对；文件编码 |
| 某些词没拦到 | 归一化后是否不足 2 字被丢弃；词表里是否有空格 |
| 改了词表没变化 | 进程是否重启 |
| 正常对话被切断 | 词表双字词过多，先降为 `audit` 或摘掉该类 |

## 责任边界

这一层做的是归一化后的字符串匹配，不保证覆盖率，也不构成内容审核或合规服务。对外提供服务时的内容合规责任在部署方自身。软件按「现状」提供，详见 [LICENSE](../LICENSE)。
