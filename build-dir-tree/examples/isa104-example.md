# 实例：ISA104-2627-S1 · AI Application Design

真实案例。**以下输出均为默认（保真）模式**，非法字符直接删除。

## 输入

```
Course_ ISA104-2627-S1-AI Application Design _ LM Core.mhtml   (6.3 MB)
```

来源 `https://core.xjtlu.edu.cn/course/view.php?id=1213`。

## 解析结果

```console
$ python -m build_dir_tree course.mhtml --summary
Title      : Course: ISA104-2627-S1-AI Application Design | LM Core
Source     : https://core.xjtlu.edu.cn/course/view.php?id=1213
Course id  : 1213
Top-level  : 14 sections
Subsections: 9
Activities : 67
```

## `sections` 布局（23 个目录）

```
ISA104-2627-S1-AI Application Design/
├── Welcome to ISA104 - AI Application Design!
├── AIAT Faculty
├── ALC- Academic Literacy and Language Support for ISA104/
│   └── Meet Your ALC Teachers/
├── Assessments/
│   ├── In-class Test/
│   └── Group Project - AI Agent Design/
├── Week 1 - Introduction to AI and GAI/
│   └── Week 1-Tutorial/
├── Week 2 - Introduction to Large-Language Models (LLMs)/
│   └── New subsection/
├── Lab 1 - Developing AI Applications with Ollama/
├── Week 3 - Prompt Engineering and Critical Engagement with AI Generated Content/
│   └── Week 3- Tutorial/
├── Week 4 - AI Safety and Ethical Considerations in AI Applications/
├── Week 5 - Retrieval Augmented Generation/
│   └── Week 5 Tutorial/
├── Week 6 - Agentic AI/
│   └── Lab 3 - RAG and Agentic AI in XIPU AI/
├── Week 7 Tutorial/
├── Week 8 - AI Applications Voice, Speech & Creativity/
└── Week 9 - Human-Centred Design with AI/
    └── Week 9 Tutorial/
```

### 与源标题的差异（仅两类）

| 源标题 | 本地目录 | 原因 |
|---|---|---|
| `Week 8 - AI Applications: Voice, ...` | `Week 8 - AI Applications Voice, ...` | 冒号是 Windows 非法字符，默认删除 |
| `Week 7 Tutorial ` | `Week 7 Tutorial` | 尾随空格被 Windows 强制丢弃 |

`Week 3- Tutorial` 连字符前 0 空格、`Week 1-Tutorial` 也是 0 空格 —— 页面怎么写就怎么建，
没有被「规整」成 `Week 3 - Tutorial`。

尾随空格的偏差会明确告警：

```
note: 3 title(s) end with a space or dot; Windows drops those, so the folder name differs slightly:
  'Meet Your ALC Teachers '
  'Week 3 - Prompt Engineering and Critical Engagement with AI Generated Content '
  'Week 7 Tutorial '
```

## `full` 布局节选（90 个目录）

```
Week 1 - Introduction to AI and GAI/
├── [LECTURE] Introduction to AI and GenAI/
├── [EXTRA-READING] Harnessing the Power of LLMs in Practice/
├── [RECORDING] L1 Introduction to AI and GAI/
├── [EXTRA-READING] GPT-4 Technical Report/
└── Week 1-Tutorial/
    ├── Week 1 workbook/
    ├── Real or AI Quiz Can You Tell the Difference?/
    └── Task 2 handout/
```

（活动标题里的 `:` 同样被删除，方括号、`&`、`,`、`?` 保留。）

## 想让非法字符换成下划线

```powershell
python -m build_dir_tree course.mhtml --layout sections --illegal-replace _
```

```
Week 8 - AI Applications_ Voice, Speech & Creativity
```

## 需要序号 / 空格替换

```powershell
python -m build_dir_tree course.mhtml --layout sections --number
python -m build_dir_tree course.mhtml --layout sections --space-replace _
```

```
00-Welcome to ISA104 - AI Application Design!
04-Week 1 - Introduction to AI and GAI/04.16-Week 1-Tutorial

Welcome_to_ISA104_-_AI_Application_Design!
Week_8_-_AI_Applications_Voice,_Speech_&_Creativity
```

## 这门课的三个坑（工具如实反映）

1. **Lab 不是分类，是和 Week 同级的 section**
   `Lab 1` 排在 `Week 2` 和 `Week 3` 之间。工具保持这个顺序，不建 `Lab/` 分组。

2. **Lab 编号不连续**
   页面只有 Lab 1（独立章节）、Lab 2（Week 4 里的一个 resource）、Lab 3（Week 6 的子章节）。
   跳号是课程本身如此，不是解析遗漏。

3. **`Week 7 Tutorial` 是顶层 section**
   其它周都拆成「周 + Tutorial 子章节」，唯独 Week 7 没有。工具不做补齐。

另有 `New subsection` 是老师建了但没放活动的空壳，默认保留；
不想要就加 `--skip-empty-subsections`。

## 路径长度

`full` 布局最长相对路径 178 字符，实测绝对路径最长 247 字符。
输出目录放浅一些可避免触发 Windows 260 字符上限告警。

## 可复现命令

```powershell
python -m build_dir_tree course.mhtml --summary
python -m build_dir_tree course.mhtml --layout sections --mkdir -o .\out
python -m build_dir_tree course.mhtml --layout full --emit-ps1 build-tree.ps1 -o .\out
python -m build_dir_tree.ui    # 图形界面
```
