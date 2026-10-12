# pdf-bilingual-layout · PDF 双语对照排版

**把外文 PDF（杂志 / 画册 / 说明书）做成「左原文 ＋ 右中文译文」的对照版：译文与原文逐块行列对齐，图片原样保留；再一键裁出纯中文版。**

版式钉在原位、行对行、列对列——不是把译文丢到附录里，而是让中文「长」在英文原来的位置上。成品页数与原文一致，每页横向扩为两倍。

> 依赖 [PyMuPDF](https://pymupdf.readthedocs.io/)（Artifex，AGPL-3.0 / 商业双许可）与 Pillow，致谢见文末。

---

## 为什么不是「翻译完另起一版」

| 常见做法的坑 | 本项目的做法 |
|---|---|
| 译文集中放在附录，对照要来回翻页 | 译文按**原坐标**写回右半页，行对行、列对列 |
| 整页贴图 + 贴字，画作被压掉 | 直接剥离内容流里的 `BT…ET` 文字块，图形、位图、背景**原样保留** |
| `apply_redactions` 假删文字（绘制指令还在，右半页中英重叠） | 以内容流 `BT` 块数量为判据，剥完必须渲染 PNG 复核 |
| 系统 TTF 经 `fontfile` 写入，汉字全变 `?` | `insert_htmlbox` + `Archive` 走 Story 引擎，Unicode 映射正确 |
| 深色底图黑字「消失」、装饰大号压正文、竖排图注整段丢失 | 按块采样背景亮度定字色、几何避让装饰号、竖排块 `rotate=90` 兜底 |

踩过的坑全部带症状、成因、检测代码与修法，沉淀在 [`docs/PDF双语对照排版指南.md`](docs/PDF双语对照排版指南.md)（坑 0–23）。

---

## 成品长什么样

```
源 PDF（1 页 603×783pt）          →  对照版（1 页 1206×783pt）
┌──────────────────┐                ┌───────────┬───────────┐
│  THE ART LIST    │                │ THE ART   │ 艺术佳作  │
│  Brad Teare      │       →        │ Brad      │ 布拉德·   │
│  ……             │                │ ……        │ 蒂尔 ……  │
└──────────────────┘                └───────────┴───────────┘
                                     左：原文      右：中文
```

纯中文版：`show_pdf_page(clip=右半)` 裁出，可选中、可检索（不重渲染、不转位图）。

---

## 快速开始

```bash
# 1) 安装依赖（Python 3.8+，PyMuPDF 必须 ≥ 1.28）
pip install -r requirements.txt

# 2) 提取版面结构 + 生成待译清单
python scripts/01_extract.py source.pdf ./work
#   产出 work/structure.json / blocks_final.json / to_translate.txt

# 3) 按 [页码][序号] 写译文（这一步交给大模型，规范见 docs）
#   产出 work/translations.json：{"0": ["译文0", ...], "1": [...]}

# 4) 合成对照版（页数 ≥30 用生产版 03，小册子可用单页版 02）
python scripts/03_build.py source.pdf work/blocks_final.json work/translations.json work/bilingual.pdf

# 5) 校验（乱码 / 截断 / 英文残留 / 尺寸）
python scripts/04_verify.py work/bilingual.pdf work/translations.json

# 6) 需要的话，裁一份纯中文版
python scripts/05_split_cn.py work/bilingual.pdf work/chinese-only.pdf
```

第 3 步必须遵守**翻译质量规范**：翻译即重写，化形合为意合、去翻译腔，每块走「初稿 → 自我批判 → 定稿」三步（[docs/顶尖英汉翻译专家与中文写作专家.md](docs/顶尖英汉翻译专家与中文写作专家.md) + 指南「译文质量规范」一节）。

生成「字体字号方案样张」供用户挑选：`python scripts/specimen.py`（四套中西文搭配 + 字号梯度 + 行距对照）。

---

## 出版物级中文排版

- **中西文同 pt**：西文字面高度约为汉字的 70%，同 pt 时英文略小是**正确**的；强行放大到 1.2–1.4 倍是错的。
- **字体同风格配对**：宋体 × Times（书刊正文）、黑体 × Arial（标题图注）；忌用 CJK 字体的西文字形排正文（会「跳出来」）。
- **首行缩进 2 字符 + 左对齐**（不用 justify——中文两端对齐会拉出难看的字距）。
- 字号以**原英文字号为基准只降不升**，放不下交给 `scale_low` 自动收窄（实测 83 块平均 scale ≈ 0.91，观感正好）。

---

## 目录结构

```
pdf-bilingual-layout/
├── README.md / LICENSE / NOTICE.md
├── requirements.txt
├── scripts/
│   ├── 01_extract.py     # 提取版面结构 → blocks_final.json + 待译清单
│   ├── 02_build.py       # 单页版合成（≤20 页参考实现）
│   ├── 03_build.py       # 生产版合成器（分块、涂底/避让/竖排/光栅化页全覆盖）
│   ├── 04_verify.py      # 校验：乱码/截断/英文残留/尺寸
│   ├── 05_split_cn.py    # 裁出纯中文版（右半）
│   └── specimen.py       # 字体字号方案样张
└── docs/
    ├── PDF双语对照排版指南.md     # 完整指南：原理 + 坑 0–23 + 校验清单
    ├── 顶尖英汉翻译专家与中文写作专家.md   # 翻译质量规范（翻译即重写）
    └── 更新日志.md
```

---

## 实测记录

- 一本 **100 页英文艺术杂志**完整交付：图文混排、跨页大表、深色广告页、烧进位图标题、竖排图注全覆盖。
- 关键判据均为代码级验证：`apply_redactions` 后 `get_text()` 为 0 但内容流 `BT` 仍有 7 块（视觉上英文照旧）——**判据不能只看 `get_text()`**。
- 块数与译文数不匹配会「整页静默错位」而校验器查不出——`03_build.py` 内置快速失败拦截。
- 中间产物缓存按源文件身份（路径 + mtime + 大小）失效，杜绝「左半新源、右半旧源」。

---

## 致谢

- **[PyMuPDF](https://pymupdf.readthedocs.io/)**（© Artifex，AGPL-3.0 / 商业双许可）——PDF 内容流操作、文本写入与渲染。
- **[Pillow](https://python-pillow.org/)**（HPND）——底色采样与像素分析。

## 开源许可

本项目以 [MIT License](LICENSE) 发布，Copyright (c) 2026 c990china。
第三方组件遵循其上游许可；PyMuPDF 为 AGPL-3.0 与商业双许可，在闭源商业产品中
分发时请注意其要求（见 [NOTICE.md](NOTICE.md)）。
