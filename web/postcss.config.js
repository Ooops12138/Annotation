// Tailwind is configured in tailwind.config.js; keep PostCSS lightweight for the
// Node 20 demo environment. The CSS utility classes used by the fixture are
// also backed by the component-level styles in src/style.css.
export default { plugins: { autoprefixer: {} } }
