# Seed photography

Demo food photographs for `scripts/seed.py` — the banners, category images and
item photos a freshly seeded Spice House comes up with.

## Why these are committed rather than fetched

The seed used to draw shapes with PIL: a rounded rectangle for a burger, a few
bars for fries. It was honest about being a placeholder, and it made the
storefront look unfinished to anyone evaluating it.

They are committed rather than downloaded at seed time on purpose:

* `make fresh` has to work on a plane and in CI, where there is no network.
* A seed that reaches the internet fails in a way that looks like a broken
  migration rather than a missing photo.
* The storefront's Content-Security-Policy allows images from `'self'` and a
  short allow-list only (see `web/docker-entrypoint.d/40-zenoeats-config.sh`).
  Hotlinking a photo host would need that host added to `CSP_EXTRA_IMG_SRC` on
  every deployment, and would put a third party in the render path of the
  busiest page in the product.

The seed passes each file through the ordinary image service, so what the
browser receives is produced by exactly the same code as a photograph a
restaurant uploads in the storefront editor. There is no second path.

## Where they came from

All twelve are from [Unsplash](https://unsplash.com), under the
[Unsplash License](https://unsplash.com/license): free to use commercially and
non-commercially, no permission or attribution required. The attribution below
is recorded because knowing the provenance of a committed binary matters, not
because the licence demands it.

| File | Unsplash photo id | Shows |
| --- | --- | --- |
| `banner-burger.webp` | `photo-1568901346375-23c9450c58cd` | double burger, dark ground |
| `banner-fries.webp` | `photo-1573080496219-bb080dd4f877` | loaded fries |
| `banner-shake.webp` | `4FujjkcI40g` | chocolate shake |
| `cat-burgers.webp` | `photo-1550547660-d9450f859349` | two burgers on a board |
| `cat-fries.webp` | `photo-1576107232684-1279f390859f` | fries in a tray |
| `cat-shakes.webp` | `wqBQhXK7OGA` | shake in a tall glass |
| `cat-drinks.webp` | `0KxfiWujzyY` | iced drink with a straw |
| `cat-secret.webp` | `photo-1521305916504-4a1121188589` | two filled buns |
| `item-double-double.webp` | `photo-1571091718767-18b5b1457add` | burger, sesame bun |
| `item-cheeseburger.webp` | `vdkyWisomns` | cheeseburger, sesame bun |
| `item-hamburger.webp` | `E94j3rMcxlw` | burger with lettuce and tomato |
| `item-fries.webp` | `ChXHveqrb28` | fries in a basket |

Ids in the `photo-…` form are CDN paths; the short ones are Unsplash photo
ids, found through `https://unsplash.com/napi/search/photos?query=…`.

**Check the picture, not the id.** Six of the first twelve chosen here were
wrong — cocktails filed as fries, a pizza under Drinks — because an id says
nothing about what it shows. Anything added here should be looked at before it
is committed; a contact sheet of the folder takes a moment to build and is the
only way to be sure.

These are stock photographs of food, not of this restaurant's food. A real
restaurant replaces them in Settings → Storefront, and should: a menu
photographed somewhere else is the fastest way to lose a customer's trust in
what arrives.

## Sizes

Banners are 1800×820 — the shape the hero slot renders — and category and item
photos are capped at 1100px on the long edge. Large enough that the image
service never upscales, small enough that the whole set is about 1 MB.
