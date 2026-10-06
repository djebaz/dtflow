"""State-matrix screenshots of real Qt widgets under the real DTFlow stylesheet (offscreen)."""

import os, sys
from pathlib import Path

ROOT = Path(os.environ.get("DTFLOW_ROOT", Path.cwd()))
sys.path.insert(0, str(ROOT / "tests"))
from support.network_guard import install

install()
os.environ["QT_QPA_PLATFORM"] = "offscreen"

from PySide6.QtCore import QPoint, QRect, QSize, Qt
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMenu,
    QMenuBar,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QTabBar,
    QTableWidget,
    QTableWidgetItem,
    QToolTip,
    QVBoxLayout,
    QWidget,
    QAbstractItemView,
    QTextBrowser,
)

from dtflow.ui.brand import connection_icon
from dtflow.ui.theme import apply_theme, load_tokens

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)
app = QApplication([])
apply_theme(app)
T = load_tokens()
BG = QColor(T["color.surface"])
CAP = QColor(T["color.secondary"])
SCALE = 2


def repolish(w):
    w.style().unpolish(w)
    w.style().polish(w)
    w.update()


def prop(w, **kw):
    for k, v in kw.items():
        w.setProperty(k, v)
    repolish(w)
    return w


class Host(QWidget):
    """Top-level host so focus/hover/active work; objectName lets scoped selectors match."""

    def __init__(self, name=None, size=(10, 10)):
        super().__init__()
        if name:
            self.setObjectName(name)
        self.setStyleSheet("")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.resize(*size)


def settle(n=1):
    for _ in range(n):
        app.processEvents()
        QTest.qWait(30)


def grab_widget(w, pad=8):
    settle()
    pm = w.grab()
    out = QPixmap(pm.width() + pad * 2, pm.height() + pad * 2)
    out.fill(BG)
    p = QPainter(out)
    p.drawPixmap(pad, pad, pm)
    p.end()
    return out


def state_cell(make, state, host_name=None, size=None):
    """Create a fresh widget in a fresh host, force `state`, return a pixmap."""
    host = Host(host_name)
    lay = QVBoxLayout(host)
    lay.setContentsMargins(8, 8, 8, 8)
    w = make()
    lay.addWidget(w, 0, Qt.AlignmentFlag.AlignLeft)
    host.setMinimumSize(0, 0)
    host.resize(size if size else host.sizeHint())
    host.show()
    host.activateWindow()
    QTest.qWaitForWindowActive(host, 500)
    settle()
    fw = QApplication.focusWidget()
    if fw is not None and "focus" not in state:
        fw.clearFocus()
        host.setFocus()
        settle()
    # reset any mouse state
    QTest.mouseMove(host, QPoint(host.width() - 2, host.height() - 2))
    settle()
    center = w.rect().center()
    if state == "hover":
        QTest.mouseMove(w, center)
        settle(2)
    elif state == "pressed":
        QTest.mouseMove(w, center)
        settle()
        QTest.mousePress(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)
        settle(2)
    elif state == "focus":
        w.setFocus(Qt.FocusReason.TabFocusReason)
        settle(2)
    elif state == "disabled":
        w.setEnabled(False)
        settle()
    elif state == "hover+checked":
        w.setChecked(True)
        QTest.mouseMove(w, center)
        settle(2)
    elif state == "focus+checked":
        w.setChecked(True)
        w.setFocus(Qt.FocusReason.TabFocusReason)
        settle(2)
    elif state == "checked":
        w.setChecked(True)
        settle()
    elif state == "pressed+checked":
        w.setChecked(True)
        QTest.mouseMove(w, center)
        settle()
        QTest.mousePress(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)
        settle(2)
    elif state == "disabled+checked":
        w.setChecked(True)
        w.setEnabled(False)
        settle()
    elif state.startswith("custom:"):
        pass
    pm = grab_widget(host, 0)
    if state == "pressed" or state == "pressed+checked":
        QTest.mouseRelease(w, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, center)
    host.hide()
    host.deleteLater()
    settle()
    return pm


def sheet(name, title, rows, col_w=None):
    """rows: list of (row label, [(state label, pixmap), ...]); compose and save a labelled sheet."""
    f = QFont("Noto Sans", 9)
    fb = QFont("Noto Sans", 11)
    fb.setBold(True)
    pad, lw = 12, 150
    cell_sizes = [(pm.width() * SCALE, pm.height() * SCALE) for _, cells in rows for _, pm in cells]
    cw = col_w or max(w for w, _ in cell_sizes) + pad
    ncol = max(len(c) for _, c in rows)
    heights = []
    for _, cells in rows:
        heights.append(max(pm.height() * SCALE for _, pm in cells) + 30 + pad)
    W = lw + ncol * cw + pad
    H = 40 + sum(heights) + pad
    img = QImage(W, H, QImage.Format.Format_ARGB32)
    img.fill(QColor(T["color.background"]))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
    p.setFont(fb)
    p.setPen(QColor(T["color.text"]))
    p.drawText(pad, 26, f"{title}   (cells shown at {SCALE}x)")
    y = 40
    for (label, cells), h in zip(rows, heights):
        p.setFont(f)
        p.setPen(QColor(T["color.text"]))
        p.drawText(QRect(pad, y, lw - pad, h), Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, label)
        for i, (sl, pm) in enumerate(cells):
            x = lw + i * cw
            p.setPen(CAP)
            p.drawText(x, y + 12, sl)
            p.drawPixmap(
                x,
                y + 18,
                pm.scaled(
                    pm.width() * SCALE,
                    pm.height() * SCALE,
                    Qt.AspectRatioMode.IgnoreAspectRatio,
                    Qt.TransformationMode.FastTransformation,
                ),
            )
        y += h
    p.end()
    path = OUT / f"{name}.png"
    img.save(str(path))
    print(path, W, H)


STATES = ["rest", "hover", "pressed", "focus", "disabled"]


def btn(text="Button", **props):
    def make():
        b = QPushButton(text)
        b.setMinimumWidth(110)
        return prop(b, **props) if props else b

    return make


def row(label, make, states, host=None):
    return (label, [(s, state_cell(make, s, host)) for s in states])


# ---------------------------------------------------------------- Buttons
def buttons():
    rows = [
        row("Neutral QPushButton", btn("Refresh"), STATES),
        row("Primary (role=primary)", btn("Generate image", role="primary"), STATES),
        row("Danger (role=danger)", btn("Remove", role="danger"), STATES),
    ]

    def nav():
        b = QPushButton("Generate")
        b.setCheckable(True)
        return prop(b, role="navigation")

    NS = [
        "rest",
        "hover",
        "pressed",
        "focus",
        "checked",
        "hover+checked",
        "pressed+checked",
        "focus+checked",
        "disabled",
        "disabled+checked",
    ]
    rows.append(row("Navigation (role=navigation, checkable)", nav, NS))
    sheet("30-buttons", "Buttons", rows)


def connection_button():
    rows = []
    for lvl in ("unknown", "ok", "warning", "error"):

        def make(lvl=lvl):
            b = QPushButton()
            b.setObjectName("connectionNavigationButton")
            b.setCheckable(True)
            b.setIcon(connection_icon(lvl))
            b.setIconSize(QSize(40, 24))
            b.setFixedSize(58, 34)
            return prop(b, state=lvl)

        cells = []
        for s in ["rest", "hover", "pressed", "focus", "checked", "disabled"]:

            def mk(lvl=lvl, s=s):
                b = make(lvl)
                if s == "focus":
                    prop(b, keyboardFocus=True)
                return b

            cells.append((s + (" (keyboardFocus)" if s == "focus" else ""), state_cell(mk, s)))
        rows.append((f"Connection button state={lvl}", cells))
    sheet("31-connection-button", "ConnectionButton (58x34)", rows)


def gallery_buttons():
    GN = "GalleryDetailPage"
    rows = []

    def mode():
        b = QPushButton("Grid")
        b.setCheckable(True)
        return prop(b, role="galleryMode")

    rows.append(
        row(
            "Gallery mode (role=galleryMode)",
            mode,
            ["rest", "hover", "pressed", "focus", "checked", "hover+checked", "disabled"],
            GN,
        )
    )

    def disc():
        b = QPushButton("Additional details")
        b.setCheckable(True)
        return prop(b, role="galleryDisclosure")

    rows.append(
        row(
            "Gallery disclosure (role=galleryDisclosure)",
            disc,
            ["rest", "hover", "pressed", "focus", "checked", "hover+checked", "pressed+checked", "disabled"],
            GN,
        )
    )

    def rnd():
        b = QPushButton("‹")
        b.setObjectName("galleryPreviousButton")
        b.setFixedSize(44, 44)
        return b

    rows.append(row("Round prev (#galleryPreviousButton)", rnd, STATES, GN))

    def thumb():
        b = QPushButton()
        b.setCheckable(True)
        b.setFixedSize(100, 70)
        return prop(b, role="galleryThumbnail")

    rows.append(
        row(
            "Filmstrip thumbnail (role=galleryThumbnail)",
            thumb,
            ["rest", "hover", "pressed", "focus", "checked", "hover+checked", "disabled"],
            GN,
        )
    )
    sheet("32-gallery-buttons", "Gallery buttons", rows)


# ---------------------------------------------------------------- Inputs
def inputs():
    rows = []

    def le(ph=None, text="", **p):
        def make():
            e = QLineEdit(text)
            e.setMinimumWidth(180)
            if ph:
                e.setPlaceholderText(ph)
            return prop(e, **p) if p else e

        return make

    rows.append(
        row("QLineEdit (value)", le(text="1988822795"), STATES + ["invalid"])
        if False
        else (
            "QLineEdit (value)",
            [(s, state_cell(le(text="1988822795"), s)) for s in STATES]
            + [("invalid", state_cell(le(text="abc", invalid=True), "rest"))],
        )
    )
    rows.append(
        (
            "QLineEdit (placeholder / read-only)",
            [
                ("placeholder", state_cell(le("Search positive prompts…"), "rest")),
                ("placeholder+hover", state_cell(le("Search positive prompts…"), "hover")),
                ("placeholder+focus", state_cell(le("Search positive prompts…"), "focus")),
                (
                    "read-only",
                    state_cell(lambda: (lambda e: (e.setReadOnly(True), e)[1])(le(text="read only")()), "rest"),
                ),
            ],
        )
    )

    def pt(**p):
        def make():
            e = QPlainTextEdit()
            e.setPlainText("A lighthouse at dawn")
            e.setFixedSize(190, 60)
            return prop(e, **p) if p else e

        return make

    rows.append(
        (
            "QPlainTextEdit",
            [(s, state_cell(pt(), s)) for s in STATES] + [("invalid", state_cell(pt(invalid=True), "rest"))],
        )
    )

    def code():
        e = QPlainTextEdit()
        e.setPlainText('{"seed": 123}')
        e.setFixedSize(190, 60)
        return prop(e, textRole="code")

    rows.append(("QPlainTextEdit textRole=code", [(s, state_cell(code, s)) for s in ["rest", "focus", "disabled"]]))
    sheet("33-text-inputs", "Text inputs", rows)

    # combo / spin
    rows = []

    def cb(**p):
        def make():
            c = QComboBox()
            c.addItems(["euler_a", "dpm++ 2m karras", "ddim"])
            c.setMinimumWidth(170)
            return prop(c, **p) if p else c

        return make

    rows.append(
        ("QComboBox", [(s, state_cell(cb(), s)) for s in STATES] + [("invalid", state_cell(cb(invalid=True), "rest"))])
    )

    def sp(cls=QSpinBox, **p):
        def make():
            s = cls()
            s.setMinimumWidth(110)
            s.setValue(20)
            return prop(s, **p) if p else s

        return make

    rows.append(
        (
            "QSpinBox",
            [(s, state_cell(sp(), s)) for s in STATES]
            + [
                ("invalid", state_cell(sp(invalid=True), "rest")),
                ("level=elevated", state_cell(sp(level="elevated"), "rest")),
            ],
        )
    )

    def ds():
        s = QDoubleSpinBox()
        s.setMinimumWidth(110)
        s.setValue(7.0)
        return s

    rows.append(("QDoubleSpinBox", [(s, state_cell(ds, s)) for s in STATES]))
    sheet("34-combo-spin", "ComboBox and SpinBox", rows)


def checks_sliders_progress():
    rows = []

    def cbx(text="Use LoRA trigger prefixes"):
        def make():
            c = QCheckBox(text)
            return c

        return make

    S = [
        "rest",
        "hover",
        "pressed",
        "focus",
        "checked",
        "hover+checked",
        "focus+checked",
        "disabled",
        "disabled+checked",
    ]
    rows.append(("QCheckBox", [(s, state_cell(cbx(), s)) for s in S]))

    def sl():
        s = QSlider(Qt.Orientation.Horizontal)
        s.setFixedWidth(150)
        s.setValue(40)
        return s

    rows.append(("QSlider horizontal", [(s, state_cell(sl, s)) for s in STATES]))

    def pb(v, fmt="%p%", rng=(0, 100)):
        def make():
            b = QProgressBar()
            b.setRange(*rng)
            b.setValue(v) if rng[1] else None
            b.setFormat(fmt)
            b.setFixedWidth(180)
            return b

        return make

    def pbusy():
        b = QProgressBar()
        b.setRange(0, 0)
        b.setFixedWidth(180)
        return b

    rows.append(
        (
            "QProgressBar",
            [
                ("0%", state_cell(pb(0), "rest")),
                ("40%", state_cell(pb(40), "rest")),
                ("100%", state_cell(pb(100), "rest")),
                ("busy (0,0)", state_cell(pbusy, "rest")),
                ("disabled 40%", state_cell(pb(40), "disabled")),
            ],
        )
    )
    sheet("35-checkbox-slider-progress", "Checkbox, Slider, ProgressBar", rows)


# ---------------------------------------------------------------- Lists / tables / tabs / scroll
def lists_tables():
    rows = []

    def lw(selected=None, **p):
        def make():
            l = QListWidget()
            l.setFixedSize(190, 110)
            for t in ["zimage_turbo_lora @1.0", "fake-soft @0.8", "portrait_enhancer_v2"]:
                l.addItem(t)
            if selected is not None:
                l.setCurrentRow(selected)
            return prop(l, **p) if p else l

        return make

    cells = [
        ("rest", state_cell(lw(), "rest")),
        ("selected row 1", state_cell(lw(1), "rest")),
        ("focus+selected", state_cell(lw(1), "focus")),
        ("disabled", state_cell(lw(1), "disabled")),
        ("invalid", state_cell(lw(invalid=True), "rest")),
    ]

    # hover over item 0: hover the viewport at item rect
    def hover_item():
        host = Host()
        lay = QVBoxLayout(host)
        l = lw()()
        lay.addWidget(l)
        host.resize(host.sizeHint())
        host.show()
        host.activateWindow()
        QTest.qWaitForWindowActive(host, 500)
        settle()
        r = l.visualItemRect(l.item(0))
        QTest.mouseMove(l.viewport(), r.center())
        settle(2)
        pm = grab_widget(host, 0)
        host.hide()
        return pm

    cells.insert(1, ("hover row 0", hover_item()))

    def empty():
        l = QListWidget()
        l.setFixedSize(190, 110)
        return l

    cells.append(("empty", state_cell(empty, "rest")))
    rows.append(("QListWidget", cells))

    def tw(sel=None, hover=False):
        def make():
            t = QTableWidget(4, 3)
            t.setFixedSize(330, 150)
            t.setHorizontalHeaderLabels(["#", "State", "Seed"])
            t.verticalHeader().hide()
            t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
            t.setAlternatingRowColors(True)
            for r in range(4):
                for c, v in enumerate([str(r + 1), ["pending", "running", "succeeded", "failed"][r], str(1000 + r)]):
                    t.setItem(r, c, QTableWidgetItem(v))
            if sel is not None:
                t.selectRow(sel)
            return t

        return make

    def hover_row():
        host = Host()
        lay = QVBoxLayout(host)
        t = tw()()
        lay.addWidget(t)
        host.resize(host.sizeHint())
        host.show()
        host.activateWindow()
        QTest.qWaitForWindowActive(host, 500)
        settle()
        r = t.visualItemRect(t.item(1, 1))
        QTest.mouseMove(t.viewport(), r.center())
        settle(2)
        pm = grab_widget(host, 0)
        host.hide()
        return pm

    rows.append(
        (
            "QTableWidget",
            [
                ("rest (alternating)", state_cell(tw(), "rest")),
                ("hover row 1", hover_row()),
                ("selected row 2", state_cell(tw(2), "rest")),
                ("focus+selected", state_cell(tw(2), "focus")),
                ("disabled", state_cell(tw(2), "disabled")),
            ],
        )
    )
    sheet("36-lists-tables", "Lists and tables", rows)


def tabs_scroll():
    rows = []

    def tabs():
        b = QTabBar()
        b.addTab("General")
        b.addTab("Advanced")
        b.addTab("Disabled")
        b.setTabEnabled(2, False)
        return b

    def hover_tab():
        host = Host()
        lay = QVBoxLayout(host)
        b = tabs()
        lay.addWidget(b)
        host.resize(host.sizeHint())
        host.show()
        host.activateWindow()
        QTest.qWaitForWindowActive(host, 500)
        settle()
        QTest.mouseMove(b, b.tabRect(1).center())
        settle(2)
        pm = grab_widget(host, 0)
        host.hide()
        return pm

    def focus_tab():
        host = Host()
        lay = QVBoxLayout(host)
        b = tabs()
        lay.addWidget(b)
        host.resize(host.sizeHint())
        host.show()
        host.activateWindow()
        QTest.qWaitForWindowActive(host, 500)
        settle()
        b.setFocus(Qt.FocusReason.TabFocusReason)
        settle(2)
        pm = grab_widget(host, 0)
        host.hide()
        return pm

    rows.append(
        (
            "QTabBar (tab 0 selected; tab 2 disabled)",
            [("rest", state_cell(tabs, "rest")), ("hover tab 1", hover_tab()), ("focus", focus_tab())],
        )
    )

    def scroller():
        a = QScrollArea()
        a.setFixedSize(170, 110)
        w = QWidget()
        l = QVBoxLayout(w)
        for i in range(12):
            l.addWidget(QLabel(f"Row {i}"))
        a.setWidget(w)
        return a

    def sb(state):
        host = Host()
        lay = QVBoxLayout(host)
        a = scroller()
        lay.addWidget(a)
        host.resize(host.sizeHint())
        host.show()
        host.activateWindow()
        QTest.qWaitForWindowActive(host, 500)
        settle()
        bar = a.verticalScrollBar()
        bar.setValue(10)
        settle()
        h = bar.mapTo(host, QPoint(bar.width() // 2, 40))
        if state == "hover":
            QTest.mouseMove(bar, QPoint(bar.width() // 2, 40))
            settle(2)
        if state == "pressed":
            QTest.mouseMove(bar, QPoint(bar.width() // 2, 40))
            settle()
            QTest.mousePress(
                bar, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(bar.width() // 2, 40)
            )
            settle(2)
        pm = grab_widget(host, 0)
        if state == "pressed":
            QTest.mouseRelease(
                bar, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(bar.width() // 2, 40)
            )
        host.hide()
        return pm

    rows.append(
        (
            "QScrollBar vertical (12px)",
            [("rest", sb("rest")), ("hover thumb", sb("hover")), ("pressed thumb", sb("pressed"))],
        )
    )
    sheet("37-tabs-scrollbar", "Tabs and scrollbars", rows)


# ---------------------------------------------------------------- Feedback / labels / chips
def labels_status():
    rows = []

    def lab(text, **p):
        def make():
            l = QLabel(text)
            return prop(l, **p)

        return make

    cells = []
    for t, p in [
        ("Eyebrow", dict(role="eyebrow")),
        ("Secondary", dict(role="secondary")),
        ("Badge", dict(role="badge")),
        ("caption", dict(textRole="caption")),
        ("value", dict(textRole="value")),
        ("supporting", dict(textRole="supporting")),
        ("section", dict(textRole="section")),
        ("identifier", dict(textRole="identifier")),
        ("panelHeading", dict(textRole="panelHeading")),
    ]:
        cells.append((list(p.values())[0], state_cell(lab(t, **p), "rest")))
    rows.append(("Label roles", cells))
    cells = []
    for st in ("ok", "warning", "error", "unknown"):
        cells.append((st, state_cell(lab(f"State {st}", state=st), "rest")))
    rows.append(("QLabel[state=…]", cells))
    cells = []
    for st in ("ok", "warning", "error"):

        def mk(st=st):
            f = QFrame()
            f.setObjectName("statusCard")
            f.setFixedSize(230, 54)
            lay = QHBoxLayout(f)
            g = QLabel("●")
            g.setObjectName("statusGlyphLabel")
            prop(g, state=st)
            s = QLabel(f"Connected ({st})")
            s.setObjectName("statusLabel")
            prop(s, state=st)
            lay.addWidget(g)
            lay.addWidget(s)
            prop(f, state=st)
            return f

        cells.append((st, state_cell(mk, "rest")))
    rows.append(("StatusCard (#statusCard[state])", cells))

    def chip():
        f = QFrame()
        prop(f, role="loraChip")
        lay = QHBoxLayout(f)
        n = QLabel("portrait_enhancer_v2")
        prop(n, textRole="loraName")
        w = QLabel("0.8")
        b = QLabel("Unknown")
        prop(b, statusBadge=True)
        lay.addWidget(n)
        lay.addWidget(w)
        lay.addWidget(b)
        return f

    rows.append(("LoRA chip (role=loraChip, statusBadge)", [("rest", state_cell(chip, "rest", "GalleryDetailPage"))]))
    sheet("38-labels-status", "Labels, status, chips", rows)


def popups():
    """Tooltip, combo popup and menu — grab the top-level popup widgets."""
    rows = []
    # combo popup
    host = Host(size=(260, 200))
    lay = QVBoxLayout(host)
    c = QComboBox()
    c.addItems(["euler_a", "dpm++ 2m karras", "ddim"])
    lay.addWidget(c)
    lay.addStretch()
    host.resize(host.sizeHint())
    host.show()
    host.activateWindow()
    QTest.qWaitForWindowActive(host, 500)
    settle()
    c.showPopup()
    settle(3)
    view = c.view().window()
    combo_pm = view.grab()
    host_pm = grab_widget(host, 0)
    c.hidePopup()
    settle()
    # tooltip
    QToolTip.showText(host.mapToGlobal(QPoint(20, 120)), "Browse committed saved outputs", host)
    settle(3)
    tips = [w for w in QApplication.topLevelWidgets() if w.metaObject().className() == "QTipLabel" and w.isVisible()]
    tip_pm = tips[0].grab() if tips else None
    QToolTip.hideText()
    settle()
    cells = [("combo (closed)", host_pm), ("combo popup", combo_pm)]
    if tip_pm:
        cells.append(("tooltip", tip_pm))
    rows.append(("Popups", cells))
    # menu
    m = QMenu()
    m.addAction("About DTFlow")
    m.addAction("Disabled item").setEnabled(False)
    m.addSeparator()
    m.addAction("Quit")
    m.popup(QPoint(40, 40))
    settle(3)
    menu_pm = m.grab()
    m.hide()
    settle()
    rows.append(("QMenu", [("menu", menu_pm)]))
    bar = QMenuBar()
    bar.addMenu("File")
    bar.addMenu("Help")
    bar.resize(220, 28)
    bar.show()
    settle()
    rows.append(("QMenuBar", [("menubar", grab_widget(bar, 0))]))
    sheet("39-popups", "Popups, menus, tooltip", rows)


if __name__ == "__main__":
    which = sys.argv[2:] or [
        "buttons",
        "connection",
        "gallery",
        "inputs",
        "checks",
        "lists",
        "tabs",
        "labels",
        "popups",
    ]
    fns = dict(
        buttons=buttons,
        connection=connection_button,
        gallery=gallery_buttons,
        inputs=inputs,
        checks=checks_sliders_progress,
        lists=lists_tables,
        tabs=tabs_scroll,
        labels=labels_status,
        popups=popups,
    )
    for k in which:
        try:
            fns[k]()
        except Exception as e:
            import traceback

            print("FAILED", k, type(e).__name__, e)
            traceback.print_exc()
