"""Capture real exported web components in Qt-style state matrices (DPR 1, enlarged 2x)."""

import json
import os
from pathlib import Path

from design_capture import capture_session, manifest, parser, ready, vendor_sources
from state_layout import compose

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
    width = f"{width}px" if isinstance(width, int) else width
    # The host fixes specimen bounds, not the component's styling/state appearance.
    return f'''<!doctype html><link rel="stylesheet" href="{base}/styles.css">
{scripts}<script src="{base}/_ds_bundle.js"></script>
<body style="margin:0;overflow:hidden"><div id="cell" style="display:inline-block;padding:8px;position:relative;background:#252525"><div id="specimen" style="width:{width}"></div></div>
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


def aligned_specs(name, definition):
    """Map explicit Qt slots to real web recipes, never compact unavailable states."""
    original = specs()
    family = next(
        key for key, (sheet, _) in original.items() if sheet == name or key == "inputs" and name == "34-combo-spin"
    )
    recipes = original[family][1]
    if name == "33-text-inputs":
        recipes = [recipes[0], recipes[1], recipes[3], recipes[4]]
    elif name == "34-combo-spin":
        recipes = recipes[5:]
    aliases = {
        "focus (keyboardFocus)": "focus",
        "placeholder": "rest",
        "placeholder+hover": "hover",
        "placeholder+focus": "focus",
        "level=elevated": "elevated",
        "0%": "0",
        "40%": "40",
        "100%": "100",
        "busy (0,0)": "busy",
        "disabled 40%": "disabled",
        "hover row 0": "hover",
        "hover row 1": "hover",
        "selected row 1": "selected",
        "selected row 2": "selected",
        "rest (alternating)": "rest",
        "hover tab 1": "hover",
        "menubar": "rest",
    }
    result = []
    for i, row in enumerate(definition["rows"]):
        for label in row["states"]:
            state = aliases.get(label, label)
            recipe = dict(recipes[min(i, len(recipes) - 1)])
            reason = None
            if name == "38-labels-status":
                if i == 0:
                    if label in ["eyebrow", "caption", "value", "supporting", "section", "identifier", "panelHeading"]:
                        recipe = next(r for r in recipes if r["label"] == label).copy()
                        text = "Eyebrow" if label == "eyebrow" else label
                        recipe["expression"] = f'e(D.Label,{{textRole:"{label}"}},"{text}")'
                    else:
                        reason = "No matching exported label role API"
                elif i == 1:
                    reason = "QLabel semantic state has no matching exported component API"
                elif i == 2:
                    recipe = next(r for r in recipes if r["label"] == "Status " + label).copy()
                else:
                    recipe = recipes[-1].copy()
                state = "rest"
            elif name == "39-popups":
                if i == 2:
                    recipe = recipes[-1].copy()
                else:
                    reason = "Web popup capture includes its trigger; no equivalent isolated Qt popup recipe yet"
            elif name == "33-text-inputs" and i == 1 and label == "read-only":
                recipe = original["inputs"][1][2].copy()
                state = "rest"
            if name == "31-connection-button":
                recipe["width"] = "max-content"
            if name == "32-gallery-buttons" and i < 3:
                recipe["width"] = "max-content"
            if state not in recipe["states"]:
                reason = reason or "No matching exported capture recipe for this state"
            reason = reason or recipe["unsupported"].get(state)
            result.append((i, label, state, recipe, reason))
    return family, result


def main():
    cli = parser(__doc__)
    cli.add_argument("families", nargs="*", choices=list(specs()))
    cli.add_argument(
        "-q",
        "--reference",
        type=Path,
        default=Path("tmp/qt-renders/states/contract.json"),
        help="Qt state contract.json; generate Qt states first",
    )
    args = cli.parse_args()
    contract = json.loads(args.reference.read_text(encoding="utf-8"))
    if contract.get("version") != 1 or contract.get("device_scale_factor") != 1:
        cli.error("Unsupported state contract version or DPR")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
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
            for name, definition in contract["sheets"].items():
                family, slots = aligned_specs(name, definition)
                if args.families and family not in args.families:
                    continue
                print(f"Capturing {name}...", flush=True)
                rows = [{} for _ in definition["rows"]]
                for row_index, label, state, spec, reason in slots:
                    identity = {"family": family, "component": definition["rows"][row_index]["label"], "state": label}
                    if reason:
                        report["unsupported"].append({**identity, "reason": reason})
                        rows[row_index][label] = "Unsupported: " + reason
                        continue
                    try:
                        page.goto(base + "/")
                        page.set_content(html(base, vendors, spec["expression"], state, spec["width"]))
                        ready(page)
                        page.locator("#specimen > *").first.wait_for(state="visible")
                        target = page.locator("#specimen").locator(spec["target"]).first
                        if not target.count():
                            target = page.locator("#specimen > *").first
                        if name == "36-lists-tables" and state == "hover":
                            target = target.nth(0) if row_index == 0 else page.locator("tbody tr").nth(1)
                        if name == "37-tabs-scrollbar" and state == "hover":
                            target = page.locator('[role="tab"]').nth(1)
                        page.mouse.move(699, 499)
                        if "focus" in state:
                            target.focus()
                        if state.startswith("hover") or state.startswith("pressed"):
                            target.hover()
                        if state.startswith("pressed"):
                            page.mouse.down()
                        observed = observe(target)
                        for requested, actual in [
                            ("focus", observed["focus"]),
                            ("pressed", observed["active"]),
                            ("hover", observed["hover"]),
                            ("disabled", observed["disabled"]),
                        ]:
                            if state.startswith(requested) and not actual:
                                raise ValueError(f"Requested {requested} was not reached")
                        data = page.locator("#cell").screenshot(animations="disabled")
                        from PySide6.QtGui import QImage

                        rows[row_index][label] = QImage.fromData(data)
                        report["cells"].append({**identity, "observed": observed})
                    except Exception as error:
                        report["failures"].append({**identity, "error": str(error)})
                        rows[row_index][label] = "Capture failed: " + str(error)
                        print(identity, str(error)[:180], flush=True)
                    finally:
                        page.mouse.up()
                compose(args.out / f"{name}.png", definition, rows)
            report["state_contract"] = contract

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
