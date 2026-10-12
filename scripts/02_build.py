# -*- coding: utf-8 -*-
"""02 合成对照 PDF：左半原文整页 / 右半（无字底图 + 中文，行列对齐）

用法: python 02_build.py <源PDF> <blocks_final.json> <translations.json> <输出PDF>
可选: --no-midline        不画中缝虚线
      --head-size 20      视为"标题"的字号阈值（>= 用无衬线组合）
      --line-height 1.45  行距上限

排版规范（出版物中英混排）：
  正文  中文宋体 + 西文 Times New Roman（衬线配衬线）
  标题  中文黑体 + 西文 Arial（无衬线配无衬线）
  字号  目标 = 原英文字号（同 pt 即视觉等效），绝不放大；放不下由 htmlbox 的 scale 自动收窄
  行距  按原文密度推算 h/(原文行数 × 字号)，夹在 1.05 ~ 上限之间
  插入  必须用 insert_htmlbox（insert_textbox 的 fontfile 会把汉字写成 notdef）
"""
import os, sys, json, re, html
import pymupdf as fitz

if len(sys.argv) < 5 or "--help" in sys.argv or "-h" in sys.argv:
    sys.exit('用法: python 02_build.py <源PDF> <blocks_final.json> <translations.json> <输出PDF> '
             '[--no-midline] [--head-size 20] [--line-height 1.45]')

args = [a for a in sys.argv[1:] if not a.startswith("--")]
if len(args) < 4:
    sys.exit('错误: 缺少必要的位置参数。用法: python 02_build.py <源PDF> <blocks_final.json> <translations.json> <输出PDF>')
SRC, BLOCKS, TRANS, DST = args[0], args[1], args[2], args[3]
MIDLINE = "--no-midline" not in sys.argv
HEAD_SIZE = float(sys.argv[sys.argv.index("--head-size") + 1]) if "--head-size" in sys.argv else 20.0
LINE_H = float(sys.argv[sys.argv.index("--line-height") + 1]) if "--line-height" in sys.argv else 1.45
FLOOR = 3.2

blocks = json.load(open(BLOCKS, encoding="utf-8"))
trans = json.load(open(TRANS, encoding="utf-8"))

# ---------- 1) 无字版：剥离内容流里的 BT...ET ----------
# 不要用 apply_redactions：它只把文字标记为不可提取，BT 块仍留在流里，英文照样渲染。
clean = fitz.open(SRC)
stripped = []
for pno in range(clean.page_count):
    page = clean[pno]
    n_bt = 0
    for xr in page.get_contents():
        data = clean.xref_stream(xr)
        n_bt += len(re.findall(rb"\bBT\b", data))
        new = re.sub(rb"BT\b.*?\bET", b"", data, flags=re.S)
        if new != data:
            clean.update_stream(xr, new)
    stripped.append((pno, n_bt))
# n_bt == 0 且页面看得见文字 => 该页被光栅化，需用 OVERLAY 白底覆盖（见 SKILL.md）

# ---------- 2) 字体（Archive 指向系统字体目录；htmlbox 走 Story 引擎，不会 notdef）----------
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
FACE_BODY = ("@font-face{font-family:cn;src:url(simsun.ttc);}"      # 宋体
             "@font-face{font-family:en;src:url(times.ttf);}")      # Times New Roman
FACE_HEAD = ("@font-face{font-family:cn;src:url(msyh.ttc);}"        # 微软雅黑
             "@font-face{font-family:en;src:url(arial.ttf);}")      # Arial
ALIGN_CSS = {"l": "left", "c": "center", "r": "right"}

# ⚠️ 坑 7：MuPDF Story 的 font-family 列表【不做】逐字符脚本回退。
#    font-family:cn,en 里的 en 永远轮不到——SimSun 自带拉丁字形，先命中就定死了，
#    于是英文一直用宋体的西文字形（这正是"英文看起来偏大"的根因）。
#    唯一可靠做法：把西文/数字/半角标点显式包进 <span class="en">。
# 外层必须是【捕获组】，否则 re.split 会把中文当分隔符整段丢弃（踩过）。
CN_PAT = ('([\u2018-\u201d\u2014\u2026\u2460-\u24ff\u25a0-\u27bf'
          '\u2e80-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff'
          '\ufe30-\ufe4f\uff00-\uffef\U00020000-\U0002ffff]+)')
CN_RE = re.compile(CN_PAT)


def css_for(size, align, lh=None, head=False):
    face = FACE_HEAD if head else FACE_BODY
    lh = LINE_H if lh is None else lh
    return (face + "p{font-family:cn;font-size:%.2fpt;line-height:%.2f;"
                   "text-align:%s;margin:0;}span.en{font-family:en;}"
                   % (size, lh, ALIGN_CSS[align]))


def to_html(t):
    """中文留给 p（cn 字体），西文/数字/半角标点包进 span.en（en 字体）"""
    e = html.escape(t, quote=False)
    out = []
    for part in CN_RE.split(e):
        if not part:
            continue
        out.append(part if CN_RE.fullmatch(part)
                   else '<span class="en">%s</span>' % part)
    return "<p>%s</p>" % "".join(out)


def fit_size(orig, h, floor=FLOOR):
    """目标字号 = 原英文字号；上限不超过框高，也绝不放大"""
    return round(max(min(orig, h), floor), 2)


# ---------- 2.8) 光栅化页补丁（BT=0 但页面有文字）----------
OVERLAY = {
    # 2: {
    #     "covers": [[60, 18, 560, 102], [16, 598, 597, 783]],
    #     "texts": [{"bbox": [100, 38, 520, 94], "text": "标题", "size": 34, "align": "c"}],
    # },
}

# ---------- 3) 合成 ----------
src = fitz.open(SRC)
doc = fitz.open()
W, H = src[0].rect.width, src[0].rect.height
DX = W
log, scales = [], []

for pno in range(src.page_count):
    page = doc.new_page(width=W * 2, height=H)
    page.show_pdf_page(fitz.Rect(0, 0, W, H), src, pno)          # 左：原文整页
    page.show_pdf_page(fitz.Rect(DX, 0, DX + W, H), clean, pno)  # 右：无字底图

    blist = blocks.get(str(pno), [])
    tlist = trans.get(str(pno), [])
    if len(blist) != len(tlist):
        log.append("!! page %d: blocks=%d translations=%d" % (pno, len(blist), len(tlist)))

    for i, b in enumerate(blist):
        if i >= len(tlist) or not tlist[i]:
            continue
        cn = tlist[i]
        x0, y0 = max(0.0, b["bbox"][0]), max(0.0, b["bbox"][1])
        x1, y1 = min(W, b["bbox"][2]), min(H, b["bbox"][3])
        w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
        floor = 5.0 if b["kind"] == "cluster" else FLOOR
        size = max(fit_size(b["size"], h, floor), floor if b["kind"] == "cluster" else 0)
        nl = max(1, b.get("nlines", 1))
        lh = min(max(h / (nl * size), 1.05), LINE_H)
        head = b.get("maxsize", b["size"]) >= HEAD_SIZE
        try:
            spare, scale = page.insert_htmlbox(fitz.Rect(x0 + DX, y0, x1 + DX, y1),
                                               to_html(cn), css=css_for(size, b["align"], lh, head),
                                               archive=ARCH, scale_low=0.4)
        except Exception as e:
            spare, scale = -1, 0
            log.append("!! page %d %s: %s" % (pno, b["id"], e))
        scales.append(scale)
        if spare < 0:
            log.append("page %d %s: FAILED (size=%.1f) %r" % (pno, b["id"], size, cn[:18]))
        elif scale < 0.75:
            log.append("page %d %s: 缩至 %.0f%% (%.1fpt) %r" %
                       (pno, b["id"], scale * 100, size * scale, cn[:14]))

    ovp = OVERLAY.get(pno)
    if ovp:
        for c in ovp["covers"]:
            page.draw_rect(fitz.Rect(c[0] + DX, c[1], c[2] + DX, c[3]),
                           color=None, fill=(1, 1, 1))
        for ov in ovp["texts"]:
            bx0, by0, bx1, by1 = ov["bbox"]
            bw, bh = bx1 - bx0, by1 - by0
            sz = fit_size(ov["size"], bh, 4.0)
            lh = min(max(bh / sz, 1.05), LINE_H)
            page.insert_htmlbox(fitz.Rect(bx0 + DX, by0, bx1 + DX, by1),
                                to_html(ov["text"]), css=css_for(sz, ov["align"], lh),
                                archive=ARCH, scale_low=0.4)

    if MIDLINE:
        page.draw_line(fitz.Point(DX, 0), fitz.Point(DX, H),
                       color=(0.78, 0.78, 0.78), width=0.6, dashes="[2 2] 0")

try:
    doc.subset_fonts(verbose=False)   # 不子集化会嵌入整套 simsun/times（20MB+）
except Exception as e:
    print("subset_fonts skipped:", e)
doc.save(DST, garbage=4, deflate=True)
print("BT blocks stripped per page:", stripped)
if scales:
    print("htmlbox scale: min %.2f  avg %.2f  n=%d" % (min(scales), sum(scales) / len(scales), len(scales)))
print("saved:", DST, round(os.path.getsize(DST) / 1048576, 2), "MB")
print("\n".join(log) if log else "no warnings")
