Icon by name: the app's own SVGs first, the two glyphs painted in code (panel, connection), and flagged Lucide substitutes for everything else.

```jsx
<Icon name="grid" size={14} />
<Icon name="server" />            // Lucide (CDN)
<Icon name="connection" state="ok" size={24} />
```

Sizes: 12px in metadata labels, 14px inside mode buttons, 16px in section headers, 20px for the refresh button. Colour is built in (light on dark); do not tint with CSS.
