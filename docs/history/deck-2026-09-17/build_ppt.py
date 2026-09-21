"""生成组会汇报 PPT（Agent Hallucination 检测器与自进化框架，2026-09-16~17）。"""
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LABEL_POSITION
from pptx.enum.shapes import MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

W, H, M = 13.333, 7.5, 0.62
FONT = "微软雅黑"

INK = RGBColor(0x0D, 0x1B, 0x22)
PAPER = RGBColor(0xFF, 0xFF, 0xFF)
TINT = RGBColor(0xEE, 0xF3, 0xF5)
TINT2 = RGBColor(0xE2, 0xEC, 0xEF)
TEAL = RGBColor(0x0E, 0x5C, 0x6B)
TEAL_LT = RGBColor(0x4E, 0x93, 0xA0)
ACCENT = RGBColor(0xE8, 0x59, 0x0C)
TEXT = RGBColor(0x12, 0x22, 0x2B)
MUTED = RGBColor(0x6B, 0x7C, 0x87)
HAIR = RGBColor(0xD8, 0xE0, 0xE4)
WHITE = RGBColor(0xFF, 0xFF, 0xFF)

prs = Presentation()
prs.slide_width, prs.slide_height = Inches(W), Inches(H)
BLANK = prs.slide_layouts[6]
TOTAL = 13


def _face(run, name=FONT):
    run.font.name = name
    rPr = run.font._element
    for tag in ("a:ea", "a:cs"):
        el = rPr.find(qn(tag))
        if el is None:
            el = rPr.makeelement(qn(tag), {})
            rPr.append(el)
        el.set("typeface", name)


def _style(run, size, color, bold, italic=False):
    run.font.size = Pt(size)
    run.font.bold = bold
    run.font.italic = italic
    run.font.color.rgb = color
    _face(run)


def block(slide, x, y, w, h, paras, size=18, color=TEXT, bold=False,
          align=PP_ALIGN.LEFT, anchor=MSO_ANCHOR.TOP, space_after=6, spacing=1.0,
          italic=False):
    """paras: str（\\n 分段）或 [[(text, {size,color,bold,italic}), ...], ...]"""
    tb = slide.shapes.add_textbox(Inches(x), Inches(y), Inches(w), Inches(h))
    tf = tb.text_frame
    tf.word_wrap = True
    tf.margin_left = tf.margin_right = tf.margin_top = tf.margin_bottom = 0
    tf.vertical_anchor = anchor
    if isinstance(paras, str):
        paras = [[(ln, {})] for ln in paras.split("\n")]
    for i, runs in enumerate(paras):
        p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
        p.alignment = align
        p.space_after = Pt(space_after)
        p.line_spacing = spacing
        for text, o in runs:
            r = p.add_run()
            r.text = text
            _style(r, o.get("size", size), o.get("color", color), o.get("bold", bold),
                   o.get("italic", italic))
    return tb


def rect(slide, x, y, w, h, fill=None, line=None, lw=1, radius=None, shadow=False):
    shp = slide.shapes.add_shape(
        MSO_SHAPE.ROUNDED_RECTANGLE if radius else MSO_SHAPE.RECTANGLE,
        Inches(x), Inches(y), Inches(w), Inches(h))
    if radius:
        shp.adjustments[0] = radius
    if fill is None:
        shp.fill.background()
    else:
        shp.fill.solid()
        shp.fill.fore_color.rgb = fill
    if line is None:
        shp.line.fill.background()
    else:
        shp.line.color.rgb = line
        shp.line.width = Pt(lw)
    if shadow:
        spPr = shp._element.spPr
        spPr.append(spPr.makeelement(qn("a:effectLst"), {}))
        eff = spPr.find(qn("a:effectLst"))
        sh = eff.makeelement(qn("a:outerShdw"), {
            "blurRad": "60000", "dist": "20000", "dir": "5400000", "rotWithShape": "0"})
        clr = sh.makeelement(qn("a:srgbClr"), {"val": "0D1B22"})
        alpha = clr.makeelement(qn("a:alpha"), {"val": "12000"})
        clr.append(alpha); sh.append(clr); eff.append(sh)
    shp.text_frame.text = ""
    return shp


def slide(bg=PAPER, page=None, footer=True):
    s = prs.slides.add_slide(BLANK)
    if bg is not None:
        s.background.fill.solid()
        s.background.fill.fore_color.rgb = bg
    if footer and page:
        block(s, W - M - 1.4, H - 0.52, 1.4, 0.3, f"{page} / {TOTAL}",
              size=12, color=MUTED, align=PP_ALIGN.RIGHT)
        block(s, M, H - 0.52, 4.2, 0.3, "组会汇报 · 2026-09-17", size=12, color=MUTED)
    return s


def head(s, title, sub=None, dark=False):
    block(s, M, 0.44, W - 2 * M, 0.62, title, size=31, bold=True,
          color=WHITE if dark else TEAL)
    if sub:
        block(s, M, 1.06, W - 2 * M, 0.42, sub, size=15,
              color=RGBColor(0xA9, 0xBD, 0xC4) if dark else MUTED)


def stat(s, x, y, w, number, label, accent=False, nsize=52, lsize=13, label_h=0.62):
    """数字 + 标签，高度由字号推导，避免与下一项重叠。返回占用高度。"""
    nh = round(nsize * 1.38 / 72.0 + 0.06, 3)
    block(s, x, y, w, nh, number, size=nsize, bold=True,
          color=ACCENT if accent else TEAL, align=PP_ALIGN.LEFT)
    block(s, x, y + nh, w, label_h, label, size=lsize, color=MUTED, spacing=1.2)
    return nh + label_h


def row(s, y, idx, title, desc, accent=False, w=None, h=0.86, gap=0.14):
    """无容器行列表：编号 + 粗体引导 + 说明。"""
    w = w or (W - 2 * M)
    block(s, M, y + 0.04, 0.42, 0.4, idx, size=19, bold=True,
          color=ACCENT if accent else TEAL_LT)
    block(s, M + 0.46, y, w - 0.46, 0.38, title, size=18, bold=True, color=TEXT)
    block(s, M + 0.46, y + 0.42, w - 0.46, h - 0.42, desc, size=13.5, color=MUTED, spacing=1.14)
    return y + h + gap


def hairline(s, y, x=M, w=None):
    rect(s, x, y, w or (W - 2 * M), 0.012, fill=HAIR)


# ---------------------------------------------------------------- 1 封面
s = slide(bg=INK, footer=False)
rect(s, 0, 0, 0.22, H, fill=TEAL)
block(s, 1.15, 1.28, 10.6, 0.4, "组会汇报 · 2026-09-16 ~ 09-17", size=15,
      color=TEAL_LT, bold=True)
block(s, 1.15, 1.86, 11.2, 2.0,
      "Agent Hallucination：\n检测器底座与自进化框架", size=42, bold=True, color=WHITE, spacing=1.06)
block(s, 1.15, 4.02, 11.0, 0.5, "先修尺子，再长手。", size=25, bold=True, color=ACCENT)
block(s, 1.15, 4.72, 10.9, 1.3,
      "修掉 13 项污染结论的缺陷 · 校准评估读数 · 把自进化框架重构成可核验的 H_t 基座\n"
      "框架改动不改任何门限与准入规则；真实收益尚未在任何真实运行中体现",
      size=15, color=RGBColor(0xA9, 0xBD, 0xC4), spacing=1.25)

# ---------------------------------------------------------------- 2 结论先行
s = slide(page=2)
head(s, "结论先行", "本周三条主线，对应“修底座 → 校准尺子 → 重构框架”")
y = 1.72
y = row(s, y, "1", "修底座 —— 13 项缺陷修复",
        "mini 留出评估必然崩溃、DSL 零命中可机械定罪、辩护投票取首票且 quorum 无下限、冻结听证永不命中、"
        "下游故障被洗成 authoritative 终态。全部配套回归测试。", h=1.06, gap=0.18)
y = row(s, y, "2", "校准尺子 —— 让评估能读出改进",
        "gain 集补齐极性下限，两种 claim 方向都可计分；巡检对准已部署版本并保留裸底座对照，"
        "把新引入退化、常驻残余、已修复分离为可读字段。", h=1.06, gap=0.18)
y = row(s, y, "3", "重构框架 —— H_t = (G, C, T, M, R) 基座",
        "可装载、双层哈希可核验的五组件清单，叠加覆盖可读数、终态区分、记忆接入、"
        "覆盖响应策略与断点续跑。", accent=True, h=1.06)
rect(s, 0, 5.86, W, 1.06, fill=TINT)
block(s, M, 6.12, W - 2 * M, 0.5,
      [[("边界：", {"bold": True, "color": TEXT}), (
          "以上都是结构与设施改动，不改变任何门槛值、语义法则或准入规则；"
          "真实收益是否成立，只能由重跑真实实验回答。", {})]],
      size=15, color=MUTED)

# ---------------------------------------------------------------- 3 真实实验
s = slide(page=3)
head(s, "真实实验：第 1 轮闭环，准入 0",
     "首次用真实 GLM-5.3-flash 跑通 LLM proposer → detector 配对评估 → 四门准入（开发 30+10 协议）")
sx, sw = M, 2.95
for i, (n, lab, acc) in enumerate([
        ("1", "个稳定失败族\n2 例同机制，诊断完整", False),
        ("3", "个合法候选\n结构门与无害门全部通过", False),
        ("0", "真实准入\n行为门/边际门全部拒绝", True),
        ("第 2 轮", "被中断未完成\n无 gate 结果", False)]):
    stat(s, sx + i * sw, 1.78, sw - 0.2, n, lab, accent=acc,
         nsize=52 if i < 3 else 34)
hairline(s, 3.58, x=M, w=W - 2 * M)
y = 3.74
y = row(s, y, "·", "门限未放宽，0 准入是门限拒绝的结果",
        "两个候选在 sentinel 上各出现一次局部改善，但没有候选满足行为方向与 gain 收益要求 → 不是“没有产出候选”，是候选没过门。", h=0.78)
y = row(s, y, "·", "缺陷注入审计通过，账本有效",
        "2/2 故意注入的缺陷候选被正确拒绝（结构门拦下泄漏案例 ID 的候选）；verify-ledger valid，12 条记录，哈希链完整。", h=0.78)
rect(s, 0, 6.34, W, 0.62, fill=TINT)
block(s, M, 6.48, W - 2 * M, 0.4,
      [[("证据边界：", {"bold": True, "color": ACCENT}), (
          "“收益未成立”只对本次轮次成立；第 2 轮未完成，不能据此推断框架在真实数据上永无收益。", {})]],
      size=14.5, color=MUTED)

# ---------------------------------------------------------------- 4 候选为何被拒
s = slide(page=4)
head(s, "三个候选为何全部被拒", "行为门看“行为是否与声明一致”，边际门看“配对评估是否净增正确案例”")
y = 1.62
for cid, decl, gate, why in [
    ("IV-1-001", "recall_trigger：把“有交付结果但工具输出为 None”的 judge 负例送进召回网",
     "行为门", "声称会触发召回，实测 recall_triggered_cases = 0，行为与声明不符"),
    ("IV-1-002", "recall_trigger：把“交付结果无成功工具观测锚定”的 judge 负例送进召回网",
     "边际门", "行为门通过，sentinel 上确实修好 1 例漏报，但 gain 集净增为 0（marginal_cases = 0 < 1）"),
    ("IV-1-003", "text_boundary：让因果升级复核未锚定/失败的交付",
     "行为门", "声明要提升召回，实测无 TP 增益，而是在 sentinel 移除了 1 个 FP，方向不符")]:
    block(s, M, y, 1.35, 0.34, cid, size=17, bold=True, color=TEAL)
    block(s, M + 1.42, y, 4.5, 0.62, decl, size=13, color=MUTED, spacing=1.1)
    block(s, M + 6.15, y, 0.95, 0.34, gate, size=15, bold=True, color=ACCENT)
    block(s, M + 7.2, y, W - 2 * M - 7.2, 0.72, why, size=13.5, color=TEXT, spacing=1.12)
    y += 1.02
    if cid != "IV-1-003":
        hairline(s, y - 0.16, x=M, w=W - 2 * M)
rect(s, M, 4.82, W - 2 * M, 1.4, fill=TINT)
block(s, M + 0.34, 5.0, W - 2 * M - 0.68, 0.4,
      "关键在 IV-1-002：真的做出了有效修改，读数为 0", size=19, bold=True, color=TEAL)
block(s, M + 0.34, 5.46, W - 2 * M - 0.68, 0.66,
      "它通过了行为门、真实触发一次、并在 sentinel 修好 1 个漏报，却被边际门以 marginal_cases = 0 < 1 拒绝 —— "
      "原因是 gain 集 G 里根本没有 FN，召回类候选无处得分。这把问题从“门太严”指向了“尺子读不出”。",
      size=13, color=MUTED, spacing=1.16)

# ---------------------------------------------------------------- 5 复核更正
s = slide(page=5)
head(s, "复核更正：patrol 口径与 gain 集构成",
     "深度复核后，早期记录的混淆矩阵与根因归因都需要更正")
block(s, M, 1.66, 6.4, 0.34, "patrol（裸底座，20 例）标准口径", size=16, bold=True, color=TEXT)
for i, (n, lab, acc) in enumerate([("12", "正确", False), ("4", "FN 漏报", False),
                                   ("1", "FP 误报", False), ("3", "U 未决", False)]):
    stat(s, M + i * 1.62, 2.08, 1.55, n, lab, accent=acc, nsize=44, lsize=12.5)
block(s, M, 3.74, 6.4, 0.72,
      "早期记录的“5 FN、3 FP”把 3 个未决/降级案例按 gold 方向混入了错误统计，"
      "不能作为标准混淆矩阵使用。", size=13.5, color=MUTED, spacing=1.14)
hairline(s, 4.58, x=M, w=6.4)
block(s, M, 4.78, 6.4, 0.34, "失败族与逐候选实测", size=16, bold=True, color=TEXT)
block(s, M, 5.22, 6.4, 0.9,
      [[("1 个稳定失败族", {"bold": True, "color": TEXT}), ("（BFCL/017、OpenDeepSearch/086 两例同机制）\n", {})],
       [("IV-1-002", {"bold": True}), (" 在 sentinel 修好 1 例 FN；", {}),
        ("IV-1-003", {"bold": True}), (" 修好 1 例 FP；两者在 G 上都未形成收益。", {})]],
      size=13.5, color=MUTED, spacing=1.16)

rect(s, 7.3, 1.66, W - M - 7.3, 4.0, fill=INK)
block(s, 7.62, 1.94, W - M - 7.94, 0.4, "gain 集 G 的构成", size=16, bold=True, color=TEAL_LT)
block(s, 7.62, 2.44, W - M - 7.94, 0.7, "2 TP  ·  1 TN  ·  1 FP  ·  0 FN",
      size=21, bold=True, color=WHITE)
block(s, 7.62, 3.28, W - M - 7.94, 1.0,
      "G 里没有 FN，而 marginal_cases 只统计全对案例的净增 —— 召回类候选在这里天然无法计分。",
      size=14, color=RGBColor(0xA9, 0xBD, 0xC4), spacing=1.2)
block(s, 7.62, 4.42, W - M - 7.94, 0.34, "引擎其实已算出该极性机会为 0，", size=13.5,
      color=RGBColor(0xA9, 0xBD, 0xC4))
block(s, 7.62, 4.76, W - M - 7.94, 0.7,
      [[("但这个信号只写进反馈，不改变本轮结论。", {"color": ACCENT, "bold": True})]],
      size=14.5)

# ---------------------------------------------------------------- 6 真实服务与数据
s = slide(page=6)
head(s, "真实服务与数据完整性", "当前配置 GLM-5.3-flash，合成两例与真实数据集小规模路径检查")
for i, (n, lab, acc) in enumerate([
        ("6/6", "真实数据集终态一致\n6 例 × 2 次重复，0 unresolved", False),
        ("12/12", "authoritative\n判定终态均为权威结果", False),
        ("695", "原始文件 SHA-256 一致\n693 个案例，未改动", False)]):
    stat(s, M + i * 4.05, 1.8, 3.9, n, lab, accent=acc, nsize=46, label_h=0.66)
hairline(s, 3.72, x=M, w=W - 2 * M)
block(s, M, 3.92, W - 2 * M, 0.36, "本周较弱的一环：定位", size=17, bold=True, color=TEXT)
block(s, M, 4.36, W - 2 * M, 0.9,
      "判定 6/6 一致，但含定位步的一致性仅 5/6，正例定位命中约 1/6。"
      "召回升级分支已用单票强制触发证明路径连通，但该条件改变了判定，"
      "正常配置下的触发率与真实增益仍未验证，需要单独度量。",
      size=14, color=MUTED, spacing=1.18)
rect(s, 0, 5.62, W, 1.3, fill=TINT)
block(s, M, 5.86, W - 2 * M, 0.9,
      [[("不能宣称的：", {"bold": True, "color": ACCENT}), (
          "合成两例与 6 例开发复测都不代表复杂 Agent 轨迹的准确率或泛化；"
          "案例已被开发使用，默认没有干净的独立终审集。", {})]],
      size=14.5, color=MUTED, spacing=1.18)

# ---------------------------------------------------------------- 7 代码 Review
s = slide(page=7)
head(s, "代码 Review：13 项缺陷修复",
     "全部配套回归测试；测试 220 → 244 passed，scripts/verify.py 七项校验全 PASS")
y = 1.68
for i, (t, d) in enumerate([
    ("mini 留出评估必然崩溃", "gold 未补齐，唯一样本外读数整体失效"),
    ("DSL 可用零命中触发机械定罪", "无证据也能判成幻觉，污染所有下游结论"),
    ("辩护投票取首票、quorum 无下限", "同一票集可能得到相反终态；单票即成多数"),
    ("冻结听证永不命中", "比较未投影字段，所谓“冻结”实为实时重跑"),
    ("下游故障被洗成 authoritative", "模型错误与服务故障无法区分，错误终态被当成权威")], 1):
    block(s, M, y, 0.5, 0.36, f"{i:02d}", size=16, bold=True, color=TEAL_LT)
    block(s, M + 0.62, y, 4.75, 0.6, t, size=16.5, bold=True, color=TEXT, spacing=1.08)
    block(s, M + 5.6, y + 0.02, W - 2 * M - 5.6, 0.6, d, size=13.5, color=MUTED, spacing=1.12)
    y += 0.86
    hairline(s, y - 0.2, x=M, w=W - 2 * M)
rect(s, M, 6.02, W - 2 * M, 0.86, fill=TINT)
block(s, M + 0.32, 6.2, W - 2 * M - 0.64, 0.55,
      [[("中等项 8 项", {"bold": True, "color": TEXT}), (
          "：registry 哈希可缺省、judge basis 类型可中断整批、upgrade.pre 钩子双执行、"
          "guard 资格探测带副作用、boundary 极性空约束、无害门默认只保护 2 例、缺陷审计可空过、正式路径无法续跑。", {})]],
      size=13.5, color=MUTED, spacing=1.14)

# ---------------------------------------------------------------- 8 评估侧优化
s = slide(page=8)
head(s, "评估侧优化：让尺子能读出改进",
     "真实第 1 轮暴露的前置问题不是“手不够”，而是“做出了有效修改却读数为 0”")
block(s, M, 1.66, 5.7, 0.34, "① 边际集 G 加极性下限", size=17, bold=True, color=TEAL)
block(s, M, 2.08, 5.7, 1.5,
      "在来源配额之上保证每极性最少案例数（开发各 1，正式 max(1, n//4)），"
      "使两种 claim 方向都可被计分；下限不可满足时直接报错，而不是静默弱化。\n"
      "split_manifest_report 新增 gain_polarity / sentinel_polarity，使构成可读。",
      size=13.5, color=MUTED, spacing=1.18)
rect(s, M, 3.66, 5.7, 0.62, fill=TINT)
block(s, M + 0.26, 3.8, 5.2, 0.4,
      "这是必要不充分条件：G 里有该极性案例 ≠ 该案例是修复机会。", size=13, color=MUTED)

block(s, M, 4.54, 5.7, 0.34, "② 巡检对准已部署版本，保留裸底座对照",
      size=17, bold=True, color=TEAL)
block(s, M, 4.96, 5.7, 1.3,
      "新增配置 patrol_registry：巡检臂测已部署版本（H_t），参照臂测裸底座。"
      "registry 为空时两臂是同一次评估，冷启动成本不变；部署后才多出第二次 sweep。"
      "保留裸底座是必需的 —— 没有参照，三类失败都无法计算。",
      size=13.5, color=MUTED, spacing=1.18)

rect(s, 6.85, 1.66, W - M - 6.85, 4.6, fill=TINT)
block(s, 7.15, 1.86, W - M - 7.15, 0.34, "巡检双臂把失败分成三类", size=14.5,
      bold=True, color=TEXT)
for i, (lab, d, c) in enumerate([
        ("new_failures", "准入引入的退化", ACCENT),
        ("persisting_failures", "常驻残余（裸底座也错）", TEXT),
        ("resolved_by_active", "已被活跃干预修复", TEAL)]):
    yy = 2.4 + i * 1.0
    rect(s, 7.15, yy, 0.16, 0.16, fill=c)
    block(s, 7.5, yy - 0.08, 5.1, 0.34, lab, size=15.5, bold=True, color=c)
    block(s, 7.5, yy + 0.3, 5.1, 0.34, d, size=13.5, color=MUTED)
rect(s, 7.15, 5.48, 0.16, 0.16, fill=MUTED)
block(s, 7.5, 5.4, 5.1, 0.7,
      "原 VaG 保证从“断言”变为可核对数据；冷启动时两臂相同，不增加成本。",
      size=13, color=MUTED, spacing=1.16)

# ---------------------------------------------------------------- 9 H_t 基座
s = slide(page=9)
head(s, "框架重构：H_t = (G, C, T, M, R) 基座",
     "把此前只存在于文档里的“一代系统”做成可装载、双层哈希可核验的载体 —— 结构改动，不动门槛值")
for i, (k, n, d) in enumerate([
        ("G", "评估集", "阶段入口逐个导入\n失败即构建失败"),
        ("C", "上下文预算", "视图预算与渲染器\n从 packet 实测读取"),
        ("T", "活跃干预", "来自活跃 registry"),
        ("M", "经验存储", "三类经验\n有界检索"),
        ("R", "策略配置", "来自配置\n配置变化只使 R 变")]):
    x = M + i * 2.44
    rect(s, x, 1.78, 2.24, 1.72, fill=TINT)
    block(s, x + 0.24, 1.92, 1.8, 0.6, k, size=30, bold=True, color=TEAL)
    block(s, x + 0.24, 2.5, 1.8, 0.3, n, size=14, bold=True, color=TEXT)
    block(s, x + 0.24, 2.84, 1.86, 0.6, d, size=11.5, color=MUTED, spacing=1.1)
block(s, M, 3.68, W - 2 * M, 0.4,
      "组件由代码推导，不是手写描述；装载时重新推导摘要 —— 被编辑过的清单会被发现。",
      size=13.5, color=MUTED, italic=True)
hairline(s, 4.24, x=M, w=W - 2 * M)
block(s, M, 4.44, W - 2 * M, 0.34, "在其上叠加三项能力（仍不改门限）",
      size=17, bold=True, color=TEAL)
y = 4.92
for t, d in [
    ("覆盖可读数", "cycle 报告新增 instrument 块，按轮报告每极性可修复案例数与不可测量极性"),
    ("终态可辨", "admitted / rejected / not_evaluated / unresolved ——“未测量”不再等于“已证伪”"),
    ("记忆接入草拟", "已准入程序性经验与开放缺口进入 proposer 提示，账本可核对“系统用上了自己的经验”")]:
    block(s, M, y, 1.85, 0.3, t, size=14.5, bold=True, color=TEAL_LT)
    block(s, M + 1.95, y, W - 2 * M - 1.95, 0.3, d, size=13, color=MUTED)
    y += 0.46
block(s, M, y + 0.06, W - 2 * M, 0.4,
      [[("边界：", {"bold": True, "color": ACCENT}), (
          "记忆“被供给”≠“改善了草拟质量”，后者需带/不带记忆的对照实验；新增 harness.py / gaps.py / memory.py / contracts.py。", {})]],
      size=12.5, color=MUTED)

# ---------------------------------------------------------------- 10 覆盖响应 + 断点续跑
s = slide(page=10)
head(s, "覆盖响应与断点续跑", "两项让框架“产生动作、可恢复”的改动；测试 308 → 312 passed")
block(s, M, 1.7, 5.7, 0.34, "覆盖响应 coverage_response", size=17, bold=True, color=TEAL)
y = 2.16
for mode, out, acc in [("strict（默认）", "not_evaluated · 无响应，行为逐字同改动前", False),
                       ("replacement", "admitted · 换替代集后确实修好了案例", True),
                       ("replacement_or_skip", "skipped_unmeasured · 无匹配集则跳过", False)]:
    block(s, M, y, 2.05, 0.3, mode, size=13.5, bold=True, color=TEXT)
    block(s, M + 2.15, y, 3.55, 0.62, out, size=13, color=ACCENT if acc else MUTED, spacing=1.12)
    y += 0.7
block(s, M, 4.32, 5.7, 1.0,
      "三种模式在同一合成场景下得到三种不同终态 —— 替代集能够改变准入结果，"
      "因此默认关闭、按方向开启，并把动作写入 instrument.coverage_responses。",
      size=13.5, color=MUTED, spacing=1.18)
rect(s, M, 5.44, 5.7, 0.8, fill=TINT)
block(s, M + 0.26, 5.58, 5.2, 0.55,
      "边界：替代集目前只驻内存，尚未持久化、也未用于真实实验；持久化需与主 gain 集同等的 gold/协议/暴露保证。",
      size=12.5, color=MUTED, spacing=1.14)

block(s, 6.85, 1.7, W - M - 6.85, 0.34, "断点续跑：第 2 轮作废的根因", size=17, bold=True, color=TEAL)
y = 2.16
for t, d in [("根因不是“中断”，是契约设计", "检查点指纹包含 registry 哈希，而该轮已提交自己的准入 → 哈希改变 → 续跑永远拒绝"),
             ("修复：契约记录本轮起始 registry", "续跑发现 live registry 已含本轮准入时，先回退到起始状态再评估，避免门控拿候选与自己比较"),
             ("回退动作写入账本", "resume_registry_restored，快照链保留，那次准入仍可恢复；同一轮只产生一次 admitted 与一次 registry_written"),
             ("实测", "“准入已提交、写报告前中断”场景修复前永久失败，修复后同一账本续跑成功且不重复准入")]:
    block(s, 6.85, y, W - M - 6.85, 0.3, t, size=13.5, bold=True, color=TEXT)
    block(s, 6.85, y + 0.3, W - M - 6.85, 0.5, d, size=12.5, color=MUTED, spacing=1.12)
    y += 0.92
rect(s, 6.85, 5.9, W - M - 6.85, 0.78, fill=TINT)
block(s, 7.11, 6.04, W - M - 7.11, 0.55,
      "诚实边界：提案未落盘就中断仍拒绝重抽（等于在同一批已固定案例上追加搜索），错误信息会指向换新 work_root。",
      size=12, color=MUTED, spacing=1.12)

# ---------------------------------------------------------------- 11 关键数字
s = slide(page=11)
head(s, "关键数字", "测试规模的增长：220 → 244 → 251 → 292 → 301 → 308 → 312 passed，全部 0 failed / 0 skipped")
data = CategoryChartData()
data.categories = ["缺陷\n修复后", "+评估\n校准", "+H_t\n基座", "+覆盖\n读数", "+覆盖\n响应", "+断点\n续跑"]
data.add_series("测试通过数", (244, 251, 292, 301, 308, 312))
gf = s.shapes.add_chart(XL_CHART_TYPE.COLUMN_CLUSTERED, Inches(M), Inches(1.86),
                        Inches(7.5), Inches(4.0), data)
ch = gf.chart
ch.has_legend = False
ch.font.size = Pt(11.5)
ch.font.name = FONT
pl = ch.plots[0]
pl.gap_width = 70
pl.has_data_labels = True
dl = pl.data_labels
dl.font.size = Pt(12)
dl.font.bold = True
dl.font.color.rgb = TEAL
dl.position = XL_LABEL_POSITION.OUTSIDE_END
ser = pl.series[0]
ser.format.fill.solid()
ser.format.fill.fore_color.rgb = TEAL_LT
ser.points[5].format.fill.solid()
ser.points[5].format.fill.fore_color.rgb = ACCENT
va = ch.value_axis
va.maximum_scale, va.minimum_scale = 340.0, 200.0
va.has_major_gridlines = True
va.major_gridlines.format.line.color.rgb = HAIR
va.major_gridlines.format.line.width = Pt(0.5)
va.tick_labels.font.size = Pt(11)
va.tick_labels.font.color.rgb = MUTED
ca = ch.category_axis
ca.tick_labels.font.size = Pt(11)
ca.tick_labels.font.color.rgb = MUTED
ca.format.line.color.rgb = HAIR
sx = 8.45
for i, (n, lab, acc) in enumerate([
        ("九项", "离线验证全 PASS · scripts/verify.py", False),
        ("13 项", "缺陷修复，全部配回归测试", False),
        ("0", "真实准入（第 1 轮，门限未放宽）", True),
        ("6/6", "真实路径终态一致（6 例 × 2 次）", False)]):
    stat(s, sx, 1.9 + i * 1.22, W - M - sx, n, lab, accent=acc,
         nsize=36, lsize=12.5, label_h=0.44)

# ---------------------------------------------------------------- 12 下一步
s = slide(page=12)
head(s, "下一步", "框架层已全部就位，但这些改动尚未在任何真实运行中发挥作用")
rect(s, M, 1.66, W - 2 * M, 1.5, fill=INK)
block(s, M + 0.36, 1.84, W - 2 * M - 0.72, 0.36, "最高优先级", size=13, bold=True, color=ACCENT)
block(s, M + 0.36, 2.18, W - 2 * M - 0.72, 0.4,
      "用现有 30+10 开发协议重跑真实两轮进化", size=21, bold=True, color=WHITE)
block(s, M + 0.36, 2.66, W - 2 * M - 0.72, 0.4,
      "重跑是唯一能判断新框架价值的方法；不放宽门限换取准入。",
      size=14, color=RGBColor(0xA9, 0xBD, 0xC4))
y = 3.44
for i, (t, d) in enumerate([
    ("先做小规模配对诊断", "验证原失败族的召回触发、听证可达性与指控一致性，再冻结新协议"),
    ("冻结新的评估协议", "必须具备目标错误机会并按机制/极性分层，保留独立验证集"),
    ("消融证明机制贡献", "裸底座 / 固定干预 / 完整进化，覆盖带不带记忆、开不开覆盖响应"),
    ("最小 improver", "读源码、局部执行、形成可装载改动 —— 前置条件已完成，排最后")], 1):
    block(s, M, y, 0.5, 0.32, f"{i}", size=15, bold=True, color=TEAL_LT)
    block(s, M + 0.56, y, 3.5, 0.32, t, size=16, bold=True, color=TEXT)
    block(s, M + 4.2, y + 0.02, W - 2 * M - 4.2, 0.4, d, size=13.5, color=MUTED)
    y += 0.72

# ---------------------------------------------------------------- 13 收尾
s = slide(bg=INK, footer=False)
rect(s, 0, 0, 0.22, H, fill=TEAL)
block(s, 1.15, 2.42, 11.0, 0.8, "先修尺子，再长手。", size=40, bold=True, color=WHITE)
block(s, 1.15, 3.4, 11.0, 0.5,
      "框架已就位，真实收益待重跑验证。", size=21, color=ACCENT, bold=True)
block(s, 1.15, 4.16, 11.0, 0.8,
      "这一周没有产生“正收益”的结论，但把“为什么测不出收益”变成了可核对的事实。\n欢迎讨论：评估协议的设计与下一步的优先级。",
      size=15, color=RGBColor(0xA9, 0xBD, 0xC4), spacing=1.3)

out = r"E:\agent hallucination\agent hallucination\docs\group-meeting-2026-09-17.pptx"
prs.save(out)
print("saved:", out, "slides:", len(prs.slides.__iter__.__self__._sldIdLst))
