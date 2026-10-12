# -*- coding: utf-8 -*-
"""01 提取版面结构并生成待译清单
用法: python 01_extract.py <源PDF> <输出目录>
产出: structure.json（坐标/字号/颜色/图片 bbox）
      blocks_final.json（清洗后的块，供合成用）
      to_translate.txt（给模型阅读的待译清单）
"""
import os, sys, json, re
import pymupdf as fitz

if len(sys.argv) < 3 or "--help" in sys.argv or "-h" in sys.argv:
    sys.exit("用法: python 01_extract.py <源PDF> <输出目录>\n"
             "产出: structure.json, blocks_final.json, to_translate.txt")

SRC, OUT = sys.argv[1], sys.argv[2]
if not os.path.isfile(SRC):
    sys.exit(f"错误: 源文件不存在: {SRC}")
os.makedirs(OUT, exist_ok=True)


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip().lower()


def area(b):
    return max(1.0, (b[2] - b[0]) * (b[3] - b[1]))


def overlap(a, b):
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    return max(0.0, x1 - x0) * max(0.0, y1 - y0)


doc = fitz.open(SRC)
pages = []
for pno in range(doc.page_count):
    page = doc[pno]
    d = page.get_text("dict")
    texts, images = [], []
    for b in d["blocks"]:
        bb = [round(x, 1) for x in b["bbox"]]
        if b["type"] == 0:
            lines = []
            for l in b["lines"]:
                spans = [{"t": s["text"], "font": s["font"], "size": round(s["size"], 2),
                          "color": s["color"], "bbox": [round(x, 1) for x in s["bbox"]]}
                         for s in l["spans"]]
                lines.append({"bbox": [round(x, 1) for x in l["bbox"]], "spans": spans})
            texts.append({"bbox": bb, "lines": lines})
        else:
            images.append({"bbox": bb, "xref": b.get("xref")})

    img_boxes = [i["bbox"] for i in images]
    for t in texts:
        a = max(1.0, area(t["bbox"]))
        t["img_overlap"] = round(max([overlap(t["bbox"], ib) for ib in img_boxes], default=0.0) / a, 3)
    pages.append({"page": pno, "rect": [round(page.rect.width, 1), round(page.rect.height, 1)],
                  "texts": texts, "images": images})

json.dump(pages, open(os.path.join(OUT, "structure.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)

# ---------- 清洗：微型字母簇合并 + 重复层去重 ----------
final, report = {}, []
for p in pages:
    pno = p["page"]
    items = []
    for idx, t in enumerate(p["texts"]):
        spans = [s for l in t["lines"] for s in l["spans"]]
        if not spans:
            continue
        txt = " ".join(s["t"] for s in spans).strip()
        if not txt:
            continue
        col = {}
        for s in spans:
            col[s["color"]] = col.get(s["color"], 0) + len(s["t"])
        votes = {"l": 0, "c": 0, "r": 0}
        for l in t["lines"]:
            lb = l["bbox"]
            lg, rg = lb[0] - t["bbox"][0], t["bbox"][2] - lb[2]
            if abs(lg - rg) <= 2.0:
                votes["c"] += 1
            elif rg < lg * 0.35:
                votes["r"] += 1
            else:
                votes["l"] += 1
        # 字号基准用「字符数加权平均」：块内常混排大标题+小正文，取 max 会严重偏大
        wsum = sum(s["size"] * len(s["t"]) for s in spans)
        wnum = sum(len(s["t"]) for s in spans)
        items.append({"id": "T%02d" % idx, "bbox": t["bbox"], "text": txt,
                      "size": round(wsum / max(1, wnum), 2),
                      "maxsize": round(max(s["size"] for s in spans), 2),
                      "color": max(col.items(), key=lambda kv: kv[1])[0],
                      "align": max(votes.items(), key=lambda kv: kv[1])[0],
                      "nlines": len(t["lines"]), "kind": "text"})

    # 链式合并：沿曲线排列的单字母（如 "Please Recycle This Magazine"）
    # 必须用 maxsize 判定（用加权 size 会改变筛选结果、打乱块顺序、导致译文错位）
    singles = [it for it in items if len(it["text"].strip()) <= 2 and it["maxsize"] < 6.0]
    if len(singles) >= 5:
        used, clusters = set(), []
        for i, a in enumerate(singles):
            if i in used:
                continue
            grp = [a]
            used.add(i)
            changed = True
            while changed:                      # 链式，非星形
                changed = False
                for j, b in enumerate(singles):
                    if j in used or abs(b["size"] - a["size"]) > 0.3:
                        continue
                    cb = ((b["bbox"][0] + b["bbox"][2]) / 2, (b["bbox"][1] + b["bbox"][3]) / 2)
                    for g in grp:
                        cg = ((g["bbox"][0] + g["bbox"][2]) / 2, (g["bbox"][1] + g["bbox"][3]) / 2)
                        if ((cg[0] - cb[0]) ** 2 + (cg[1] - cb[1]) ** 2) ** 0.5 < 6.0 * a["size"]:
                            grp.append(b); used.add(j); changed = True
                            break
            if len(grp) >= 5:
                clusters.append(grp)
        for grp in clusters:
            order = sorted(grp, key=lambda g: g["bbox"][0])
            txt = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", "".join(g["text"].strip() for g in order))
            ub = [min(g["bbox"][0] for g in grp), min(g["bbox"][1] for g in grp),
                  max(g["bbox"][2] for g in grp), max(g["bbox"][3] for g in grp)]
            for g in grp:
                items.remove(g)
            items.append({"id": "C" + grp[0]["id"], "bbox": [round(v, 1) for v in ub],
                          "text": txt.strip(),
                          "size": max(5.0, round(max(g["size"] for g in grp) * 2.2, 2)),
                          "color": grp[0]["color"], "align": "l", "nlines": 1, "kind": "cluster"})
            report.append("page %d: merged %d glyphs -> %r" % (pno, len(grp), txt.strip()))

    # 去重：文本互相包含且位置高重叠 -> 保留更长的（纯数字/符号不参与）
    drop = set()
    for i in range(len(items)):
        for j in range(i + 1, len(items)):
            a, b = items[i], items[j]
            if overlap(a["bbox"], b["bbox"]) / min(area(a["bbox"]), area(b["bbox"])) < 0.5:
                continue
            na, nb = norm(a["text"]), norm(b["text"])
            if not re.search(r"[A-Za-z一-鿿]", a["text"]) or not re.search(r"[A-Za-z一-鿿]", b["text"]):
                continue
            if na == nb or (na and na in nb) or (nb and nb in na):
                lose = a if len(nb) >= len(na) else b
                drop.add(id(lose))
                report.append("page %d: dropped duplicate %s" % (pno, lose["id"]))
    items = [it for it in items if id(it) not in drop]
    items.sort(key=lambda it: (round(it["bbox"][1], 0), it["bbox"][0]))
    final[str(pno)] = items

json.dump(final, open(os.path.join(OUT, "blocks_final.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=1)
with open(os.path.join(OUT, "to_translate.txt"), "w", encoding="utf-8") as f:
    for pno in sorted(final, key=int):
        f.write("\n===== PAGE %s =====\n" % pno)
        for i, it in enumerate(final[pno]):
            f.write("[%d] %s | %s\n" % (i, it["id"], it["text"]))

print("\n".join(report))
for pno in sorted(final, key=int):
    print("page %s: %d blocks" % (pno, len(final[pno])))
