# -*- coding: utf-8 -*-
"""04 校验成品：乱码 / 截断 / 英文残留 / 尺寸
用法: python 04_verify.py <输出PDF> <translations.json> [--dump 目录]
      python 04_verify.py --charset          仅扫描内置字体对字符集的覆盖
"""
import os, sys, json, re
import pymupdf as fitz

# ---------- 字符集扫描模式 ----------
if "--charset" in sys.argv:
    chars = list("ABCabc0123456789") + list("@.,:;/-_()[]&") + list("#$%") \
        + list("《》·—×©•’“”") + list("、，。；：？！")
    bad = []
    for ch in chars:
        d = fitz.open(); p = d.new_page(width=200, height=100)
        p.insert_textbox(fitz.Rect(10, 10, 190, 60), ch, fontname="china-s", fontsize=20)
        got = p.get_text().strip()
        if got != ch:
            bad.append((ch, got))
    print("fontname=china-s  missing/mismatch: %d of %d" % (len(bad), len(chars)))
    for a, b in bad:
        print("   %r -> %r" % (a, b))
    sys.exit(0)

if len(sys.argv) < 3 or "--help" in sys.argv or "-h" in sys.argv:
    sys.exit("用法: python 04_verify.py <输出PDF> <translations.json> [--dump 目录]\n"
             "      python 04_verify.py --charset  (仅扫描内置字体对字符集的覆盖)")

DST, TRANS = sys.argv[1], sys.argv[2]
if not os.path.isfile(DST):
    sys.exit(f"错误: 输出PDF文件不存在: {DST}")
if not os.path.isfile(TRANS):
    sys.exit(f"错误: 翻译JSON文件不存在: {TRANS}")

DUMP = sys.argv[sys.argv.index("--dump") + 1] if "--dump" in sys.argv else None
trans = json.load(open(TRANS, encoding="utf-8"))

doc = fitz.open(DST)
W = doc[0].rect.width / 2
# 通用白名单（网址、常见顶级域名、出版常见缩写/标识）
COMMON_RESERVED = (
    r"(https?://|www\.|\.com|\.org|\.net|\.cn|\.io|\.edu|"
    r"@[a-zA-Z0-9_\-\.]+|"
    r"USPS|ISSN|ISBN|GST|POSTMASTER|info|Studio|Gallery|LLC|Inc\.)"
)
RESERVED = re.compile(COMMON_RESERVED, re.I)


def squash(s):
    return re.sub(r"\s+", "", s)


issues, dump = [], []
for pno in range(doc.page_count):
    page = doc[pno]
    right = "".join(b[4] for b in page.get_text("blocks") if b[0] >= W - 1)
    dump.append("===== PAGE %d =====\n%s\n" % (pno, right.strip()))

    expect = "".join(squash(t) for t in trans.get(str(pno), []))
    got = squash(right)
    if abs(len(expect) - len(got)) > max(3, len(expect) * 0.02):
        issues.append("page %d: expected %d chars, got %d" % (pno, len(expect), len(got)))

    if len(re.findall(r"\?", right)) > 3:
        issues.append("page %d: mojibake? '?' x%d" % (pno, right.count("?")))

    # 判据：英文词若出现在译文中 => 有意保留的专有名词；否则才是原页真残留
    compact_trans = re.sub(r"\s+", "", " ".join(t for v in trans.values() for t in v))
    residue = [w for w in re.findall(r"[A-Za-z]{4,}", right)
               if w not in compact_trans and not RESERVED.search(w)]
    if residue:
        issues.append("page %d: english residue -> %s" % (pno, residue[:8]))

if DUMP:
    open(os.path.join(DUMP, "verify_dump.txt"), "w", encoding="utf-8").write("\n".join(dump))
out = ["pages=%d size=%.0fx%.0f" % (doc.page_count, doc[0].rect.width, doc[0].rect.height)]
out += issues if issues else ["ALL CHECKS PASSED"]
print("\n".join(out))
