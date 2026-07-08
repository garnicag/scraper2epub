import requests
from bs4 import BeautifulSoup
import lxml.etree as ET
import io
import re
import os
import uuid
import mimetypes
from datetime import datetime
from urllib.parse import urljoin, urlparse
from xml.sax.saxutils import escape

from ebooklib import epub

# URL del feed RSS
RSS_URL = "https://www.elotrolado.net/feed/"
OUTPUT_EPUB = "elotrolado-noticias.epub"

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
img { max-width: 100%; height: auto; }
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
    content = resp.content

    tree = ET.parse(io.BytesIO(content))
    root = tree.getroot()

    items = []
    for item in root.findall(".//item"):
        title = item.findtext("title")
        link = item.findtext("link")
        description = item.findtext("description")

        # Buscar enlace "leer noticia completa"
        leer_link = None
        if description:
            soup = BeautifulSoup(description, "html.parser")
            for a in soup.find_all("a"):
                if "leer noticia completa" in a.text.lower():
                    leer_link = a.get("href")
                    break

        items.append({
            "title": title or "Sin título",
            "link": leer_link or link
        })
    return items


def scrape_news_body(url):
    try:
        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")

        # Detectar si es noticia o hilo
        if "/noticias" in url:
            news_div = soup.find("div", id="news-body")
            if news_div:
                return str(news_div)
        else:
            # Detectar prefijo "hilo_"
            match = re.search(r"elotrolado\.net/(hilo_[^/]+)", url)
            if match:
                content_div = soup.find("div", class_="content")
                if content_div:
                    message = content_div.find("div", class_="message")
                    if message:
                        # Eliminar div con clase "shr-bts"
                        shr_bts = message.find("div", class_="shr-bts")
                        if shr_bts:
                            shr_bts.decompose()
                        return str(message)

        return "<p><em>No se encontró contenido en esta noticia.</em></p>"

    except Exception as e:
        return f"<p><em>Error al cargar noticia: {e}</em></p>"


def embed_images(html, base_url, book, counter):
    """Descarga las imágenes del artículo, las incrusta en el EPUB y
    reescribe los src para que apunten a los recursos locales. Los lectores
    de EPUB no cargan imágenes remotas, por eso hay que empaquetarlas."""
    soup = BeautifulSoup(html, "html.parser")

    for img in soup.find_all("img"):
        # Algunos sitios usan lazy-loading con data-src
        src = img.get("src") or img.get("data-src")
        if not src:
            img.decompose()
            continue

        # Limpiar atributos que estorban en un EPUB
        for attr in ("srcset", "data-src", "loading", "class", "style"):
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
    book.set_title("ElOtroLado.net - Noticias")
    book.set_language("es")
    book.add_author("ElOtroLado.net")
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
        raw = scrape_news_body(item["link"])
        content = embed_images(raw, item["link"], book, img_counter)

        chapter = epub.EpubHtml(
            title=item["title"],
            file_name=f"chap_{idx}.xhtml",
            lang="es",
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
    generate_epub(items)
    print(f"Archivo generado: {OUTPUT_EPUB}")


if __name__ == "__main__":
    main()
