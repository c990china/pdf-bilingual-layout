# -*- coding: utf-8 -*-
"""字体字号方案样张生成器（两页）
P1: 四套中西文搭配，各自演示 标题/正文/图注
P2: 字号梯度 + 行距 + 中西文比例 + 标点数字

用法:
    python specimen.py [输出目录]      # 默认当前目录
产出:
    字体字号方案样张.pdf

⚠️ 核心约束（见 SKILL.md 坑 6）：
    MuPDF Story 的 font-family 列表【不做】逐字符脚本回退，
    `font-family: cn,en` 里的 en 永远轮不到。必须用显式 <span class="en"> 包西文段。
    且 re.split 的模式外层必须是【捕获组】，否则中文会被整段丢弃。
"""
import os
import re
import sys
import html
import pymupdf as fitz

OUTDIR = sys.argv[1] if len(sys.argv) > 1 else "."
OUT = os.path.join(OUTDIR, "字体字号方案样张.pdf")

def _find_font_dir():
    """字体目录探测（Windows 不装在 C 盘也能用）：
    PDFBL_FONT_DIR 环境变量 > WINDIR/Fonts > 各盘 Windows/Fonts > Linux 字体目录。"""
    override = os.environ.get("PDFBL_FONT_DIR")
    if override and os.path.isdir(override):
        return override
    cands = []
    if os.environ.get("WINDIR"):
        cands.append(os.path.join(os.environ["WINDIR"], "Fonts"))
    cands += ["C:/Windows/Fonts", "D:/Windows/Fonts", "E:/Windows/Fonts",
              "F:/Windows/Fonts", "G:/Windows/Fonts",
              "/usr/share/fonts", "/usr/local/share/fonts"]
    for c in cands:
        if os.path.isdir(c):
            return c
    return "/usr/share/fonts"


FONT_DIR = _find_font_dir()
ARCH = fitz.Archive(FONT_DIR)

# ---- 中西文切分：CJK 及中文标点走中文字体，其余（拉丁/数字/半角标点）走西文字体 ----
CN_PAT = ('([\u2018-\u201d\u2014\u2026\u2460-\u24ff\u25a0-\u27bf'
          '\u2e80-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff'
          '\ufe30-\ufe4f\uff00-\uffef\U00020000-\U0002ffff]+)')
CN_RE = re.compile(CN_PAT)


def mk(text):
    """把西文/数字段包进 .en，其余保持中文字体"""
    esc = html.escape(text, quote=False)
    out = []
    for part in CN_RE.split(esc):
        if not part:
            continue
        out.append(part if CN_RE.fullmatch(part)
                   else '<span class="en">%s</span>' % part)
    return "".join(out)


# 每套方案：中文 / 西文 / 名称 / 说明 / 是否推荐
SCHEMES = [
    ("simsun.ttc", "times.ttf", "宋体 + Times New Roman",
     "衬线 · 传统书刊，最稳妥", True),
    ("STSONG.TTF", "gara.ttf", "华文宋体 + Garamond",
     "衬线 · 笔画精致、书卷气足", False),
    ("msyh.ttc", "arial.ttf", "微软雅黑 + Arial",
     "无衬线 · 现代简洁，小字清晰", False),
    ("Deng.ttf", "georgia.ttf", "等线 + Georgia",
     "无衬线 · 字重纤细，数字好看", False),
]

HEAD_CN = "西南艺术圈不可错过的 10 个展览"
HEAD_EN = "The Art List"
BODY = ("位于亚利桑那州图森市的 Settlers West 画廊将于 5 月 2 日（周六）举办一年一度的夏季展。"
        "这项传统以广度见长，既有知名大师的绘画与雕塑，也有新锐艺术家的作品。 settlerswest.com")
NOTE = "PAULINE ROCHE，《为索罗拉所迷》，亚麻布油画，36 × 24。由第 18 届国际 ARC 沙龙提供。"
LONG = ("位于亚利桑那州图森市的 Settlers West 画廊将于 5 月 2 日（周六）举办一年一度的夏季展，"
        "这项传统以广度见长。")

# ---------- CSS ----------
face, secs = [], []
for i, (cn, en, name, desc, rec) in enumerate(SCHEMES):
    face.append("@font-face{font-family:cn%d;src:url(%s);}" % (i, cn))
    face.append("@font-face{font-family:en%d;src:url(%s);}" % (i, en))
    mark = "　（推荐）" if rec else ""
    secs.append(
        '<div class="sec s%d">'
        '<p class="tag">%s</p>'
        '<p class="big">%s</p>'
        '<p class="big-en">%s</p>'
        '<p class="body">%s</p>'
        '<p class="note">%s</p>'
        '</div>' % (i, mk("方案 %d ｜ %s —— %s%s" % (i + 1, name, desc, mark)),
                    mk(HEAD_CN), mk(HEAD_EN), mk(BODY), mk(NOTE)))

css = "".join(face) + """
body{margin:0;}
.sec{margin-bottom:26pt;padding-bottom:12pt;border-bottom:0.5pt solid #ddd;}
.tag{font-size:8pt;color:#777;margin:0 0 5pt 0;line-height:1.3;}
.big{font-size:18pt;margin:0 0 3pt 0;line-height:1.22;}
.big-en{font-size:18pt;margin:0 0 5pt 0;line-height:1.22;}
.body{font-size:9pt;margin:0 0 5pt 0;line-height:1.55;}
.note{font-size:7pt;color:#444;margin:0;line-height:1.4;}
.hdr .big{font-size:15pt;margin:0 0 3pt 0;}
.hdr .note{font-size:7.5pt;margin:0 0 12pt 0;color:#555;}
.lab{font-size:8pt;color:#333;margin:12pt 0 5pt 0;line-height:1.3;}
.foot{font-size:7.5pt;color:#555;margin-top:8pt;line-height:1.5;}
.lad p{margin:0 0 6pt 0;line-height:1.28;}
.cmp p{margin:0 0 7pt 0;line-height:1.5;}
""" + "".join(
    ".s%d{font-family:cn%d;}.s%d .en{font-family:en%d;}" % (i, i, i, i)
    for i in range(len(SCHEMES)))

doc = fitz.open()

# ================= Page 1 =================
p1 = doc.new_page(width=595, height=842)
hdr1 = ('<div class="hdr s0"><p class="big">%s</p><p class="note">%s</p></div>'
        % (mk("中英文搭配方案样张（一）· 字体搭配"),
           mk("同一段原文 · 标题 18pt ／ 正文 9pt ／ 图注 7pt · "
              "西文与中文同 pt 值（西文字高约为中文 70%，同 pt 视觉略小属正常）")))
foot1 = ('<div class="s0 foot">%s</div>'
         % mk("说明：英文此前一直由中文字体自带的西文字形渲染（并非 Times），"
              "故看起来偏大、与中文不协调；本样张已改为西文单独指定字体。"
              "出版物惯例为「衬线配衬线、无衬线配无衬线」，正文中西文同 pt 值。"))
s1, k1 = p1.insert_htmlbox(fitz.Rect(40, 34, 555, 812), hdr1 + "".join(secs) + foot1,
                           css=css, archive=ARCH, scale_low=0.6)
print("P1 spare=%.1f scale=%.2f" % (s1, k1))

# ================= Page 2 =================
p2 = doc.new_page(width=595, height=842)
hdr2 = ('<div class="hdr s0"><p class="big">%s</p><p class="note">%s</p></div>'
        % (mk("中英文搭配方案样张（二）· 字号与行距"),
           mk("统一用「宋体 + Times New Roman」演示 · 判断哪种读起来最舒服")))

heads = "".join('<p style="font-size:%spt">%s　<span class="en">The Art List</span></p>'
                % (s, mk(HEAD_CN)) for s in (14, 16, 18, 20, 22))
bodies = "".join('<p style="font-size:%spt;line-height:1.55">%s</p>'
                 % (s, mk(BODY)) for s in (7.5, 8, 8.5, 9, 9.5, 10))
lh_rows = "".join(
    '<div class="cmp"><p style="font-size:7.5pt;color:#888;margin:0 0 2pt 0">%s</p>'
    '<p style="font-size:9pt;line-height:%.2f;width:330pt">%s</p></div>'
    % (mk("行距 %.2f" % lh), lh, mk(LONG)) for lh in (1.25, 1.45, 1.60))
cmp_html = "".join(
    '<p style="font-size:9pt">%s</p>' % mk(t) for t in (
        "中文 9pt ＋ 西文 9pt（同 pt，规范做法）：西南艺术 9pt Settlers West 9pt",
        "中文 9pt ＋ 西文 10pt（西文放大 1pt，视觉更“等高”但基线偏跳）：西南艺术 9pt Settlers West 10pt",
        "中文 9pt ＋ 西文 8pt（西文过小，易与中文脱节）：西南艺术 9pt Settlers West 8pt",
    ))
punc = ('<p style="font-size:9pt;line-height:1.6">%s</p>'
        % mk("标点与数字：，。、；：「」（）—— …… 10 个展览 · 36 × 24 · 1,234.56 · 2026 年 5 月 2 日"))

page2 = ('<div class="s0">' + hdr2 +
         '<p class="lab">%s</p><div class="lad">%s</div>' % (mk("■ 标题字号梯度（14 → 22pt）"), heads) +
         '<p class="lab">%s</p><div class="lad">%s</div>' % (mk("■ 正文字号梯度（7.5 → 10pt）"), bodies) +
         '<p class="lab">%s</p>%s' % (mk("■ 行距对照（正文 9pt）"), lh_rows) +
         '<p class="lab">%s</p><div class="cmp">%s</div>' % (mk("■ 中西文比例对照"), cmp_html) +
         '<p class="lab">%s</p>%s' % (mk("■ 标点与数字"), punc) +
         '<p class="lab">%s</p><div class="foot">%s</div>'
         % (mk("■ 选用建议"),
            mk("艺术/文化类杂志正文推荐「宋体 + Times New Roman」：字形中性、不抢画面，"
               "与图版共存最稳。若版面偏现代设计感，可用「微软雅黑 + Arial」。"
               "正文 9pt、图注 7pt、大标题 18–22pt 是实测常用取值。")) +
         '</div>')
s2, k2 = p2.insert_htmlbox(fitz.Rect(40, 34, 555, 812), page2,
                           css=css, archive=ARCH, scale_low=0.6)
print("P2 spare=%.1f scale=%.2f" % (s2, k2))

try:
    doc.subset_fonts()
except Exception as e:
    print("subset skip:", e)
doc.save(OUT, garbage=4, deflate=True)
print("saved:", OUT, round(os.path.getsize(OUT) / 1048576, 2), "MB")

# 自检：中西文字体都必须嵌入
for i, pg in enumerate(doc):
    print("--- page %d fonts:" % (i + 1))
    for f in pg.get_fonts(full=True):
        print("     ", f[3])
    pg.get_pixmap(dpi=140).save(os.path.join(OUTDIR, "_specimen_p%d.png" % (i + 1)))
doc.close()
