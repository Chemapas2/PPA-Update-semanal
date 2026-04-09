from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any
from urllib.parse import urljoin

import fitz  # PyMuPDF
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from PIL import Image


# =========================
# Configuración general
# =========================
st.set_page_config(
    page_title="PPA España - Comité de Dirección",
    page_icon="🐗",
    layout="wide",
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

CACHE_TTL_SECONDS = 6 * 60 * 60  # 6 horas
MAPA_NEWS_URL = (
    "https://www.mapa.gob.es/es/ganaderia/temas/"
    "sanidad-animal-higiene-ganadera/sanidad-animal"
)
MAPA_DISEASE_URL = (
    "https://www.mapa.gob.es/es/ganaderia/temas/"
    "sanidad-animal-higiene-ganadera/sanidad-animal/"
    "enfermedades/peste-porcina-africana/peste_porcina_africana"
)
MERCOLLEIDA_HOME_URL = "https://www.mercolleida.com/"
MERCOLLEIDA_CERDO_URL = (
    "https://www.mercolleida.com/index.php/es/servicios/mercados/"
    "porcino/cerdo-cebado/mercolleida"
)
THREETHREE_PRICES_URL = "https://www.3tres3.com/cotizaciones-de-porcino/"

SPANISH_MONTHS = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}

SPANISH_NUMBER_WORDS = {
    "un": 1,
    "una": 1,
    "uno": 1,
    "dos": 2,
    "tres": 3,
    "cuatro": 4,
    "cinco": 5,
    "seis": 6,
    "siete": 7,
    "ocho": 8,
    "nueve": 9,
    "diez": 10,
    "once": 11,
    "doce": 12,
    "trece": 13,
    "catorce": 14,
    "quince": 15,
    "dieciseis": 16,
    "dieciséis": 16,
    "diecisiete": 17,
    "dieciocho": 18,
    "diecinueve": 19,
    "veinte": 20,
}


@dataclass
class NoteRecord:
    title: str
    url: str
    report_date: date


def normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").replace("\xa0", " ")).strip()


def strip_accents(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in value if not unicodedata.combining(ch))


def parse_date_generic(value: str) -> date | None:
    if not value:
        return None
    value = value.strip()
    for fmt in ("%d/%m/%Y", "%d.%m.%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(value, fmt).date()
        except ValueError:
            continue
    return None


def parse_spanish_textual_date(value: str) -> date | None:
    value = normalize_text(value).lower()
    match = re.search(r"(\d{1,2}) de ([a-záéíóúñ]+) de (\d{4})", value)
    if not match:
        return None
    day = int(match.group(1))
    month_name = strip_accents(match.group(2)).lower()
    year = int(match.group(3))
    month = SPANISH_MONTHS.get(month_name)
    if not month:
        return None
    return date(year, month, day)


def parse_spanish_number(token: str | None) -> int | None:
    if token is None:
        return None
    token = normalize_text(token).strip(" .,:;()[]{}")
    if not token:
        return None
    if re.fullmatch(r"[\d\.]+", token):
        return int(token.replace(".", ""))
    normalized = strip_accents(token.lower())
    return SPANISH_NUMBER_WORDS.get(normalized)


def int_from_match(pattern: str, text: str, group: int = 1, flags: int = re.IGNORECASE) -> int | None:
    match = re.search(pattern, text, flags)
    if not match:
        return None
    return parse_spanish_number(match.group(group))


def make_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(HEADERS)
    return session


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def fetch_text(url: str) -> str:
    session = make_session()
    response = session.get(url, timeout=30)
    response.raise_for_status()
    response.encoding = response.encoding or "utf-8"
    return response.text


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def fetch_bytes(url: str) -> bytes:
    session = make_session()
    response = session.get(url, timeout=60)
    response.raise_for_status()
    return response.content


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def get_mapa_notes() -> list[dict[str, Any]]:
    html = fetch_text(MAPA_NEWS_URL)
    soup = BeautifulSoup(html, "html.parser")

    records: dict[str, dict[str, Any]] = {}
    for anchor in soup.find_all("a", href=True):
        title = normalize_text(anchor.get_text(" ", strip=True))
        if "Peste Porcina Africana" not in title or "Catalu" not in title:
            continue

        date_match = re.search(r"\((\d{2}[./]\d{2}[./]\d{4})\)", title)
        report_date = parse_date_generic(date_match.group(1)) if date_match else None
        href = urljoin(MAPA_NEWS_URL, anchor["href"])

        if report_date is None:
            continue
        if not href.lower().endswith(".pdf"):
            continue

        records[href] = {
            "title": title,
            "url": href,
            "report_date": report_date,
        }

    notes = sorted(records.values(), key=lambda row: row["report_date"], reverse=True)
    return notes


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def extract_pdf_text(pdf_bytes: bytes, max_pages: int | None = None) -> str:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page_count = doc.page_count if max_pages is None else min(doc.page_count, max_pages)
        text_parts = [doc.load_page(i).get_text("text") for i in range(page_count)]
    return "\n".join(text_parts)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def render_pdf_page(pdf_bytes: bytes, page_number: int, zoom: float = 1.7) -> Image.Image:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page = doc.load_page(page_number)
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
        png_bytes = pixmap.tobytes("png")
    return Image.open(io.BytesIO(png_bytes))


def split_municipalities(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    cleaned = raw_value.strip().strip(". ")
    cleaned = re.sub(r",\s+y\s+", ", ", cleaned)
    cleaned = cleaned.replace(" y ", ", ")
    parts = [part.strip(" .") for part in cleaned.split(",")]
    return [part for part in parts if part]


def extract_measures(text: str, farms: int | None) -> list[str]:
    text_lower = strip_accents(text.lower())
    measures: list[str] = []

    if "busqueda de cadaveres de jabalies" in text_lower:
        measures.append(
            "Búsqueda intensiva de cadáveres de jabalíes y vigilancia reforzada en la zona restringida y su periferia."
        )
    if "reduccion de las poblaciones de jabalies" in text_lower or "control poblacional" in text_lower:
        measures.append(
            "Reducción poblacional de jabalíes mediante trampas de captura y control por agentes rurales/cazadores específicamente formados."
        )
    if "reforzado los vallados" in text_lower or "medidas de aislamiento" in text_lower:
        measures.append(
            "Refuerzo de vallados, barreras y medidas de aislamiento, priorizando corredores de paso de jabalíes."
        )
    if "medidas de bioseguridad" in text_lower:
        farm_text = f" en {farms} explotaciones comerciales" if farms else " en las explotaciones de porcino"
        measures.append(
            f"Inspección de bioseguridad y vigilancia pasiva reforzada{farm_text}."
        )
    if "alto nivel de alerta" in text_lower:
        measures.append(
            "Mantenimiento de un alto nivel de alerta en Cataluña y en el resto de España."
        )
    if "obligacion de comunicar" in text_lower:
        measures.append(
            "Recordatorio de notificación inmediata a los Servicios Veterinarios Oficiales ante cualquier sospecha en jabalíes o porcino doméstico."
        )

    return measures


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def parse_note(pdf_url: str) -> dict[str, Any]:
    pdf_bytes = fetch_bytes(pdf_url)
    full_text = normalize_text(extract_pdf_text(pdf_bytes))

    note_date = parse_date_generic(re.search(r"(\d{2}/\d{2}/\d{4})", full_text).group(1))
    previous_note_match = re.search(
        r"Desde la última nota .*? enviada el (\d{1,2} de [a-záéíóúñ]+ de \d{4})",
        full_text,
        re.IGNORECASE,
    )
    previous_note_date = parse_spanish_textual_date(previous_note_match.group(1)) if previous_note_match else None

    new_foci = None
    for pattern in (
        r"detección de ([\d\.]+|[A-Za-záéíóúñ]+) nuevos focos",
        r"detección de ([\d\.]+|[A-Za-záéíóúñ]+) nuevo foco",
    ):
        match = re.search(pattern, full_text, re.IGNORECASE)
        if match:
            new_foci = parse_spanish_number(match.group(1))
            break

    new_positives = int_from_match(r"incluye(?:n)? un total de ([\d\.]+) casos", full_text)
    total_foci = int_from_match(r"ascienden a ([\d\.]+) los focos notificados", full_text)
    primary_foci = int_from_match(r"([\d\.]+) de ellos primarios y ([\d\.]+) secundarios", full_text, group=1)
    secondary_foci = int_from_match(r"([\d\.]+) de ellos primarios y ([\d\.]+) secundarios", full_text, group=2)
    total_positives = int_from_match(r"incluyen un total de ([\d\.]+) casos/jabal[ií]es positivos", full_text)
    negatives_total = int_from_match(
        r"se han analizado otros ([\d\.]+) animales que han resultado negativos",
        full_text,
    )
    negatives_captured = int_from_match(
        r"de los cuales ([\d\.]+) corresponden a animales capturados o abatidos",
        full_text,
    )
    negatives_passive = int_from_match(
        r"y ([\d\.]+) se han investigado por vigilancia pasiva",
        full_text,
    )
    farms = int_from_match(r"en las ([\d\.]+) explotaciones comerciales", full_text)
    municipalities_raw_match = re.search(r"municipios:\s*(.+?)\s*\(ver mapa 2\)", full_text, re.IGNORECASE)
    municipalities = split_municipalities(municipalities_raw_match.group(1) if municipalities_raw_match else None)

    first_new_municipality_match = re.search(
        r"por primera vez un caso en este último municipio",
        full_text,
        re.IGNORECASE,
    )
    zone = "Zona restringida II" if re.search(r"zona restringida II", full_text, re.IGNORECASE) else None
    domestic_status = "Sin casos positivos en cerdo doméstico" if re.search(
        r"sin haberse detectado ningún caso positivo en cerdo doméstico",
        full_text,
        re.IGNORECASE,
    ) else "Revisar nota oficial"

    measures = extract_measures(full_text, farms)

    return {
        "pdf_url": pdf_url,
        "pdf_bytes": pdf_bytes,
        "full_text": full_text,
        "report_date": note_date,
        "previous_note_date": previous_note_date,
        "new_foci": new_foci,
        "new_positives": new_positives,
        "total_foci": total_foci,
        "primary_foci": primary_foci,
        "secondary_foci": secondary_foci,
        "total_positives": total_positives,
        "negatives_total": negatives_total,
        "negatives_captured": negatives_captured,
        "negatives_passive": negatives_passive,
        "farms": farms,
        "municipalities": municipalities,
        "zone": zone,
        "domestic_status": domestic_status,
        "first_new_municipality_flag": bool(first_new_municipality_match),
        "measures": measures,
    }


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def build_history(notes: list[dict[str, Any]], max_items: int = 12) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for note in notes[:max_items]:
        try:
            stats = parse_note(note["url"])
            rows.append(
                {
                    "Fecha": stats["report_date"],
                    "Nuevos focos": stats["new_foci"],
                    "Nuevos positivos": stats["new_positives"],
                    "Focos totales": stats["total_foci"],
                    "Positivos totales": stats["total_positives"],
                    "Negativos analizados": stats["negatives_total"],
                    "URL": note["url"],
                }
            )
        except Exception:
            continue

    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows).sort_values("Fecha").reset_index(drop=True)
    return df


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def get_mercolleida_snapshot() -> dict[str, Any]:
    result: dict[str, Any] = {
        "home_date": None,
        "detail_date": None,
        "comment_date": None,
        "comment_text": None,
        "access_denied": None,
        "price_source": None,
        "price_date": None,
        "price_value": None,
        "price_unit": None,
        "price_delta": None,
        "price_delta_pct": None,
    }

    # 1) Web principal de Mercolleida
    try:
        html = fetch_text(MERCOLLEIDA_HOME_URL)
        text = normalize_text(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))

        home_date_match = re.search(
            r"Últimas cotizaciones del mercado ganadero\s+(\d{2}/\d{2}/\d{4})",
            text,
            re.IGNORECASE,
        )
        if home_date_match:
            result["home_date"] = parse_date_generic(home_date_match.group(1))

        comment_match = re.search(
            r"Comentario porcino - (\d{2}/\d{2}/\d{4})\s+(.*?)(?:Comentario|Suscríbete|$)",
            text,
            re.IGNORECASE,
        )
        if comment_match:
            result["comment_date"] = parse_date_generic(comment_match.group(1))
            result["comment_text"] = normalize_text(comment_match.group(2))
    except Exception:
        pass

    # 2) Página de cerdo cebado de Mercolleida
    try:
        html = fetch_text(MERCOLLEIDA_CERDO_URL)
        text = normalize_text(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
        detail_date_match = re.search(r"Última actualización:\s*(\d{2}/\d{2}/\d{4})", text, re.IGNORECASE)
        if detail_date_match:
            result["detail_date"] = parse_date_generic(detail_date_match.group(1))
        result["access_denied"] = "Acceso denegado" in text
    except Exception:
        pass

    # 3) Fallback público de precio numérico vía 3tres3
    try:
        html = fetch_text(THREETHREE_PRICES_URL)
        text = normalize_text(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
        price_match = re.search(
            r"Mercolleida\s+Vivo\s+(\d{1,2}\s+[A-Za-záéíóúñ]{3})\s+([\d,\.]+)\s+EUR\s+kg\s+([\-\d,\.]+)\s+([\-\d,\.]+%)",
            text,
            re.IGNORECASE,
        )
        if price_match:
            result["price_source"] = "3tres3"
            result["price_date"] = price_match.group(1)
            result["price_value"] = price_match.group(2).replace(".", "").replace(",", ".")
            result["price_unit"] = "EUR/kg vivo"
            result["price_delta"] = price_match.group(3).replace(".", "").replace(",", ".")
            result["price_delta_pct"] = price_match.group(4)
    except Exception:
        pass

    return result


def format_date_es(value: date | None) -> str:
    if value is None:
        return "N/D"
    return value.strftime("%d/%m/%Y")


def metric_delta(current: int | None, previous: int | None) -> str | None:
    if current is None or previous is None:
        return None
    delta = current - previous
    if delta == 0:
        return "0"
    return f"{delta:+d}"


def render_sources(latest_note: dict[str, Any], merco: dict[str, Any]) -> None:
    with st.sidebar:
        st.header("Fuentes")
        st.markdown(f"- [Noticias sanidad animal MAPA]({MAPA_NEWS_URL})")
        st.markdown(f"- [Ficha PPA MAPA]({MAPA_DISEASE_URL})")
        st.markdown(f"- [Mercolleida]({MERCOLLEIDA_HOME_URL})")
        st.markdown(f"- [Cerdo cebado Mercolleida]({MERCOLLEIDA_CERDO_URL})")
        st.markdown(f"- [Cotizaciones 3tres3]({THREETHREE_PRICES_URL})")
        st.divider()
        st.caption(f"Última nota MAPA cargada: {format_date_es(latest_note.get('report_date'))}")
        if merco.get("home_date"):
            st.caption(f"Última fecha ganadero Mercolleida: {format_date_es(merco['home_date'])}")
        st.caption("La app relee Internet en cada ejecución y cachea resultados 6 horas.")


# =========================
# Interfaz
# =========================
st.title("🐗 Actualización automática PPA España")
st.caption("Panel Streamlit para Comité de Dirección: cifras clave, mapas oficiales, medidas administrativas y mercado porcino.")

col_a, col_b = st.columns([1, 4])
with col_a:
    if st.button("🔄 Forzar actualización", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
with col_b:
    st.info(
        "Pensado para ejecutarse en GitHub/Streamlit Cloud. La app consulta las fuentes online cada vez que se abre y renueva la caché cada 6 horas."
    )

try:
    notes = get_mapa_notes()
except Exception as exc:
    st.error(f"No se ha podido leer la página de noticias del MAPA: {exc}")
    st.stop()

if not notes:
    st.error("No se han encontrado notas oficiales de PPA en Cataluña en la página del MAPA.")
    st.stop()

latest_meta = notes[0]
previous_meta = notes[1] if len(notes) > 1 else None

try:
    latest_note = parse_note(latest_meta["url"])
except Exception as exc:
    st.error(f"No se ha podido procesar la última nota oficial del MAPA: {exc}")
    st.stop()

previous_note = None
if previous_meta:
    try:
        previous_note = parse_note(previous_meta["url"])
    except Exception:
        previous_note = None

merco = get_mercolleida_snapshot()
render_sources(latest_note, merco)

# Ajustes finales de métricas si alguna no sale explícita en la nota
if latest_note.get("new_foci") is None and previous_note:
    if latest_note.get("total_foci") is not None and previous_note.get("total_foci") is not None:
        latest_note["new_foci"] = latest_note["total_foci"] - previous_note["total_foci"]

if latest_note.get("new_positives") is None and previous_note:
    if latest_note.get("total_positives") is not None and previous_note.get("total_positives") is not None:
        latest_note["new_positives"] = latest_note["total_positives"] - previous_note["total_positives"]

st.subheader("Resumen ejecutivo")
st.markdown(
    f"**Última nota oficial MAPA:** {format_date_es(latest_note.get('report_date'))}  \\n"
    f"**Nota anterior:** {format_date_es(previous_note.get('report_date')) if previous_note else 'N/D'}  \\n"
    f"**Ámbito principal:** {latest_note.get('zone') or 'Revisar nota'}  \\n"
    f"**Estado en porcino doméstico:** {latest_note.get('domestic_status')}"
)

m1, m2, m3, m4 = st.columns(4)
m1.metric(
    "Nuevos focos",
    latest_note.get("new_foci") if latest_note.get("new_foci") is not None else "N/D",
    delta=None,
)
m2.metric(
    "Nuevos positivos",
    latest_note.get("new_positives") if latest_note.get("new_positives") is not None else "N/D",
    delta=metric_delta(latest_note.get("total_positives"), previous_note.get("total_positives") if previous_note else None),
)
m3.metric(
    "Focos totales",
    latest_note.get("total_foci") if latest_note.get("total_foci") is not None else "N/D",
    delta=metric_delta(latest_note.get("total_foci"), previous_note.get("total_foci") if previous_note else None),
)
m4.metric(
    "Positivos totales",
    latest_note.get("total_positives") if latest_note.get("total_positives") is not None else "N/D",
    delta=metric_delta(latest_note.get("total_positives"), previous_note.get("total_positives") if previous_note else None),
)

m5, m6, m7, m8 = st.columns(4)
m5.metric(
    "Negativos analizados",
    f"{latest_note['negatives_total']:,}".replace(",", ".") if latest_note.get("negatives_total") is not None else "N/D",
)
m6.metric(
    "Negativos capturados/abatidos",
    f"{latest_note['negatives_captured']:,}".replace(",", ".") if latest_note.get("negatives_captured") is not None else "N/D",
)
m7.metric(
    "Negativos vigilancia pasiva",
    f"{latest_note['negatives_passive']:,}".replace(",", ".") if latest_note.get("negatives_passive") is not None else "N/D",
)
m8.metric(
    "Explotaciones vigiladas",
    latest_note.get("farms") if latest_note.get("farms") is not None else "N/D",
)

if latest_note.get("municipalities"):
    st.write("**Municipios con positivos acumulados**")
    st.write(", ".join(latest_note["municipalities"]))

with st.expander("Enlace y descarga de la última nota oficial"):
    st.link_button("Abrir PDF oficial MAPA", latest_meta["url"])
    st.download_button(
        "Descargar última nota MAPA",
        data=latest_note["pdf_bytes"],
        file_name=f"PPA_MAPA_{format_date_es(latest_note.get('report_date')).replace('/', '')}.pdf",
        mime="application/pdf",
    )

# Tabs principales
tab1, tab2, tab3, tab4, tab5 = st.tabs(
    ["📍 Cifras y lectura", "🗺️ Mapas", "🛡️ Medidas", "📈 Evolución", "💶 Mercado"]
)

with tab1:
    left, right = st.columns([1.3, 1])
    with left:
        st.write("### Lectura rápida")
        bullets = []
        if latest_note.get("previous_note_date"):
            bullets.append(f"La nota compara frente a la actualización del {format_date_es(latest_note['previous_note_date'])}.")
        if latest_note.get("primary_foci") is not None and latest_note.get("secondary_foci") is not None:
            bullets.append(
                f"Los focos acumulados se desglosan en {latest_note['primary_foci']} primarios y {latest_note['secondary_foci']} secundarios."
            )
        if latest_note.get("zone"):
            bullets.append(f"Los hallazgos reportados se mantienen en {latest_note['zone']}.")
        if latest_note.get("domestic_status"):
            bullets.append(f"Situación en cerdo doméstico: {latest_note['domestic_status'].lower()}.")
        for item in bullets:
            st.markdown(f"- {item}")

        st.write("### Últimas notas detectadas")
        for note in notes[:8]:
            st.markdown(
                f"- [{note['report_date'].strftime('%d/%m/%Y')}]({note['url']}) — {note['title']}"
            )

    with right:
        st.write("### Texto base para briefing")
        briefing = []
        if latest_note.get("new_foci") is not None:
            briefing.append(f"Nuevos focos: {latest_note['new_foci']}")
        if latest_note.get("new_positives") is not None:
            briefing.append(f"Nuevos positivos: {latest_note['new_positives']}")
        if latest_note.get("total_foci") is not None:
            briefing.append(f"Focos totales: {latest_note['total_foci']}")
        if latest_note.get("total_positives") is not None:
            briefing.append(f"Positivos acumulados: {latest_note['total_positives']}")
        if latest_note.get("negatives_total") is not None:
            briefing.append(f"Negativos analizados: {latest_note['negatives_total']:,}".replace(",", "."))
        if latest_note.get("domestic_status"):
            briefing.append(latest_note["domestic_status"])
        st.code("\n".join(briefing) if briefing else "Sin datos extraídos", language="text")

with tab2:
    st.write("### Mapas oficiales de la última nota")
    try:
        img_map_1 = render_pdf_page(latest_note["pdf_bytes"], 1)
        st.image(img_map_1, caption="Página 2 del PDF oficial: zonas restringidas y distribución de focos/casos.")
    except Exception as exc:
        st.warning(f"No se ha podido renderizar la página 2 del PDF: {exc}")

    try:
        img_map_2 = render_pdf_page(latest_note["pdf_bytes"], 2)
        st.image(img_map_2, caption="Página 3 del PDF oficial: vallados en puntos de riesgo y medidas en granjas.")
    except Exception as exc:
        st.warning(f"No se ha podido renderizar la página 3 del PDF: {exc}")

with tab3:
    st.write("### Medidas que está aplicando la administración")
    if latest_note.get("measures"):
        for measure in latest_note["measures"]:
            st.markdown(f"- {measure}")
    else:
        st.warning("No se han podido resumir automáticamente las medidas desde la nota actual.")

    st.write("### Observación operativa")
    st.info(
        "Las medidas se extraen de la nota oficial más reciente. Si MAPA cambia la redacción, la app seguirá mostrando el PDF y los mapas, aunque alguna frase-resumen pueda requerir ajuste de regex."
    )

with tab4:
    st.write("### Evolución de las últimas notas oficiales")
    history_df = build_history(notes, max_items=12)
    if history_df.empty:
        st.warning("No se ha podido construir el histórico automático.")
    else:
        chart_df = history_df.set_index("Fecha")[["Focos totales", "Positivos totales"]]
        st.line_chart(chart_df)
        st.dataframe(
            history_df.drop(columns=["URL"]),
            use_container_width=True,
            hide_index=True,
        )
        csv_bytes = history_df.to_csv(index=False).encode("utf-8-sig")
        st.download_button(
            "Descargar histórico CSV",
            data=csv_bytes,
            file_name="ppa_historico_mapa.csv",
            mime="text/csv",
        )

with tab5:
    st.write("### Cerdo cebado / Mercolleida")

    k1, k2, k3 = st.columns(3)
    k1.metric(
        "Última fecha ganadero Mercolleida",
        format_date_es(merco.get("home_date")),
    )
    k2.metric(
        "Detalle cerdo cebado Mercolleida",
        format_date_es(merco.get("detail_date")),
    )
    k3.metric(
        "Acceso detalle",
        "Restringido" if merco.get("access_denied") else "Abierto/No detectado",
    )

    if merco.get("price_value"):
        delta_text = None
        if merco.get("price_delta"):
            try:
                delta_float = float(merco["price_delta"])
                delta_text = f"{delta_float:+.3f} €/kg"
            except ValueError:
                delta_text = merco["price_delta"]
        st.metric(
            "Precio Mercolleida (fallback público)",
            f"{float(merco['price_value']):.3f} €/kg vivo",
            delta=delta_text,
        )
        st.caption(
            f"Fuente pública secundaria: {merco['price_source']} · fecha visible en mercado: {merco.get('price_date') or 'N/D'}"
        )
    else:
        st.warning(
            "No se ha podido recuperar la cotización numérica en esta ejecución. La app conserva las fechas de actualización y el comentario de mercado, pero el detalle de Mercolleida puede quedar bajo acceso restringido."
        )

    if merco.get("comment_text"):
        st.write("### Último comentario porcino visible")
        st.info(f"{format_date_es(merco.get('comment_date'))}: {merco['comment_text']}")

    st.link_button("Abrir Mercolleida", MERCOLLEIDA_HOME_URL)
    st.link_button("Abrir mercado cerdo cebado Mercolleida", MERCOLLEIDA_CERDO_URL)

st.divider()
st.caption(
    "Nota técnica: esta versión está diseñada para actualizarse automáticamente desde Internet al abrirse. "
    "Si más adelante quieres actualización desatendida sin abrir la app, conviene añadir un workflow de GitHub Actions o un job programado en tu despliegue."
)
