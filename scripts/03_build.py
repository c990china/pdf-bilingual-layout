# -*- coding: utf-8 -*-
"""03 生产版合成器：左原文 / 右（无字底图 + 中文，行列对齐）

用法:
    python 03_build.py <源PDF> <blocks_final.json> <translations.json> <输出PDF> [页数上限] [--chunk N]

能力（对应 SKILL.md 的坑 7 ~ 坑 16）：
  坑 7  涂底：抹掉「烧进位图、BT 剥离抹不掉」的英文，避免中英重影
  坑 8  装饰大号拆分 + 主块三向避让
  坑 9  深色底自动反白
  坑 10 小 bbox 外扩重试 + insert_text 兜底
  坑 11 同栏两条文章被合并 → 按 y 间隙切开（含译文）
  坑 13 装饰号不涂底；烧进位图的装饰号手工补（MANUAL_DECORATIONS）
  坑 14 覆盖块（cover）在所有文字之前画
  坑 15 同页装饰号统一字号
  坑 16 装饰号基线在原 bbox 内垂直居中
  中文排版：首行缩进 2 字符 + 左对齐

★ 分块合成：每 CHUNK 页存一份中间 PDF，最后合并 —— 页数一多（≥30）
  整份文档同时驻留会 MemoryError（src + clean + out 各一份整页位图）。
"""
import os, sys, json, re, html, math, unicodedata
import pymupdf as fitz
from collections import Counter
from PIL import Image
import statistics


def cjk_width(s):
    """粗估排版宽度：全角算 1 字宽，西文算 0.5 字宽。"""
    n = 0.0
    for ch in s:
        n += 1.0 if unicodedata.east_asian_width(ch) in ('W', 'F') else 0.5
    return n


_argv = sys.argv[1:]
_chunk = None
if '--chunk' in _argv:                       # ★ 必须先摘掉 `--chunk N` 这两个 token，
    _i = _argv.index('--chunk')              #   否则 N 会顶替位置参数被当成页数上限
    _chunk = int(_argv[_i + 1])
    _argv = _argv[:_i] + _argv[_i + 2:]
if len(_argv) < 4 or '--help' in sys.argv or '-h' in sys.argv:
    sys.exit('用法: python 03_build.py <源PDF> <blocks_final.json> <translations.json> '
             '<输出PDF> [页数上限] [--chunk N]\n'
             '说明: 若未指定 [页数上限]，默认处理源 PDF 的全部页面；--chunk 默认 10')
SRC, BLOCKS_JSON, TRANS_JSON = _argv[0], _argv[1], _argv[2]
DST = _argv[3]
PAGE_LIMIT = int(_argv[4]) if len(_argv) > 4 else None
CHUNK = _chunk if _chunk else 10
WORK = os.path.dirname(os.path.abspath(DST)) + os.sep   # 中间产物与缓存 PNG 落在这里
HEAD_SIZE = 20.0
LINE_H = 1.45
FLOOR = 3.2
DPI = 150
SCALE = DPI / 72.0
INK_T = 0.05      # 残留墨迹阈值
BG_UNIFORM_T = 0.62  # 背景占比阈值（越高越均匀）

CLEAN = WORK + '_clean.pdf'

# ---------- 光栅化页补丁（BT=0 但页面看得见文字）----------
# 这类页的整页内容（画作 + 文字）是一张位图：文字**既提取不到也抹不掉**，
# 所以既没有块、BT 剥离也无效。SKILL.md「光栅化页」一节的办法是
# **白底衬 + 中文覆盖**：把文字带整块涂白后重排，画作区域不动。
#
# 本机制在 v1.28 之前**只有 02_build.py（单页版）有**，生产版（分块）漏掉了 ——
# 于是「页数 ≥30 必须用 03_build.py」的场景下，光栅化页根本无法处理。
# 现在改为**从外部 JSON 读配置**，不再写死在脚本里：
#
#   路径：输出 PDF 同目录下的 `overlays.json`（或用环境变量 PDFBL_OVERLAY 指定）
#   格式：{"<页码>": {"covers": [[x0,y0,x1,y1], ...],
#                     "texts":  [{"bbox":[x0,y0,x1,y1], "text":"…",
#                                 "size":10.5, "align":"l"}]}}
#   · covers 是涂白矩形（**宁可整块大一点**，不要试图精确对齐单行文字：
#     估算位置容易差 3~5pt，结果就是"英文露一条边"）
#   · 画作区域**绝对不能**涂白 —— 先用渲染图确认画作的 bbox
#   · texts 的中文译文要**同时**登记进 translations.json 对应页码，否则 04_verify 会报字符数不符
OVERLAY_PATH = os.environ.get('PDFBL_OVERLAY') or (WORK + 'overlays.json')
OVERLAY = {}
if os.path.exists(OVERLAY_PATH):
    _raw_ov = json.load(open(OVERLAY_PATH, encoding='utf-8'))
    OVERLAY = dict((int(k), v) for k, v in _raw_ov.items())
    print('光栅化页补丁: %s（%d 页）' % (OVERLAY_PATH, len(OVERLAY)))

# ---------- 字体 ----------
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
FACE_BODY = ("@font-face{font-family:cn;src:url(simsun.ttc);}"
             "@font-face{font-family:en;src:url(times.ttf);}")
FACE_HEAD = ("@font-face{font-family:cn;src:url(msyh.ttc);}"
             "@font-face{font-family:en;src:url(arial.ttf);}")
CN_RE = re.compile('([\u2018-\u201d\u2014\u2026\u2460-\u24ff\u25a0-\u27bf'
                   '\u2e80-\u303f\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff'
                   '\ufe30-\ufe4f\uff00-\uffef\U00020000-\U0002ffff]+)')


def to_html(t):
    e = html.escape(t, quote=False)
    return "<p>%s</p>" % "".join(
        p if CN_RE.fullmatch(p) else '<span class="en">%s</span>' % p
        for p in CN_RE.split(e) if p)


def fit_size(orig, h, floor=FLOOR):
    return round(max(min(orig, h), floor), 2)


def luma(rgb):
    return 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]


def sample(im, bbox, step=4):
    W, H = im.size
    x0, y0, x1, y1 = bbox
    px0 = max(0, int(x0 * SCALE)); py0 = max(0, int(y0 * SCALE))
    px1 = min(W - 1, int(x1 * SCALE)); py1 = min(H - 1, int(y1 * SCALE))
    out = []
    for y in range(py0, py1 + 1, step):
        for x in range(px0, px1 + 1, step):
            out.append(im.getpixel((x, y)))
    return out


def mode_color(pix):
    """量化聚类找主色，但返回该簇像素的【实际均值】（不能用量化值，否则填色会偏）。"""
    if not pix:
        return (255, 255, 255)
    cnt = Counter(tuple((c // 24) * 24 for c in p) for p in pix)
    key = cnt.most_common(1)[0][0]
    grp = [p for p in pix if tuple((c // 24) * 24 for c in p) == key]
    n = len(grp)
    return (sum(p[0] for p in grp) // n, sum(p[1] for p in grp) // n, sum(p[2] for p in grp) // n)


def bg_analysis(im, bbox):
    """返回 (背景色 rgb, 背景占比, 墨迹颜色均值)。"""
    pix = sample(im, bbox)
    if not pix:
        return (255, 255, 255), 1.0, None
    cnt = Counter(tuple((c // 24) * 24 for c in p) for p in pix)
    key = cnt.most_common(1)[0][0]
    bg = mode_color(pix)
    ink = [p for p in pix if sum((a - b) ** 2 for a, b in zip(p, key)) ** 0.5 > 50]
    bg_frac = 1 - len(ink) / len(pix)
    if ink:
        n = len(ink)
        avg = (sum(p[0] for p in ink) // n, sum(p[1] for p in ink) // n, sum(p[2] for p in ink) // n)
    else:
        avg = None
    return bg, bg_frac, avg


def frame_bg(im, bbox, pad=5):
    """从 bbox 外框（文字区之外）取背景色，比 bbox 内取众数更可靠。"""
    W, H = im.size
    x0, y0, x1, y1 = bbox
    px0 = max(0, int((x0 - pad) * SCALE)); py0 = max(0, int((y0 - pad) * SCALE))
    px1 = min(W - 1, int((x1 + pad) * SCALE)); py1 = min(H - 1, int((y1 + pad) * SCALE))
    ix0 = int(x0 * SCALE); iy0 = int(y0 * SCALE)
    ix1 = int(x1 * SCALE); iy1 = int(y1 * SCALE)
    band = max(2, int(pad * SCALE))
    pix = []
    for y in range(py0, py1 + 1, 2):
        for x in range(px0, px1 + 1, 2):
            inside = (ix0 + band <= x <= ix1 - band) and (iy0 + band <= y <= iy1 - band)
            if not inside:
                pix.append(im.getpixel((x, y)))
    if not pix:
        return None, 1.0
    cnt = Counter(tuple((c // 24) * 24 for c in p) for p in pix)
    key = cnt.most_common(1)[0][0]
    return mode_color(pix), cnt.most_common(1)[0][1] / len(pix)


def luma_std(im, bbox, step=2):
    """bbox 内像素亮度的标准差 —— 判断该处是否还有可见结构（残影/画作）。"""
    W, H = im.size
    x0, y0, x1, y1 = bbox
    px0 = max(0, int(x0 * SCALE)); py0 = max(0, int(y0 * SCALE))
    px1 = min(W - 1, int(x1 * SCALE)); py1 = min(H - 1, int(y1 * SCALE))
    ls = []
    for y in range(py0, py1 + 1, step):
        for x in range(px0, px1 + 1, step):
            p = im.getpixel((x, y))
            ls.append(0.299 * p[0] + 0.587 * p[1] + 0.114 * p[2])
    if len(ls) < 4:
        return 0.0
    return statistics.pstdev(ls)


def text_color(bg, ink_avg):
    if luma(bg) < 100:
        return '#FFFFFF'
    if ink_avg is not None:
        return '#%02X%02X%02X' % ink_avg
    return '#000000'


# ---------- 中间产物缓存：按**源文件身份**失效 ----------
# ⚠️ v1.28 之前这里只写 `if not os.path.exists(CLEAN)`，**没有任何失效机制**：
#   源 PDF 一改（或换了源文件但沿用同一输出目录），旧的 `_clean.pdf` 与
#   `*_p%03d.png` 会被**静默复用** ⇒ 左半页是新源、右半页是旧源，无任何报错。
#   实测踩过：样例源改版后重跑，右半页仍印着上一版的长句残片，只有逐像素比对才查出来。
#   —— 与「块数/译文数错位」同一类失败：**看起来正常，其实是错的**。
#   现在按 (绝对路径, mtime_ns, 文件大小) 生成缓存键，键不符就把旧中间产物全删掉重建。
def _cache_key(path):
    st = os.stat(path)
    return '%s|%d|%d' % (os.path.abspath(path), st.st_mtime_ns, st.st_size)


_CK = WORK + '_cache.key'
_KEY = _cache_key(SRC)
_CACHED = (os.path.exists(_CK) and open(_CK, encoding='utf-8').read().strip() == _KEY)
if not _CACHED:
    _stale = [CLEAN]
    if os.path.isdir(WORK):
        _stale += [WORK + f for f in os.listdir(WORK)
                   if f.startswith(('src_p', 'cln_p')) and f.endswith('.png')]
    _n = 0
    for _f in _stale:
        if os.path.exists(_f):
            os.remove(_f)
            _n += 1
    if _n:
        print('源文件已变更 → 清掉 %d 个过期中间产物' % _n)

# ---------- 无字版：剥离内容流里的 BT...ET（缓存到 CLEAN）----------
# ⚠️ 不能用 apply_redactions：它只把文字标记为不可提取，绘制指令仍在（见坑 0）。
if not _CACHED or not os.path.exists(CLEAN):
    _c = fitz.open(SRC)
    for _p in _c:
        for _xr in _p.get_contents():
            _d = _c.xref_stream(_xr)
            if not _d:
                continue
            _new = re.sub(rb"BT\b.*?\bET", b"", _d, flags=re.S)
            if _new != _d:
                _c.update_stream(_xr, _new)
    _c.save(CLEAN, garbage=4, deflate=True)
    _c.close()
    print('无字版已生成:', CLEAN)

# ---------- 渲染源页 & clean 页 ----------
src = fitz.open(SRC)
clean = fitz.open(CLEAN)
PAGE_END = src.page_count if PAGE_LIMIT is None else min(PAGE_LIMIT, src.page_count)
for pno in range(PAGE_END):
    for tag, doc in (('src', src), ('cln', clean)):
        fp = WORK + '%s_p%03d.png' % (tag, pno)
        if not os.path.exists(fp):
            doc[pno].get_pixmap(dpi=DPI).save(fp)

# ---------- 拆分装饰大号 ----------
b_raw = json.load(open(BLOCKS_JSON, encoding='utf-8'))
t_raw = json.load(open(TRANS_JSON, encoding='utf-8'))

# ---------- 对位自检：每页的块数必须与译文数相等 ----------
# SKILL.md「译文质量规范 · 格式保留」的铁律：译文逐块对位，块的数量/顺序/归属不许动。
# 若此处不拦住，后面的渲染会**静默错位**——多余的译文被丢弃、缺失的块被跳过，
# 成品照样生成、页面照样"看起来正常"，但整页译文串行。
# 实测踩过：源 PDF 改版后忘记重出 translations.json（块数 8 → 7 仍喂 8 条），
# 结果右页左栏空、右栏串成左栏译文、页脚显示的是深色块文字，只有渲染出来才发现。
#
# ⚠️ 光栅化页是**例外**：那类页 BT=0 ⇒ 提取到的块数为 0，但中文是经 overlays.json
#    写进去的、且按规范**也要登记进 translations.json**（否则 04_verify 会报字符数不符）。
#    所以该页的期望条数 = 块数 + overlay 段数，不能直接用块数比。
_mis = []
for _p in sorted(set(b_raw) | set(t_raw), key=lambda x: int(x) if str(x).isdigit() else 0):
    _nb = len(b_raw.get(_p, []))
    _ovn = len(OVERLAY.get(int(_p), {}).get('texts', [])) if str(_p).isdigit() else 0
    _nt = len(t_raw.get(_p, []))
    if _nb + _ovn != _nt:
        _mis.append('page %s: blocks=%d + overlay=%d, translations=%d'
                    % (_p, _nb, _ovn, _nt))
if _mis:
    sys.exit('错误：块数与译文数不匹配 —— 译文必须逐块对位，不许增删。\n  '
             + '\n  '.join(_mis)
             + '\n提示：源 PDF 或 01_extract 的结果一旦变更，必须重新生成 '
               'translations.json（对照 to_translate.txt 的 [页码][序号] 重排）；'
               '光栅化页的中文登记在 overlays.json 里，也要同步进 translations.json。')


def split_decorations(b, t):
    new_b, new_t = {}, {}
    for p, items in b.items():
        page_doc = src[int(p)]
        spans = [s for blk in page_doc.get_text('dict')['blocks'] if blk['type'] == 0
                 for l in blk['lines'] for s in l['spans'] if s['text'].strip()]
        splits = {}
        for bi, item in enumerate(items):
            ix0, iy0, ix1, iy1 = item['bbox']
            ins = [s for s in spans
                   if s['bbox'][0] >= ix0 - 1 and s['bbox'][1] >= iy0 - 1
                   and s['bbox'][2] <= ix1 + 1 and s['bbox'][3] <= iy1 + 1]
            if len(ins) < 2: continue
            sizes = [s['size'] for s in ins]
            mx = max(sizes)
            others = [z for z in sizes if z < mx * 0.6]
            if others and mx / (sum(others) / len(others)) > 2.5:
                dec = [s for s in ins if s['size'] == mx][0]
                if 1 <= len(dec['text'].strip()) <= 5:
                    splits[bi] = (dec, ins)
        if not splits:
            new_b[p] = [dict(it) for it in items]
            new_t[p] = list(t[p])
            continue
        plist, tlist = [], []
        for bi, it in enumerate(items):
            if bi not in splits:
                plist.append(dict(it)); tlist.append(t[p][bi]); continue
            dec, ins = splits[bi]
            dec_text = dec['text'].strip()
            dx0, dy0, dx1, dy1 = dec['bbox']
            dec_item = dict(it)
            dec_item.update(bbox=[round(v, 1) for v in dec['bbox']],
                            size=round(dec['size'], 2), maxsize=round(dec['size'], 2),
                            text=dec_text, kind='decoration')
            main_item = dict(it)
            others = [(s['text'], s['size']) for s in ins if s['size'] < dec['size'] * 0.6]
            if others:
                ws = sum(sz * len(tx) for tx, sz in others)
                wn = sum(len(tx) for tx, sz in others)
                main_item['size'] = round(ws / max(1, wn), 2)
            # ★ 避让：装饰位于主块「前缘」（左/右/上）时收缩主块 bbox。
            #   预留宽度按**实际字形宽**算，不能用装饰 span 的原始 bbox
            #   （那是英文窄字形，换成中文字体/西文字体后会变宽）。
            dsize = dec['size']
            gw = dsize * (0.62 * len(dec_text) if dec_text.isascii() else len(dec_text))
            gap = 2.0
            mx0, my0, mx1, my1 = main_item['bbox']
            mw = max(1.0, mx1 - mx0); mh = max(1.0, my1 - my0)
            if dx0 <= mx0 + 6 and dy0 <= my0 + mh * 0.4:          # 左侧
                # 只要重叠就缩（text-indent 只管第一行，第 2 行起仍会压装饰号）
                main_item['bbox'] = [round(max(mx0, dx0 + gw + gap), 1), round(my0, 1), mx1, my1]
                log_avoid.append('page %s %s: 主块左侧避让 %r (留 %.0fpt)' % (p, it.get('id'), dec_text, gw))
            elif dx1 >= mx1 - 6 and dy0 <= my0 + mh * 0.4:        # 右侧
                main_item['bbox'] = [mx0, round(my0, 1), round(min(mx1, dx1 - gw - gap), 1), my1]
                log_avoid.append('page %s %s: 主块右侧避让 %r (留 %.0fpt)' % (p, it.get('id'), dec_text, gw))
            elif dy1 <= my0 + mh * 0.4:                            # 上方
                main_item['bbox'] = [mx0, round(max(my0, dy1 + gap), 1), mx1, my1]
                log_avoid.append('page %s %s: 主块上方避让 %r' % (p, it.get('id'), dec_text))
            # 去掉主译文首尾的装饰文本（数字型常在前，如 "5 元素之力…"）
            main_trans = t[p][bi].strip()
            if main_trans.endswith(dec_text):
                main_trans = main_trans[:-len(dec_text)].strip()
            elif main_trans.startswith(dec_text):
                main_trans = main_trans[len(dec_text):].strip()
            # ★ 守卫：只对**字母型**装饰生效。数字型（"3"/"5"）在正文里本就常见
            #   （"5 月""30 年"），拿它当重复会把装饰整个丢掉。
            if dec_text.isalpha() and re.search(
                    r'(?<![A-Za-z])' + re.escape(dec_text) + r'(?![A-Za-z])', main_trans):
                plist.append(dict(it))
                tlist.append(t[p][bi])
                continue
            plist += [dec_item, main_item]
            tlist += [dec_text, main_trans]
        new_b[p] = plist
        new_t[p] = tlist
    return new_b, new_t


log_avoid = []


def is_decor(it):
    """独立装饰块：文本很短 + 字号很大 + 框子窄（如 "1" "2" "10" "Z"）。"""
    txt = (it.get('text') or '').strip()
    if not txt or len(txt) > 5:
        return False
    sz = it.get('size', 0)
    if sz < 18:
        return False
    w = it['bbox'][2] - it['bbox'][0]
    h = it['bbox'][3] - it['bbox'][1]
    return (w < sz * 1.5) or (h < sz * 2)


_FONT_CACHE = {}


def glyph_w(it):
    """装饰号渲染后的实际宽度。用字体度量算，别估 —— 大写字母比 0.62em 宽不少
    （实测 Times "BRAD" @41.9pt = 118.7pt，按 0.62em 估只有 104pt，会压到正文）。"""
    txt = (it.get('text') or '').strip()
    sz = it.get('size', 10)
    if not txt:
        return 0.0
    if txt.isascii():
        fname = 'tiro'
        if fname not in _FONT_CACHE:
            _FONT_CACHE[fname] = fitz.Font(fname)
        try:
            return _FONT_CACHE[fname].text_length(txt, fontsize=sz)
        except Exception:
            return sz * 0.72 * len(txt)
    return sz * len(txt)


def bbox_ov(a, b, pad=0.0):
    return not (a[2] <= b[0] + pad or a[0] >= b[2] - pad
                or a[3] <= b[1] + pad or a[1] >= b[3] - pad)


# ---------- 按 y 间隙切开合并块（典型：同一栏里的两条独立文章）----------
log = []


def _x_groups(sps):
    """按 x 区间把 spans 分组成「栏」。

    ★ 两步走，顺序不能颠倒：
      1) **先按行分组** —— 同一 y 上的 span 必属同一栏。
         否则「同一行里并排的多个 span」（如 `bradteare.com | Instagram: @bradteare | 电话`）
         会被当成三栏，把一行文字切成三段（实测 p1/p44/p98 大面积误伤）。
      2) **再把行按 x 区间合并成栏** —— 区间有重叠，或左边缘相距 < 25pt ⇒ 同栏。
         只用 x0 差判不行：段末缩进行（如「— Norman Kolpas」）的 x0 会比同栏首行偏右 45pt。
    """
    lines = {}
    for s in sps:
        lines.setdefault(round(s['bbox'][1]), []).append(s)
    rows = []
    for y in sorted(lines):
        g = lines[y]
        rows.append([min(s['bbox'][0] for s in g), max(s['bbox'][2] for s in g), g])
    groups = []
    for x0, x1, g in rows:
        for gr in groups:
            gx0 = min(z[0] for z in gr)
            gx1 = max(z[1] for z in gr)
            if min(x1, gx1) - max(x0, gx0) > 0 or abs(x0 - gx0) < 25:
                gr.append((x0, x1, g))
                break
        else:
            groups.append([(x0, x1, g)])
    return [[s for _, _, g in gr for s in g] for gr in groups]


def _assign_spans(items, all_spans):
    """把每个 span 分给「包含它且面积最小」的块（最紧的那个）。

    ★ 不能只按 bbox 包含关系取 span —— 一个跨栏块会把**被它包住的另一块**的 span
      也一并捞进来（实测 page 32 的 T05 捞到了 T03 的第 2 栏），
      于是按栏切会切出「3 栏」并把第 2 栏内容复制一份。
    """
    areas = [(it['bbox'][2] - it['bbox'][0]) * (it['bbox'][3] - it['bbox'][1])
             for it in items]
    out = {k: [] for k in range(len(items))}
    for s in all_spans:
        cx = (s['bbox'][0] + s['bbox'][2]) / 2.0
        cy = (s['bbox'][1] + s['bbox'][3]) / 2.0
        best, best_a = None, None
        for k, it in enumerate(items):
            bx0, by0, bx1, by1 = it['bbox']
            if bx0 - 1 <= cx <= bx1 + 1 and by0 - 1 <= cy <= by1 + 1:
                if best_a is None or areas[k] < best_a:
                    best, best_a = k, areas[k]
        if best is not None:
            out[best].append(s)
    return out


def split_large_x_gaps(b, t):
    """块内 span 在 x 方向分成多「栏」时，按栏切成多块。

    ★ 为什么必须切：PyMuPDF 会把**同一 y 带、不同 x 栏**的文本并进一个 block。
    实测 page 32 的 T05 把「第 1 栏图注」与「第 3 栏正文」并成一块，
    bbox 横跨 x 36→569.5，**正好盖住第 2 栏的 T03** ⇒ 两栏中文在同一区域交错渲染
    （用户看到的是「并且热爱的题材…」与「尤其令格林兴奋的是…」逐行交织）。

    译文按「每一栏最后一个英文 span」在译文中的位置依次落刀
    （与 `split_large_y_gaps` 同法）；找不到就按该栏字符数占比回退。
    """
    for p, items in list(b.items()):
        page_doc = src[int(p)]
        all_spans = [s for blk in page_doc.get_text('dict')['blocks']
                     if blk['type'] == 0
                     for l in blk['lines'] for s in l['spans']
                     if s['text'].strip()]
        mine = _assign_spans(items, all_spans)
        new_items, new_t = [], []
        for i, it in enumerate(items):
            cn = t[p][i]
            sps = mine[i]
            if len(sps) < 4:
                new_items.append(it); new_t.append(cn); continue
            groups = _x_groups(sps)
            if len(groups) < 2:
                new_items.append(it); new_t.append(cn); continue
            # ★ 各栏的 x 区间必须**互不相交且留有栏沟** —— 否则那不是「栏」，
            #   只是同一行里并排的 span 被拆开了（实测 p44/p60/p16 会被误切）。
            rng = sorted((min(s['bbox'][0] for s in g), max(s['bbox'][2] for s in g))
                         for g in groups)
            if any(b[0] < a[1] + 15 for a, b in zip(rng, rng[1:])):
                new_items.append(it); new_t.append(cn); continue
            # 按「提取顺序」给栏排序 —— 译文是照提取顺序译的，切刀必须按这个顺序落
            groups.sort(key=lambda g: min(all_spans.index(s) for s in g))
            # 求各栏切点；找不到英文锚点就按该栏字符数占比回退
            tot = sum(len(s['text']) for g in groups for s in g)
            cuts, acc = [], 0
            for g in groups[:-1]:
                acc += sum(len(s['text']) for s in g)
                tail = g[-1]['text'].strip()
                cut = -1
                if len(tail) >= 4 and tail in cn:
                    cut = cn.rfind(tail) + len(tail) - 1
                    # 切点后紧跟收尾标点时一并归入上一段 ——
                    # 否则下一段会以「)」这类孤零零的收尾符开头（实测 p32 左栏）。
                    while cut + 1 < len(cn) and cn[cut + 1] in '）)】」』”’、，。；：':
                        cut += 1
                if cut < 0:
                    # 回退：按该栏字符数占比定位，再**吸附到就近标点**（与 y 切分同法），
                    # 避免把「霍华德·波斯|特」这种人名从中间劈开。
                    base = max(0, int(len(cn) * acc / max(1, tot)) - 1)
                    cut = base
                    for sep in ('。', '；', '，', '、', '. ', ';'):
                        cands = [c for c in (cn.find(sep, base),
                                             cn.rfind(sep, 0, base + 1)) if c >= 0]
                        if cands:
                            c = min(cands, key=lambda z: abs(z - base))
                            if abs(c - base) <= 12:
                                cut = c
                                break
                cuts.append(cut)
            for k in range(1, len(cuts)):        # 切点必须严格递增
                if cuts[k] <= cuts[k - 1]:
                    cuts[k] = min(len(cn) - 1, cuts[k - 1] + 1)
            segs, prev = [], -1
            for c in cuts:
                segs.append(cn[prev + 1:c + 1].strip()); prev = c
            segs.append(cn[prev + 1:].strip())
            # 生成子块（bbox 按栏收紧；size/nlines 按栏内 span 重算）
            for g, seg in zip(groups, segs):
                if not seg:
                    continue
                sub = dict(it)
                sub['bbox'] = [round(min(s['bbox'][0] for s in g), 1),
                               round(min(s['bbox'][1] for s in g), 1),
                               round(max(s['bbox'][2] for s in g), 1),
                               round(max(s['bbox'][3] for s in g), 1)]
                ws = sum(s['size'] * len(s['text']) for s in g)
                wn = sum(len(s['text']) for s in g)
                sub['size'] = round(ws / max(1, wn), 2)
                sub['maxsize'] = round(max(s['size'] for s in g), 2)
                sub['nlines'] = len({round(s['bbox'][1]) for s in g})
                new_items.append(sub); new_t.append(seg)
            log.append('page %s %s: 按栏切 %d 块' % (p, it.get('id'), len(groups)))
        b[p] = new_items
        t[p] = new_t


def split_large_y_gaps(b, t):
    """如果一个块的 spans 之间 y 间隙 > 阈值，按间隙切为两块（带译文按 spans 字符数
    比例切，在最近的「。/；/，」边界落刀）。"""
    for p, items in list(b.items()):
        page_doc = src[int(p)]
        all_spans = [s for blk in page_doc.get_text('dict')['blocks']
                     if blk['type'] == 0
                     for l in blk['lines'] for s in l['spans']
                     if s['text'].strip()]
        new_items, new_t = [], []
        for i, it in enumerate(items):
            cn = t[p][i]
            ix0, iy0, ix1, iy1 = it['bbox']
            sps = [s for s in all_spans
                   if s['bbox'][0] >= ix0-1 and s['bbox'][2] <= ix1+1
                   and s['bbox'][1] >= iy0-1 and s['bbox'][3] <= iy1+1]
            if len(sps) < 4:
                new_items.append(it); new_t.append(cn); continue
            sps.sort(key=lambda s: s['bbox'][1])
            gaps = [(sps[j+1]['bbox'][1] - sps[j]['bbox'][3], j)
                    for j in range(len(sps)-1)]
            max_gap, max_idx = max(gaps)
            if max_gap < 12:
                new_items.append(it); new_t.append(cn); continue
            # ★ 上段不足 2 行 ⇒ 它是「kicker / 城市行」，不是独立段落，**不切**。
            #   实测 page 33 的 T01 = `PASADENA, CA` + `Eric Bowman` + `Maxwell Alexander
            #   Gallery, May 16-June 6`：切开后译文按字符比例回退落刀，
            #   把「埃里克·鲍曼」从中间劈成「…帕萨迪纳 埃里克·」＋「鲍曼 (Eric Bowman)…」。
            #   同一模式在展览预告页**每两页一次**，共 14 页（p29–53）。
            #   ⚠️ 不能用「字符数」判（p13 的 `43 rd` 也只有 19 字但确该切）—— 只有行数能区分。
            nlines_top = len({round(s['bbox'][1]) for s in sps[:max_idx + 1]})
            if nlines_top < 2:
                new_items.append(it); new_t.append(cn); continue
            # ★ 切点贴近下半段第一行（不要取中点）：中文比英文长，上半段需要更多空间
            split_y = max(sps[max_idx]['bbox'][3] + 1.0,
                          sps[max_idx + 1]['bbox'][1] - 1.0)
            upper = dict(it); upper['bbox'] = [ix0, iy0, ix1, round(split_y, 1)]
            upper['_split_upper'] = True
            lower = dict(it); lower['bbox'] = [round(ix0, 1), round(split_y, 1), ix1, iy1]
            for part in (upper, lower):
                py0, py1 = part['bbox'][1], part['bbox'][3]
                pp = [s for s in sps if s['bbox'][1] >= py0-1 and s['bbox'][3] <= py1+1]
                if pp:
                    ws = sum(s['size']*len(s['text']) for s in pp)
                    wn = sum(len(s['text']) for s in pp)
                    part['size'] = round(ws/max(1, wn), 2)
                    part['maxsize'] = round(max(s['size'] for s in pp), 2)
                    # ★ 行数必须数「不同 y 的行数」，不能只数首行 —— 数错会让
                    #   line-height 被算成 1.45，中文多出来的行就溢出压到下一段
                    part['nlines'] = len({round(s['bbox'][1]) for s in pp})
            # 切译文：优先用「上半段最后一个英文 span 的文本」在译文里的位置落刀
            # （如 page 21 的 "settlerswest.com" 正是 item 1 的结尾、item 3 的开头），
            # 否则退回按字符数比例 + 就近标点。
            tail_en = sps[max_idx]['text'].strip()
            cut = -1
            if len(tail_en) >= 4 and tail_en in cn:
                cut = cn.rfind(tail_en) + len(tail_en) - 1
            if cut < 0:
                n_top = sum(len(s['text']) for s in sps[:max_idx+1])
                n_bot = sum(len(s['text']) for s in sps[max_idx+1:])
                tot = max(1, n_top + n_bot)
                cut_char = int(len(cn) * n_top / tot)
                for sep in ('。', '；', '，', '. ', ';'):
                    c = cn.find(sep, cut_char)
                    if 0 <= c <= cut_char + 20:
                        cut = c
                        break
                else:
                    cut = max(cut_char - 1, 0)
            c1 = cn[:cut + 1].strip()
            c2 = cn[cut + 1:].strip()
            new_items += [upper, lower]
            new_t += [c1, c2]
            log.append('page %s %s: 切 2 块（y gap %.1f）' % (p, it.get('id'), max_gap))
        b[p] = new_items
        t[p] = new_t


def avoid_collisions(items, pn):
    """装饰块压住正文块时，正文块让开（独立装饰块也能处理）。"""
    for it in items:
        if is_decor(it):
            it['kind'] = 'decoration'
    for i, a in enumerate(items):
        if not is_decor(a):
            continue
        ax0, ay0, ax1, ay1 = a['bbox']
        gx1 = ax0 + glyph_w(a)
        for j, bb in enumerate(items):
            if i == j or is_decor(bb):
                continue
            bx0, by0, bx1, by1 = bb['bbox']
            if not bbox_ov([ax0, ay0, gx1, ay1], [bx0, by0, bx1, by1]):
                continue
            if ax0 <= bx0 + (bx1 - bx0) * 0.5:            # 装饰在左 → 正文右移
                # ⚠️ 不能用「text-indent 够不够」来判断要不要缩：text-indent 只作用于
                #    **第一行**，正文第 2 行起仍会压到装饰号上（实测 page 21 的 "3"/"1"/"5"
                #    都是这样被压的）。所以只要重叠就缩。
                nx0 = max(bx0, gx1 + 2.0)
                if nx0 < bx1 - 25:
                    bb['bbox'] = [round(nx0, 1), by0, bx1, by1]
                    log_avoid.append('page %s %s ← 装饰%s 左避让' % (pn, bb.get('id'), a.get('text')))
            else:                                          # 装饰在右 → 正文左收
                nx1 = min(bx1, ax0 - 2.0)
                if nx1 > bx0 + 25:
                    bb['bbox'] = [bx0, by0, round(nx1, 1), by1]
                    log_avoid.append('page %s %s ← 装饰%s 右避让' % (pn, bb.get('id'), a.get('text')))


print('拆分装饰大号 + 按 y 间隙切 + 碰撞避让...')
b, t = split_decorations(b_raw, t_raw)

# ★ 顺序：split_decorations → split_large_y_gaps → avoid_collisions
#   （split 必须在 avoid 之前，否则避免改的 bbox 太小、split 看不见 y-gap）
split_large_x_gaps(b, t)      # ★ 必须先按栏切：跨栏块的 bbox 会盖住同带的其他栏
split_large_y_gaps(b, t)
for pn, items in b.items():
    avoid_collisions(items, pn)

# ---------- 手工补「烧进位图、无文字层」的装饰号（可选按项目扩展）----------
# 若特定项目某页存在烧进位图而提取不到的大号装饰字，可在字典中按页码配置补全；默认通用状态下为空。
# 示例：
# MANUAL_DECORATIONS = {
#     '21': [{'id': 'T04D', 'bbox': [35.8, 492.7, 83.5, 543.4],
#             'size': 30.0, 'maxsize': 30.0, 'text': '4', 'kind': 'decoration',
#             'align': 'l', 'color': 0x9C806E, 'nlines': 1, 'cover': True}]
# }
MANUAL_DECORATIONS = {}
for pn, extra in MANUAL_DECORATIONS.items():
    if pn not in b:
        continue
    for e in extra:
        b[pn].append(dict(e))
        t[pn].append(e['text'])
    # 补完再跑一次避让（让相邻正文块让开新装饰号）
    avoid_collisions(b[pn], pn)
    log_avoid.append('page %s: 手工补装饰号 %s' % (pn, [e['text'] for e in extra]))

# ---------- 同页装饰号统一字号 ----------
# 原刊的装饰号大小不一（实测 page 21 是 30/64/46/30），中文页里看着很乱。
# 统一取同页 decoration 的**最小 size**（以较小的 "1"/"5" 为准）。
for pn, items in b.items():
    decs = [it for it in items if it.get('kind') == 'decoration']
    if len(decs) < 2:
        continue
    uni = min(it['size'] for it in decs)
    for it in decs:
        if it['size'] != uni:
            log_avoid.append('page %s: 装饰号 %r 字号 %.1f → %.1f' %
                             (pn, it.get('text'), it['size'], uni))
            it['size'] = uni
            it['maxsize'] = uni

for line in (log_avoid + log):
    print(line)

# ---------- 合成 ----------
W, H = src[0].rect.width, src[0].rect.height
DX = W
chunks = []
scales = []
for _c0 in range(0, PAGE_END, CHUNK):
    _c1 = min(_c0 + CHUNK, PAGE_END)
    out = fitz.open()
    for pno in range(_c0, _c1):
        page = out.new_page(width=W * 2, height=H)
        page.show_pdf_page(fitz.Rect(0, 0, W, H), src, pno)
        page.show_pdf_page(fitz.Rect(DX, 0, DX + W, H), clean, pno)

        # ★ 光栅化页：涂白矩形必须在**所有文字之前**画（画序铁律，见坑 14）。
        _ovp = OVERLAY.get(pno)
        if _ovp:
            for _c in _ovp.get('covers', []):
                page.draw_rect(fitz.Rect(_c[0] + DX, _c[1], _c[2] + DX, _c[3]),
                               color=None, fill=(1, 1, 1))
            log.append('page %d: 光栅化页涂白 %d 块' % (pno, len(_ovp.get('covers', []))))

        blist = b.get(str(pno), [])
        tlist = t.get(str(pno), [])
        src_im = Image.open(WORK + 'src_p%03d.png' % pno).convert('RGB')
        cln_im = Image.open(WORK + 'cln_p%03d.png' % pno).convert('RGB')

        # ★ 预扫：先把所有 cover=True 的覆盖矩形画掉。
        #   不能放在主循环里 —— 手工补的装饰号排在列表末尾，会在正文之后才画，
        #   结果把正文左侧盖掉（实测 page 21 的 "4" 盖掉了「尺」「Ander」等）。
        for i, bi_ in enumerate(blist):
            if not bi_.get('cover'):
                continue
            bx0, by0 = bi_['bbox'][0], bi_['bbox'][1]
            bx1, by1 = bi_['bbox'][2], bi_['bbox'][3]
            cp = max(2.0, bi_.get('size', 10) * 0.25)
            cb, _, _ = bg_analysis(cln_im, bi_['bbox'])
            page.draw_rect(fitz.Rect(bx0 - cp + DX, by0 - cp, bx1 + cp + DX, by1 + cp),
                           color=None, fill=tuple(c / 255.0 for c in cb), overlay=True)
            log.append('page %d %s: 盖掉位图原字形 %s' % (pno, bi_.get('id'), cb))

        # ★ 预先算好每个块的「下方可用空间」上限 —— 同栏、x 有重叠、且位于本块下方的
        #   最近块的上沿。没有这个上限时，「按需扩行」会把标题框一路撑下去，
        #   直接压进下方正文块（实测 page 33 的标题扩到 134.6pt，压住正文首行）。
        y_limits = {}
        for j, bj in enumerate(blist):
            if j >= len(tlist) or not tlist[j]:
                continue
            jx0, jy0, jx1, _ = bj['bbox']
            best = float(H)
            for k, bk in enumerate(blist):
                if k == j or k >= len(tlist) or not tlist[k]:
                    continue
                kx0, ky0, kx1, _ = bk['bbox']
                if kx1 <= jx0 + 2 or kx0 >= jx1 - 2:      # x 方向无重叠 → 不算
                    continue
                if ky0 > jy0 + 1:                         # 在本块下方
                    best = min(best, ky0)
            y_limits[j] = best

        # ★ 预扫②：把所有「涂底」矩形先画掉，并缓存采样结果供主循环复用。
        #   与坑 14 同源：涂底若和文字同轮，**下方块的涂底会盖住上方块刚写的字** ——
        #   实测 page 33 的 T01 被「按 y 间隙切 2 块」切成上下两块，下块的涂底
        #   pad=29pt 向上扩，把上块的字裁掉一半（只有上半截可见）。
        #   ⇒ 一切底色/遮罩都必须排在**所有文字之前**。
        fill_prep = {}
        for i, bi_ in enumerate(blist):
            if i >= len(tlist) or not tlist[i]:
                continue
            bx0, by0 = max(0.0, bi_['bbox'][0]), max(0.0, bi_['bbox'][1])
            bx1, by1 = min(W, bi_['bbox'][2]), min(H, bi_['bbox'][3])
            bw, bh = max(1.0, bx1 - bx0), max(1.0, by1 - by0)
            bfloor = 5.0 if bi_.get('kind') == 'cluster' else FLOOR
            bsize = max(fit_size(bi_['size'], bh, bfloor),
                        bfloor if bi_.get('kind') == 'cluster' else 0)
            if bi_.get('_split_upper'):
                bsize = round(bsize * 0.9, 2)
            cbg, cfrac, cink = bg_analysis(cln_im, bi_['bbox'])
            sd = luma_std(cln_im, bi_['bbox'])
            fill_prep[i] = (cbg, cfrac, cink, sd)
            if bi_.get('kind') == 'decoration':      # 装饰号不涂底（坑 13 陷阱 A）
                continue
            need_fill, use_bg = False, None
            if (1 - cfrac) > 0.10 and cfrac >= BG_UNIFORM_T:
                fbg, ffrac = frame_bg(cln_im, bi_['bbox'], pad=max(5, bsize * 0.6))
                if fbg and ffrac >= 0.35:
                    d = sum((a - b) ** 2 for a, b in zip(fbg, cbg)) ** 0.5
                    if d <= 26:
                        need_fill, use_bg = True, fbg
            if not need_fill and sd < 4.0 and bsize >= 20:
                need_fill, use_bg = True, cbg
            if need_fill and use_bg:
                pad_fill = max(4.0, min(bsize * 1.4, 80.0))
                fx0 = max(0.0, bx0 - pad_fill); fy0 = max(0.0, by0 - pad_fill * 0.5)
                fx1 = min(W, bx1 + pad_fill); fy1 = min(H, by1 + pad_fill * 0.5)
                page.draw_rect(fitz.Rect(fx0 + DX, fy0, fx1 + DX, fy1),
                               color=None, fill=tuple(c / 255.0 for c in use_bg),
                               overlay=True)
                log.append('page %d %s: 涂底 %s pad=%.0f sd=%.1f ink=%.2f' %
                           (pno, bi_.get('id'), use_bg, pad_fill, sd, 1 - cfrac))

        for i, bi_ in enumerate(blist):
            if i >= len(tlist) or not tlist[i]:
                continue
            cn = tlist[i]
            x0, y0 = max(0.0, bi_['bbox'][0]), max(0.0, bi_['bbox'][1])
            x1, y1 = min(W, bi_['bbox'][2]), min(H, bi_['bbox'][3])
            w, h = max(1.0, x1 - x0), max(1.0, y1 - y0)
            floor = 5.0 if bi_.get('kind') == 'cluster' else FLOOR
            size = max(fit_size(bi_['size'], h, floor), floor if bi_.get('kind') == 'cluster' else 0)
            # ★ 切分出的「上半块」：中文比英文长，且下方紧邻装饰号/下一段，没有扩展余地。
            #   htmlbox 在这种「刚好差几行」的情形不会自动缩，实测会溢出 8pt 压到下一段
            #   → 主动打 0.9 折保险。
            if bi_.get('_split_upper'):
                size = round(size * 0.9, 2)
            nl = max(1, bi_.get('nlines', 1))
            lh = min(max(h / (nl * size), 1.05), LINE_H)
            head = bi_.get('maxsize', bi_['size']) >= HEAD_SIZE
            face = FACE_HEAD if head else FACE_BODY

            # ★ 竖排/旋转块先判定：这类块 w 极窄、h 极长，上面的「按需扩行」逻辑
            #   对它毫无意义，反而会把 h 撑大（实测 96.6 → 213.8），
            #   导致竖排字号算得过大而放不下 ⇒ 必须跳过扩行，并用**原始** bbox 高。
            rotated = (w < size * 1.5 and h > size * 3)

            # ★ 单行框（图注/页脚常见：bbox 高 ≈ 字号）放不下更长的中文 → 会被压到 3pt 之类。
            #   按中文实际长度估算所需行数，把框**向下**扩到够用（上限 2.5 倍）。
            if not rotated:
                lw = cjk_width(cn) * size
                need_lines = max(1, math.ceil(lw / max(1.0, w)))
                if need_lines > 1:
                    need_h = need_lines * size * lh
                    if need_h > h:
                        # ★ 扩行不得越过同栏下方最近块的上沿（否则压住下一块）
                        lim = y_limits.get(i, float(H)) - y0 - 1.0
                        h = min(need_h, h * 2.5, H - y0, max(h, lim))

            # 文字色：优先用 clean 版采样（那是实际底色）。
            # 采样已在预扫②做过，这里直接复用（避免重复的像素分析）。
            if i in fill_prep:
                cbg, cfrac, cink, sd = fill_prep[i]
            else:
                cbg, cfrac, cink = bg_analysis(cln_im, bi_['bbox'])
                sd = luma_std(cln_im, bi_['bbox'])
            color = text_color(cbg, cink)

            # 涂底已在预扫②统一画完（必须在所有文字之前，见坑 14/坑 19）

            # 首行缩进只给**正文**：标题（head）与装饰号一律不缩进。
            # ⚠️ 原来只排除了 decoration ⇒ 标题首行也被缩进 2 字符。
            #   标题块常被合并成「位置行 + 大标题 + 副标题」，缩进后视觉上很怪。
            #   ⚠️ 别把「单行块」一律算作图注 —— 实测会误伤 497/1091（46%）的正文块。
            ti = '0' if (bi_.get('kind') == 'decoration' or head) else '2em'
            css = (face +
                   "p{font-family:cn;font-size:%.2fpt;line-height:%.2f;"
                   "text-align:left;margin:0;color:%s;text-indent:%s;}"
                   "span.en{font-family:en;}" % (size, lh, color, ti))
            try:
                # ★ 竖排/旋转块：原文 dir=(0,±1)（沿画作边缘的图注）。
                #   这类块 bbox 极窄（实测 page 24 的 T12 只有 7.1pt 宽、96.6pt 高），
                #   htmlbox 既不支持旋转、又必然 FAILED → 中文整段丢失。
                #   改用 insert_textbox(rotate=90) 沿纵向排，字号按纵向长度自适应。
                rotated = (w < size * 1.5 and h > size * 3)
                if rotated:
                    rgb = tuple(int(color[j:j + 2], 16) / 255.0 for j in (1, 3, 5))
                    rbox = fitz.Rect(x0 + DX, y0, x1 + DX, y1)
                    # ★ 先用**探针页**试排定字号，再在正式页写一次 ——
                    #   insert_textbox 放不下也会画，重复调用会叠字（坑 3）。
                    #   探针页用完即弃，不进成品。实测 33 字塞进 96.6pt 需 2.6pt，
                    #   而解析式估不准（CJK 实际约占 1.4em/字），故用试排。
                    probe = fitz.open()
                    pp = probe.new_page(width=W * 2, height=H)
                    vsize = size
                    rc = -1.0
                    for _ in range(25):          # 0.9 步长 × 25 次 ≈ 可降到原字号 7%
                        if vsize < 1.5:          # 再小就没意义了
                            break
                        rc = pp.insert_textbox(rbox, cn, fontname="china-s",
                                               fontsize=vsize, rotate=90,
                                               color=rgb, align=0)
                        if rc >= 0:
                            break
                        vsize *= 0.9
                    probe.close()
                    page.insert_textbox(rbox, cn, fontname="china-s",
                                        fontsize=vsize, rotate=90, color=rgb, align=0)
                    spare, scale = (0 if rc >= 0 else -1), 1.0
                    log.append('page %d %s: 竖排 %.2fpt (w=%.1f box_h=%.1f len=%d rc=%.2f)'
                               % (pno, bi_.get('id'), vsize, w, y1 - y0, len(cn), rc))
                elif bi_.get('kind') == 'decoration':
                    # ★ 装饰号一律用 insert_text：htmlbox 会给窄 bbox 外扩，
                    #   外扩后的大号字会压住旁边的正文（实测 page 21 的 "1" 压住「获」）。
                    # ⚠️ 纯 ASCII 装饰必须用西文字体：china-s 会把数字渲染成**全角**
                    #   （30pt 字号 → 30pt 宽，而原字只有 10.5pt），照样压正文。
                    rgb = tuple(int(color[j:j + 2], 16) / 255.0 for j in (1, 3, 5))
                    fname = "tiro" if cn.isascii() else "china-s"
                    # ★ 基线不能写死 `y0 + size*0.82` —— 统一字号后 size 变小，
                    #   基线跟着上移，字形会顶到框顶（实测 page 21 的 "2" 原 size 64→30
                    #   后上移约 17pt）。改为**在原 bbox 内垂直居中**：
                    #   数字字形约从 baseline-0.66em 到 baseline，中心 = baseline-0.33em
                    base_y = (y0 + y1) / 2 + size * 0.33
                    page.insert_text(fitz.Point(x0 + DX, base_y), cn,
                                     fontname=fname, fontsize=size, color=rgb)
                    spare, scale = 0, 1.0
                else:
                    spare, scale = page.insert_htmlbox(fitz.Rect(x0 + DX, y0, x1 + DX, y0 + h),
                                                       to_html(cn), css=css, archive=ARCH, scale_low=0.4)
                if spare < 0:
                    seq = (1.2, 1.5, 2.0, 2.5, 3.0) if bi_.get('kind') == 'decoration' else (1.15, 1.3, 1.4, 1.6)
                    for g in seq:
                        gx1 = min(W, x0 + w * g); gy1 = min(H, y0 + h * g)
                        spare, scale = page.insert_htmlbox(fitz.Rect(x0 + DX, y0, gx1 + DX, gy1),
                                                           to_html(cn), css=css, archive=ARCH, scale_low=0.4)
                        if spare >= 0: break
                if spare < 0 and len(cn) <= 3:
                    rgb = tuple(int(color[j:j + 2], 16) / 255.0 for j in (1, 3, 5))
                    page.insert_text(fitz.Point(x0 + DX, y0 + size * 0.82), cn,
                                     fontname="china-s", fontsize=size, color=rgb)
                    spare, scale = 0, 1.0
                    log.append('page %d %s: 兜底 insert_text %r' % (pno, bi_.get('id'), cn))
            except Exception as e:
                spare, scale = -1, 0
                log.append('!! page %d %s: %s' % (pno, bi_.get('id'), e))
            scales.append(scale)
            if spare < 0:
                log.append('page %d %s: FAILED (size=%.1f) %r' % (pno, bi_.get('id'), size, cn[:18]))

        # ★ 光栅化页：涂白之后写中文（字号仍用"以原文为基准、只降不升"的策略）。
        if _ovp:
            for _ov in _ovp.get('texts', []):
                _bx0, _by0, _bx1, _by1 = _ov['bbox']
                _bw, _bh = _bx1 - _bx0, _by1 - _by0
                _sz = fit_size(_ov['size'], _bh, 4.0)
                _lh = min(max(_bh / max(1.0, _sz), 1.05), LINE_H)
                _align = _ov.get('align', 'l')
                # 注意：ALIGN_CSS 只定义在 02_build.py 里，生产版没有 —— 这里直接映射，
                # 不要引用不存在的常量（ast 语法自检查不出 NameError）。
                _css_align = {'l': 'left', 'c': 'center', 'r': 'right'}.get(_align, 'left')
                try:
                    page.insert_htmlbox(
                        fitz.Rect(_bx0 + DX, _by0, _bx1 + DX, _by1),
                        to_html(_ov['text']),
                        css=(FACE_BODY + "p{font-family:cn;font-size:%.2fpt;"
                             "line-height:%.2f;text-align:%s;margin:0;}"
                             "span.en{font-family:en;}"
                             % (_sz, _lh, _css_align)),
                        archive=ARCH, scale_low=0.4)
                except Exception as _e:
                    log.append('!! page %d overlay text 失败: %s' % (pno, _e))
            log.append('page %d: 光栅化页写入 %d 段中文'
                       % (pno, len(_ovp.get('texts', []))))

        page.draw_line(fitz.Point(DX, 0), fitz.Point(DX, H),
                       color=(0.78, 0.78, 0.78), width=0.6, dashes="[2 2] 0")

    _fp = WORK + "_chunk%04d.pdf" % (_c0 // CHUNK)
    try:
        out.subset_fonts(verbose=False)
    except Exception:
        pass
    out.save(_fp, garbage=4, deflate=True)
    out.close()
    del out
    chunks.append(_fp)
    print("chunk pages %d-%d -> %s" % (_c0, _c1 - 1, _fp))

# ---------- 合并分块 ----------
merged = fitz.open(chunks[0])
for _fp in chunks[1:]:
    _d = fitz.open(_fp)
    merged.insert_pdf(_d)
    _d.close()
out = merged
try:
    out.subset_fonts(verbose=False)
except Exception:
    pass
out.save(DST, garbage=4, deflate=True)
# 构建成功后才写缓存键 —— 中途失败不会把"半成品"标记成可用。
open(_CK, 'w', encoding='utf-8').write(_KEY)
print('saved:', DST, round(os.path.getsize(DST) / 1048576, 2), 'MB  pages=%d' % out.page_count)
if scales:
    print('scale min %.2f avg %.2f n=%d' % (min(scales), sum(scales) / len(scales), len(scales)))
print('--- 涂底/避让/兜底 日志 ---')
print('\n'.join(log_avoid + log) if (log_avoid or log) else 'none')