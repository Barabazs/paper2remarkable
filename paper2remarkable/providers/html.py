# -*- coding: utf-8 -*-

"""Provider for HTML documents

This provider is a little bit special, in that it isn't simply pulling an
academic paper from a site, but instead aims to pull a HTML article.

Author: G.J.J. van den Burg
License: See LICENSE file.
Copyright: 2020, G.J.J. van den Burg

"""

import re
import urllib

import html2text
import markdown
import readability
import titlecase
import unidecode
import weasyprint
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
import time

from ..log import Logger
from ..utils import clean_string
from ..utils import get_content_type_with_retry
from ..utils import get_page_with_retry
from ._base import Provider
from ._info import Informer

logger = Logger()

CSS = """
@page { size: 702px 936px; margin: 1in; }
a { color: black; }
img { display: block; margin: 0 auto; text-align: center; max-width: 70%; max-height: 300px; }
p, li { font-size: 10pt; font-family: 'EB Garamond'; hyphens: auto; text-align: justify; }
h1,h2,h3 { font-family: 'Noto Serif'; }
h1 { font-size: 26px; }
h2 { font-size: 18px; }
h3 { font-size: 14px; }
blockquote { font-style: italic; }
pre { font-family: 'Inconsolata'; padding-left: 2.5%; background: #efefef; }
code { font-family: 'Inconsolata'; font-size: .7rem; background: #efefef; }
"""

# NOTE: For some reason, Weasyprint no longer accepts the @import statement in
# the CSS to load the fonts. This may have to do with recent changes they've
# introduced. Providing the font urls separately does seem to work.
FONT_URLS = [
    "https://fonts.googleapis.com/css2?family=EB+Garamond&family=Noto+Serif&family=Inconsolata"
]


def url_fetcher(url):
    if url.startswith("//"):
        url = "https:" + url
    elif url.startswith("file:///"):
        url = "https:" + url[len("file:/") :]
    return weasyprint.default_url_fetcher(url)


def scroll_and_get_content(url):
    """Use Selenium to scroll the page and let JavaScript load all content"""
    chrome_options = Options()
    chrome_options.add_argument("--headless")  # Run in headless mode
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--window-size=1920,1080")
    chrome_options.add_argument(
        "--disable-blink-features=AutomationControlled"
    )  # Try to avoid detection

    driver = webdriver.Chrome(options=chrome_options)
    try:
        logger.info(f"Attempting to load URL: {url}")
        driver.get(url)

        # Initial wait for page load
        time.sleep(5)

        def force_load_images():
            return driver.execute_script("""
                let changed = false;
                let imageCount = 0;
                let loadedCount = 0;
                
                // Helper to check if URL is valid
                function isValidUrl(url) {
                    return url && url !== '' && !url.startsWith('data:') && !url.startsWith('blob:');
                }
                
                // Log image states
                function logImageState(img) {
                    console.log('Image:', {
                        src: img.src,
                        'data-src': img.getAttribute('data-src'),
                        'data-srcset': img.getAttribute('data-srcset'),
                        'data-lazy': img.getAttribute('data-lazy'),
                        class: img.className,
                        loading: img.loading,
                        complete: img.complete,
                        naturalWidth: img.naturalWidth
                    });
                }
                
                // Wired-specific handling
                document.querySelectorAll('img.ResponsiveImageContainer-dlOMGF').forEach(img => {
                    imageCount++;
                    logImageState(img);
                    
                    // Try to find parent figure with data attributes
                    let figure = img.closest('figure');
                    if (figure) {
                        let sources = figure.getAttribute('data-sources');
                        if (sources) {
                            try {
                                let imageData = JSON.parse(sources);
                                if (imageData && imageData.length > 0) {
                                    // Use the highest resolution image
                                    let bestImage = imageData[imageData.length - 1];
                                    if (bestImage.url) {
                                        img.src = bestImage.url;
                                        changed = true;
                                        loadedCount++;
                                    }
                                }
                            } catch (e) {
                                console.error('Failed to parse image data:', e);
                            }
                        }
                    }
                });
                
                // General image handling
                document.querySelectorAll('img').forEach(img => {
                    imageCount++;
                    logImageState(img);
                    
                    if (img.loading === 'lazy') {
                        img.loading = 'eager';
                        changed = true;
                    }
                    
                    ['data-src', 'data-original', 'data-load-src', 'data-lazy-src', 'data-original-src'].forEach(attr => {
                        if (img.hasAttribute(attr) && isValidUrl(img.getAttribute(attr))) {
                            img.src = img.getAttribute(attr);
                            changed = true;
                            loadedCount++;
                        }
                    });
                    
                    if (img.complete && img.naturalWidth > 0) {
                        loadedCount++;
                    }
                });
                
                console.log(`Found ${imageCount} images, ${loadedCount} appear to be loaded`);
                return {changed: changed, total: imageCount, loaded: loadedCount};
            """)

        last_height = driver.execute_script("return document.body.scrollHeight")
        scroll_attempts = 0
        max_scroll_attempts = 20

        while scroll_attempts < max_scroll_attempts:
            current_position = driver.execute_script("return window.pageYOffset;")
            driver.execute_script(f"window.scrollTo(0, {current_position + 300});")
            time.sleep(1)

            result = force_load_images()
            if isinstance(result, dict):
                logger.info(
                    f"Images found: {result['total']}, Loaded: {result['loaded']}"
                )
                if result["changed"]:
                    time.sleep(3)

            if scroll_attempts % 3 == 0:
                driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
                time.sleep(2)
                new_height = driver.execute_script("return document.body.scrollHeight")
                if new_height == last_height:
                    if not (isinstance(result, dict) and result["changed"]):
                        break
                last_height = new_height

            scroll_attempts += 1

        # Get console logs
        logs = driver.get_log("browser")
        for log in logs:
            logger.info(f"Browser log: {log}")

        # Final image check
        result = force_load_images()
        if isinstance(result, dict):
            logger.info(
                f"Final image count - Total: {result['total']}, Loaded: {result['loaded']}"
            )

        page_source = driver.page_source

        # Save debug HTML if requested
        if logger.level <= 10:  # DEBUG level
            with open("debug_page.html", "w", encoding="utf-8") as f:
                f.write(page_source)
            logger.debug("Saved debug HTML to debug_page.html")

        return page_source
    except Exception as e:
        logger.error(f"Error during page loading: {str(e)}")
        raise
    finally:
        driver.quit()


def make_readable(request_html, url=None):
    """Use an extraction method to get the main article html

    This function checks if ReadabiliPy is installed with NodeJS support, as
    that generally yields better results. If that is not available, it falls
    back on readability.

    If a URL is provided, it will first try to scroll the page to load all content.
    """
    if url and "wired.com" in url:
        try:
            logger.info("Detected Wired article, using custom extraction")
            request_html = scroll_and_get_content(url)

            # Save raw HTML before any processing
            with open("raw_page_before_processing.html", "w", encoding="utf-8") as f:
                f.write(request_html)
            logger.info("Saved raw HTML to raw_page_before_processing.html")

            from bs4 import BeautifulSoup

            soup = BeautifulSoup(request_html, "html.parser")

            # Print available classes for debugging
            logger.info("Looking for article content...")
            article_divs = soup.find_all(
                "div", class_=lambda x: x and "article" in x.lower()
            )
            for div in article_divs:
                logger.info(
                    f"Found article-like div with classes: {div.get('class', [])}"
                )

            # Create a new article container
            article_container = soup.new_tag("article")

            # Add the title
            title_elem = soup.find("h1")  # Just find any h1 for now
            if title_elem:
                title = title_elem.text.strip()
                title_tag = soup.new_tag("h1")
                title_tag.string = title
                article_container.append(title_tag)
            else:
                title = "Wired Article"
                logger.info("Could not find title element")

            # Find the article content - try multiple possible selectors
            article_content = None
            possible_selectors = [
                ("div", {"class_": "body__inner-container"}),
                ("article", {}),
                ("div", {"class_": "article-body"}),
                (
                    "div",
                    {
                        "class_": lambda x: x
                        and "article" in x.lower()
                        and "body" in x.lower()
                    },
                ),
            ]

            for tag, attrs in possible_selectors:
                content = soup.find(tag, **attrs)
                if content:
                    logger.info(f"Found content using selector: {tag}, {attrs}")
                    article_content = content
                    break

            if article_content:
                # Process all figures in the content
                figures = article_content.find_all("figure")
                logger.info(f"Found {len(figures)} figures in content")

                for figure in figures:
                    try:
                        # First try to find picture elements
                        picture = figure.find("picture")
                        if picture:
                            logger.info("Found picture element")
                            # Try to get the highest quality source
                            sources = picture.find_all("source")
                            if sources:
                                # Sort sources by srcset width if available
                                best_source = None
                                max_width = 0
                                for source in sources:
                                    srcset = source.get("srcset", "")
                                    if srcset:
                                        logger.info(
                                            f"Found source with srcset: {srcset}"
                                        )
                                        # Parse srcset to find highest resolution
                                        for src_str in srcset.split(","):
                                            src_parts = src_str.strip().split()
                                            if len(src_parts) == 2:
                                                url, width_str = src_parts
                                                width = int(width_str.replace("w", ""))
                                                if width > max_width:
                                                    max_width = width
                                                    best_source = url

                                if best_source:
                                    # Create new img with the best source
                                    new_img = soup.new_tag("img", src=best_source)
                                    picture.clear()  # Remove all sources
                                    picture.append(new_img)  # Add our new img
                                    logger.info(
                                        f"Set image from picture source: {best_source}"
                                    )
                                    continue

                        # If no picture element or no good source found, try regular img
                        img = figure.find("img")
                        if img:
                            logger.info(f"Found img with attributes: {img.attrs}")

                            # Try data-sources first
                            sources = figure.get("data-sources")
                            if sources:
                                try:
                                    import json

                                    image_data = json.loads(sources)
                                    if image_data and len(image_data) > 0:
                                        best_image = image_data[-1]
                                        if best_image.get("url"):
                                            img["src"] = best_image["url"]
                                            logger.info(
                                                f"Set image src from data-sources: {best_image['url']}"
                                            )
                                except Exception as e:
                                    logger.warning(
                                        f"Failed to process data-sources: {str(e)}"
                                    )

                            # If no data-sources, try other common attributes
                            for attr in ["data-src", "data-original-src", "src"]:
                                if img.get(attr):
                                    img["src"] = img[attr]
                                    logger.info(
                                        f"Set image src from {attr}: {img[attr]}"
                                    )
                                    break
                    except Exception as e:
                        logger.warning(f"Failed to process figure: {str(e)}")
                        continue

                # Add the processed content to our container
                article_container.append(article_content)

                if len(article_container.contents) > 1:
                    logger.info(
                        f"Successfully extracted Wired article with images: {title}"
                    )

                    # Save the extracted content for debugging
                    with open("wired_extracted.html", "w", encoding="utf-8") as f:
                        f.write(str(article_container))
                    logger.info(
                        "Saved Wired-specific extraction to wired_extracted.html"
                    )

                    return title, str(article_container)
            else:
                logger.warning("Could not find article content with any selector")

        except Exception as e:
            logger.warning(f"Failed to extract Wired article: {str(e)}")
            import traceback

            logger.warning(f"Traceback: {traceback.format_exc()}")

    # If not Wired or if Wired extraction failed, try normal loading
    if url:
        try:
            logger.info("Attempting to load full page content with Selenium")
            request_html = scroll_and_get_content(url)
        except Exception as e:
            logger.warning(
                f"Failed to load with Selenium, falling back to direct request: {str(e)}"
            )

    # Save HTML before Readability
    with open("before_readability.html", "w", encoding="utf-8") as f:
        f.write(request_html)
    logger.info("Saved HTML before Readability to before_readability.html")

    have_readabilipy_js = False
    try:
        import readabilipy

        have_readabilipy_js = readabilipy.simple_json.have_node()
    except ImportError:
        raise ImportError("readabilipy is not installed")

    if have_readabilipy_js:
        logger.info("Converting HTML using Readability.js")
        article = readabilipy.simple_json_from_html_string(
            request_html, use_readability=True
        )
        title = article["title"]
        raw_html = article["content"]

        # Save Readability output
        with open("after_readability.html", "w", encoding="utf-8") as f:
            f.write(raw_html)
        logger.info("Saved Readability output to after_readability.html")
    else:
        logger.info("Converting HTML using readability")
        doc = readability.Document(request_html)
        title = doc.title()
        raw_html = doc.summary(html_partial=True)

        # Save Readability output
        with open("after_readability.html", "w", encoding="utf-8") as f:
            f.write(raw_html)
        logger.info("Saved Readability output to after_readability.html")

    return title, raw_html


class ImgProcessor(markdown.treeprocessors.Treeprocessor):
    def __init__(self, base_url, *args, **kwargs):
        self._base_url = base_url
        super().__init__(*args, **kwargs)

    def run(self, root):
        """Ensure all img src urls are absolute"""
        for img in root.iter("img"):
            img.attrib["src"] = urllib.parse.urljoin(self._base_url, img.attrib["src"])
            img.attrib["src"] = img.attrib["src"].rstrip("/")


class HTMLInformer(Informer):
    def __init__(self):
        super().__init__()
        self._cached_title = None
        self._cached_article = None

    def get_filename(self, abs_url):
        request_html = get_page_with_retry(abs_url, return_text=True)
        title, article = make_readable(request_html)

        self._cached_title = title
        self._cached_article = article

        # Clean the title and make it titlecase
        title = clean_string(title)
        title = titlecase.titlecase(title)
        title = title.replace(" ", "_")
        title = clean_string(title)
        name = title.strip("_") + ".pdf"
        name = unidecode.unidecode(name)
        logger.info("Created filename: %s" % name)
        return name


class HTML(Provider):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.informer = HTMLInformer()

    def get_abs_pdf_urls(self, url):
        return url, url

    def fix_lazy_loading(self, article):
        if not self.experimental:
            return article

        # This attempts to fix sites where the image src element points to a
        # placeholder and the data-src attribute contains the url to the actual
        # image. Note that results may differ between readability and
        # Readability.JS
        regex = r'<img src="(?P<src>.*?)" (?P<rest1>.*) data-src="(?P<datasrc>.*?)" (?P<rest2>.*?)>'
        sub = r'<img src="\g<datasrc>" \g<rest1> \g<rest2>>'

        article, nsub = re.subn(regex, sub, article, flags=re.MULTILINE)
        if nsub:
            logger.info(
                f"[experimental] Attempted to fix lazy image loading ({nsub} times). "
                "Please report bad results."
            )
        return article

    def preprocess_html(self, pdf_url, title, article):
        article = self.fix_lazy_loading(article)

        h2t = html2text.HTML2Text()
        h2t.wrap_links = False
        text = h2t.handle(article)

        # Add the title back to the document
        article = "# {title}\n\n{text}".format(title=title, text=text)

        # Convert to html, fixing relative image urls.
        md = markdown.Markdown()
        md.treeprocessors.register(ImgProcessor(pdf_url), "img", 10)
        html_article = md.convert(article)
        return html_article

    def retrieve_pdf(self, pdf_url, filename):
        """Turn the HTML article in a clean pdf file

        This function takes the following steps:

        1. Pull the HTML page using requests, if not done in Informer
        2. Extract the article part of the page using readability/readabiliPy
        3. Convert the article HTML to markdown using html2text
        4. Convert the markdown back to HTML (done to sanitize the HTML)
        4. Convert the HTML to PDF, pulling in images where needed
        5. Save the PDF to the specified filename.
        """
        if self.informer._cached_title and self.informer._cached_article:
            title = self.informer._cached_title
            article = self.informer._cached_article
        else:
            request_html = get_page_with_retry(pdf_url, return_text=True)
            title, article = make_readable(request_html, url=pdf_url)

        html_article = self.preprocess_html(pdf_url, title, article)

        if self.debug:
            with open("./paper.html", "w") as fp:
                fp.write(html_article)

        html = weasyprint.HTML(string=html_article, url_fetcher=url_fetcher)
        css = CSS if self.css is None else self.css
        font_urls = FONT_URLS if self.font_urls is None else self.font_urls
        style = weasyprint.CSS(string=css)
        html.write_pdf(filename, stylesheets=[style] + font_urls)

    @staticmethod
    def validate(src):
        # first check if it is a valid url
        parsed = urllib.parse.urlparse(src)
        if not all([parsed.scheme, parsed.netloc, parsed.path]):
            return False
        # next, get the header and check the content type
        ct = get_content_type_with_retry(src)
        if ct is None:
            return False
        return ct.startswith("text/html")
