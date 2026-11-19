# AGENTS.md

本仓库是 `omni`（OMNI 元工具链）命令行工具。README 面向用户，本文件写给在这个仓库里
改代码的 agent：**动 CLI 之前先读「CLI 呈现规范」，改完按「改完要跑」验证，能力有变按
「能力变更要同步 skill」更新手册，并按「版本号」升版本。**

- 用户文档 / 命令参考：`README.md`
- 版本历史：`docs/changelog.md`
- 后端协议：`docs/backend-protocol.md`

## CLI 呈现规范

`omni` 的终端呈现向 `python -h`、`pip -h` 这类成熟命令行工具看齐：**靠排版和文字表达，
不靠颜色和装饰符号。**

### 帮助输出

- **不加颜色**：`omni -h` 及各级子命令帮助都不输出 ANSI 转义；不要在帮助渲染里引入
  `Palette` / `color_enabled`。颜色只属于 `omni build --color` 这类显式开关控制的输出。
- **不加装饰符号**：不用 `▍`、`›`、`✓`、`✗`、`·`、`⏳` 之类符号表达层级或状态；
  层级用缩进表达，状态用文字表达。
- **分组标题、栏目标题用英文**：`commands:`、`options:`（argparse 自带）、`Examples:`；
  分组名为 `Backend` / `Debug & Flash` / `Project Build` / `Serial / Network / USB`，
  未归类命令落到 `Other`。命令描述、提示与错误保持中文。
- **示例块逐行**：带 `epilog` 示例的 parser 必须传
  `formatter_class=argparse.RawDescriptionHelpFormatter`，否则 argparse 会把多行示例
  压成一段。
- **排版**：命令名按最长名字对齐；帮助文本按终端显示宽度折行（CJK 记 2 列），续行对齐到
  帮助列；中文禁则（`）` 不落行首、`（` 不留行尾）。
- **树形数据视图**例外：`usb list --all` 这类树形数据可以用 `├──` / `└──` / `│` 画层级；
  帮助输出仍只用缩进，不加符号。
- **实现位置**：分组数据在 `omni_cli/cli.py` 的 `COMMAND_CATEGORIES`
  （`(英文组名, 命令名元组)`），排版在 `_CategorizedHelpParser`。不要为分组去改写
  argparse 私有的 `_choices_actions`。

### 帮助文案

- 一条命令一句话：`动词 + 对象（补充说明）`，结尾不加句号，中文用全角括号。
- 术语统一：`默认`（不写「缺省」）、`列出`（不写「枚举」，USB enumeration 例外）、
  `烧录`、`探针`、`目标`、`会话`、`后端`。
- 默认值写成 `（默认 X）`；全局细节（例如 `--timeout` 的分档默认值）只写在对应选项的
  help 里，不要在每条子命令的一句话帮助里重复。
- 跨命令差异（如 `flash reset` 与 `debug reset` 默认值相反）用半句交代，完整说明放 README。

### 提示与进度

- 提示 / 警告 / 错误用纯文本前缀 `提示:` / `警告:` / `错误:`；错误与警告走 stderr。
  跟结果一起读的说明性提示（如 `pack list` 的缺失文件提示）可以留在 stdout，但
  `--json` 模式下 stdout 必须只有 JSON。
- 进度只在 TTY（`stream.isatty()`）里用 `\r` 原地刷新；管道、重定向、CI 下不输出。
- 退出码沿用 README「退出码约定」，不要为了排版改变退出码。

## 改完要跑

```powershell
python -m omni_cli -h           # 顶层分组与对齐
python -m omni_cli flash -h     # 子命令帮助（argparse 原生排版）
python tests/test_serial_cli.py # 帮助分组 / 渲染断言

# 全部离线回归（无需硬件与后端）
Get-ChildItem tests\test_*.py | ForEach-Object { python $_.FullName }
```

- 帮助文案或分组有变动：同步 `tests/test_serial_cli.py` 的断言和 README 命令表。
- 命令、选项或输出格式有变动：按「能力变更要同步 skill」更新 `skills/omni-cli/SKILL.md`。
- 版本历史只写 `docs/changelog.md`；README 里只留一行指针。

## 能力变更要同步 skill

`skills/omni-cli/SKILL.md` 是随 CLI 一起发布的 agent 操作手册，能力变了它就是产品的一部分。
**同一次改动里一起改完，不要留到下次：**

| 变化 | 必须同步 |
| --- | --- |
| 新增/改名/删除命令、选项、默认值 | `skills/omni-cli/SKILL.md` 的示例、`README.md` 命令表、`tests/test_*_cli.py` |
| 输出格式（表格列、JSON 字段、提示文案） | skill 里的示例片段与 `tests/` 断言 |
| 后端字段/协议（如 `transfers` 的 `dataHex`） | skill 注明版本要求与旧后端的降级表现 |
| 退出码、错误码、修复提示 | skill 的排错段落与 README |

判断标准：**skill 里能照抄执行的命令，改完必须还能跑通**；跑不通就是同步漏了。涉及抓包或
后端启动时用 `scripts/dev-bus.ps1`（源码后端）验证，不要为此重新打包安装。

## 版本号

**任何用户可见的改动都必须升版本**，不允许攒着「未发布」等下次：

- 版本号只有一个来源：`omni_cli/__init__.py` 的 `__version__`（`pyproject.toml` 动态读取，
  安装包/发布包的文件名也由它决定，不要手改这些地方）。
- 规则：新增命令/选项、输出格式变化、安装形态变化 → minor（`0.29.0`）；兼容性修复 →
  patch（`0.29.1`）；只有内部重构、测试或文档、用户不可见 → 可以不升。
- 同一次改动里改齐：`docs/changelog.md` 新增该版本段落（标题带日期，不写「未发布」）、
  `README.md` 顶部版本行、`skills/omni-cli/SKILL.md` 的 description 与版本表、
  `skills/omni-cli/scripts/omni_runner.py` 的适配说明。
- 改完重新构建安装包前，先确认 `omni --version` 已经是新版本号。

## 项目速览

- `omni_cli/` 是包；每个命令组一个模块（`omni_cli/commands/*.py`，导出 `add_parser(sub)`）。
- 两层子命令 `omni <group> <sub>`；叶子命令用 `run_command(handler)` 包装，统一处理后端
  连接、异常与退出码。
- 源码直跑：在仓库根执行 `python -m omni_cli ...`（无需安装）；打包/发布用 `scripts/`
  下的脚本，入口是 `build.ps1`。
- 开发循环：`python -m omni_cli ...`（源码 CLI）+ `scripts/dev-bus.ps1`（源码后端），改完
  即生效；打包只用于发版或验证 frozen 产物。安装版 `omni.exe` 只会复用安装目录里的后端。
