---
name: pdf-bilingual-layout
description: |
  把外文 PDF（杂志/画册/说明书）做成「左原文 ＋ 右中文译文」对照版：译文与原文逐块行列对齐、
  图片原样保留；并附带译文质量规范（翻译即重写、去翻译腔/欧化表达的三步工作流）。
  当用户提出「PDF 中英对照」「双语对照排版」「左原文右译文」「A4 扩成 A3」
  「PDF 翻译保留版式」「原文图片保留文字翻译」「译文去翻译腔」等需求时使用。
  完整原理与 24 条实测坑见 docs/PDF双语对照排版指南.md。
version: 1.28
---

# PDF 双语对照排版（左原文 / 右译文，行列对齐）

> 依赖：PyMuPDF ≥ 1.28 + Pillow（`pip install -r requirements.txt`）。
> Windows 需宋体/黑体（中文）+ Times New Roman / Arial（西文）；**不要用变量字体（VF）**。

## 用法（交给 AI）

```bash
# 1) 提取版面结构 + 待译清单
python scripts/01_extract.py <源PDF> <输出目录>
#    → structure.json / blocks_final.json / to_translate.txt（按 [页码][序号] 编号）

# 2) 写 translations.json（{"0": ["译文0", ...], ...}）——遵守译文质量规范，逐块对位、不得增删

# 3) 合成（页数 ≥30 必须用 03_build.py 分块版；≤20 页可用 02_build.py）
python scripts/03_build.py <源PDF> <blocks_final.json> <translations.json> <输出PDF>

# 4) 校验（必做）
python scripts/04_verify.py <输出PDF> <translations.json>

# 5) 可选：裁纯中文版
python scripts/05_split_cn.py <对照版PDF> <纯中文PDF>
```

## 铁律（每条都会毁掉成品，详见指南）

1. **剥文字用内容流 `BT…ET` 剥离，不用 `apply_redactions`**——后者只标记不可提取，
   绘制指令还在，右半页中英重叠。判据是数内容流 `BT` 块，不是看 `get_text()`。
2. **中文写入用 `insert_htmlbox` + `fitz.Archive`**；`insert_textbox(fontfile=...)`
   写入的汉字全是 notdef（`?`）。中西文分字体必须显式 `<span class="en">`，
   `font-family:cn,en` 回退是假的。
3. **改源 PDF 后必须重出 translations.json**——块数一变就整页静默错位，
   `03_build.py` 会在合成前拦截（块数 ≠ 译文数即退出）。
4. **画序铁律**：cover 矩形 → 涂底矩形 → 全部文字；凡是「往纸上盖东西」的操作一律进预扫。
5. **交付前过校验清单**（指南末节）：页数/尺寸/块数对位/无重影/无连续 `?`/英文残留为 0/
   深色底浅色字/装饰号不压正文/首行缩进 2 字符/译文无翻译腔。
6. **光栅化页**（`BT=0` 但有文字）：用输出目录下 `overlays.json`（或 `PDFBL_OVERLAY`）
   指定涂白矩形与中文段落，`03_build.py` 自动应用。

## 文件地图

| 脚本 | 用途 |
|---|---|
| `01_extract.py` | 版面结构提取 + 块清洗（去重、单字母簇合并） |
| `02_build.py` | 单页版参考实现（≤20 页） |
| `03_build.py` | 生产版（分块合成，坑 7–23 的全部对策） |
| `04_verify.py` | 乱码 / 截断 / 英文残留 / 尺寸校验 |
| `05_split_cn.py` | 裁纯中文版（`show_pdf_page(clip=右半)`，不用 set_cropbox） |
| `specimen.py` | 字体字号方案样张（四套中西文搭配 + 字号梯度） |
