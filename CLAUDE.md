# Working on this plugin

A TRMNL recipe: `src/` holds the markup (one file per view plus `shared.liquid`), `tests/` the specs `bin/trmnlp test` runs.

## Layout changes: review every view

A change to markup or CSS is done only after every view has been looked at on every screen, not just the one the
change was for. Layout bugs show on the screen nobody looked at, with data the fixtures did not have.

1. Run `bin/trmnlp test --report report` and open `report/index.html`.
2. Look at each image from `tests/layout_spec.rb`: 4 views (full, half horizontal, half vertical, quadrant) on
   TRMNL OG and TRMNL X, each in landscape and portrait. That is 16 images per case. "Unchanged against main" is
   not a review.
3. For each one, check:
   - Text that wraps: icons and bullets stay on the first line, nothing pokes out of its box.
   - Text that is cut: it ends in an ellipsis, not mid-letter at a box edge.
   - Long lists: the last row is whole and the "and N more" count shows.
   - Empty and error states: centred, nothing left over from the list layout.
   - Rows of labels: they wrap, not squash or overlap.
4. A new kind of data (a field, a state, a language) gets a case in `tests/layout_spec.rb` with its longest text.

`tests/layout_spec.rb` fails on overflow, clipped text, text over text, clamped text with room to spare and icons
off their text's first line. The checks are in `tests/support/layout_checks.rb`, the same file in each of Samu's
TRMNL plugins: change it in all of them.

## Edits made on TRMNL

Edits made in TRMNL's editor (by Samu or a reviewer) arrive as "Updated from TRMNL" commits from trmnl-sync on
`main`. Pull before starting work. These commits may rewrite files with CRLF line endings; keep the endings a file has.

## Commits and pull requests

No AI attribution anywhere: no Co-Authored-By or session trailers, no "Generated with" lines, no tool names in branch
names. Commits are authored by Samu.
