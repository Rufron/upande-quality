# Quality v2 — page contract

Every Quality dashboard www page is being rebuilt as `<page>-v2` on one shared design
system. The original pages stay untouched until the v2 versions are signed off;
then the `-v2` suffix is dropped and the old page replaced.

Design source: the Upande Universal Dashboard System
(`Designs/ufd-modern-index.html` + its five dashboards). Black ink on warm
off-white, borderless large cards, black pill tabs and toggles, Poppins.

Reference: `www/quality-v2-foundation-check.html` (+ `www/quality_v2_foundation_check.py`) shows the shell, a filter bar, KPIs, a table and a modal.
The Packhouse v2 pages (`upande_packhouse/www/*-v2.html`, e.g. `stock-visibility-v2.html`) are the richer examples; same classes, same `window.PH`.

## Files

| File | What it is |
|---|---|
| `public/css/quality-v2.css` | The design system. All tokens and components. |
| `public/js/quality-v2.js` | `window.PH` runtime: calls, formatting, filters, tables, modals, charts. |
| `templates/includes/quality_v2/shell_start.html` / `shell_end.html` | Sidebar + topbar + page wrapper. |
| `quality_v2.py` | `page_context()` for controllers; the sidebar `NAV` list. |

Shared files are owned centrally. Page work never edits them — if a page needs
a new shared component, note it and ask.

## Page skeleton

`www/<page>-v2.html`

```jinja
{% extends "templates/web.html" %}
{% block title %}<Title> · Quality{% endblock %}
{% block page_content %}
{% include "upande_quality/templates/includes/quality_v2/shell_start.html" %}

<header class="ph-head">
  <div>
    <div class="ph-head__eyebrow">Quality · <Section></div>
    <h1 class="ph-head__title"><Title></h1>
    <p class="ph-head__sub" id="sub"><one line></p>
  </div>
  <div class="ph-head__tools"><!-- primary actions only (New, Export) --></div>
</header>

<div class="ph-filters"> … </div>
<section class="ph-kpis" id="kpis"></section>
<section class="ph-card"> … </section>

{% include "upande_quality/templates/includes/quality_v2/shell_end.html" %}
{% raw %}
<script>
(function () { "use strict"; /* page code, using PH.* */ })();
</script>
{% endraw %}
{% endblock %}
```

`www/<page_underscored>_v2.py`

```python
from upande_quality.quality_v2 import page_context

no_cache = 1


def get_context(context):
	return page_context(context, "<page key>", "<Title>")
```

`page_context` also sends signed-out visitors to `/login` and back.
`<page key>` is the page's route without `-v2` (e.g. `coldroom-overview`; it must be a key in `NAV`); it marks the
active sidebar entry. Set `context.ph_wide = 1` for full-width grids/planners.

## Layout order (every page)

1. `.ph-head` — eyebrow, title, one-line sub, optional primary actions.
2. `.ph-filters` — left to right: scope pills · selects · search · *(spacer)* · date presets · date range.
3. `.ph-tabs` — only if the page has sections.
4. `.ph-kpis` — 3–5 headline numbers.
5. Content: `.ph-card`s, `.ph-row--2-1 / --2 / --3` grids, `.ph-tiles`, tables.

Refresh lives in the topbar (`PH.onRefresh(fn)`), with `PH.markUpdated()` after
each successful load. Never add a second refresh button.

## Components (all in quality-v2.css)

- Head: `.ph-head`, `__eyebrow`, `__title`, `__sub`, `__tools`
- Filters: `.ph-filters`, `__spacer`, `.ph-pills > button[data-value]`, `.ph-select`,
  `.ph-search > svg + input`, `.ph-daterange > input[type=date] + .ph-daterange__sep + input`, `.ph-filter-chip`
- Buttons: `.ph-btn` (`--primary --ghost --danger --sm --block`), `.ph-iconbtn` (`--flat --sm`), `.ph-link`
- Tabs: `.ph-tabs > .ph-tab(.is-on)` with optional `.ph-tab__count`
- KPIs: `.ph-kpis > .ph-kpi` — render with `PH.kpis()`
- Cards: `.ph-card` (`--flush` for edge-to-edge tables), `__head`, `__title`, `__meta`, `__tools`
- Grids: `.ph-row.ph-row--2-1 | --1-2 | --2 | --3`
- Tiles: `.ph-tiles(--sm) > .ph-tile(.is-link)`, `__head __name __sub __tags __stats`
- Lists: `.ph-list > .ph-list__row`, `__rank(.lead) __name __meta __qty`; `.ph-list--plain`
- Key/values: `dl.ph-kv > dt + dd`; stat strip `.ph-stats > .ph-stat > small + b`
- Badges: `.ph-badge` (`--signal --good --warn --bad --ink --outline`), `.ph-dot(--good…)`
- Progress: `.ph-progress > i(.good|.warn|.bad)`, `.ph-progress-row`; bars `.ph-hb`; `.ph-funnel`
- Tables: build with `PH.table()`; raw markup `.ph-table-wrap > table.ph-table`, cells `.is-num .is-strong .is-wrap`, `.ph-table-input`
- Forms: `.ph-form(--1|--2) > .ph-field > label + .ph-control`, `.ph-check`, `.ph-switch`
- States: `PH.skeleton()`, `PH.empty()`, `PH.error()`, `.ph-notice(--warn|--bad|--good|--signal)`
- Overlays: `PH.modal()` (also `drawer:true`), `PH.confirm()`, `PH.menu()`, `PH.toast()`
- Charts: `PH.chart()` in a `.ph-chart` holder; `.ph-legend` (`i` = line key, `i.is-bar` = block key)
- Detail pages: `.ph-backbtn`, `.ph-hero` (`__eyebrow __name __sub __stats`)
- Helpers: `.ph-flex(--wrap|--between) .ph-stack .ph-grow .ph-muted .ph-strong .ph-mono .ph-num .ph-truncate .ph-mt .ph-mb .ph-mb-0 .ph-divider`

## PH runtime (quality-v2.js)

```js
PH.call(method, args, {silent, type}) -> Promise<message>   // toasts + rejects on error / {success:false}
PH.getList(doctype, {fields, filters, order_by, limit})
const st = PH.state({from, to, team:'', q:''})  // URL-synced; st.get(k) st.set({..}) st.on((s, changedKeys)=>…)
PH.pills('#el', st, 'key')   PH.bindSelect('#el', st, 'key')   PH.bindSearch('#input', st, 'key')
PH.dateRange({pills:'#presets', from:'#from', to:'#to', state:st})   // presets: today yesterday tomorrow 7d 14d 30d next7 mtd
PH.options('#select', items|[{value,label}], selected)
PH.kpiSkeleton('#kpis', labels)  PH.kpis('#kpis', [{label, value, unit, suffix, trend:{dir,text,note}, bar, onClick, active, dark}])
PH.table('#el', {columns:[{key,label,num,format,html,total,sortable,cls,width}], rows, sort, onRowClick, rowClass, pageSize, tall, compact, empty, emptyText}) -> {update(rows)}
PH.modal({title, sub, body, size:'lg'|'xl', drawer, actions:[{label, primary, danger, onClick}]})  PH.confirm(title, text, {danger, ok})
PH.autocomplete(input, {source: PH.linkSource('Customer'), onSelect, display})  // display(item) = text shown after a pick
PH.chart('#holder', {type:'line'|'bar'|'doughnut', labels, series:[{label,data,color}], stacked, horizontal, onClick})
PH.fmt.num/qty/compact/pct/money/date/datetime/time/ago/plural   PH.date.today/add/parse/preset/label
PH.esc  PH.html`…${x}…` (auto-escaped)  PH.raw  PH.set(el, html)  PH.text(sel, v)  PH.icon(name)
PH.onRefresh(fn)  PH.markUpdated()  PH.poll(fn, ms)  PH.keepScroll(fn)  PH.csv(name, rows, cols)  PH.openDoc(doctype, name)
```

## Rules

1. **No page-level design.** No hex colours, shadows, radii, font sizes or
   font families in a page. A page `<style>` block is allowed only for layout
   that is genuinely unique to the page (a planner grid, a tree), uses only
   `var(--…)` tokens and the `--sp-*` scale, and prefixes its classes with a
   page abbreviation (e.g. `.cr-…` for cold room).
2. **Filters must work.** Every filter lives in `PH.state` (so it is in the
   URL), and every one either goes to the server as an argument the Python
   method actually reads, or filters rows client-side. Check each against the
   method's signature. A filter that does nothing does not ship.
3. **One request per filter change**, with a sequence guard so a slow old
   response never overwrites a newer one (see the reference page's `seq`).
4. **Every async area has three states:** skeleton while loading, empty with a
   helpful sentence, error with retry. Background polls (`load(true)`) never
   show skeletons and keep scroll (`PH.keepScroll`).
5. **Escape all server text** (`PH.esc` / `PH.html`). No inline `onclick=`
   attributes — use delegated listeners and `data-*` attributes.
6. **Same API.** v2 pages call the existing whitelisted methods with the same
   arguments; they do not change Python APIs. If an API forces a bad UX, note it.
7. **Feature parity.** Everything the original page could do, v2 can do —
   actions, dialogs, exports, deep links (`?param=`). Drop only dead code.
8. Original pages and shared files are not edited.

## Quality v2 specifics

### Pages, keys and routes

Every page is `www/<key>-v2.html` + `www/<key_underscored>_v2.py`, served at `/quality-dashboard-v2/<key>-v2`
(`hooks.website_route_rules`). `/quality-dashboard-v2` redirects to Intake FTR Overview. `NAV` (in `quality_v2.py`) holds:

| Page key | Title | Group |
|---|---|---|
| `intake-ftr-overview` | Intake FTR Overview | Overview |
| `coldroom-overview` | Coldroom Operations | Overview |
| `packhouse-overview` | Packhouse Overview | Overview |
| `shelving-report` | Shelving Report | Operations |
| `transit-time` | Transit Time | Operations |
| `solution-mixing-overview` | Solution Mixing | Operations |
| `quality-trends` | Quality Trends | Insights |
| `quality-raw-data` | Quality Raw Data | Insights |
| `rejects` | Rejects | Exceptions |
| `discard-requests` | Discard Requests | Exceptions |

The shell includes are `upande_quality/templates/includes/quality_v2/shell_start.html` / `shell_end.html`.
Pages load the css/js only through the shell (nothing is in `web_include_*`). The runtime's in-place sidebar
navigation works under `/quality-dashboard-v2`.

### Quality v2 rules

1. **Charts are never changed.** Existing Chart.js blocks, configs and colours move verbatim into v2 cards
   (a `.ph-chart` holder with the same `<canvas>` and the same `new Chart(...)` call). Load Chart.js as the old page does if
   the page keeps raw Chart.js; do not port them to `PH.chart`.
2. **Every existing filter is kept, and more are added.** Check the old page's filters one by one.
3. **Filters are multi-select, and multi-site where the data has a site/farm** (`PH.multiselect`, Site/Farm grouped,
   `multiSite:true`). Send `st.get(key)` (comma-separated) to the server, or filter client-side with `PH.values()`.
4. **No backend changes** unless a page truly needs one; say so in the page notes when it does.
5. **Old pages stay untouched.** v2 pages are new files only.
6. Page code lives in a `{% raw %}` block; the controller sets `no_cache = 1` (page_context does).
7. Everything from the rules above applies (no page-level design, filters in `PH.state`, three states, escape text).

### PH.multiselect (new in quality-v2.js)

```js
const m = PH.multiselect(container, st, key, {
  label: 'Farm',                 // prefix on the button: "Farm: 3 farms"
  options: [ {group:'Kapkolia', items:['Kapkolia','Torongo']}, {group:'Karen', items:['Karen']} ],
  // or flat ['A','B'] / [{value,label}], or 'regions' (= PH.regionGroups(): site > farm from PH.REGIONS)
  multiSite: true,               // group headings get a tri-state checkbox that picks/clears the whole site
  search: true,                  // default: shown when more than 8 options
  all: 'All farms', plural: 'farms',
  onChange: (vals) => {}         // optional; st.on(...) fires anyway
});
m.setOptions(list);              // options that arrive later from the server (same shapes)
m.values(); m.set(['Karen']); m.clear(); m.open(); m.close();
```

- Container: an empty `<div id="f-farm"></div>` in `.ph-filters`. State key default must be `""` (= nothing picked = all).
- The state value is a comma-separated list in the URL (`?farm=Karen,Torongo`); option values must not contain commas.
  `PH.values(str)` splits it into an array.
- Popover: search, checkboxes, Select all (what the search shows) / Clear, outside-click and Esc close. Picks in the URL
  that are not in the options (yet) still show as checked.
- `PH.multiSelect` (capital S) is the older flat list without search; prefer `PH.multiselect`.

### PH.activeFilters (new)

```js
PH.activeFilters('#chips', st, [
  {key:'farm', label:'Farm', multi:true},       // one chip per picked value
  {key:'q', label:'Search'},                     // single value; x resets it to the default
  {key:'from', label:'From', text: PH.fmt.date}  // optional text(value)
]);
```
Place `<div id="chips"></div>` right under `.ph-filters`. It hides itself when nothing is active and adds "Clear all".

### Adding a page

1. Create `www/<key>-v2.html` (skeleton above, include `upande_quality/templates/includes/quality_v2/shell_*.html`) and `www/<key_underscored>_v2.py` calling `page_context(context, "<key>", "<Title>")`.
2. The key must already be in `NAV` and `hooks.website_route_rules` (all ten are). A new page needs entries in both.
3. `bench --site <site> clear-cache`; hard refresh. Routes are read from hooks at cache build.
