/* @ds-bundle: {"format":4,"namespace":"DTFlowDesignSystem_cee425","components":[{"name":"Button","sourcePath":"components/controls/Button.jsx"},{"name":"Checkbox","sourcePath":"components/controls/Checkbox.jsx"},{"name":"ComboBox","sourcePath":"components/controls/ComboBox.jsx"},{"name":"ConnectionButton","sourcePath":"components/controls/ConnectionButton.jsx"},{"name":"Slider","sourcePath":"components/controls/Slider.jsx"},{"name":"SpinBox","sourcePath":"components/controls/SpinBox.jsx"},{"name":"Tabs","sourcePath":"components/controls/Tabs.jsx"},{"name":"TextArea","sourcePath":"components/controls/TextArea.jsx"},{"name":"TextField","sourcePath":"components/controls/TextField.jsx"},{"name":"DataTable","sourcePath":"components/data/DataTable.jsx"},{"name":"ListBox","sourcePath":"components/data/ListBox.jsx"},{"name":"ListItem","sourcePath":"components/data/ListItem.jsx"},{"name":"LoraChip","sourcePath":"components/data/LoraChip.jsx"},{"name":"Badge","sourcePath":"components/feedback/Badge.jsx"},{"name":"EmptyState","sourcePath":"components/feedback/EmptyState.jsx"},{"name":"ProgressBar","sourcePath":"components/feedback/ProgressBar.jsx"},{"name":"StatusCard","sourcePath":"components/feedback/StatusCard.jsx"},{"name":"Tooltip","sourcePath":"components/feedback/Tooltip.jsx"},{"name":"FloatingToolbar","sourcePath":"components/gallery/FloatingToolbar.jsx"},{"name":"ImageCard","sourcePath":"components/gallery/ImageCard.jsx"},{"name":"MetadataBlock","sourcePath":"components/gallery/MetadataBlock.jsx"},{"name":"OverlayChip","sourcePath":"components/gallery/OverlayChip.jsx"},{"name":"RoundButton","sourcePath":"components/gallery/RoundButton.jsx"},{"name":"Thumbnail","sourcePath":"components/gallery/Thumbnail.jsx"},{"name":"ToolbarRow","sourcePath":"components/gallery/ToolbarRow.jsx"},{"name":"Icon","sourcePath":"components/icons/Icon.jsx"},{"name":"Panel","sourcePath":"components/layout/Panel.jsx"},{"name":"SectionCard","sourcePath":"components/layout/SectionCard.jsx"},{"name":"StatusBar","sourcePath":"components/layout/StatusBar.jsx"},{"name":"Brand","sourcePath":"components/shell/Brand.jsx"},{"name":"MenuBar","sourcePath":"components/shell/MenuBar.jsx"},{"name":"Navigation","sourcePath":"components/shell/Navigation.jsx"},{"name":"Label","sourcePath":"components/text/Label.jsx"}],"sourceHashes":{"components/controls/Button.jsx":"f14ec6d739bb","components/controls/Checkbox.jsx":"f332472f9420","components/controls/ComboBox.jsx":"e15bf83b7b25","components/controls/ConnectionButton.jsx":"233a0443e571","components/controls/Slider.jsx":"f4556b87ee4e","components/controls/SpinBox.jsx":"a4211b926753","components/controls/Tabs.jsx":"d4e4e705f1b6","components/controls/TextArea.jsx":"67025bc56697","components/controls/TextField.jsx":"bd72feeda989","components/data/DataTable.jsx":"03eff42da8fd","components/data/ListBox.jsx":"916112a411db","components/data/ListItem.jsx":"5642cecf3a58","components/data/LoraChip.jsx":"5895c133f0a6","components/feedback/Badge.jsx":"c66c8e10200d","components/feedback/EmptyState.jsx":"acb08ae18792","components/feedback/ProgressBar.jsx":"4f2dab81e37f","components/feedback/StatusCard.jsx":"23714abe526f","components/feedback/Tooltip.jsx":"5b4e1864d5eb","components/gallery/FloatingToolbar.jsx":"9fd57e566d7b","components/gallery/ImageCard.jsx":"dfa22147cf84","components/gallery/MetadataBlock.jsx":"7e5482a10822","components/gallery/OverlayChip.jsx":"993e3d1c925d","components/gallery/RoundButton.jsx":"56134f2608fb","components/gallery/Thumbnail.jsx":"e6a4fb41d455","components/gallery/ToolbarRow.jsx":"aa1f557189a3","components/icons/Icon.jsx":"19c963de1fd2","components/layout/Panel.jsx":"ecb20e5b5515","components/layout/SectionCard.jsx":"93730e97c5e7","components/layout/StatusBar.jsx":"e46b8008847a","components/shell/Brand.jsx":"305fba5ddd21","components/shell/MenuBar.jsx":"b0bedd4b2b2c","components/shell/Navigation.jsx":"88b962083626","components/text/Label.jsx":"0b494e9af7b5","ui_kits/Chrome.jsx":"459d7b3d4182","ui_kits/connection/ConnectionScreen.jsx":"7a9771a70833","ui_kits/generate/GenerateScreen.jsx":"665b41ab2c20","ui_kits/images/DetailView.jsx":"d20bc8c7757c","ui_kits/images/GridView.jsx":"12ca127afd43","ui_kits/images/Header.jsx":"56de6859e778","ui_kits/images/ImagesScreen.jsx":"d7818ea07fab","ui_kits/images/ListView.jsx":"6eed4cabae4f","ui_kits/images/data.js":"cf4811173fd5","ui_kits/queues/QueuesScreen.jsx":"b6b6307a5ac8"},"inlinedExternals":[],"unexposedExports":[]} */

(() => {

const __ds_ns = (window.DTFlowDesignSystem_cee425 = window.DTFlowDesignSystem_cee425 || {});

const __ds_scope = {};

(__ds_ns.__errors = __ds_ns.__errors || []);

// components/controls/Button.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
function Button({
  variant = "neutral",
  checked = false,
  disabled,
  className = "",
  children,
  ...rest
}) {
  return /*#__PURE__*/React.createElement("button", _extends({
    type: "button",
    disabled: disabled,
    "aria-pressed": checked || undefined,
    className: ["dt-btn", variant, checked ? "checked" : "", className].filter(Boolean).join(" ")
  }, rest), children);
}
Object.assign(__ds_scope, { Button });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/controls/Button.jsx", error: String((e && e.message) || e) }); }

// components/controls/Checkbox.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
function Checkbox({
  label,
  disabled,
  ...rest
}) {
  return /*#__PURE__*/React.createElement("label", {
    className: "dt-check" + (disabled ? " disabled" : "")
  }, /*#__PURE__*/React.createElement("input", _extends({
    type: "checkbox",
    disabled: disabled
  }, rest)), label);
}
Object.assign(__ds_scope, { Checkbox });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/controls/Checkbox.jsx", error: String((e && e.message) || e) }); }

// components/controls/ComboBox.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
function ComboBox({
  options = [],
  value,
  defaultValue,
  onChange,
  disabled,
  invalid,
  className = "",
  style,
  title,
  ...rest
}) {
  const [open, setOpen] = React.useState(false);
  const [inner, setInner] = React.useState(defaultValue !== undefined ? defaultValue : options[0]);
  const cur = value !== undefined ? value : inner;
  const ref = React.useRef(null);
  React.useEffect(() => {
    if (!open) return;
    const f = e => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false);
    };
    document.addEventListener("mousedown", f);
    return () => document.removeEventListener("mousedown", f);
  }, [open]);
  const pick = o => {
    setInner(o);
    setOpen(false);
    onChange && onChange({
      target: {
        value: o
      },
      currentTarget: {
        value: o
      }
    });
  };
  const key = e => {
    const i = options.indexOf(cur);
    if (e.key === "Escape") setOpen(false);else if (e.key === "ArrowDown") {
      e.preventDefault();
      if (open) pick(options[Math.min(options.length - 1, i + 1)]);else setOpen(true);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      if (open) pick(options[Math.max(0, i - 1)]);
    }
  };
  return /*#__PURE__*/React.createElement("div", {
    ref: ref,
    className: "dt-combo",
    style: style,
    title: title
  }, /*#__PURE__*/React.createElement("button", _extends({
    type: "button",
    disabled: disabled,
    "aria-haspopup": "listbox",
    "aria-expanded": open,
    onClick: () => setOpen(!open),
    onKeyDown: key,
    className: ["dt-field", "dt-combo-btn", invalid ? "invalid" : "", className].filter(Boolean).join(" ")
  }, rest), cur), open ? /*#__PURE__*/React.createElement("div", {
    role: "listbox",
    className: "dt-combo-pop"
  }, options.map(o => /*#__PURE__*/React.createElement("div", {
    key: o,
    role: "option",
    "aria-selected": o === cur,
    className: "dt-combo-opt" + (o === cur ? " selected" : ""),
    onMouseDown: e => {
      e.preventDefault();
      pick(o);
    }
  }, o))) : null);
}
Object.assign(__ds_scope, { ComboBox });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/controls/ComboBox.jsx", error: String((e && e.message) || e) }); }

// components/controls/ConnectionButton.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const DOT = {
  ok: "var(--color-connection-accent)",
  warning: "var(--color-warning)",
  error: "var(--color-error)",
  unknown: "var(--color-secondary)"
};
function ConnectionButton({
  state = "unknown",
  checked = false,
  disabled,
  ...rest
}) {
  return /*#__PURE__*/React.createElement("button", _extends({
    type: "button",
    disabled: disabled,
    "aria-label": "Connection: " + state,
    title: "Show connection workspace \u2014 " + state,
    className: ["dt-conn", state === "unknown" ? "" : state, checked ? "checked" : ""].filter(Boolean).join(" ")
  }, rest), /*#__PURE__*/React.createElement("svg", {
    width: "30",
    height: "18",
    viewBox: "0 0 40 24",
    fill: "none",
    "aria-hidden": "true"
  }, /*#__PURE__*/React.createElement("g", {
    stroke: "var(--color-connection-glyph)",
    strokeWidth: "1.6",
    strokeLinecap: "round"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M13.76 7.76A6 6 0 1 0 22.24 7.76"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "18",
    y1: "3",
    x2: "18",
    y2: "11"
  })), /*#__PURE__*/React.createElement("circle", {
    cx: "37",
    cy: "4",
    r: "3",
    fill: DOT[state] || DOT.unknown
  })));
}
Object.assign(__ds_scope, { ConnectionButton });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/controls/ConnectionButton.jsx", error: String((e && e.message) || e) }); }

// components/controls/Slider.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
function Slider({
  className = "",
  ...rest
}) {
  return /*#__PURE__*/React.createElement("input", _extends({
    type: "range",
    className: ["dt-slider", className].filter(Boolean).join(" ")
  }, rest));
}
Object.assign(__ds_scope, { Slider });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/controls/Slider.jsx", error: String((e && e.message) || e) }); }

// components/controls/SpinBox.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
function SpinBox({
  className = "",
  ...rest
}) {
  return /*#__PURE__*/React.createElement("input", _extends({
    type: "number",
    className: ["dt-field", className].filter(Boolean).join(" ")
  }, rest));
}
Object.assign(__ds_scope, { SpinBox });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/controls/SpinBox.jsx", error: String((e && e.message) || e) }); }

// components/controls/Tabs.jsx
try { (() => {
function Tabs({
  tabs = [],
  value,
  onChange,
  children
}) {
  return /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("div", {
    className: "dt-tabbar",
    role: "tablist"
  }, tabs.map(t => /*#__PURE__*/React.createElement("button", {
    key: t,
    type: "button",
    role: "tab",
    "aria-selected": value === t,
    className: "dt-tab" + (value === t ? " on" : ""),
    onClick: () => onChange && onChange(t)
  }, t))), /*#__PURE__*/React.createElement("div", {
    className: "dt-tabpane"
  }, children));
}
Object.assign(__ds_scope, { Tabs });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/controls/Tabs.jsx", error: String((e && e.message) || e) }); }

// components/controls/TextArea.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
function TextArea({
  invalid,
  rows = 3,
  className = "",
  ...rest
}) {
  return /*#__PURE__*/React.createElement("textarea", _extends({
    rows: rows,
    className: ["dt-field", invalid ? "invalid" : "", className].filter(Boolean).join(" ")
  }, rest));
}
Object.assign(__ds_scope, { TextArea });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/controls/TextArea.jsx", error: String((e && e.message) || e) }); }

// components/controls/TextField.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
function TextField({
  invalid,
  className = "",
  ...rest
}) {
  return /*#__PURE__*/React.createElement("input", _extends({
    className: ["dt-field", invalid ? "invalid" : "", className].filter(Boolean).join(" ")
  }, rest));
}
Object.assign(__ds_scope, { TextField });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/controls/TextField.jsx", error: String((e && e.message) || e) }); }

// components/data/DataTable.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
function DataTable({
  columns = [],
  rows = [],
  selectedIndex,
  onSelect,
  variant = "default",
  ...rest
}) {
  return /*#__PURE__*/React.createElement("table", _extends({
    className: "dt-table" + (variant === "gallery" ? " gallery" : "")
  }, rest), /*#__PURE__*/React.createElement("thead", null, /*#__PURE__*/React.createElement("tr", null, columns.map(c => /*#__PURE__*/React.createElement("th", {
    key: c.key
  }, c.label)))), /*#__PURE__*/React.createElement("tbody", null, rows.map((r, i) => /*#__PURE__*/React.createElement("tr", {
    key: i,
    className: selectedIndex === i ? "selected" : "",
    onClick: () => onSelect && onSelect(i)
  }, columns.map(c => /*#__PURE__*/React.createElement("td", {
    key: c.key,
    style: c.mono ? {
      fontFamily: "var(--font-family-mono)"
    } : undefined
  }, r[c.key]))))));
}
Object.assign(__ds_scope, { DataTable });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/DataTable.jsx", error: String((e && e.message) || e) }); }

// components/data/ListBox.jsx
try { (() => {
function ListBox({
  children,
  minHeight = 90,
  label,
  style
}) {
  return /*#__PURE__*/React.createElement("div", {
    role: "listbox",
    "aria-label": label,
    className: "dt-listbox",
    style: {
      minHeight,
      ...style
    }
  }, children);
}
Object.assign(__ds_scope, { ListBox });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/ListBox.jsx", error: String((e && e.message) || e) }); }

// components/data/ListItem.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
function ListItem({
  selected,
  children,
  ...rest
}) {
  return /*#__PURE__*/React.createElement("div", _extends({
    role: "option",
    "aria-selected": !!selected,
    className: "dt-item" + (selected ? " selected" : "")
  }, rest), children);
}
Object.assign(__ds_scope, { ListItem });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/ListItem.jsx", error: String((e && e.message) || e) }); }

// components/data/LoraChip.jsx
try { (() => {
function LoraChip({
  name,
  weight,
  status = "Unknown"
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "dt-chip"
  }, /*#__PURE__*/React.createElement("span", {
    style: {
      flex: 1,
      fontSize: "var(--font-size-small)",
      fontWeight: 500
    }
  }, name), /*#__PURE__*/React.createElement("span", {
    style: {
      fontFamily: "var(--font-family-mono)",
      fontSize: "var(--font-size-small)"
    }
  }, weight), /*#__PURE__*/React.createElement("span", {
    className: "dt-unknown",
    title: "Offline fixture: no compatibility evidence recorded."
  }, status));
}
Object.assign(__ds_scope, { LoraChip });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/data/LoraChip.jsx", error: String((e && e.message) || e) }); }

// components/feedback/Badge.jsx
try { (() => {
function Badge({
  children
}) {
  return /*#__PURE__*/React.createElement("span", {
    className: "dt-badge"
  }, children);
}
Object.assign(__ds_scope, { Badge });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/feedback/Badge.jsx", error: String((e && e.message) || e) }); }

// components/feedback/EmptyState.jsx
try { (() => {
function EmptyState({
  title,
  children,
  logo,
  style
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "dt-empty",
    style: style
  }, logo ? /*#__PURE__*/React.createElement("img", {
    src: logo,
    width: "56",
    height: "56",
    alt: "",
    style: {
      borderRadius: 8
    }
  }) : null, title ? /*#__PURE__*/React.createElement("span", {
    className: "dt-label section"
  }, title) : null, /*#__PURE__*/React.createElement("span", {
    className: "dt-label supporting",
    style: {
      textAlign: "center"
    }
  }, children));
}
Object.assign(__ds_scope, { EmptyState });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/feedback/EmptyState.jsx", error: String((e && e.message) || e) }); }

// components/feedback/ProgressBar.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
function ProgressBar({
  value = 0,
  max = 100,
  label,
  style,
  ...rest
}) {
  const pct = Math.min(100, value / max * 100) + "%";
  return /*#__PURE__*/React.createElement("div", _extends({
    className: "dt-bar",
    role: "progressbar",
    "aria-valuenow": value,
    "aria-valuemax": max,
    style: label ? {
      height: 24,
      position: "relative",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      ...style
    } : style
  }, rest), /*#__PURE__*/React.createElement("i", {
    style: label ? {
      position: "absolute",
      left: 0,
      top: 0,
      bottom: 0,
      width: pct
    } : {
      width: pct
    }
  }), label ? /*#__PURE__*/React.createElement("span", {
    style: {
      position: "relative",
      fontSize: "var(--font-size-body)"
    }
  }, label) : null);
}
Object.assign(__ds_scope, { ProgressBar });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/feedback/ProgressBar.jsx", error: String((e && e.message) || e) }); }

// components/feedback/StatusCard.jsx
try { (() => {
function StatusCard({
  state = "neutral",
  heading,
  children,
  glyph = "\u25CF",
  minHeight
}) {
  const cls = state === "neutral" ? "unknown" : state;
  return /*#__PURE__*/React.createElement("div", {
    className: "dt-status " + (state === "neutral" || state === "unknown" ? "" : state),
    style: minHeight ? {
      minHeight,
      alignItems: "flex-start"
    } : undefined
  }, /*#__PURE__*/React.createElement("span", {
    className: "dt-glyph dt-st-" + (state === "neutral" ? "unknown" : cls),
    style: state === "neutral" ? {
      color: "#8a8a8a"
    } : undefined
  }, glyph), /*#__PURE__*/React.createElement("div", {
    style: {
      display: "flex",
      flexDirection: "column",
      gap: 5
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "dt-st-" + cls,
    style: {
      fontSize: "12pt",
      fontWeight: 500,
      color: state === "neutral" ? "#d7d7d7" : undefined
    }
  }, heading), /*#__PURE__*/React.createElement("div", {
    style: {
      fontSize: "var(--font-size-small)",
      color: "var(--color-secondary)"
    }
  }, children)));
}
Object.assign(__ds_scope, { StatusCard });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/feedback/StatusCard.jsx", error: String((e && e.message) || e) }); }

// components/feedback/Tooltip.jsx
try { (() => {
function Tooltip({
  text,
  children
}) {
  return /*#__PURE__*/React.createElement("span", {
    className: "dt-tip-host"
  }, children, /*#__PURE__*/React.createElement("span", {
    role: "tooltip",
    className: "dt-tip"
  }, text));
}
Object.assign(__ds_scope, { Tooltip });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/feedback/Tooltip.jsx", error: String((e && e.message) || e) }); }

// components/gallery/FloatingToolbar.jsx
try { (() => {
function FloatingToolbar({
  position = "top-left",
  pill = false,
  children
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "dt-ftb " + position + (pill ? " pill" : "")
  }, children);
}
Object.assign(__ds_scope, { FloatingToolbar });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/gallery/FloatingToolbar.jsx", error: String((e && e.message) || e) }); }

// components/gallery/ImageCard.jsx
try { (() => {
function ImageCard({
  src,
  ratio = "1 / 1",
  badge,
  title,
  subtitle,
  onClick
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "dt-icard",
    onClick: onClick
  }, /*#__PURE__*/React.createElement("div", {
    className: "dt-tile",
    style: {
      aspectRatio: ratio
    }
  }, src ? /*#__PURE__*/React.createElement("img", {
    src: src,
    alt: "",
    style: {
      width: "100%",
      height: "100%",
      objectFit: "cover"
    }
  }) : badge), /*#__PURE__*/React.createElement("span", {
    className: "dt-label value",
    style: {
      padding: "0 8px",
      fontSize: "var(--font-size-small)"
    }
  }, title), /*#__PURE__*/React.createElement("span", {
    className: "dt-label supporting",
    style: {
      padding: "0 8px 8px"
    }
  }, subtitle));
}
Object.assign(__ds_scope, { ImageCard });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/gallery/ImageCard.jsx", error: String((e && e.message) || e) }); }

// components/gallery/MetadataBlock.jsx
try { (() => {
function MetadataBlock({
  icon,
  label,
  children,
  minWidth
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "dt-mblock",
    style: minWidth ? {
      minWidth
    } : undefined
  }, /*#__PURE__*/React.createElement("span", {
    className: "dt-label caption",
    style: {
      display: "flex",
      alignItems: "center",
      gap: 5
    }
  }, icon, label), /*#__PURE__*/React.createElement("div", {
    className: "dt-mvalue"
  }, children));
}
Object.assign(__ds_scope, { MetadataBlock });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/gallery/MetadataBlock.jsx", error: String((e && e.message) || e) }); }

// components/gallery/OverlayChip.jsx
try { (() => {
function OverlayChip({
  children,
  muted
}) {
  return /*#__PURE__*/React.createElement("span", {
    className: "dt-ov" + (muted ? " muted" : "")
  }, children);
}
Object.assign(__ds_scope, { OverlayChip });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/gallery/OverlayChip.jsx", error: String((e && e.message) || e) }); }

// components/gallery/RoundButton.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
function RoundButton({
  direction = "next",
  ...rest
}) {
  return /*#__PURE__*/React.createElement("button", _extends({
    type: "button",
    "aria-label": direction === "next" ? "Next" : "Previous",
    className: "dt-round " + direction
  }, rest), direction === "next" ? "\u203A" : "\u2039");
}
Object.assign(__ds_scope, { RoundButton });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/gallery/RoundButton.jsx", error: String((e && e.message) || e) }); }

// components/gallery/Thumbnail.jsx
try { (() => {
function Thumbnail({
  src,
  label,
  selected,
  onClick
}) {
  return /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "dt-thumb" + (selected ? " on" : ""),
    onClick: onClick
  }, /*#__PURE__*/React.createElement("div", {
    className: "dt-tile",
    style: {
      aspectRatio: "1 / 1",
      borderRadius: 6
    }
  }, src ? /*#__PURE__*/React.createElement("img", {
    src: src,
    alt: "",
    style: {
      width: "100%",
      height: "100%",
      objectFit: "cover"
    }
  }) : label));
}
Object.assign(__ds_scope, { Thumbnail });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/gallery/Thumbnail.jsx", error: String((e && e.message) || e) }); }

// components/gallery/ToolbarRow.jsx
try { (() => {
function ToolbarRow({
  children,
  style
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "dt-toolrow",
    style: style
  }, children);
}
Object.assign(__ds_scope, { ToolbarRow });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/gallery/ToolbarRow.jsx", error: String((e && e.message) || e) }); }

// components/icons/Icon.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
const LOCAL = ["grid", "list", "detail", "refresh", "document", "json", "calendar", "fullscreen", "chevron"];
const DOT = {
  ok: "var(--color-connection-accent)",
  warning: "var(--color-warning)",
  error: "var(--color-error)",
  unknown: "var(--color-secondary)"
};
function Icon({
  name,
  size = 16,
  base = "../../assets/icons/",
  state = "unknown",
  style,
  ...rest
}) {
  const s = {
    display: "block",
    flex: "none",
    ...style
  };
  if (name === "panel") return /*#__PURE__*/React.createElement("svg", _extends({
    width: size,
    height: size,
    viewBox: "0 0 32 32",
    fill: "none",
    stroke: "var(--color-secondary)",
    strokeWidth: "2",
    "aria-hidden": "true",
    style: s
  }, rest), /*#__PURE__*/React.createElement("rect", {
    x: "5",
    y: "6",
    width: "22",
    height: "20",
    rx: "4"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "20",
    y1: "6",
    x2: "20",
    y2: "26"
  }));
  if (name === "connection") return /*#__PURE__*/React.createElement("svg", _extends({
    width: size * 40 / 24,
    height: size,
    viewBox: "0 0 40 24",
    fill: "none",
    "aria-hidden": "true",
    style: s
  }, rest), /*#__PURE__*/React.createElement("g", {
    stroke: "var(--color-connection-glyph)",
    strokeWidth: "1.6",
    strokeLinecap: "round"
  }, /*#__PURE__*/React.createElement("path", {
    d: "M13.76 7.76A6 6 0 1 0 22.24 7.76"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "18",
    y1: "3",
    x2: "18",
    y2: "11"
  })), /*#__PURE__*/React.createElement("circle", {
    cx: "37",
    cy: "4",
    r: "3",
    fill: DOT[state] || DOT.unknown
  }));
  if (LOCAL.includes(name)) return /*#__PURE__*/React.createElement("img", _extends({
    src: base + name + ".svg",
    width: size,
    height: size,
    alt: "",
    style: s
  }, rest));
  return /*#__PURE__*/React.createElement("img", _extends({
    src: "https://unpkg.com/lucide-static@latest/icons/" + name + ".svg",
    width: size,
    height: size,
    alt: "",
    style: {
      ...s,
      filter: "invert(1) brightness(.96)"
    }
  }, rest));
}
Object.assign(__ds_scope, { Icon });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/icons/Icon.jsx", error: String((e && e.message) || e) }); }

// components/layout/Panel.jsx
try { (() => {
function Panel({
  eyebrow,
  children,
  style,
  className = ""
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "dt-panel " + className,
    style: style
  }, eyebrow ? /*#__PURE__*/React.createElement("span", {
    className: "dt-label eyebrow"
  }, eyebrow) : null, children);
}
Object.assign(__ds_scope, { Panel });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/layout/Panel.jsx", error: String((e && e.message) || e) }); }

// components/layout/SectionCard.jsx
try { (() => {
function SectionCard({
  title,
  icon,
  checkable = false,
  checked = true,
  onCheckedChange,
  disabled = false,
  children,
  style
}) {
  const open = !checkable || checked;
  const edge = disabled ? "var(--color-disabled-border)" : "var(--color-section-border)";
  const heading = {
    color: disabled ? "var(--color-disabled)" : "var(--color-text)",
    fontSize: "var(--font-size-small)",
    fontWeight: "var(--font-weight-emphasis)"
  };
  return /*#__PURE__*/React.createElement("section", {
    "aria-disabled": disabled,
    style: {
      background: "var(--color-surface)",
      border: `1px solid ${edge}`,
      borderRadius: 8,
      flex: "none",
      ...style
    }
  }, /*#__PURE__*/React.createElement("header", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 8,
      padding: "6px 10px",
      background: "var(--color-section-header)",
      borderBottom: open ? `1px solid ${edge}` : "0",
      borderTopLeftRadius: 7,
      borderTopRightRadius: 7
    }
  }, icon ? /*#__PURE__*/React.createElement("span", {
    style: {
      width: 16,
      height: 16,
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
      flex: "none"
    }
  }, icon) : null, checkable ? /*#__PURE__*/React.createElement("label", {
    style: {
      display: "flex",
      alignItems: "center",
      gap: 7,
      cursor: disabled ? "default" : "pointer",
      ...heading
    }
  }, /*#__PURE__*/React.createElement("input", {
    type: "checkbox",
    checked: checked,
    disabled: disabled,
    onChange: e => onCheckedChange && onCheckedChange(e.target.checked),
    style: {
      width: 14,
      height: 14,
      accentColor: "var(--color-accent)"
    }
  }), title) : /*#__PURE__*/React.createElement("span", {
    style: heading
  }, title)), open ? /*#__PURE__*/React.createElement("div", {
    style: {
      padding: "var(--layout-panel-padding)",
      opacity: disabled ? 0.6 : 1,
      pointerEvents: disabled ? "none" : "auto"
    }
  }, children) : null);
}
Object.assign(__ds_scope, { SectionCard });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/layout/SectionCard.jsx", error: String((e && e.message) || e) }); }

// components/layout/StatusBar.jsx
try { (() => {
function StatusBar({
  children,
  trailing
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "dt-statusbar"
  }, /*#__PURE__*/React.createElement("span", {
    className: "dt-statusbar-text"
  }, children), trailing ? /*#__PURE__*/React.createElement("span", {
    className: "dt-statusbar-trailing"
  }, trailing) : null);
}
Object.assign(__ds_scope, { StatusBar });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/layout/StatusBar.jsx", error: String((e && e.message) || e) }); }

// components/shell/Brand.jsx
try { (() => {
function Brand({
  title = "DTFlow",
  subtitle,
  logo = "../../assets/dtflow-app.png",
  children
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "dt-brand"
  }, /*#__PURE__*/React.createElement("img", {
    src: logo,
    alt: "DTFlow"
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minWidth: 0
    }
  }, /*#__PURE__*/React.createElement("b", null, title), subtitle ? /*#__PURE__*/React.createElement("small", null, subtitle) : null), children);
}
Object.assign(__ds_scope, { Brand });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/shell/Brand.jsx", error: String((e && e.message) || e) }); }

// components/shell/MenuBar.jsx
try { (() => {
function MenuBar({
  menus = []
}) {
  const [open, setOpen] = React.useState(null);
  return /*#__PURE__*/React.createElement("div", {
    className: "dt-menubar",
    onMouseLeave: () => setOpen(null)
  }, menus.map(m => /*#__PURE__*/React.createElement("div", {
    key: m.label,
    style: {
      position: "relative"
    }
  }, /*#__PURE__*/React.createElement("button", {
    type: "button",
    className: "dt-menuitem" + (open === m.label ? " on" : ""),
    onClick: () => setOpen(open === m.label ? null : m.label)
  }, m.label), open === m.label ? /*#__PURE__*/React.createElement("div", {
    className: "dt-menu"
  }, m.items.map(i => /*#__PURE__*/React.createElement("button", {
    key: i,
    type: "button",
    className: "dt-menuitem",
    onClick: () => setOpen(null)
  }, i))) : null)));
}
Object.assign(__ds_scope, { MenuBar });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/shell/MenuBar.jsx", error: String((e && e.message) || e) }); }

// components/shell/Navigation.jsx
try { (() => {
function Navigation({
  current,
  onChange,
  items = ["Generate", "Queues"]
}) {
  return /*#__PURE__*/React.createElement("div", {
    className: "dt-nav"
  }, items.map(t => /*#__PURE__*/React.createElement(__ds_scope.Button, {
    key: t,
    variant: "navigation",
    checked: current === t,
    onClick: () => onChange && onChange(t)
  }, t)));
}
Object.assign(__ds_scope, { Navigation });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/shell/Navigation.jsx", error: String((e && e.message) || e) }); }

// components/text/Label.jsx
try { (() => {
function _extends() { return _extends = Object.assign ? Object.assign.bind() : function (n) { for (var e = 1; e < arguments.length; e++) { var t = arguments[e]; for (var r in t) ({}).hasOwnProperty.call(t, r) && (n[r] = t[r]); } return n; }, _extends.apply(null, arguments); }
function Label({
  textRole = "body",
  children,
  className = "",
  ...rest
}) {
  return /*#__PURE__*/React.createElement("span", _extends({
    className: ["dt-label", textRole, className].filter(Boolean).join(" ")
  }, rest), children);
}
Object.assign(__ds_scope, { Label });
})(); } catch (e) { __ds_ns.__errors.push({ path: "components/text/Label.jsx", error: String((e && e.message) || e) }); }

// ui_kits/Chrome.jsx
try { (() => {
const Ic = (n, s = 16) => {
  const {
    Icon
  } = window.DTFlowDesignSystem_cee425;
  return Icon ? /*#__PURE__*/React.createElement(Icon, {
    name: n,
    size: s
  }) : null;
};
function Chrome({
  tab,
  conn,
  onTab
}) {
  const {
    Brand,
    Navigation,
    Button
  } = window.DTFlowDesignSystem_cee425;
  const [on, setOn] = React.useState(true);
  return /*#__PURE__*/React.createElement("div", {
    className: "chrome"
  }, /*#__PURE__*/React.createElement(Brand, {
    logo: "../../assets/dtflow-app.png"
  }, /*#__PURE__*/React.createElement(Button, {
    variant: "neutral",
    checked: on,
    "aria-label": "Toggle right details panel",
    title: "Show or hide the right details panel",
    style: {
      width: 32,
      height: 32,
      padding: 0,
      marginLeft: 8
    },
    onClick: () => {
      setOn(!on);
      window.dispatchEvent(new Event("dt-inspector"));
    }
  }, /*#__PURE__*/React.createElement("svg", {
    width: "16",
    height: "16",
    viewBox: "0 0 32 32",
    fill: "none",
    stroke: "var(--color-secondary)",
    strokeWidth: "2",
    "aria-hidden": "true"
  }, /*#__PURE__*/React.createElement("rect", {
    x: "5",
    y: "6",
    width: "22",
    height: "20",
    rx: "4"
  }), /*#__PURE__*/React.createElement("line", {
    x1: "20",
    y1: "6",
    x2: "20",
    y2: "26"
  })))), /*#__PURE__*/React.createElement(Navigation, {
    current: tab,
    connectionState: conn,
    onChange: onTab
  }));
}
function useInspector() {
  const [o, s] = React.useState(true);
  React.useEffect(() => {
    const f = () => s(x => !x);
    window.addEventListener("dt-inspector", f);
    return () => window.removeEventListener("dt-inspector", f);
  }, []);
  return o;
}
function StatusBar() {
  const {
    StatusBar: SB
  } = window.DTFlowDesignSystem_cee425;
  return /*#__PURE__*/React.createElement(SB, null, "DTFlow 0.3.0 | revision 2026-10-06 03:42 | source: dev");
}
Object.assign(window, {
  Chrome,
  useInspector,
  Ic,
  StatusBar
});
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/Chrome.jsx", error: String((e && e.message) || e) }); }

// ui_kits/connection/ConnectionScreen.jsx
try { (() => {
function ConnectionScreen() {
  const {
    SectionCard,
    ComboBox,
    TextField,
    Button,
    StatusCard,
    Label,
    ListBox,
    EmptyState,
    Panel,
    FloatingToolbar
  } = window.DTFlowDesignSystem_cee425;
  const insp = useInspector();
  const [backend, setBackend] = React.useState("fake");
  const [key, setKey] = React.useState(""),
    [saved, setSaved] = React.useState(false);
  const [st, setSt] = React.useState({
    s: "neutral",
    h: "Not checked. Check the fake connection.",
    t: "A connection check is bounded, non-generative, and does not consume generation credits."
  });
  const cloud = backend === "cloud";
  const pick = b => {
    setBackend(b);
    setSt({
      s: "neutral",
      h: "Not checked. Check the " + b + " connection.",
      t: st.t
    });
  };
  const check = () => {
    setSt({
      s: "warning",
      h: "Checking…",
      t: "Contacting the " + backend + " backend."
    });
    setTimeout(() => setSt({
      s: "ok",
      h: "Connected. The " + backend + " backend answered.",
      t: "An explicit, non-generative check. It does not poll."
    }), 700);
  };
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "shell"
  }, /*#__PURE__*/React.createElement("div", {
    className: "rail"
  }, /*#__PURE__*/React.createElement(Chrome, {
    tab: "Connection",
    conn: st.s === "neutral" ? "unknown" : st.s,
    onTab: go
  }), /*#__PURE__*/React.createElement(Panel, {
    eyebrow: "CONNECTION CONTEXT",
    className: "railscroll"
  }, /*#__PURE__*/React.createElement(SectionCard, {
    title: "Backend",
    icon: Ic("server")
  }, /*#__PURE__*/React.createElement("div", {
    className: "form"
  }, /*#__PURE__*/React.createElement(Label, {
    textRole: "caption"
  }, "Provider"), /*#__PURE__*/React.createElement(ComboBox, {
    options: ["fake", "local", "cloud"],
    value: backend,
    onChange: e => pick(e.target.value),
    "aria-label": "Generation backend"
  }), /*#__PURE__*/React.createElement(Label, {
    textRole: "caption"
  }, "Endpoint"), /*#__PURE__*/React.createElement(TextField, {
    placeholder: "HOST:PORT",
    disabled: backend !== "local",
    "aria-label": "Local Draw Things endpoint"
  }), /*#__PURE__*/React.createElement(Button, {
    variant: "primary",
    onClick: check,
    style: {
      gridColumn: "1/-1"
    }
  }, "Check connection"))), /*#__PURE__*/React.createElement(SectionCard, {
    title: "DT+ credentials",
    icon: Ic("key-round"),
    disabled: !cloud
  }, /*#__PURE__*/React.createElement("div", {
    className: "stack"
  }, /*#__PURE__*/React.createElement(Label, {
    textRole: "supporting"
  }, "Saved securely in Windows Credential Manager. An environment key keeps precedence."), /*#__PURE__*/React.createElement(TextField, {
    type: "password",
    placeholder: "DT+ API key",
    value: key,
    onChange: e => setKey(e.target.value),
    "aria-label": "DT plus API key"
  }), /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement(Button, {
    style: {
      flex: 1
    },
    disabled: !key,
    onClick: () => setSaved(true)
  }, "Save key"), /*#__PURE__*/React.createElement(Button, {
    style: {
      flex: 1
    },
    variant: "danger",
    disabled: !saved,
    onClick: () => {
      setSaved(false);
      setKey("");
    }
  }, "Remove")), /*#__PURE__*/React.createElement(Label, {
    textRole: "value",
    style: {
      fontSize: "var(--font-size-small)",
      fontWeight: 400
    }
  }, saved ? "Key saved in Windows Credential Manager." : "No API key available."))))), /*#__PURE__*/React.createElement(Panel, {
    eyebrow: "CONNECTION",
    className: "work"
  }, /*#__PURE__*/React.createElement(Label, {
    textRole: "title"
  }, "Connection readiness"), /*#__PURE__*/React.createElement(Label, {
    textRole: "supporting"
  }, "Verify the selected backend without submitting generation. DTFlow never falls back to another provider."), /*#__PURE__*/React.createElement(StatusCard, {
    state: st.s,
    heading: st.h,
    minHeight: 160
  }, st.t)), insp ? /*#__PURE__*/React.createElement("div", {
    className: "rail"
  }, /*#__PURE__*/React.createElement(Panel, {
    eyebrow: "SAFETY & EVIDENCE",
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement(SectionCard, {
    title: "Connection check",
    icon: Ic("shield-check")
  }, /*#__PURE__*/React.createElement(Label, {
    textRole: "supporting",
    style: {
      display: "block",
      whiteSpace: "pre-line",
      color: "var(--color-text)"
    }
  }, "• No generation request\n• No automatic backend fallback\n• Credentials are never shown or logged\n• Failures retain explicit provider evidence")))) : null), /*#__PURE__*/React.createElement(StatusBar, null));
}
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/connection/ConnectionScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/generate/GenerateScreen.jsx
try { (() => {
function GenerateScreen() {
  const {
    SectionCard,
    ComboBox,
    TextField,
    TextArea,
    SpinBox,
    Button,
    Checkbox,
    ProgressBar,
    ListItem,
    Label,
    ListBox,
    EmptyState,
    Panel,
    FloatingToolbar
  } = window.DTFlowDesignSystem_cee425;
  const insp = useInspector();
  const [loras, setLoras] = React.useState(["fake-soft @ 0.8"]),
    [loraSel, setLoraSel] = React.useState(null);
  const [big, setBig] = React.useState(false),
    [det, setDet] = React.useState(false),
    [done, setDone] = React.useState(false),
    [busy, setBusy] = React.useState(false);
  const F = (l, c) => /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement(Label, {
    textRole: "caption"
  }, l), c);
  const gen = () => {
    setBusy(true);
    setDone(false);
    setTimeout(() => {
      setBusy(false);
      setDone(true);
    }, 600);
  };
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "shell"
  }, /*#__PURE__*/React.createElement("div", {
    className: "rail"
  }, /*#__PURE__*/React.createElement(Chrome, {
    tab: "Generate",
    conn: "unknown",
    onTab: go
  }), /*#__PURE__*/React.createElement("div", {
    className: "railscroll"
  }, /*#__PURE__*/React.createElement(SectionCard, {
    title: "Backend",
    icon: Ic("server")
  }, /*#__PURE__*/React.createElement("div", {
    className: "form"
  }, F("Provider", /*#__PURE__*/React.createElement(ComboBox, {
    options: ["fake"],
    disabled: true
  })), F("Model", /*#__PURE__*/React.createElement(ComboBox, {
    options: ["fake-fixture (default)"]
  })), /*#__PURE__*/React.createElement(Button, {
    style: {
      gridColumn: "1/-1"
    }
  }, "Refresh inventory"), /*#__PURE__*/React.createElement(Label, {
    textRole: "supporting",
    style: {
      gridColumn: "1/-1"
    }
  }, "Inventory has not been refreshed."))), /*#__PURE__*/React.createElement(SectionCard, {
    title: "Parameters",
    icon: Ic("detail")
  }, /*#__PURE__*/React.createElement("div", {
    className: "form"
  }, F("Seed", /*#__PURE__*/React.createElement(TextField, {
    defaultValue: "0"
  })), F("Steps", /*#__PURE__*/React.createElement(SpinBox, {
    min: 1,
    max: 150,
    defaultValue: 4
  })), F("Width", /*#__PURE__*/React.createElement(SpinBox, {
    min: 64,
    max: 4096,
    defaultValue: 320
  })), F("Height", /*#__PURE__*/React.createElement(SpinBox, {
    min: 64,
    max: 4096,
    defaultValue: 192
  })), F("Guidance", /*#__PURE__*/React.createElement(SpinBox, {
    step: "0.5",
    defaultValue: 1
  })), F("Sampler", /*#__PURE__*/React.createElement(ComboBox, {
    options: ["euler_a", "dpmpp_2m_karras"]
  })))), /*#__PURE__*/React.createElement(SectionCard, {
    title: "Simulation (fake provider)",
    icon: Ic("flask-conical")
  }, /*#__PURE__*/React.createElement("div", {
    className: "form"
  }, F("Outcome", /*#__PURE__*/React.createElement(ComboBox, {
    options: ["Succeeds", "Fails"]
  })), F("Duration", /*#__PURE__*/React.createElement(SpinBox, {
    step: "0.5",
    defaultValue: 0
  })), /*#__PURE__*/React.createElement("span", {
    style: {
      gridColumn: "1/-1"
    }
  }, /*#__PURE__*/React.createElement(Checkbox, {
    label: "Show previews while waiting"
  })))), /*#__PURE__*/React.createElement(SectionCard, {
    title: "Configuration",
    icon: Ic("settings")
  }, /*#__PURE__*/React.createElement("div", {
    className: "stack"
  }, /*#__PURE__*/React.createElement(ComboBox, {
    options: ["Current values"]
  }), /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement(Button, {
    style: {
      flex: 1
    }
  }, "Load"), /*#__PURE__*/React.createElement(Button, {
    style: {
      flex: 1
    }
  }, "Save")), /*#__PURE__*/React.createElement(Label, {
    textRole: "supporting"
  }, "Credential-free versioned configuration only."))))), /*#__PURE__*/React.createElement("div", {
    className: "center",
    style: {
      overflow: "hidden"
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      justifyContent: "space-between"
    }
  }, /*#__PURE__*/React.createElement(Label, {
    textRole: "section",
    style: {
      fontSize: "11pt"
    }
  }, "Prompt"), /*#__PURE__*/React.createElement(Button, {
    checked: big,
    onClick: () => setBig(!big)
  }, "Expand prompt")), /*#__PURE__*/React.createElement(TextArea, {
    rows: 3,
    placeholder: "Describe the image to generate\u2026",
    defaultValue: "an apple",
    style: {
      height: big ? 180 : 76,
      resize: "none"
    }
  }), /*#__PURE__*/React.createElement(TextField, {
    placeholder: "Negative prompt (optional)"
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minHeight: 200,
      background: "var(--color-canvas)",
      border: "1px solid var(--color-button-hover)",
      borderRadius: 8,
      position: "relative",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      flexDirection: "column",
      gap: 10
    }
  }, /*#__PURE__*/React.createElement(FloatingToolbar, {
    position: "top-left"
  }, /*#__PURE__*/React.createElement(Button, {
    disabled: !done
  }, "Fit"), /*#__PURE__*/React.createElement(Button, {
    disabled: !done
  }, "100%"), /*#__PURE__*/React.createElement(Button, {
    disabled: !done
  }, "\u2212"), /*#__PURE__*/React.createElement("input", {
    type: "range",
    className: "dt-slider",
    disabled: !done,
    style: {
      width: 70
    }
  }), /*#__PURE__*/React.createElement(Button, {
    disabled: !done
  }, "+")), /*#__PURE__*/React.createElement(Button, {
    style: {
      position: "absolute",
      top: 10,
      right: 10
    }
  }, "Expand"), done ? /*#__PURE__*/React.createElement("div", {
    style: {
      width: "86%",
      aspectRatio: "320 / 192",
      background: "var(--color-surface)",
      border: "1px dashed var(--color-border-strong)",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      color: "var(--color-secondary)",
      fontSize: "var(--font-size-small)"
    }
  }, "Fixture output \u2014 sample image placeholder") : /*#__PURE__*/React.createElement(EmptyState, {
    logo: "../../assets/dtflow-app.png"
  }, "Your next image starts here", /*#__PURE__*/React.createElement("br", null), "Set a prompt and press Generate."), done ? /*#__PURE__*/React.createElement("span", {
    style: {
      position: "absolute",
      left: 12,
      bottom: 12,
      background: "var(--color-toolbar)",
      borderRadius: 4,
      padding: "4px 7px",
      fontSize: "var(--font-size-small)"
    }
  }, "320 \xD7 192 \xB7 Fixture \xB7 no inference") : null, /*#__PURE__*/React.createElement(FloatingToolbar, {
    position: "bottom-right"
  }, /*#__PURE__*/React.createElement(Button, {
    disabled: !done
  }, "Open file"), /*#__PURE__*/React.createElement(Button, {
    disabled: !done
  }, "Open folder"), /*#__PURE__*/React.createElement(Button, {
    disabled: !done
  }, "Metadata"), /*#__PURE__*/React.createElement(Button, {
    disabled: !done
  }, "Copy path"))), /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement(Button, {
    variant: "primary",
    style: {
      flex: 1,
      minHeight: 36
    },
    onClick: gen,
    disabled: busy
  }, "Generate image"), /*#__PURE__*/React.createElement(Button, {
    style: {
      flex: 1,
      minHeight: 36
    },
    disabled: !busy,
    title: "Stops DTFlow waiting locally. It does not confirm cancellation on the server."
  }, "Stop waiting")), /*#__PURE__*/React.createElement(ProgressBar, {
    value: done ? 1 : 0,
    max: 1,
    label: done ? "Saved" : busy ? "Generating…" : "Idle"
  }), /*#__PURE__*/React.createElement("span", {
    className: done ? "dt-st-ok" : "dt-st-unknown",
    style: {
      fontSize: "var(--font-size-small)",
      fontWeight: 600
    }
  }, done ? "Saved. 1 image with reproducibility metadata. Fixture output; no model inference performed." : "No generation yet.")), insp ? /*#__PURE__*/React.createElement("div", {
    className: "rail"
  }, /*#__PURE__*/React.createElement(Panel, {
    className: "railscroll",
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement(ListBox, {
    label: "LoRAs",
    minHeight: 110,
    style: {
      flex: "none"
    }
  }, loras.map(l => /*#__PURE__*/React.createElement(ListItem, {
    key: l,
    selected: loraSel === l,
    onClick: () => setLoraSel(loraSel === l ? null : l)
  }, l))), /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement(Button, {
    style: {
      flex: 1
    },
    onClick: () => setLoras([...loras, "fake-lora-" + (loras.length + 1) + " @ 0.8"])
  }, "Add LoRA"), /*#__PURE__*/React.createElement(Button, {
    style: {
      flex: 1
    },
    disabled: !loraSel,
    onClick: () => {
      setLoras(loras.filter(x => x !== loraSel));
      setLoraSel(null);
    }
  }, "Remove"), /*#__PURE__*/React.createElement(Button, {
    style: {
      flex: 1
    }
  }, "Check")), /*#__PURE__*/React.createElement(Checkbox, {
    label: "Use LoRA trigger prefixes"
  }), /*#__PURE__*/React.createElement(Label, {
    textRole: "value",
    className: "dt-st-unknown",
    style: {
      fontSize: "var(--font-size-small)"
    }
  }, "Compatibility not checked."), /*#__PURE__*/React.createElement(SectionCard, {
    title: "Cloud assets",
    icon: Ic("cloud")
  }, /*#__PURE__*/React.createElement("div", {
    className: "stack"
  }, /*#__PURE__*/React.createElement("div", {
    className: "host",
    style: {
      minHeight: 90,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      textAlign: "center",
      background: "var(--color-input)"
    }
  }, /*#__PURE__*/React.createElement(Label, {
    textRole: "supporting"
  }, "No local receipts", /*#__PURE__*/React.createElement("br", null), "Prepare a cloud asset to begin.")), /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement(Button, {
    style: {
      flex: 1
    },
    disabled: true
  }, "Prepare\u2026"), /*#__PURE__*/React.createElement(Button, {
    style: {
      flex: 1
    },
    disabled: true
  }, "Reconcile"), /*#__PURE__*/React.createElement(Button, {
    style: {
      flex: 1
    },
    disabled: true
  }, "Use")), /*#__PURE__*/React.createElement(Label, {
    textRole: "supporting",
    style: {
      color: "var(--color-text)"
    }
  }, "Cloud only. Asset actions need the cloud backend (choose it on the Connection page)."), /*#__PURE__*/React.createElement(Label, {
    textRole: "supporting"
  }, "Local registry of receipts, not DT+ account inventory. Only Prepare uploads."))), /*#__PURE__*/React.createElement(Button, {
    checked: det,
    onClick: () => setDet(!det)
  }, "Attempt details"), det ? /*#__PURE__*/React.createElement("div", {
    className: "stack"
  }, /*#__PURE__*/React.createElement("div", {
    className: "host",
    style: {
      minHeight: 50
    }
  }), /*#__PURE__*/React.createElement(Label, {
    textRole: "supporting"
  }, "No durable attempt yet."), /*#__PURE__*/React.createElement("div", {
    className: "host",
    style: {
      padding: 8,
      color: "var(--color-secondary)"
    }
  }, /*#__PURE__*/React.createElement(Label, {
    textRole: "identifier"
  }, "Effective request and resolution evidence"))) : null)) : null), /*#__PURE__*/React.createElement(StatusBar, null));
}
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/generate/GenerateScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/images/DetailView.jsx
try { (() => {
function DetailView({
  i,
  setI,
  size
}) {
  const {
    Button,
    LoraChip,
    FloatingToolbar,
    OverlayChip,
    RoundButton,
    MetadataBlock,
    Thumbnail,
    Label,
    EmptyState
  } = window.DTFlowDesignSystem_cee425;
  const [panel, setPanel] = React.useState(null),
    [fit, setFit] = React.useState("Cover");
  const cur = IMG_FIX[i],
    step = d => setI((i + d + IMG_FIX.length) % IMG_FIX.length);
  const rows = [["ban", "Negative prompt", "Not recorded"], ["dices", "Actual resolved seed", "Not recorded"], ["file-image", "Filename", "portrait.jpg (illustrative fixture)"], ["hard-drive", "Source", "None — offline fixture"], ["weight", "File size", "403 KB (fixture file)"], ["timer", "Recorded duration", "Not recorded"]];
  return /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minWidth: 0,
      display: "flex",
      gap: 8
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "center"
  }, /*#__PURE__*/React.createElement("div", {
    className: "gprev"
  }, /*#__PURE__*/React.createElement(EmptyState, {
    title: "Illustrative fixture"
  }, "No generation or saved output"), /*#__PURE__*/React.createElement("div", {
    style: {
      position: "absolute",
      top: 10,
      left: 10,
      display: "flex"
    }
  }, /*#__PURE__*/React.createElement(OverlayChip, null, i + 1, " / ", IMG_FIX.length), /*#__PURE__*/React.createElement(OverlayChip, null, cur.dim), /*#__PURE__*/React.createElement(OverlayChip, {
    muted: true
  }, "Sample preview")), /*#__PURE__*/React.createElement(RoundButton, {
    direction: "prev",
    onClick: () => step(-1)
  }), /*#__PURE__*/React.createElement(RoundButton, {
    direction: "next",
    onClick: () => step(1)
  }), /*#__PURE__*/React.createElement(FloatingToolbar, {
    position: "bottom-center",
    pill: true
  }, /*#__PURE__*/React.createElement("select", {
    className: "ghost",
    value: fit,
    onChange: e => setFit(e.target.value),
    "aria-label": "View mode"
  }, /*#__PURE__*/React.createElement("option", null, "Cover"), /*#__PURE__*/React.createElement("option", null, "Fit")), /*#__PURE__*/React.createElement("button", {
    className: "ghost"
  }, "\u2212"), /*#__PURE__*/React.createElement("button", {
    className: "ghost"
  }, "100%"), /*#__PURE__*/React.createElement("button", {
    className: "ghost"
  }, "+"), /*#__PURE__*/React.createElement("button", {
    className: "ghost",
    "aria-label": "Fullscreen"
  }, Ic("fullscreen")))), /*#__PURE__*/React.createElement("div", {
    className: "meta"
  }, /*#__PURE__*/React.createElement(MetadataBlock, {
    minWidth: 190,
    icon: Ic("box", 12),
    label: "Model"
  }, /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      gap: 8
    }
  }, /*#__PURE__*/React.createElement("span", {
    className: "mmark"
  }, Ic("box")), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement("b", null, "Illustrative model"), /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Label, {
    textRole: "supporting"
  }, "fixture-model.ckpt"))))), /*#__PURE__*/React.createElement(MetadataBlock, {
    minWidth: 240,
    icon: Ic("layers", 12),
    label: "LoRAs"
  }, /*#__PURE__*/React.createElement(LoraChip, {
    name: "portrait_enhancer_v2",
    weight: "0.8"
  }), /*#__PURE__*/React.createElement(LoraChip, {
    name: "film_photography_xl",
    weight: "0.4"
  })), /*#__PURE__*/React.createElement(MetadataBlock, {
    icon: Ic("repeat", 12),
    label: "Steps"
  }, "30"), /*#__PURE__*/React.createElement(MetadataBlock, {
    icon: Ic("compass", 12),
    label: "CFG"
  }, "7.0"), /*#__PURE__*/React.createElement(MetadataBlock, {
    icon: Ic("waypoints", 12),
    label: "Sampler"
  }, "Not recorded"), /*#__PURE__*/React.createElement(MetadataBlock, {
    icon: Ic("calendar", 12),
    label: "Date"
  }, "Not recorded")), /*#__PURE__*/React.createElement("div", {
    className: "pp"
  }, /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      fontSize: 14,
      fontWeight: 500,
      lineHeight: 1.35
    },
    "aria-label": "Positive prompt"
  }, IMG_PROMPT), /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      alignItems: "flex-start"
    }
  }, [["add", "Additional details", "document"], ["raw", "Raw metadata JSON", "json"]].map(([k, l, n]) => /*#__PURE__*/React.createElement(Button, {
    key: k,
    variant: "disclosure",
    checked: panel === k,
    onClick: () => setPanel(panel === k ? null : k)
  }, Ic(n, 12), l)))), panel === "add" ? /*#__PURE__*/React.createElement("div", {
    className: "pp",
    style: {
      display: "grid",
      gridTemplateColumns: "1fr 1fr",
      gap: "6px 24px",
      fontSize: "var(--font-size-small)"
    }
  }, rows.map(([n, l, v]) => /*#__PURE__*/React.createElement("div", {
    key: l,
    style: {
      display: "flex",
      gap: 10,
      borderBottom: "1px solid var(--color-section-border)",
      paddingBottom: 3
    }
  }, /*#__PURE__*/React.createElement(Label, {
    textRole: "caption",
    style: {
      display: "flex",
      gap: 6,
      width: 150,
      alignItems: "center"
    }
  }, Ic(n, 14), l), /*#__PURE__*/React.createElement("span", null, v))), /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      gridColumn: "1/-1",
      gap: 8
    }
  }, /*#__PURE__*/React.createElement(Button, {
    style: {
      flex: 1
    },
    disabled: true,
    title: "Offline layout preview: no saved output exists yet, so file actions stay disabled."
  }, "Copy path"), /*#__PURE__*/React.createElement(Button, {
    style: {
      flex: 1
    },
    disabled: true,
    title: "Offline layout preview: no saved output exists yet, so file actions stay disabled."
  }, "Open folder"), /*#__PURE__*/React.createElement(Button, {
    style: {
      flex: 1
    },
    disabled: true,
    title: "Offline layout preview: no saved output exists yet, so file actions stay disabled."
  }, "Open in external viewer"))) : panel === "raw" ? /*#__PURE__*/React.createElement("div", {
    className: "pp",
    style: {
      background: "var(--color-canvas)"
    }
  }, /*#__PURE__*/React.createElement(Label, {
    textRole: "identifier"
  }, '{ "model": "fixture-model.ckpt", "steps": 30, "cfg": 7.0 }')) : null), /*#__PURE__*/React.createElement("div", {
    className: "film",
    style: {
      width: Math.round(size * 0.7)
    }
  }, IMG_FIX.map((f, n) => /*#__PURE__*/React.createElement(Thumbnail, {
    key: f.id,
    label: n + 1,
    selected: n === i,
    onClick: () => setI(n)
  }))));
}
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/images/DetailView.jsx", error: String((e && e.message) || e) }); }

// ui_kits/images/GridView.jsx
try { (() => {
function GridView({
  size,
  onOpen
}) {
  const {
    ImageCard
  } = window.DTFlowDesignSystem_cee425;
  return /*#__PURE__*/React.createElement("div", {
    className: "ggrid",
    style: {
      gridTemplateColumns: "repeat(auto-fill,minmax(" + size + "px,1fr))"
    }
  }, IMG_FIX.map((f, n) => /*#__PURE__*/React.createElement(ImageCard, {
    key: f.id,
    badge: f.dim,
    title: "sample" + (n + 1) + ".png · 403 KB",
    subtitle: "SDXL 1.0 (sample)",
    onClick: () => onOpen(n)
  })));
}
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/images/GridView.jsx", error: String((e && e.message) || e) }); }

// ui_kits/images/Header.jsx
try { (() => {
function GalleryHeader({
  mode,
  setMode,
  size,
  setSize
}) {
  const {
    Brand,
    Navigation,
    Button,
    Slider,
    TextField,
    ComboBox,
    ToolbarRow,
    Label
  } = window.DTFlowDesignSystem_cee425;
  const M = [["grid", "Grid", "grid"], ["list", "List", "list"], ["detail", "Detail", "image"]];
  return /*#__PURE__*/React.createElement("div", {
    className: "chrome",
    style: {
      gap: 8
    }
  }, /*#__PURE__*/React.createElement("div", {
    className: "grow"
  }, /*#__PURE__*/React.createElement("div", {
    className: "gslot"
  }, /*#__PURE__*/React.createElement(Brand, {
    logo: "../../assets/dtflow-app.png"
  })), /*#__PURE__*/React.createElement(ToolbarRow, null, M.map(([k, l, ic]) => /*#__PURE__*/React.createElement(Button, {
    key: k,
    variant: "mode",
    checked: mode === k,
    onClick: () => setMode(k)
  }, Ic(ic, 14), l)), /*#__PURE__*/React.createElement(Label, {
    textRole: "caption",
    style: {
      marginLeft: 8
    }
  }, mode === "list" ? "Row density" : mode === "detail" ? "Filmstrip size" : "Thumbnail size"), /*#__PURE__*/React.createElement(Slider, {
    min: 96,
    max: 240,
    value: size,
    onChange: e => setSize(+e.target.value),
    style: {
      flex: 1,
      minWidth: 80
    }
  }), /*#__PURE__*/React.createElement(Label, {
    textRole: "supporting"
  }, size, " px"), /*#__PURE__*/React.createElement(Button, {
    disabled: true,
    title: "Preview only: this application action is not wired.",
    style: {
      background: "var(--color-surface)",
      borderColor: "var(--color-disabled-border)"
    }
  }, "\u2022 Auto-refresh: Off"), /*#__PURE__*/React.createElement(Button, {
    "aria-label": "Refresh",
    style: {
      width: 44,
      height: 34,
      background: "var(--color-surface)",
      borderColor: "var(--color-border)"
    }
  }, Ic("refresh", 20)))), /*#__PURE__*/React.createElement("div", {
    className: "grow"
  }, /*#__PURE__*/React.createElement("div", {
    className: "gslot"
  }, /*#__PURE__*/React.createElement(Navigation, {
    current: "Images",
    onChange: go
  })), /*#__PURE__*/React.createElement(ToolbarRow, null, /*#__PURE__*/React.createElement(TextField, {
    placeholder: "Search positive prompts\u2026",
    "aria-label": "Search positive prompts",
    style: {
      flex: 2
    }
  }), ["All models", "All LoRAs", "All time", "Any ratio"].map(t => /*#__PURE__*/React.createElement(ComboBox, {
    key: t,
    options: [t],
    "aria-label": "Filter: " + t,
    style: {
      flex: 1
    }
  })))));
}
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/images/Header.jsx", error: String((e && e.message) || e) }); }

// ui_kits/images/ImagesScreen.jsx
try { (() => {
function ImagesScreen() {
  const [mode, setMode] = React.useState("detail"),
    [size, setSize] = React.useState(160),
    [i, setI] = React.useState(0);
  const open = n => {
    setI(n);
    setMode("detail");
  };
  return /*#__PURE__*/React.createElement("div", {
    style: {
      height: "100vh",
      display: "flex",
      flexDirection: "column",
      gap: 8,
      padding: 8
    }
  }, /*#__PURE__*/React.createElement(GalleryHeader, {
    mode: mode,
    setMode: setMode,
    size: size,
    setSize: setSize
  }), /*#__PURE__*/React.createElement("div", {
    style: {
      flex: 1,
      minHeight: 0,
      display: "flex"
    }
  }, mode === "grid" ? /*#__PURE__*/React.createElement(GridView, {
    size: size,
    onOpen: open
  }) : mode === "list" ? /*#__PURE__*/React.createElement(ListView, {
    size: size,
    onOpen: open
  }) : /*#__PURE__*/React.createElement(DetailView, {
    i: i,
    setI: setI,
    size: size
  })));
}
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/images/ImagesScreen.jsx", error: String((e && e.message) || e) }); }

// ui_kits/images/ListView.jsx
try { (() => {
function ListView({
  size,
  onOpen
}) {
  const {
    DataTable
  } = window.DTFlowDesignSystem_cee425;
  const th = Math.round(size / 4);
  const thumb = f => /*#__PURE__*/React.createElement("div", {
    className: "gtile",
    style: {
      width: Math.round(th * 1.4),
      height: th,
      borderRadius: 4,
      fontSize: 8
    }
  }, f.dim);
  const cols = [{
    key: "i",
    label: "Image"
  }, {
    key: "f",
    label: "Filename"
  }, {
    key: "m",
    label: "Model"
  }, {
    key: "l",
    label: "LoRAs / weights / status"
  }, {
    key: "d",
    label: "Dimensions"
  }, {
    key: "z",
    label: "File size"
  }, {
    key: "t",
    label: "Date / time"
  }, {
    key: "p",
    label: "Positive prompt"
  }];
  const rows = IMG_FIX.map((f, n) => ({
    i: thumb(f),
    f: "sample" + (n + 1) + ".png",
    m: "SDXL 1.0 (sample)",
    l: "portrait_enhancer_v2 · 0.8 · Unknown; film_photography_xl · 0.4 · Unknown",
    d: f.dim,
    z: "403 KB",
    t: "Not recorded",
    p: "Illustrative sample artwork; no recorded generation request."
  }));
  return /*#__PURE__*/React.createElement("div", {
    className: "host",
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement(DataTable, {
    variant: "gallery",
    columns: cols,
    rows: rows,
    onSelect: onOpen
  }));
}
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/images/ListView.jsx", error: String((e && e.message) || e) }); }

// ui_kits/images/data.js
try { (() => {
window.IMG_FIX = [{
  id: 1,
  r: "2 / 3",
  dim: "832 × 1216"
}, {
  id: 2,
  r: "1 / 1",
  dim: "1024 × 1024"
}, {
  id: 3,
  r: "16 / 9",
  dim: "1344 × 756"
}, {
  id: 4,
  r: "2 / 3",
  dim: "832 × 1216"
}, {
  id: 5,
  r: "4 / 3",
  dim: "1152 × 864"
}, {
  id: 6,
  r: "1 / 1",
  dim: "1024 × 1024"
}];
window.IMG_PROMPT = "A quiet harbour at dawn, soft mist over the water, film grain, muted palette";
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/images/data.js", error: String((e && e.message) || e) }); }

// ui_kits/queues/QueuesScreen.jsx
try { (() => {
function QueuesScreen() {
  const {
    SectionCard,
    ListItem,
    SpinBox,
    Button,
    Badge,
    ComboBox,
    TextField,
    TextArea,
    Checkbox,
    DataTable,
    ProgressBar,
    Label,
    ListBox,
    EmptyState,
    Panel,
    FloatingToolbar
  } = window.DTFlowDesignSystem_cee425;
  const insp = useInspector();
  const [qs, setQs] = React.useState([{
    id: "22762d11",
    be: "cloud",
    st: "finished",
    done: 1,
    total: 1
  }, {
    id: "9d0020ea",
    be: "fake",
    st: "finished",
    done: 5,
    total: 5
  }, {
    id: "2d58df38",
    be: "fake",
    st: "paused",
    done: 0,
    total: 1
  }]);
  const [sel, setSel] = React.useState("2d58df38"),
    [count, setCount] = React.useState(1),
    [plan, setPlan] = React.useState(false);
  const q = qs.find(x => x.id === sel);
  const set = st => setQs(qs.map(x => x.id === sel ? {
    ...x,
    st
  } : x));
  const create = () => {
    const id = Math.random().toString(16).slice(2, 10);
    setQs([...qs, {
      id,
      be: "fake",
      st: "paused",
      done: 0,
      total: plan ? 1 : count
    }]);
    setSel(id);
  };
  const tone = !q ? "unknown" : q.st === "running" ? "ok" : q.st === "paused" ? "warning" : "unknown";
  const msg = !q ? "Create a queue from the Generate form, then Start it here." : q.st === "paused" ? "Paused. Nothing dispatches until Start." : q.st === "running" ? "Running. Dispatching items." : "Finished.";
  const rows = q ? Array.from({
    length: q.total
  }, (_, i) => ({
    n: /*#__PURE__*/React.createElement("span", {
      style: {
        display: "block",
        textAlign: "right"
      }
    }, i + 1),
    i: "d8293ddf",
    sd: 3567690377 + i,
    s: i < q.done ? "succeeded" : "pending",
    a: "—",
    o: "—"
  })) : [];
  const cols = [{
    key: "n",
    label: "#"
  }, {
    key: "i",
    label: "Item"
  }, {
    key: "sd",
    label: "Seed"
  }, {
    key: "s",
    label: "State"
  }, {
    key: "a",
    label: "Attempt"
  }, {
    key: "o",
    label: "Outputs"
  }];
  return /*#__PURE__*/React.createElement(React.Fragment, null, /*#__PURE__*/React.createElement("div", {
    className: "shell"
  }, /*#__PURE__*/React.createElement("div", {
    className: "rail"
  }, /*#__PURE__*/React.createElement(Chrome, {
    tab: "Queues",
    conn: "unknown",
    onTab: go
  }), /*#__PURE__*/React.createElement(Panel, {
    eyebrow: "DURABLE QUEUES",
    className: "railscroll",
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement(ListBox, {
    label: "Durable queues",
    minHeight: 200,
    style: {
      flex: 1
    }
  }, qs.map(x => /*#__PURE__*/React.createElement(ListItem, {
    key: x.id,
    selected: sel === x.id,
    onClick: () => setSel(x.id)
  }, x.id, " \xB7 ", x.be, " \xB7 ", x.st, " \xB7 ", x.done, "/", x.total))), /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      justifyContent: "space-between"
    }
  }, /*#__PURE__*/React.createElement(Label, {
    textRole: "caption"
  }, "Items"), /*#__PURE__*/React.createElement(SpinBox, {
    min: 1,
    max: 1000,
    value: plan ? 1 : count,
    disabled: plan,
    onChange: e => setCount(+e.target.value),
    style: {
      width: 106
    },
    title: "Item N uses the form's seed + N - 1."
  })), /*#__PURE__*/React.createElement(Button, {
    variant: "primary",
    style: {
      minHeight: 36
    },
    onClick: create,
    title: "Creates a paused queue from the Generate form. Contacts no backend and uploads nothing."
  }, plan ? "Create queue from plan" : "Create queue"), /*#__PURE__*/React.createElement(Button, {
    style: {
      minHeight: 36
    }
  }, "Refresh"), /*#__PURE__*/React.createElement(SectionCard, {
    title: "Dispatch policy",
    icon: Ic("send")
  }, /*#__PURE__*/React.createElement("div", {
    className: "form"
  }, /*#__PURE__*/React.createElement(Label, {
    textRole: "caption"
  }, "Cloud max"), /*#__PURE__*/React.createElement(SpinBox, {
    min: 1,
    max: 4,
    defaultValue: 2
  }), /*#__PURE__*/React.createElement(Label, {
    textRole: "supporting",
    style: {
      gridColumn: "1/-1"
    }
  }, "Local Draw Things remains one effective lane. This is a client policy, not entitlement, and the memory budget currently admits about two."))))), /*#__PURE__*/React.createElement("div", {
    className: "center",
    style: {
      padding: "0 4px"
    }
  }, /*#__PURE__*/React.createElement("div", null, /*#__PURE__*/React.createElement(Badge, null, "NOT DISPATCHING")), /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement(Button, {
    disabled: !q || q.st === "running" || q.st === "finished",
    onClick: () => set("running")
  }, "Start"), /*#__PURE__*/React.createElement(Button, {
    disabled: !q || q.st !== "running",
    onClick: () => set("paused")
  }, "Pause"), /*#__PURE__*/React.createElement(Button, {
    disabled: !q || q.st !== "paused",
    onClick: () => set("running")
  }, "Resume"), /*#__PURE__*/React.createElement(Button, {
    variant: "danger",
    disabled: !q || q.st === "finished",
    onClick: () => set("cancelled")
  }, "Cancel"), /*#__PURE__*/React.createElement("span", {
    className: "val dt-st-" + tone,
    style: {
      marginLeft: "auto"
    }
  }, q ? msg : "")), /*#__PURE__*/React.createElement(Label, {
    textRole: "supporting"
  }, "Create a queue from the Generate form, then Start it here."), /*#__PURE__*/React.createElement(SectionCard, {
    title: "Plan batch (pair or cross)",
    icon: Ic("layers"),
    checkable: true,
    checked: plan,
    onCheckedChange: setPlan
  }, /*#__PURE__*/React.createElement("div", {
    className: "stack"
  }, /*#__PURE__*/React.createElement("div", {
    className: "form"
  }, /*#__PURE__*/React.createElement(Label, {
    textRole: "caption"
  }, "Mode"), /*#__PURE__*/React.createElement(ComboBox, {
    options: ["Cross", "Pair"]
  }), /*#__PURE__*/React.createElement(Label, {
    textRole: "caption"
  }, "Prompts"), /*#__PURE__*/React.createElement(TextArea, {
    rows: 2,
    placeholder: "One prompt per line"
  }), /*#__PURE__*/React.createElement(Label, {
    textRole: "caption"
  }, "Models"), /*#__PURE__*/React.createElement(TextField, {
    placeholder: "e.g. model-a.ckpt; model-b.ckpt"
  }), /*#__PURE__*/React.createElement(Label, {
    textRole: "caption"
  }, "Seeds"), /*#__PURE__*/React.createElement(TextField, {
    placeholder: "e.g. 1; 2; 10..20"
  }), /*#__PURE__*/React.createElement(Label, {
    textRole: "caption"
  }, "Steps"), /*#__PURE__*/React.createElement(TextField, {
    placeholder: "e.g. 20; 30"
  }), /*#__PURE__*/React.createElement(Label, {
    textRole: "caption"
  }, "Guidance"), /*#__PURE__*/React.createElement(TextField, {
    placeholder: "e.g. 4.5; 7"
  }), /*#__PURE__*/React.createElement(Label, {
    textRole: "caption"
  }, "Samplers"), /*#__PURE__*/React.createElement(TextField, {
    placeholder: "e.g. euler_a; dpmpp_2m_karras"
  }), /*#__PURE__*/React.createElement(Label, {
    textRole: "caption"
  }, "Sizes"), /*#__PURE__*/React.createElement(TextField, {
    placeholder: "e.g. 512x512; 768x512"
  }), /*#__PURE__*/React.createElement(Label, {
    textRole: "caption"
  }, "LoRA weights"), /*#__PURE__*/React.createElement(TextArea, {
    rows: 2,
    placeholder: "IDENTIFIER: 0.6; 1.0 (one LoRA per line)"
  })), /*#__PURE__*/React.createElement(Checkbox, {
    label: "Increment the seed per item (otherwise the base seed is kept)"
  }), /*#__PURE__*/React.createElement("div", {
    className: "row"
  }, /*#__PURE__*/React.createElement(Button, {
    style: {
      minWidth: 160
    }
  }, "Dry run"), /*#__PURE__*/React.createElement(Label, {
    textRole: "supporting",
    style: {
      color: "var(--color-text)"
    }
  }, "A cross plan needs at least one dimension.")), /*#__PURE__*/React.createElement("div", {
    className: "host",
    style: {
      minHeight: 120
    }
  }))), q ? /*#__PURE__*/React.createElement("div", {
    className: "host",
    style: {
      flex: 1,
      background: "var(--color-input)",
      border: "1px solid var(--color-border)"
    }
  }, /*#__PURE__*/React.createElement(DataTable, {
    columns: cols,
    rows: rows,
    selectedIndex: 0
  })) : /*#__PURE__*/React.createElement("div", {
    className: "host",
    style: {
      flex: 1,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      flexDirection: "column",
      gap: 8
    }
  }, /*#__PURE__*/React.createElement(Label, {
    textRole: "section"
  }, "No queue selected"), /*#__PURE__*/React.createElement(Label, {
    textRole: "supporting"
  }, "Select a queue from the left, or create one from the Generate form.")), q ? /*#__PURE__*/React.createElement(ProgressBar, {
    value: q.done,
    max: q.total,
    label: q.done + " of " + q.total + " settled · " + q.done + " succeeded",
    "aria-label": "Queue progress"
  }) : null), insp ? /*#__PURE__*/React.createElement("div", {
    className: "rail"
  }, /*#__PURE__*/React.createElement(Panel, {
    eyebrow: "ITEM EVIDENCE",
    style: {
      flex: 1
    }
  }, /*#__PURE__*/React.createElement(SectionCard, {
    title: "Selected item",
    icon: Ic("list-checks")
  }, /*#__PURE__*/React.createElement("div", {
    className: "form"
  }, /*#__PURE__*/React.createElement(Label, {
    textRole: "caption"
  }, "Item"), /*#__PURE__*/React.createElement(Label, {
    textRole: "identifier"
  }, q ? "d8293ddf" : "—"), /*#__PURE__*/React.createElement(Label, {
    textRole: "caption"
  }, "Attempt"), /*#__PURE__*/React.createElement("span", null, "\u2014"), /*#__PURE__*/React.createElement(Label, {
    textRole: "caption"
  }, "Backend"), /*#__PURE__*/React.createElement(Label, {
    textRole: "value"
  }, q ? q.be : "—"), /*#__PURE__*/React.createElement(Label, {
    textRole: "caption"
  }, "Outputs"), /*#__PURE__*/React.createElement("div", {
    className: "row",
    style: {
      justifyContent: "space-between"
    }
  }, /*#__PURE__*/React.createElement("span", null, "\u2014"), /*#__PURE__*/React.createElement(Button, {
    disabled: true,
    style: {
      minWidth: 110
    }
  }, "Open folder")))), /*#__PURE__*/React.createElement(SectionCard, {
    title: "Uncertainty",
    icon: Ic("triangle-alert")
  }, /*#__PURE__*/React.createElement("div", {
    className: "stack"
  }, /*#__PURE__*/React.createElement("span", {
    className: "dt-st-ok",
    style: {
      fontSize: "var(--font-size-small)",
      color: "var(--color-success)"
    }
  }, "No unresolved reservation for this item."), /*#__PURE__*/React.createElement(Button, {
    disabled: true
  }, "Acknowledge and override"))), /*#__PURE__*/React.createElement("div", {
    className: "host",
    style: {
      padding: 10,
      flex: 1,
      minHeight: 120,
      background: "var(--color-input)",
      border: "1px solid var(--color-border)"
    }
  }, /*#__PURE__*/React.createElement(Label, {
    textRole: "identifier"
  }, "03:37:13 \xA0created, items 1, cap 2")))) : null), /*#__PURE__*/React.createElement(StatusBar, null));
}
})(); } catch (e) { __ds_ns.__errors.push({ path: "ui_kits/queues/QueuesScreen.jsx", error: String((e && e.message) || e) }); }

__ds_ns.Button = __ds_scope.Button;

__ds_ns.Checkbox = __ds_scope.Checkbox;

__ds_ns.ComboBox = __ds_scope.ComboBox;

__ds_ns.ConnectionButton = __ds_scope.ConnectionButton;

__ds_ns.Slider = __ds_scope.Slider;

__ds_ns.SpinBox = __ds_scope.SpinBox;

__ds_ns.Tabs = __ds_scope.Tabs;

__ds_ns.TextArea = __ds_scope.TextArea;

__ds_ns.TextField = __ds_scope.TextField;

__ds_ns.DataTable = __ds_scope.DataTable;

__ds_ns.ListBox = __ds_scope.ListBox;

__ds_ns.ListItem = __ds_scope.ListItem;

__ds_ns.LoraChip = __ds_scope.LoraChip;

__ds_ns.Badge = __ds_scope.Badge;

__ds_ns.EmptyState = __ds_scope.EmptyState;

__ds_ns.ProgressBar = __ds_scope.ProgressBar;

__ds_ns.StatusCard = __ds_scope.StatusCard;

__ds_ns.Tooltip = __ds_scope.Tooltip;

__ds_ns.FloatingToolbar = __ds_scope.FloatingToolbar;

__ds_ns.ImageCard = __ds_scope.ImageCard;

__ds_ns.MetadataBlock = __ds_scope.MetadataBlock;

__ds_ns.OverlayChip = __ds_scope.OverlayChip;

__ds_ns.RoundButton = __ds_scope.RoundButton;

__ds_ns.Thumbnail = __ds_scope.Thumbnail;

__ds_ns.ToolbarRow = __ds_scope.ToolbarRow;

__ds_ns.Icon = __ds_scope.Icon;

__ds_ns.Panel = __ds_scope.Panel;

__ds_ns.SectionCard = __ds_scope.SectionCard;

__ds_ns.StatusBar = __ds_scope.StatusBar;

__ds_ns.Brand = __ds_scope.Brand;

__ds_ns.MenuBar = __ds_scope.MenuBar;

__ds_ns.Navigation = __ds_scope.Navigation;

__ds_ns.Label = __ds_scope.Label;

})();
