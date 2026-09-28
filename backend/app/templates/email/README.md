# Email templates

Every email Zenoeats sends is a folder here:

| Folder | Sent when |
| --- | --- |
| `order_confirmation/` | A customer's payment for an order succeeds |
| `order_ready/` | The kitchen marks a pick-up order ready |
| `order_on_the_way/` | The driver picks the order up |
| `order_delivered/` | The driver marks the order delivered |
| `order_cancelled/` | The restaurant cancels a paid order, with the refund if one was issued |
| `refund_issued/` | Money goes back later: a refund from the board, or from Stripe's dashboard |
| `staff_invitation/` | A restaurant adds someone to its team, or resends the invitation |

Each email is sent at most once for what it's about. Order emails go to the
address the customer gave at checkout, or their account's address.

Each folder holds three files:

- `subject.txt` is the subject line.
- `email.html` is what most mail apps show.
- `email.txt` is the plain-text copy, for the apps that show nothing else.
  Change it whenever you change `email.html`, so the two say the same thing.

Two files are shared by every email:

- `_layout.html` is the frame: the page colour, the white card and its heading.
- `_components.html` holds the colours, the fonts, the button and the rows of
  figures under an order. Change a colour here and every email picks it up.

## Editing

The files are [Jinja](https://jinja.palletsprojects.com/templates/) templates:
ordinary HTML or text with placeholders.

- `{{ restaurant_name }}` puts in a value.
- `{% if delivering %} ... {% else %} ... {% endif %}` shows one wording or
  another.
- The comment at the top of each `email.html` says when that email is sent and
  what it must never contain.

Values are escaped for you in `.html` files, so a restaurant name with `&` or
`<` in it shows as text. Don't turn that off with `|safe` on anything a
customer or restaurant typed.

A placeholder the code does not provide is an error, not a blank. If you
misspell one, the preview and the tests fail and tell you which.

Mail apps ignore stylesheets, so styles go inline on each tag (`style="..."`),
and the layout uses tables. Don't add images, web fonts or SVG: many apps
block or strip them.

## Seeing a change

From `backend/`:

```
python scripts/preview_emails.py
```

It renders every email with made-up data and prints the path to an
`index.html` you can open in a browser. It doesn't send anything. The made-up
data is in `app/services/email_samples.py`. When you add a new email, add an
example there too; the tests check that every email has one.

## Adding an email

1. Copy a folder here and rename it (for example `order_delivered/`).
2. Render it from the code with
   `email_templates.render("order_delivered", ...values...)`, as
   `app/services/notifications.py` does for the others.
3. Add an example to `app/services/email_samples.py`.
