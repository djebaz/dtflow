Titled group card for rails and inspectors (Qt `sectionCard` + `sectionHeader` + `sectionContents`); use instead of native group-box titles.

```jsx
<SectionCard title="Dispatch policy"><label>Cloud max</label></SectionCard>
<SectionCard title="Plan" checkable checked={on} onCheckedChange={setOn}>…</SectionCard>
```

Variants: `checkable` (header checkbox collapses contents, like `planBoxToggle`), `disabled` (muted border + heading), `icon` (16px slot; no icon assets were supplied, so omitted by default).
