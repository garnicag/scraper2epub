import os
import glob
import uuid
import datetime
from xml.sax.saxutils import escape

from ebooklib import epub

# --- Configuración ---
OUTPUT_DIR = "public"                      # carpeta que publica GitHub Pages
BOOKS_DIR = os.path.join(OUTPUT_DIR, "books")
CATALOG_PATH = os.path.join(OUTPUT_DIR, "catalog.xml")
INDEX_PATH = os.path.join(OUTPUT_DIR, "index.html")

LIBRARY_TITLE = "Latest Articles for null2077"
LIBRARY_AUTHOR = "GitHub Actions"


FEED_TMPL = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"
      xmlns:dc="http://purl.org/dc/terms/"
      xmlns:opds="http://opds-spec.org/2010/catalog">
  <id>{id}</id>
  <title>{title}</title>
  <updated>{updated}</updated>
  <author><name>{author}</name></author>
  <link rel="self"  href="{self_href}" type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>
  <link rel="start" href="{self_href}" type="application/atom+xml;profile=opds-catalog;kind=acquisition"/>
{entries}
</feed>
"""

ENTRY_TMPL = """  <entry>
    <title>{title}</title>
    <id>{id}</id>
    <updated>{updated}</updated>
    <author><name>{author}</name></author>
    <dc:language>{language}</dc:language>
    <content type="text">{summary}</content>
    <link rel="http://opds-spec.org/acquisition"
          href="{href}"
          type="application/epub+zip"/>
  </entry>"""

INDEX_TMPL = """<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width, initial-scale=1"/>
  <title>{title}</title>
  <style>
    body {{ font-family: system-ui, sans-serif; max-width: 640px; margin: 2rem auto; padding: 0 1rem; }}
    code {{ background: #eee; padding: 2px 5px; border-radius: 4px; word-break: break-all; }}
    li {{ margin: .4rem 0; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <p>Catálogo OPDS (añádelo en KOReader):</p>
  <p><code>{catalog}</code></p>
  <h2>Libros disponibles</h2>
  <ul>
{items}
  </ul>
</body>
</html>
"""


def base_url():
    """URL base del sitio en GitHub Pages. Se calcula sola dentro de Actions,
    o puedes forzarla con la variable de entorno PAGES_BASE_URL."""
    override = os.environ.get("PAGES_BASE_URL")
    if override:
        return override if override.endswith("/") else override + "/"

    repo = os.environ.get("GITHUB_REPOSITORY", "")  # formato: owner/repo
    owner, _, name = repo.partition("/")
    if owner and name and name.lower() != f"{owner.lower()}.github.io":
        return f"https://{owner}.github.io/{name}/"
    if owner:
        return f"https://{owner}.github.io/"
    return ""  # sin base -> enlaces relativos (para pruebas locales)


def rfc3339(ts):
    return datetime.datetime.fromtimestamp(
        ts, datetime.timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_meta(book, field, default=""):
    data = book.get_metadata("DC", field)
    return data[0][0] if data else default


def main():
    base = base_url()
    epubs = sorted(glob.glob(os.path.join(BOOKS_DIR, "*.epub")))
    now = rfc3339(datetime.datetime.now(datetime.timezone.utc).timestamp())

    entries, html_items = [], []
    for path in epubs:
        fname = os.path.basename(path)
        try:
            book = epub.read_epub(path)
            title = get_meta(book, "title", fname)
            author = get_meta(book, "creator", LIBRARY_AUTHOR)
            language = get_meta(book, "language", "en")
        except Exception:
            title, author, language = fname, LIBRARY_AUTHOR, "en"

        updated = rfc3339(os.path.getmtime(path))
        href = (base + "books/" + fname) if base else ("books/" + fname)
        book_id = "urn:uuid:" + str(
            uuid.uuid5(uuid.NAMESPACE_URL, href or fname)
        )
        summary = f"Actualizado: {updated}"

        entries.append(ENTRY_TMPL.format(
            title=escape(title), id=book_id, updated=updated,
            author=escape(author), language=escape(language),
            summary=escape(summary), href=escape(href),
        ))
        html_items.append(
            f'    <li><a href="{escape(href)}">{escape(title)}</a>'
            f' <small>({escape(language)}, {escape(updated)})</small></li>'
        )

    self_href = (base + "catalog.xml") if base else "catalog.xml"
    feed_id = "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, self_href or "catalog"))

    feed = FEED_TMPL.format(
        id=feed_id, title=escape(LIBRARY_TITLE), updated=now,
        author=escape(LIBRARY_AUTHOR), self_href=escape(self_href),
        entries="\n".join(entries),
    )
    html = INDEX_TMPL.format(
        title=escape(LIBRARY_TITLE),
        catalog=escape(self_href),
        items="\n".join(html_items) or "    <li>(sin libros)</li>",
    )

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    with open(CATALOG_PATH, "w", encoding="utf-8") as f:
        f.write(feed)
    with open(INDEX_PATH, "w", encoding="utf-8") as f:
        f.write(html)

    print(f"OPDS generado: {len(epubs)} libro(s) -> {CATALOG_PATH}")
    print(f"URL base: {base or '(relativa, solo pruebas locales)'}")


if __name__ == "__main__":
    main()
