# Writing a clip

A clip is a small HTML fragment in `motion/clips/NN-name.html`. `morph.mjs build` wraps
it with the engine, the brand's colours and fonts, and a cursor into one self-contained
page, where `draw(t)` paints the frame at time `t`. **Every style is a pure function of
`t`:** no CSS transitions, no `setTimeout`, no state carried from frame to frame. That is
what makes frame-by-frame rendering with motion blur possible. Never keep a counter in
`update(t)` that does not come from `t`.

## Fragment skeleton

```html
<title>04 Master prompt</title>
<style>/* clip CSS — use brand tokens: var(--accent), var(--ink), var(--shape) … */</style>

<div data-slot="world"> <!-- optional: elements beside the shape (a drop target) --> </div><!--/world-->
<div data-slot="shape">
  <!-- one layer per state; .L layers are anchored inside the shape -->
  <div class="L" id="Lnotes"> … </div>
  <div class="L" id="Ldoc"> … </div>
</div><!--/shape-->

<script>
const B = M.brand;                // brand colours: B.canvas, B.shape, B.surface, B.ink, B.muted, B.line, B.accent, B.success, B.error
M.scene({
  W:1920, H:1080, T:10,           // frame size (see placements) and length in seconds
  // background: omit = brand canvas; null = transparent (panel → .mov); or any colour
  center:[960,540],               // screen point the camera centres on; omit it to centre (and fit) in the area
                                  // left free by layout.protect — set it for panels
  intro:0.15,                     // when the shape pops in; null = already on screen
  shapes:{                        // the states the ONE shape morphs through
    notes:{w:780,h:560,r:36,fill:B.shape,zoom:1.3},
    doc:{w:600,h:720,r:28,fill:B.shape,zoom:1.2},
  },
  start:'notes', flow:[[5.1,'doc']],             // [clip-local time, state] — each on its word
  layers:[
    {el:'Lnotes', in:0.15, out:5.1, anchor:'t'},  // anchor: 'c' centre (default), 't' top edge, 'l' left edge
    {el:'Ldoc', in:5.1, out:null, anchor:'t', update:(t,g)=>{ /* per-frame content: typing, bars */ }},
  ],
  cursor:{size:44, clicks:[2.3], drags:[[6.9,7.8]], keys:[[0,760,460],[1.2,760,460],[1.9,330,250]]},
  geom:(t,g)=>g,    // optional: change geometry (a dragged shape g.cx/g.cy, camera focus g.fx/g.fy)
  extra:(t,g)=>{},  // optional: per-frame work outside layers
});
</script>
```

Slots are matched literally: `<div data-slot="shape">` … `</div><!--/shape-->`.

## Frame size by placement

| placement | W×H | background | output |
|---|---|---|---|
| `full` | the video's size (e.g. 1920×1080) | brand canvas | .mp4 |
| `region` | `layout.region` size rounded down to even (1490×801 → 1490×800), `center` = half of it | brand canvas | .mp4, scaled into the region |
| `panel` | the video's size | `null` | ProRes 4444 .mov with alpha |

Coordinates are **world pixels**. The shape is centred at world (0,0) unless `geom` moves
it. The camera maps world to screen as `screen = center + zoom × (world − focus)`. Pick
`zoom` per state so the state fills the frame: about 1.2–1.7 for cards, 1.8–2.2 for
pills; keep `h × zoom` well under H. The cursor keeps a constant size on screen.

Layer content is positioned from its anchor: in an `anchor:'t'` layer, `left:-330px;
top:40px` means 330 px left of centre, 40 px below the shape's top edge. Top-anchored
content rides the top edge as the shape grows.

## Engine API (`window.M`)

| Call | What it does |
|---|---|
| `M.track(v0, [[t, value, spring?], …])` | A value that changes target many times: the sum of one closed-form spring per change. Returns `t => value`. |
| `M.colorTrack('#hex', [[t,'#hex'], …])` | The same for colours. Returns `t => 'rgb(...)'`. |
| `M.SPRING.MORPH / FAST / SLOW / SOFT / CAMERA / INSTANT` | `[stiffness, damping]`. FAST for leading edges, SLOW for trailing ones, INSTANT for invisible resets. |
| `M.spring(tau, w, z)` | One spring step response 0→1 (grow items one by one). |
| `M.show(t, in, out, {delay, enter, exit, blur})` | Content swap: exit blurs out fast, entry waits `delay` then sharpens. Returns `{o, blur, s, a, b}`. |
| `M.apply(el, v)` | Applies a `show` result (opacity, blur, scale); returns `false` when hidden. |
| `M.path([[t,x,y], …])` | Cursor or any point path, eased between keys with a slight human arc. |
| `M.crossings(f, t0, t1, thresholds)` | When a rising value first crosses each threshold — to trigger springs from a dragged value. |
| `M.icon(name, size, colour, weight?)` | An icon from `M.ICONS`, stroke normalised so every icon matches. |
| `M.text(el, s)` | Sets text only when it changed (cheap per frame). |
| `M.brand` | The brand colours as an object. |
| `M.safeArea(W, H, boxes)` | The largest free strip beside the boxes: `{x, y, w, h, cx, cy}`. |
| `M.step`, `M.easeOut`, `M.easeInOut`, `M.clamp`, `M.lerp` | Steps, easing and helpers. |

Icons in `M.ICONS`: arrow, check, x, plus, folder, terminal, file, pencil, clock, chip,
sparkle, search, book, brain, plug, layers, lock, alert. Add more as 24-grid stroke paths
(Lucide paths work, ISC licence).

## Patterns

| Pattern | How | Example |
|---|---|---|
| Pill → card → control | `shapes` states + layers with `in`/`out` | 01 |
| Typing | `COMMAND.slice(0, n)` with `n` from time, and a caret | 01 |
| Rows or bars appearing on words | Per-row `M.show(t, wordTime, …)`, width from `M.spring` | 01, 03 |
| Confirmation | A box that fills and pops a check on its word | 03 |
| Drag and drop | While dragged the item follows the cursor path; on release it springs to the target | 01 |
| Liquid indicator | Two `M.track`s for the left/right edges; the edge moving forward uses `FAST`, the trailing one `SLOW` | — |
| Direct manipulation (slider) | While pressed, the value comes from the cursor position; on release it springs; `M.crossings` triggers effects | — |
| Chapter card | Dot → card, underline drawn with `M.track` | 02 |
| Transparent panel | `background:null`, `center` in the empty area | 03 |

## Interaction vocabulary

Map what the speaker says onto one of these: primary action (pill + click) · progress
(loader, bars, lanes) · confirmation (check, toast) · live status (island) · detail card
with a drag · slider · toggle · tabs · chart with tooltip · search and filter list ·
file → drag → drop target · terminal typing · side-by-side comparison · chapter card.

## plan.json

```json
{
  "title": "Claude Skills",
  "video": "../edit/final.mp4",
  "fps": "30", "width": 1920, "height": 1080,
  "brand": "dark",
  "layout": {"region": null, "protect": [[40, 700, 360, 340]]},
  "clips": [
    {"id": "01", "clip": "01-command-to-context", "title": "Command to context",
     "line": "“When I type /code-review, only that SKILL.md is loaded.”",
     "in": 83.4, "out": 90.8, "placement": "full", "illustrative": "bar widths are relative"},
    {"id": "02", "clip": "02-chapter-card", "title": "Chapter · Skills", "in": 120.0, "out": 122.6, "placement": "full", "protect": false}
  ],
  "notes": []
}
```

- `video` is relative to plan.json. `fps` must match the source exactly (`30000/1001` for 29.97).
- `brand`: `dark`, `light`, or a path to a brand file (relative to plan.json).
- `layout.region`: the box a `region` clip fills. `layout.protect`: boxes restored from the
  source on top of `full` and `region` clips (face cam, logo). `"protect": false` on a clip turns it off.
  A `full` clip without `center` is centred in the largest strip beside the protected boxes and
  scaled so its biggest state fits (`M.safeArea`); `stills` flags any text that still lands under a box.
- `out` is when the clip leaves the timeline; if the clip is shorter, its last frame is held.

## Brand file

```json
{
  "name": "my-brand",
  "colors": {"canvas": "#101418", "shape": "#1b242c", "surface": "#24313b", "ink": "#f1f4f6",
             "muted": "#8c99a3", "line": "#ffffff1f", "accent": "#ff8a3d",
             "success": "#63d6a4", "error": "#ff6b6b", "cursor": "#f1f4f6", "cursorStroke": "#101418"},
  "shadow": "0 18px 50px -18px #000000cc",
  "fonts": {"ui": {"family": "My Sans", "files": [{"file": "fonts/MySans.woff2", "weight": "100 900"}]},
            "mono": {"family": "My Mono", "files": [{"file": "fonts/MyMono.woff2"}]}}
}
```

Missing keys fall back to `dark`. Font paths are relative to the brand file; `.woff2`,
`.woff`, `.ttf` and `.otf` work, and `range` sets a `unicode-range`. Every colour becomes
a CSS token (`cursorStroke` → `--cursor-stroke`) and a key of `M.brand`.
