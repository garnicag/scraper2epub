import io
import os
import uuid
import mimetypes
from datetime import datetime
from urllib.parse import urljoin, urlparse
from xml.sax.saxutils import escape

import requests
import lxml.etree as ET
from bs4 import BeautifulSoup
from ebooklib import epub

# URL del feed RSS
RSS_URL = "https://gbatemp.net/official/index.rss"
OUTPUT_EPUB = "gbatemp.epub"

# Muchos sitios bloquean el User-Agent por defecto de requests
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0 Safari/537.36"
    )
}

CSS = """
body { font-family: serif; line-height: 1.6; }
h1 { border-bottom: 2px solid #888; padding-bottom: 10px; }
h2 { color: #5a2ca0; }
a { color: #006b63; text-decoration: none; }
img { max-width: 40%; height: auto; width: auto; }
blockquote { border-left: 3px solid #ccc; margin: 1em 0; padding-left: 1em; color: #555; }
.source { margin-top: 1.5em; font-size: 0.9em; }
nav ol { line-height: 1.8; }
"""

# ebooklib construye el <html>/<head>/<body>: aquí solo damos el cuerpo.
CHAPTER_TMPL = """<h2>{title}</h2>
{content}
<p class="source"><a href="{link}">Fuente original</a></p>"""


def parse_rss(url):
    resp = requests.get(url, headers=HEADERS, timeout=10)
    resp.raise_for_status()

    tree = ET.parse(io.BytesIO(resp.content))
    root = tree.getroot()

    items = []
    for item in root.findall(".//item"):
        title = item.findtext("title")
        link = item.findtext("link")
        items.append({
            "title": title or "Sin título",
            "link": link,
        })
    return items


def scrape_article_body(url):
    """GBAtemp usa XenForo: el artículo es el primer mensaje del hilo,
    cuyo contenido está en article.message -> .bbWrapper."""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Primer mensaje del hilo (el post original = la noticia)
        article = soup.find("article", class_="message")
        container = article if article else soup

        body = container.find("div", class_="bbWrapper")
        if not body:
            # Fallbacks para variaciones de plantilla de XenForo
            body = container.find("div", class_="message-body") \
                or container.find("div", class_="message-userContent")

        if not body:
            return "<p><em>No se encontró contenido en esta noticia.</em></p>"

        # Limpiar elementos que no forman parte del artículo
        junk_selectors = [
            ".bbCodeBlock-expandLink",   # enlaces "Click to expand!"
            ".js-expandLink",
            ".message-signature",
            "script", "style", "noscript",
        ]
        for sel in junk_selectors:
            for el in body.select(sel):
                el.decompose()

        return str(body)

    except Exception as e:
        return f"<p><em>Error al cargar noticia: {e}</em></p>"


def embed_images(html, base_url, book, counter):
    """Descarga las imágenes del artículo, las incrusta en el EPUB y
    reescribe los src para que apunten a los recursos locales. Los lectores
    de EPUB no cargan imágenes remotas, por eso hay que empaquetarlas."""
    soup = BeautifulSoup(html, "html.parser")

    for img in soup.find_all("img"):
        # XenForo suele usar lazy-loading (data-url / data-src)
        src = img.get("src") or img.get("data-url") or img.get("data-src")
        if not src or src.startswith("data:"):
            src = img.get("data-url") or img.get("data-src")
        if not src or src.startswith("data:"):
            img.decompose()
            continue

        # Limpiar atributos que estorban en un EPUB
        for attr in ("srcset", "data-src", "data-url", "loading", "class", "style", "width", "height"):
            if img.has_attr(attr):
                del img[attr]

        abs_url = urljoin(base_url, src)
        try:
            r = requests.get(abs_url, headers=HEADERS, timeout=10)
            r.raise_for_status()
            ctype = r.headers.get("Content-Type", "").split(";")[0].strip()
            ext = (mimetypes.guess_extension(ctype)
                   or os.path.splitext(urlparse(abs_url).path)[1]
                   or ".jpg")
            if not ctype:
                ctype = mimetypes.guess_type("x" + ext)[0] or "image/jpeg"

            counter[0] += 1
            fname = f"images/img_{counter[0]}{ext}"

            item = epub.EpubItem(
                uid=f"img_{counter[0]}",
                file_name=fname,
                media_type=ctype,
                content=r.content,
            )
            book.add_item(item)
            img["src"] = fname
        except Exception:
            # Si falla la descarga, quitamos la imagen para no dejar enlaces rotos
            img.decompose()

    return str(soup)


def generate_epub(items):
    book = epub.EpubBook()
    book.set_identifier(str(uuid.uuid4()))
    book.set_title("GBAtemp.net")
    book.set_language("en")
    book.add_author("GBAtemp.net")
    book.add_metadata("DC", "date", datetime.now().strftime("%Y-%m-%d"))

    # Hoja de estilos compartida
    css_item = epub.EpubItem(
        uid="style",
        file_name="style.css",
        media_type="text/css",
        content=CSS,
    )
    book.add_item(css_item)

    img_counter = [0]
    chapters = []

    for idx, item in enumerate(items, start=1):
        print(f"[{idx}/{len(items)}] {item['title']}")
        raw = scrape_article_body(item["link"])
        content = embed_images(raw, item["link"], book, img_counter)

        chapter = epub.EpubHtml(
            title=item["title"],
            file_name=f"chap_{idx}.xhtml",
            lang="en",
        )
        chapter.content = CHAPTER_TMPL.format(
            title=escape(item["title"]),
            content=content,
            link=escape(item["link"] or "", {'"': "&quot;"}),
        )
        chapter.add_link(href="style.css", rel="stylesheet", type="text/css")
        book.add_item(chapter)
        chapters.append(chapter)

    # Índice navegable (TOC) + navegación
    book.toc = tuple(chapters)
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    # El "nav" al principio hace las veces de índice/página de contenidos.
    # Cada capítulo es un fichero XHTML propio => salto de página entre artículos.
    book.spine = ["nav"] + chapters

    # epub3_pages=False evita el escaneo opcional de "page-list" que puede
    # fallar con contenido scrapeado.
    epub.write_epub(OUTPUT_EPUB, book, {"epub3_pages": False})


def main():
    items = parse_rss(RSS_URL)
    print(f"Procesando feed: {RSS_URL}")
    print(f"Entradas encontradas: {len(items)}\n")
    generate_epub(items)
    print(f"\nArchivo generado: {OUTPUT_EPUB}")


if __name__ == "__main__":
    main()
