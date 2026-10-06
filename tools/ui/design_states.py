"""Capture real exported web components in Qt-style state matrices (DPR 1, enlarged 2x)."""

import json
import os

from design_capture import capture_session, manifest, parser, ready, vendor_sources

BASE = ["rest", "hover", "pressed", "focus", "disabled"]
CHECK = BASE + ["checked", "hover+checked", "pressed+checked", "focus+checked", "disabled+checked"]


def specs():
    """Export APIs only; no copied component styles or invented unsupported states."""

    def row(label, expression, states=BASE, target="button,input,textarea", width=180, unsupported=None):
        return {
            "label": label,
            "expression": expression,
            "states": states,
            "target": target,
            "width": width,
            "unsupported": unsupported or {},
        }

    def button(text, variant):
        return f'e(D.Button,{{...p,variant:"{variant}",style:{{minWidth:110}}}},"{text}")'

    rows = {
        "buttons": (
            "30-buttons",
            [
                row("Neutral QPushButton", button("Refresh", "neutral")),
                row("Primary (role=primary)", button("Generate image", "primary")),
                row("Danger (role=danger)", button("Remove", "danger")),
                row("Navigation (role=navigation)", button("Generate", "navigation"), CHECK),
            ],
        ),
        "connection": (
            "31-connection-button",
            [
                row(
                    f"Connection button state={state}",
                    f'e(D.ConnectionButton,{{...p,state:"{state}"}})',
                    BASE + ["checked"],
                )
                for state in ["unknown", "ok", "warning", "error"]
            ],
        ),
        "gallery": (
            "32-gallery-buttons",
            [
                row("Gallery mode", button("Grid", "mode"), CHECK),
                row("Gallery disclosure", button("Additional details", "disclosure"), CHECK),
                row(
                    "Round prev",
                    'e(D.RoundButton,{direction:"prev",...p})',
                    BASE,
                    width=60,
                    unsupported={"disabled": "RoundButton has no disabled prop"},
                ),
                row(
                    "Filmstrip thumbnail",
                    'e(D.Thumbnail,{label:"",selected:p.checked})',
                    CHECK,
                    width=100,
                    unsupported={
                        "disabled": "Thumbnail has no disabled prop",
                        "disabled+checked": "Thumbnail has no disabled prop",
                    },
                ),
            ],
        ),
        "inputs": (
            "33-text-inputs",
            [
                row("QLineEdit (value)", 'e(D.TextField,{...p,defaultValue:"1988822795"})', BASE + ["invalid"]),
                row(
                    "QLineEdit (placeholder)",
                    'e(D.TextField,{...p,placeholder:"Search positive prompts…"})',
                    ["rest", "hover", "focus"],
                ),
                row("QLineEdit read-only", 'e(D.TextField,{defaultValue:"read only",readOnly:true})', ["rest"]),
                row(
                    "QPlainTextEdit",
                    'e(D.TextArea,{...p,defaultValue:"A lighthouse at dawn",style:{width:190,height:60}})',
                    BASE + ["invalid"],
                    width=190,
                ),
                row(
                    "QPlainTextEdit textRole=code",
                    "e(D.TextArea,{...p,defaultValue:'{\"seed\":123}'})",
                    ["rest", "focus", "disabled"],
                    unsupported={s: "TextArea has no code text-role mapping" for s in ["rest", "focus", "disabled"]},
                ),
            ],
        ),
        "checks": (
            "35-checkbox-slider-progress",
            [
                row(
                    "QCheckBox",
                    'e(D.Checkbox,{...p,label:"Use LoRA trigger prefixes",onChange:()=>{}})',
                    CHECK,
                    width=220,
                ),
                row("QSlider horizontal", "e(D.Slider,{...p,defaultValue:40,style:{width:150}})"),
                row(
                    "QProgressBar",
                    'e(D.ProgressBar,{value:Number(state)||40,max:100,label:state+"%",style:{width:180}})',
                    ["0", "40", "100", "busy", "disabled"],
                    target=".dt-progress",
                    unsupported={"busy": "No indeterminate API", "disabled": "No semantic disabled API"},
                ),
            ],
        ),
        "lists": (
            "36-lists-tables",
            [
                row(
                    "QListWidget",
                    'e(D.ListBox,{minHeight:110},...["zimage_turbo_lora @1.0","fake-soft @0.8","portrait_enhancer_v2"].map((v,i)=>e(D.ListItem,{selected:i===1&&state.includes("selected"),tabIndex:0},v)))',
                    ["rest", "hover", "selected", "focus+selected", "disabled", "invalid"],
                    target='[role="option"]',
                    width=190,
                    unsupported={"disabled": "ListBox has no disabled API", "invalid": "ListBox has no invalid API"},
                ),
                row(
                    "QTableWidget",
                    'e(D.DataTable,{columns:[{key:"n",label:"#"},{key:"s",label:"State"},{key:"seed",label:"Seed"}],rows:["pending","running","succeeded","failed"].map((s,n)=>({n:n+1,s,seed:1000+n})),selectedIndex:state.includes("selected")?2:undefined})',
                    ["rest", "hover", "selected", "focus+selected", "disabled"],
                    target="tbody tr",
                    width=330,
                    unsupported={
                        "disabled": "DataTable has no disabled API",
                        "focus+selected": "Table rows have no keyboard focus target",
                    },
                ),
            ],
        ),
        "tabs": (
            "37-tabs-scrollbar",
            [
                row(
                    "QTabBar",
                    'e(D.Tabs,{tabs:["General","Advanced","Disabled"],value:"General"})',
                    ["rest", "hover", "focus", "disabled tab"],
                    target='[role="tab"]',
                    width=270,
                    unsupported={"disabled tab": "Tabs has no per-tab disabled API"},
                ),
                row(
                    "QScrollBar vertical",
                    'e("div",{style:{width:170,height:110,overflowY:"scroll"}},...Array.from({length:12},(_,i)=>e(D.Label,{style:{display:"block"}},"Row "+i)))',
                    ["rest", "hover thumb", "pressed thumb"],
                    target="div",
                    unsupported={
                        "hover thumb": "Native browser scrollbar state cannot be asserted through a DOM locator",
                        "pressed thumb": "Native browser scrollbar state cannot be asserted through a DOM locator",
                    },
                ),
            ],
        ),
        "labels": (
            "38-labels-status",
            [
                *[
                    row(role, f'e(D.Label,{{textRole:"{role}"}},"{role}")', ["rest"], target="span")
                    for role in ["eyebrow", "caption", "value", "supporting", "section", "identifier", "panelHeading"]
                ],
                *[
                    row(
                        f"Status {state}",
                        f'e(D.StatusCard,{{state:"{state}",heading:"Connected ({state})"}})',
                        ["rest"],
                        target=".dt-status",
                        width=230,
                    )
                    for state in ["ok", "warning", "error"]
                ],
                row(
                    "LoRA chip",
                    'e(D.LoraChip,{name:"portrait_enhancer_v2",weight:"0.8"})',
                    ["rest"],
                    target=".dt-chip",
                    width=270,
                ),
            ],
        ),
        "popups": (
            "39-popups",
            [
                row(
                    "Combo popup",
                    'e(D.ComboBox,{options:["euler_a","dpm++ 2m karras","ddim"]})',
                    ["rest", "open"],
                    width=220,
                ),
                row(
                    "Tooltip",
                    'e(D.Tooltip,{text:"Browse committed saved outputs"},e(D.Button,null,"Browse"))',
                    ["hover"],
                    width=280,
                ),
                row(
                    "QMenu / QMenuBar",
                    'e(D.MenuBar,{menus:[{label:"Help",items:["About DTFlow","Quit"]}]})',
                    ["rest", "open", "disabled item"],
                    width=220,
                    unsupported={"disabled item": "MenuBar items are strings with no disabled-item API"},
                ),
            ],
        ),
    }
    rows["inputs"][1].extend(
        [
            row("QComboBox", 'e(D.ComboBox,{...p,options:["euler_a","dpm++ 2m karras","ddim"]})', BASE + ["invalid"]),
            row(
                "QSpinBox",
                "e(D.SpinBox,{...p,defaultValue:20})",
                BASE + ["invalid", "elevated"],
                unsupported={
                    "elevated": "SpinBox has no elevated-level mapping",
                    "invalid": "SpinBox has no invalid-state mapping",
                },
            ),
            row("QDoubleSpinBox", "e(D.SpinBox,{...p,defaultValue:7,step:0.01})"),
        ]
    )
    return rows


def html(base, vendors, expression, state, width):
    scripts = "".join(f'<script src="{url}"></script>' for url in vendors if "babel" not in url)
    props = {"disabled": "disabled" in state, "checked": "checked" in state, "invalid": state == "invalid"}
    # The host fixes specimen bounds, not the component's styling/state appearance.
    return f'''<!doctype html><link rel="stylesheet" href="{base}/styles.css">
{scripts}<script src="{base}/_ds_bundle.js"></script>
<body style="overflow:hidden"><div id="cell" style="display:inline-block;padding:16px;min-width:{width + 32}px;min-height:90px;position:relative"><div id="specimen" style="width:{width}px"></div></div>
<script>const D=window[Object.keys(window).find(k=>k.startsWith('DTFlowDesignSystem_'))];
const e=React.createElement,p={json.dumps(props)},state={json.dumps(state)};
ReactDOM.createRoot(document.getElementById('specimen')).render({expression});</script>'''


def observe(target):
    return target.evaluate("""el => {const s=getComputedStyle(el),r=el.getBoundingClientRect();return {
      bounds:{x:r.x,y:r.y,width:r.width,height:r.height},
      styles:Object.fromEntries(['backgroundColor','color','borderColor','borderRadius','fontFamily','fontSize','fontWeight','padding'].map(k=>[k,s[k]])),
      hover:el.matches(':hover'),active:el.matches(':active'),focus:el.matches(':focus'),
      disabled:el.matches(':disabled'),checked:el.checked??el.getAttribute('aria-pressed'),
      classes:el.className};}""")


def compose(path, rows):
    from PySide6.QtCore import QRect, Qt
    from PySide6.QtGui import QColor, QFont, QImage, QPainter

    loaded = [(label, [(state, QImage.fromData(data)) for state, data in cells]) for label, cells in rows if cells]
    if not loaded:
        return
    cw = max(image.width() * 2 for _, cells in loaded for _, image in cells) + 12
    heights = [max(image.height() * 2 for _, image in cells) + 42 for _, cells in loaded]
    image = QImage(
        150 + max(len(cells) for _, cells in loaded) * cw + 12, 52 + sum(heights), QImage.Format.Format_ARGB32
    )
    image.fill(QColor("#1f1f1f"))
    painter = QPainter(image)
    painter.setPen(QColor("#e6e6e6"))
    painter.setFont(QFont("Segoe UI", 9))
    painter.drawText(12, 26, path.stem + " — web components, cells enlarged 2x")
    y = 40
    for (label, cells), height in zip(loaded, heights, strict=True):
        painter.drawText(QRect(12, y, 126, height), Qt.TextFlag.TextWordWrap, label)
        for i, (state, cell) in enumerate(cells):
            x = 150 + i * cw
            painter.drawText(x, y + 12, state)
            painter.drawImage(x, y + 18, cell.scaled(cell.width() * 2, cell.height() * 2))
        y += height
    painter.end()
    if not image.save(str(path)):
        raise ValueError(f"Cannot save {path}")


def main():
    cli = parser(__doc__)
    cli.add_argument("families", nargs="*", choices=list(specs()))
    args = cli.parse_args()
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from pathlib import Path

    from PySide6.QtGui import QFontDatabase, QGuiApplication

    app = QGuiApplication([])
    # The Windows offscreen plugin can have an empty font database. This font is
    # for sheet annotations only; browser component fonts are never overridden.
    if not QFontDatabase.families():
        font = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts/segoeui.ttf"
        if not font.exists() or QFontDatabase.addApplicationFont(str(font)) < 0:
            raise RuntimeError("No annotation font available for Qt offscreen sheet composition")
    report = {"cells": [], "failures": [], "unsupported": []}
    try:
        vendors = vendor_sources(args.design_system)
        with capture_session(args) as (page, base, diagnostics):
            report.update(diagnostics)
            page.set_viewport_size({"width": 700, "height": 500})
            for family in args.families or list(specs()):
                print(f"Capturing {family}...", flush=True)
                name, definitions = specs()[family]
                rows = []
                for spec in definitions:
                    cells = []
                    for state in spec["states"]:
                        identity = {"family": family, "component": spec["label"], "state": state}
                        if state in spec["unsupported"]:
                            report["unsupported"].append({**identity, "reason": spec["unsupported"][state]})
                            continue
                        try:
                            page.goto(base + "/")
                            page.set_content(html(base, vendors, spec["expression"], state, spec["width"]))
                            ready(page)
                            page.locator("#specimen > *").first.wait_for(state="visible")
                            target = page.locator("#specimen").locator(spec["target"]).first
                            if not target.count():
                                target = page.locator("#specimen > *").first
                            if family == "popups" and state == "hover":
                                page.locator("#cell").evaluate("el=>el.style.paddingTop='64px'")
                            page.mouse.move(699, 499)
                            if "focus" in state:
                                target.focus()
                            if state.startswith("hover") or state.startswith("pressed"):
                                target.hover()
                            if state.startswith("pressed"):
                                page.mouse.down()
                            if state == "open":
                                target.click()
                            observed = observe(target)
                            for requested, actual in [
                                ("focus", observed["focus"]),
                                ("pressed", observed["active"]),
                                ("hover", observed["hover"]),
                                ("disabled", observed["disabled"]),
                            ]:
                                if state.startswith(requested) and not actual:
                                    raise ValueError(f"Requested {requested} was not reached")
                            if state == "open":
                                page.locator(".dt-combo-pop, .dt-menu").first.wait_for(state="visible")
                            # Popups overflow their control; include them in the specimen host capture.
                            if state == "open":
                                page.locator("#cell").evaluate("el=>el.style.height='240px'")
                            data = page.locator("#cell").screenshot(animations="disabled")
                            cells.append((state, data))
                            report["cells"].append({**identity, "observed": observed})
                        except Exception as error:
                            report["failures"].append({**identity, "error": str(error)})
                            print(identity, str(error)[:180], flush=True)
                        finally:
                            page.mouse.up()
                    rows.append((spec["label"], cells))
                if family == "inputs":
                    compose(args.out / "33-text-inputs.png", rows[:5])
                    compose(args.out / "34-combo-spin.png", rows[5:])
                else:
                    compose(args.out / f"{name}.png", rows)
    except Exception as error:
        report["failures"].append({"setup": str(error)})
    if (args.design_system / "_ds_bundle.js").exists():
        manifest(args, report)
    print(
        f"Captured {len(report['cells'])} cells; {len(report['unsupported'])} unsupported; {len(report['failures'])} failures"
    )
    for failure in report["failures"]:
        print(failure)
    app.quit()
    return int(bool(report["failures"] or report.get("errors")))


if __name__ == "__main__":
    raise SystemExit(main())
