# build-dir-tree

重建 **LMC / Moodle** 课程页的本地目录树 —— 把浏览器保存的课程页快照（`.mhtml`）解析成
「章节 → 子章节 → 活动」的层级，并据此生成目录骨架、JSON 规格或独立的 PowerShell 脚本。

零第三方依赖，只用 Python 3.8+ 标准库。

> 本项目是 [xjtlu-lmc-downloader](../README.md) 的一个组件：先把课程结构还原出来，
> 下载器再按这份结构落盘。单独用也完全可以。

---

## 为什么需要它

从 Learning Mall Core 下载下来的课程资料是一堆平铺文件，看不出课程原本的组织方式：
哪个是 Week 1 的讲义、哪个是 Tutorial 的 workbook、Lab 编号为什么跳号。
本工具从**页面快照**里恢复真实层级，让你在本地按课程原样建目录，再往里放下载好的文件。

它解决的具体问题：

- Moodle 的 section 有父子关系（`Week 1` → `Week 1-Tutorial`），这种关系只存在于页面 DOM 里；
- `Lab` 与 `Week` 在页面中**同级混排**（Lab 1 排在 Week 2 和 Week 3 之间），不是独立分类；
- 活动标题含 `[LECTURE]` / `[EXTRA-READING]` 等前缀、`&`、括号、`:` 等字符，其中 `:` 之类直接当目录名在 Windows 上非法；
- 手工数一遍容易错，换个学期又得重来一次。

## 安装

`python -m build_dir_tree` **必须先安装**（或让 Python 找得到这个包），否则会报
`No module named build_dir_tree`。三选一：

> **三个常见误区**，先看这段能省一次报错：
> 1. 克隆下来不等于装好了 —— `pip install .` 这一步不能跳。
> 2. `cd` 要进 **`build-dir-tree`** 这个子目录，不是仓库根目录。
> 3. `ls` / `pwsh` 在 `cmd.exe` 里并不存在，要先敲 `powershell`。

### 方式 A：普通安装（推荐，装一次即可，之后可以随便移动代码目录）

```bash
git clone https://github.com/zhuhaoxiangAndy/xjtlu-lmc-downloader.git
cd xjtlu-lmc-downloader/build-dir-tree
python -m pip install .
```

注意是 `pip install .`，**不要**加 `-e`。加了 `-e` 是「可编辑安装」，它只记录源目录路径；
你一旦移动或重命名那个目录，`import build_dir_tree` 立刻失效，报
`No module named build_dir_tree`。普通安装会把代码复制进 Python 环境，与源目录无关。

> 如果你已经用 `-e` 装过又移动了目录：
> `python -m pip uninstall -y build-dir-tree`，然后在新的目录里 `python -m pip install .`。

### 方式 A2：可编辑安装（要改代码时用）

想在仓库里改代码并即时生效，才用这个。代价是**不能移动那个目录**：

```bash
cd xjtlu-lmc-downloader/build-dir-tree
python -m pip install -e .
```

装完后在**任何目录**都能用：

```bash
python -m build_dir_tree course.mhtml --summary
build-dir-tree course.mhtml --summary          # 等价的命令行入口
build-dir-tree-ui                              # 图形界面
```

要求 Python 3.8+。零第三方依赖。

### 方式 B：不安装，直接跑（临时试用）

在仓库的 `build-dir-tree` 目录里执行，用 `-m` 前先设 `PYTHONPATH`：

```bash
cd xjtlu-lmc-downloader/build-dir-tree
python run.py course.mhtml --summary
```

或：

```powershell
$env:PYTHONPATH = "."
python -m build_dir_tree course.mhtml --summary
```

### 方式 C：装在虚拟环境里

```bash
cd xjtlu-lmc-downloader/build-dir-tree
python -m venv .venv
.venv\Scripts\activate        # Windows
python -m pip install -e .
```

### 装完自检

```bash
python -m build_dir_tree --version
python -m unittest discover -s tests -t .    # 可选：跑测试
```

## 快速开始

1. **保存快照**：在浏览器里打开课程页 → 右键「另存为」 → 类型选 **网页，单个文件 (*.mhtml)**。
2. **看一眼解析结果**：

```bash
python -m build_dir_tree "Course_ ISA104-2627-S1-AI Application Design _ LM Core.mhtml" --summary
```

```
Title      : Course: ISA104-2627-S1-AI Application Design | LM Core
Source     : https://core.xjtlu.edu.cn/course/view.php?id=1213
Course id  : 1213
Top-level  : 14 sections
Subsections: 9
Activities : 67
```

3. **预演目录**（不落盘）：

```bash
python -m build_dir_tree course.mhtml --layout sections
```

```
Layout=sections  Dirs=23  Root=ISA104-2627-S1-AI Application Design
  + Welcome to ISA104 - AI Application Design!
  + AIAT Faculty
  + ALC- Academic Literacy and Language Support for ISA104
  + ALC- Academic Literacy and Language Support for ISA104/Meet Your ALC Teachers
  ...
(dry-run, nothing created; pass --mkdir -o <dir> to create)
```

4. **真正创建**：

```bash
python -m build_dir_tree course.mhtml --layout sections --mkdir -o "D:\Courses"
```

## 两种布局

| 布局 | 内容 | ISA104 实例 |
|---|---|---|
| `sections` | 章节骨架：每个 section / subsection 一个目录 | 23 个目录 |
| `full` | 在章节骨架之上，每个活动再加一个叶子目录 | 90 个目录 |

`full` 的叶子同样用活动原标题，只是多一层目录：

```
Week 1 - Introduction to AI and GAI/[LECTURE] Introduction to AI and GenAI
Week 1 - Introduction to AI and GAI/[RECORDING] L1 Introduction to AI and GAI
Week 1 - Introduction to AI and GAI/Week 1-Tutorial/Week 1 workbook
```

## 三种输出

| 参数 | 产物 | 用途 |
|---|---|---|
| （默认） | 打印到终端 | 预览、贴进 issue |
| `--mkdir -o DIR` | 真实建目录 | 直接使用 |
| `--emit-ps1 FILE` | 独立 PowerShell 脚本 | 交给别人执行，或需要脱离 Python 运行 |
| `--json FILE` | 完整规格 JSON | 接管道、二次处理 |

`--emit-ps1` 生成的脚本自带 `-DryRun` 开关和 `-Base` 参数：

```powershell
python -m build_dir_tree course.mhtml --emit-ps1 build-tree.ps1 -o D:\Courses
pwsh -File .\build-tree.ps1 -Base D:\Courses -DryRun   # 预演
pwsh -File .\build-tree.ps1 -Base D:\Courses           # 执行
```

## 命名规则

**默认逐字符保真**：目录名就是课程页上的标题。

| 情况 | 默认行为 |
|---|---|
| 空格（1 / 2 / 3 个…） | **原样保留**，`a  b` 还是 `a  b` |
| `&` `()` `[]` `,` `'` `!` `-` | 原样保留 |
| 中文、重音字母、`–` `’` | 原样保留 |
| `<` `>` `:` `"` `/` `\` `\|` `?` `*` | **删除**（Windows 存不了） |
| 控制字符 | 删除 |
| 末尾空格 / 点 | 去掉（Windows 强制丢弃，会告警提示） |

实测：

```
源 'Week 1-Tutorial'                 → 'Week 1-Tutorial'            连字符前 0 空格，保留
源 'Week 3- Tutorial'                → 'Week 3- Tutorial'           同上
源 'Week 7 Tutorial '                → 'Week 7 Tutorial'            尾随空格被丢弃，告警
源 'Week 8 - AI Applications: Voice' → 'Week 8 - AI Applications Voice'   冒号删除
源 'Models (LLMs)'                   → 'Models (LLMs)'              括号保留
```

### 加工选项（默认全部关闭）

工具**不替你想替换成什么**。要不要换、换成什么，由你指定：

| 选项 | 作用 | 不写时 |
|---|---|---|
| `--number` | 加序号前缀（`04-` / `04.16-` / `01 `） | 不加 |
| `--collapse-spaces` | 连续空格压成一个 | 保留原样 |
| `--space-replace TEXT` | 空格替换为 TEXT | 保留空格 |
| `--illegal-replace TEXT` | 非法字符替换为 TEXT | **删除**（默认） |
| `--keep-trailing` | 保留末尾空格和点 | 去掉 |

```bash
python -m build_dir_tree course.mhtml --layout full                          # 全保真
python -m build_dir_tree course.mhtml --illegal-replace _                    # 冒号换下划线
python -m build_dir_tree course.mhtml --space-replace _                      # 空格换下划线
python -m build_dir_tree course.mhtml --collapse-spaces --illegal-replace -  # 组合
```

`.mhtml` 里的实体（`&amp;`）总会被解码成真实字符，这是解析而非改写；
Moodle 的双重转义（`&amp;amp;`）也会还原成 `&`。

## 图形界面

图形界面把课程结构显示成**可勾选的树**：先载入快照，再逐个勾选要创建的目录，最后点「创建选中目录」。

```bash
build-dir-tree-ui                      # 安装后可用
python -m build_dir_tree.ui            # 或这样启动，会自动打开浏览器
python -m build_dir_tree.ui --port 8777 --no-browser
```

### 使用顺序

1. **填快照路径** —— 课程页 `.mhtml` / `.html` 的完整路径。
2. **点「载入课程」** —— 右侧出现整棵目录树，默认全部勾选。
3. **调整命名选项**（可选）—— 加序号、合并空格、指定替换字符。改完点「重新载入」看效果。
4. **勾选要创建的目录** —— 章节可以整块勾选，也可以只勾个别活动。
5. **填输出根目录，点「创建选中目录」** —— 只创建勾上的那些。

### 树的交互

| 操作 | 效果 |
|---|---|
| 点箭头 | 展开 / 折叠该节点 |
| 勾选章节 | 连同其下所有活动一起勾上 |
| 勾选单个活动 | 只勾它，但父目录会自动创建（否则路径不成立） |
| 取消勾选 | 该节点及其子孙都不创建 |
| 全部展开 / 全部折叠 | 一次调整整棵树的展开状态 |
| 全选 / 全不选 | 一次调整所有勾选状态 |
| 只要章节 | 只勾章节，不勾活动 |

右上角实时显示「已选 N / 总数 个目录」。

### 为什么需要本地服务

浏览器里的网页无法直接读取本地 `.mhtml` 文件，也无法创建本地目录。
因此这里启动一个只监听 `127.0.0.1` 的小服务，所有解析和写入都在本机完成，
**不联网、不上传任何数据**。

## 命名规则

**默认逐字符保真**：目录名就是课程页上的标题。

| 情况 | 默认行为 |
|---|---|
| 空格（1 / 2 / 3 个…） | **原样保留**，`a  b` 还是 `a  b` |
| `&` `()` `[]` `,` `'` `!` `-` | 原样保留 |
| 中文、重音字母、`–` `’` | 原样保留 |
| `<` `>` `:` `"` `/` `\` `\|` `?` `*` | **删除**（Windows 存不了） |
| 控制字符 | 删除 |
| 末尾空格 / 点 | 去掉（Windows 强制丢弃，会告警提示） |

实测：

```
源 'Week 1-Tutorial'                 → 'Week 1-Tutorial'            连字符前 0 空格，保留
源 'Week 3- Tutorial'                → 'Week 3- Tutorial'           同上
源 'Week 7 Tutorial '                → 'Week 7 Tutorial'            尾随空格被丢弃，告警
源 'Week 8 - AI Applications: Voice' → 'Week 8 - AI Applications Voice'   冒号删除
源 'Models (LLMs)'                   → 'Models (LLMs)'              括号保留
```

### 加工选项（默认全部关闭）

工具**不替你想替换成什么**。要不要换、换成什么，由你指定：

| 选项 | 作用 | 不写时 |
|---|---|---|
| `--number` | 加序号前缀（`04-` / `04.16-` / `01 `） | 不加 |
| `--collapse-spaces` | 连续空格压成一个 | 保留原样 |
| `--space-replace TEXT` | 空格替换为 TEXT | 保留空格 |
| `--illegal-replace TEXT` | 非法字符替换为 TEXT | **删除**（默认） |
| `--keep-trailing` | 保留末尾空格和点 | 去掉 |

```bash
python -m build_dir_tree course.mhtml --layout full                          # 全保真
python -m build_dir_tree course.mhtml --illegal-replace _                    # 冒号换下划线
python -m build_dir_tree course.mhtml --space-replace _                      # 空格换下划线
python -m build_dir_tree course.mhtml --collapse-spaces --illegal-replace -  # 组合
```

`.mhtml` 里的实体（`&amp;`）总会被解码成真实字符，这是解析而非改写；
Moodle 的双重转义（`&amp;amp;`）也会还原成 `&`。

## 图形界面

不想敲命令就用本地 UI：

```bash
python -m build_dir_tree.ui            # 自动打开浏览器
build-dir-tree-ui                      # 安装后可用
python -m build_dir_tree.ui --port 8777 --no-browser
```

页面里可填快照路径、选布局、勾选命名选项，点「预览」看结果、点「创建目录」落盘。

UI 是只监听 `127.0.0.1` 的小服务 —— 浏览器页面无法直接读本地 `.mhtml`、也无法写本地目录。
所有处理都在本机完成，**不上传任何数据**。

### 关于路径长度

保留原字符意味着路径会比 slug 化版本长。实测 ISA104 的 `full` 布局最长相对路径 178 字符，
绝对路径最长 247 字符 —— 逼近 Windows 默认的 260 上限。

工具**只警告、不截断**（截断会破坏你要的结构保真度）：

```
warning: 4 of 90 path(s) may exceed the Windows 260-character limit (longest: 299 chars)
  Week 3 - Prompt Engineering ... /[Extra-Reading] Security Risks ...
  hint: use a shorter -o/--output, or enable long paths in Windows (see docs: LongPathsEnabled)
```

对策：把输出目录放浅一点（如 `D:\LMC\`），或开启 Windows 长路径支持。

## 完整参数

```
positional arguments:
  snapshot              课程页快照路径 (.mhtml / .html)

options:
  -o, --output DIR      --mkdir 的目标根目录
  --layout {sections,full}
                        sections = 章节骨架（默认）；full = 另加每个活动的叶子目录
  --root-name NAME      顶层文件夹名（默认取课程标题）
  --mkdir               真正创建目录
  --dry-run             只打印（不给 --mkdir 时的默认行为）
  --summary             只打印统计后退出
  --json FILE           把完整规格写成 JSON
  --emit-ps1 FILE       生成独立 PowerShell 脚本
  --separator SEP       打印路径时用的分隔符（默认 /）
  --skip-empty-subsections
                        跳过没有任何活动的空子章节

naming policy (defaults are faithful):
  --number              加序号前缀（默认不加）
  --collapse-spaces     连续空格压成一个（默认保留原样）
  --space-replace TEXT  空格替换为 TEXT（默认保留空格）
  --illegal-replace TEXT
                        非法字符替换为 TEXT（默认删除）
  --keep-trailing       保留末尾空格和点（默认去掉）
  -q, --quiet           只在出错时输出
```

## 作为库使用

```python
from build_dir_tree import parse_bytes

with open("course.mhtml", "rb") as fh:
    course = parse_bytes(fh.read())          # 自动识别 mhtml / html

print(course.summary())
for top in course.top_sections():
    print(top.num, top.name, [a.name for a in top.real_activities()])
    for child in top.children:
        print("  ", child.num, child.name)

# 默认：逐字符保真
spec = course.to_spec(layout="full", separator="\\")
print(spec["count"], "directories")

# 需要时显式开启加工
from build_dir_tree import NamePolicy
policy = NamePolicy(number=True, space_replace="_", illegal_replace="-")
spec = course.to_spec(layout="full", policy=policy)
```

## 设计说明与已知限制

- **只认页面 DOM**，不调用任何 Moodle Web Service，不需要登录态、不碰 cookie。离线可用。
- 子章节的父子关系靠 DOM 嵌套判定（Moodle 把 subsection 的 `<li>` 嵌进父章节里），
  不依赖 `data-sectionreturnnum` —— 实测该属性常为空。
- `subsection` 类型的模块是 Moodle 内部的占位项，不会生成叶子目录。
- 活动名优先取 `data-activityname`，回退到 `activitytitle` / `instancename`。
  若某版 Moodle 主题改动了这些属性，可能取到空名（此时该目录会落到 `untitled`）。
- **折叠的章节也能解析**：即使页面里某些 section 处于折叠状态，其 DOM 依然存在。
- 不下载任何文件 —— 本工具只负责目录结构。

## 测试

```bash
python -m unittest discover -s tests -t . -v
```

79 个用例覆盖：保真命名（单/双/三空格、括号、方括号、`&`、Unicode、双重转义实体）、
每个加工选项（序号、压缩空格、空格替换、非法字符替换、保留尾随）、章节/子章节父子关系、
subsection 占位过滤、两种布局、重名去重、CLI 四条输出路径、路径长度告警、保真度告警、
树结构构建、勾选解析（整块选中、单叶选中补父目录、排除子树）、Web UI 接口测试
（含路径穿越拦截）、幂等性，以及错误输入。

## 目录结构

```
build-dir-tree/
├── build_dir_tree/
│   ├── __init__.py        公开 API
│   ├── __main__.py        python -m build_dir_tree 入口
│   ├── ui.py              python -m build_dir_tree.ui 入口
│   ├── course_parser.py   MHTML/HTML 解码 + 层级重建 + 命名策略
│   ├── cli.py             命令行与各类输出
│   └── web/               本地图形界面（server + static）
├── tests/
│   ├── fixtures/demo_course.html
│   └── test_build_dir_tree.py
├── examples/
│   └── isa104-example.md
├── pyproject.toml
└── README.md
```

## 许可证

MIT
