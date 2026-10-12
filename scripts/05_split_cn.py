# -*- coding: utf-8 -*-
"""从「左原文 / 右中文」的对照版 PDF 裁出**纯中文版**。

对照版每页是原宽 ×2（左半原文、右半中文）。本脚本把右半裁出来，
得到一份**与原始杂志同尺寸**、只有中文（画作/版式原样保留）的 PDF。

用法:
    python 05_split_cn.py <对照版PDF> <输出纯中文PDF>

要点：
  - 用 `show_pdf_page(..., clip=右半)` 而不是 `set_cropbox` ——
    cropbox 只是改显示范围，页面 MediaBox 仍是双宽，某些阅读器/打印会出问题。
  - 裁完同样要 `subset_fonts()`，否则中文字体会整份嵌入（体积翻倍）。
"""
import os
import sys

import pymupdf as fitz

if len(sys.argv) < 3:
    sys.exit('用法: python 05_split_cn.py <对照版PDF> <输出纯中文PDF>')

SRC, DST = sys.argv[1], sys.argv[2]

src = fitz.open(SRC)
W = src[0].rect.width / 2.0      # 单页原宽
H = src[0].rect.height

out = fitz.open()
for pno in range(src.page_count):
    page = out.new_page(width=W, height=H)
    # 只把右半（中文侧）贴到新页的整幅
    page.show_pdf_page(fitz.Rect(0, 0, W, H), src, pno,
                       clip=fitz.Rect(W, 0, W * 2, H))

try:
    out.subset_fonts(verbose=False)
except Exception:
    pass
out.save(DST, garbage=4, deflate=True)
out.close()

print('saved: %s  (%.2f MB, %d 页, %.0f x %.0f pt)'
      % (DST, os.path.getsize(DST) / 1048576, src.page_count, W, H))
