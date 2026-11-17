# Our Fresh Air — website

Redesigned marketing site for **Our Fresh Air** (ourfreshair.com), a NADCA-certified commercial
HVAC and air duct cleaning company in Miami, FL.

The site is plain HTML, CSS and a small amount of JavaScript. There is no build step and no
dependencies, so it can be hosted anywhere that serves static files.

## Pages

| Path | Page |
|------|------|
| `/` | Home |
| `/services/` | Services and full services list, FAQ |
| `/about-us/` | About and team |
| `/breathing-clean-champions/` | Client logos |
| `/contact/` | Quote request form, contact details, map |

The paths match the current live site so existing links and search listings keep working.

## Preview locally

```bash
cd website
python3 -m http.server 8000
# open http://localhost:8000
```

## Deploy

Upload the contents of the `website/` folder to the web root of any static host
(Netlify, Vercel, Cloudflare Pages, GitHub Pages, cPanel / public_html, etc.).

If the site stays on WordPress, the simplest route is to switch the domain to a static host
and keep WordPress only if the blog is needed. The HTML can also be used as a reference for
rebuilding the pages inside a page builder.

## Contact form delivery

The form on `/contact/` works in two modes, set on the `<form id="quote-form">` tag in
`contact/index.html`:

1. **Form endpoint (recommended).** Set `data-endpoint` to a form service URL, for example a
   Formspree form (`https://formspree.io/f/XXXXXXXX`) or a Netlify Forms / Basin / your own API
   endpoint. Submissions are posted with `fetch` and a success or error message is shown inline.
2. **Email fallback (current default).** Leave `data-endpoint` empty and the form opens the
   visitor's email app with the request pre-filled, addressed to `data-mailto`
   (`Jonathan@OurFreshAir.com`).

A hidden honeypot field (`company_website`) is included to reduce spam.

## Editing content

* Phone, email, address and license number appear in the top bar, footer and contact page of
  every file. Search for `786-369-9016` or `CAC1822263` to find them all.
* Client logos live in `images/client-*.png|jpg` and are listed on the home page (first six) and
  the Breathing Clean Champions page (all).
* Colors and fonts are defined as CSS variables at the top of `css/styles.css`.
* `sitemap.xml` and `robots.txt` are included for search engines; structured data
  (`HVACBusiness` schema) is embedded in each page's `<head>`.
