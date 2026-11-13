# HMX Webx responsive shell parity

Reference: read-only HMX worktree
`/home/yusufmjt/hmx-human-resources-port8000-latest/hmx` on server
`192.168.0.108`, commit `b59d2b9d743799729ca969e559ae992fea8b19f0`.

## Mapping

| Concern | HMX owner | ERP GSSHOP owner | Decision |
| --- | --- | --- | --- |
| Compact navigation | `webx` `layout.vue`, `sidebar.vue`, `toggle-sidebar.vue` | `templates/base.html` | ADAPTED: same overlay contract, GSSHOP three-dot trigger |
| Breakpoint and drawer size | `webx` layout/sidebar CSS | `static/css/app.css` | MATCHED: compact at 1024px, fixed 290px drawer |
| Open/close lifecycle | Webx layout store and sidebar backdrop | `static/js/responsive-shell.js` | MATCHED: toggle, backdrop, menu choice, Escape, viewport reset |
| List/form content | Webx `layout-content` and `main-content` | Core `main-content`, list, and document shells | MATCHED: sidebar leaves document flow so content keeps full width |

## Observable contract

- At widths above 1024px the normal 250px ERP sidebar remains part of the
  desktop grid.
- At widths up to 1024px navigation is closed by default and the content remains
  full width. A compact `•••` button opens a fixed 290px drawer over the current
  list or form, with a backdrop and an explicit `×` close button.
- Backdrop click, menu navigation, `Escape`, or leaving the compact breakpoint
  closes the drawer. Keyboard focus moves to the close button and returns to the
  invoking control after an explicit close.
- At phone width the topbar stays a single compact row, the organization chooser
  becomes an icon control, and document padding is reduced. List tables retain
  their existing horizontal scroll instead of shrinking columns into unreadable
  cells.

## Verification

- `manage.py check`: pass.
- Focused responsive-shell contract: 3 tests pass.
- Organization/developer shell plus GashyAI shell regression: 23 tests pass.
- Rebuilt deployment on port 8010 passed the web health check, responsive-shell
  static-asset check, and authenticated shell-marker smoke check. Final visual
  acceptance remains with the user in a real mobile/responsive browser.

## Generic list sorting

The visible list-column headers follow the HMX interaction pattern while data
ordering stays on the Django server. A first click selects ascending order, a
second click selects descending order, and the active header exposes its
direction through both an indicator and `aria-sort`.

Only concrete model fields already declared as visible entity columns are
accepted as sort keys. The server appends the primary key in the same direction
for deterministic pagination, preserves search/scope/layout query parameters,
and ignores unknown or virtual-field sort requests. Sorting remains bounded by
the existing 25-record interactive page. The HMX host was unreachable during
the final source recheck, so exact line-level HMX evidence for this subsection
is `NOT VERIFIED`; the requested observable server-sorted behavior is an
adapted core contract.
