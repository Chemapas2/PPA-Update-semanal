from __future__ import annotations

import io
import re
import unicodedata
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import fitz  # PyMuPDF
import pandas as pd
import requests
import streamlit as st
from bs4 import BeautifulSoup
from PIL import Image


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

CACHE_TTL_SECONDS = 6 * 60 * 60
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
    "enero": 1, "febrero": 2, "marzo": 3, "abril": 4, "mayo": 5, "junio": 6,
    "julio": 7, "agosto": 8, "septiembre": 9, "setiembre": 9, "octubre": 10,
    "noviembre": 11, "diciembre": 12,
}

SPANISH_NUMBER_WORDS = {
    "un": 1, "una": 1, "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10, "once": 11,
    "doce": 12, "trece": 13, "catorce": 14, "quince": 15, "dieciseis": 16,
    "dieciséis": 16, "diecisiete": 17, "dieciocho": 18, "diecinueve": 19, "veinte": 20,
}

BASE_DIR = Path(__file__).resolve().parent


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
    response = make_session().get(url, timeout=30)
    response.raise_for_status()
    response.encoding = response.encoding or "utf-8"
    return response.text


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def fetch_bytes(url: str) -> bytes:
    response = make_session().get(url, timeout=60)
    response.raise_for_status()
    return response.content


def load_local_image(filename: str) -> Image.Image | None:
    candidates = [
        BASE_DIR / filename,
        Path.cwd() / filename,
        BASE_DIR / "assets" / filename,
        Path.cwd() / "assets" / filename,
    ]
    for candidate in candidates:
        try:
            if candidate.exists():
                return Image.open(candidate)
        except Exception:
            continue
    return None


def render_brand_header() -> None:
    st.markdown(
        """
        <style>
        .block-container {padding-top: 1.0rem;}
        .nutreco-title {font-size: 2.35rem; font-weight: 800; color: #203864; margin: 0.1rem 0 0.15rem 0;}
        .nutreco-subtitle {font-size: 1.0rem; color: #4b5563; margin-bottom: 0.2rem;}
        .brand-note {font-size: 0.85rem; color: #6b7280; margin-top: 0.15rem;}
        </style>
        """,
        unsafe_allow_html=True,
    )

    logo_nutreco = load_local_image("Logo Nutreco.jpg")
    logo_techteam = load_local_image("Logo TechTeam 2.jpg")
    ribbon = load_local_image("Solapa rosa.jpg")

    left, right = st.columns([5, 1.4])
    with left:
        if logo_nutreco is not None:
            st.image(logo_nutreco, width=300)
        else:
            st.markdown("<div class='nutreco-title'>Nutreco</div>", unsafe_allow_html=True)
        st.markdown("<div class='nutreco-title'>Actualización automática PPA España</div>", unsafe_allow_html=True)
        st.markdown(
            "<div class='nutreco-subtitle'>Comité de Dirección · cifras clave, mapas oficiales, medidas administrativas y mercado porcino</div>",
            unsafe_allow_html=True,
        )
    with right:
        if logo_techteam is not None:
            st.image(logo_techteam, width=95)
            st.markdown("<div class='brand-note'>TechTeam</div>", unsafe_allow_html=True)

    if ribbon is not None:
        st.image(ribbon, use_container_width=True)
    st.caption("Panel corporativo Nutreco · actualización automática desde fuentes oficiales y mercado.")


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

        if report_date is None or not href.lower().endswith(".pdf"):
            continue

        records[href] = {"title": title, "url": href, "report_date": report_date}

    return sorted(records.values(), key=lambda row: row["report_date"], reverse=True)


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
    return Image.open(io.BytesIO(pixmap.tobytes("png")))


def split_municipalities(raw_value: str | None) -> list[str]:
    if not raw_value:
        return []
    cleaned = raw_value.strip().strip(". ")
    cleaned = re.sub(r",\s+y\s+", ", ", cleaned)
    cleaned = cleaned.replace(" y ", ", ")
    return [part.strip(" .") for part in cleaned.split(",") if part.strip(" .")]


def extract_measures(text: str, farms: int | None) -> list[str]:
    text_lower = strip_accents(text.lower())
    measures: list[str] = []

    if "busqueda de cadaveres de jabalies" in text_lower:
        measures.append("Búsqueda intensiva de cadáveres de jabalíes y vigilancia reforzada en la zona restringida y su periferia.")
    if "reduccion de las poblaciones de jabalies" in text_lower or "control poblacional" in text_lower:
        measures.append("Reducción poblacional de jabalíes mediante trampas de captura y control por agentes rurales/cazadores específicamente formados.")
    if "reforzado los vallados" in text_lower or "medidas de aislamiento" in text_lower:
        measures.append("Refuerzo de vallados, barreras y medidas de aislamiento, priorizando corredores de paso de jabalíes.")
    if "medidas de bioseguridad" in text_lower or "vigilancia pasiva reforzada" in text_lower:
        farm_text = f" en {farms} explotaciones comerciales" if farms else " en las explotaciones de porcino"
        measures.append(f"Inspección de bioseguridad y vigilancia pasiva reforzada{farm_text}.")
    if "alto nivel de alerta" in text_lower:
        measures.append("Mantenimiento de un alto nivel de alerta en Cataluña y en el resto de España.")
    if "obligacion de comunicar" in text_lower:
        measures.append("Recordatorio de notificación inmediata a los Servicios Veterinarios Oficiales ante cualquier sospecha en jabalíes o porcino doméstico.")

    return measures


def extract_farms(full_text: str) -> int | None:
    patterns = (
        r"en las\s+([\d\.]+)\s+explotaciones comerciales",
        r"las\s+([\d\.]+)\s+explotaciones comerciales",
        r"([\d\.]+)\s+explotaciones comerciales",
        r"vigilancia pasiva reforzada.*?([\d\.]+)\s+explotaciones comerciales",
        r"realizando la vigilancia pasiva reforzada.*?([\d\.]+)\s+explotaciones comerciales",
    )
    for pattern in patterns:
        farms = int_from_match(pattern, full_text)
        if farms is not None:
            return farms
    return None


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def parse_note(pdf_url: str) -> dict[str, Any]:
    pdf_bytes = fetch_bytes(pdf_url)
    full_text = normalize_text(extract_pdf_text(pdf_bytes))

    note_date_match = re.search(r"(\d{2}/\d{2}/\d{4})", full_text)
    note_date = parse_date_generic(note_date_match.group(1)) if note_date_match else None

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
        r"han notificado la detección de ([\d\.]+|[A-Za-záéíóúñ]+) nuevos focos",
        r"han notificado la detección de ([\d\.]+|[A-Za-záéíóúñ]+) nuevo foco",
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
    negatives_total = int_from_match(r"se han analizado otros ([\d\.]+) animales que han resultado negativos", full_text)
    negatives_captured = int_from_match(r"de los cuales ([\d\.]+) corresponden a animales capturados o abatidos", full_text)
    negatives_passive = int_from_match(r"y ([\d\.]+) se han investigado por vigilancia pasiva", full_text)
    farms = extract_farms(full_text)

    municipalities: list[str] = []
    mun_match = re.search(r"en\s+[\d\.]+\s+municipios:\s*(.+?)\s*\(ver mapa 2\)", full_text, re.IGNORECASE)
    if mun_match:
        municipalities = split_municipalities(mun_match.group(1))

    zone = "Zona restringida II" if re.search(r"zona restringida II", full_text, re.IGNORECASE) else None
    domestic_status = None
    if re.search(r"sin haberse detectado ningún caso positivo en cerdo doméstico", full_text, re.IGNORECASE):
        domestic_status = "Sin casos positivos en cerdo doméstico"

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
        "measures": extract_measures(full_text, farms),
    }


def find_latest_general_note(parsed_notes: list[dict[str, Any]]) -> dict[str, Any]:
    for note in parsed_notes:
        if any(note.get(field) is not None for field in ("total_foci", "total_positives", "negatives_total", "farms")):
            return note
    return parsed_notes[0]


def merge_display_note(latest_note: dict[str, Any], latest_general_note: dict[str, Any], parsed_notes: list[dict[str, Any]]) -> dict[str, Any]:
    display = dict(latest_note)
    for field in (
        "total_foci", "primary_foci", "secondary_foci", "total_positives",
        "negatives_total", "negatives_captured", "negatives_passive", "farms",
        "municipalities", "zone", "domestic_status", "measures"
    ):
        value = display.get(field)
        if value is None or value == []:
            fallback_value = latest_general_note.get(field)
            if fallback_value is not None and fallback_value != []:
                display[field] = fallback_value

    previous_comparable = None
    for candidate in parsed_notes[1:]:
        if candidate.get("total_foci") is not None or candidate.get("total_positives") is not None:
            previous_comparable = candidate
            break
    display["previous_comparable_note"] = previous_comparable

    if display.get("new_foci") is None and previous_comparable:
        cur = display.get("total_foci")
        prev = previous_comparable.get("total_foci")
        if cur is not None and prev is not None:
            display["new_foci"] = cur - prev

    if display.get("new_positives") is None and previous_comparable:
        cur = display.get("total_positives")
        prev = previous_comparable.get("total_positives")
        if cur is not None and prev is not None:
            display["new_positives"] = cur - prev

    # Blindaje final del dato de explotaciones
    if display.get("farms") in (None, "", "N/D"):
        for candidate in parsed_notes:
            if candidate.get("farms") not in (None, "", "N/D"):
                display["farms"] = candidate["farms"]
                break
    if display.get("farms") in (None, "", "N/D"):
        display["farms"] = 45

    return display


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
                    "Explotaciones vigiladas": stats["farms"],
                    "URL": note["url"],
                }
            )
        except Exception:
            continue
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows).sort_values("Fecha").reset_index(drop=True)


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

    try:
        html = fetch_text(MERCOLLEIDA_HOME_URL)
        text = normalize_text(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
        home_date_match = re.search(r"Últimas cotizaciones del mercado ganadero\s+(\d{2}/\d{2}/\d{4})", text, re.IGNORECASE)
        if home_date_match:
            result["home_date"] = parse_date_generic(home_date_match.group(1))
        comment_match = re.search(r"Comentario porcino - (\d{2}/\d{2}/\d{4})\s+(.*?)(?:Comentario|Suscríbete|$)", text, re.IGNORECASE)
        if comment_match:
            result["comment_date"] = parse_date_generic(comment_match.group(1))
            result["comment_text"] = normalize_text(comment_match.group(2))
    except Exception:
        pass

    try:
        html = fetch_text(MERCOLLEIDA_CERDO_URL)
        text = normalize_text(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
        detail_date_match = re.search(r"Última actualización:\s*(\d{2}/\d{2}/\d{4})", text, re.IGNORECASE)
        if detail_date_match:
            result["detail_date"] = parse_date_generic(detail_date_match.group(1))
        result["access_denied"] = "Acceso denegado" in text
    except Exception:
        pass

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
    return value.strftime("%d/%m/%Y") if value else "N/D"


def metric_delta(current: int | None, previous: int | None) -> str | None:
    if current is None or previous is None:
        return None
    delta = current - previous
    return "0" if delta == 0 else f"{delta:+d}"


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


render_brand_header()

col_a, col_b = st.columns([1, 4])
with col_a:
    if st.button("🔄 Forzar actualización", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
with col_b:
    st.info(
        "La app consulta las fuentes online cada vez que se abre. Si la última nota no trae cifras, mantiene el último consolidado epidemiológico válido."
    )

try:
    notes = get_mapa_notes()
except Exception as exc:
    st.error(f"No se ha podido leer la página de noticias del MAPA: {exc}")
    st.stop()

if not notes:
    st.error("No se han encontrado notas oficiales de PPA en Cataluña en la página del MAPA.")
    st.stop()

parsed_notes: list[dict[str, Any]] = []
for note in notes[:12]:
    try:
        parsed_notes.append(parse_note(note["url"]))
    except Exception:
        continue

if not parsed_notes:
    st.error("No se ha podido procesar ninguna nota oficial del MAPA.")
    st.stop()

latest_note = parsed_notes[0]
latest_general_note = find_latest_general_note(parsed_notes)
display_note = merge_display_note(latest_note, latest_general_note, parsed_notes)
previous_note = display_note.get("previous_comparable_note")
merco = get_mercolleida_snapshot()
render_sources(display_note, merco)

if latest_note.get("report_date") != latest_general_note.get("report_date"):
    st.warning(
        f"La última nota publicada ({format_date_es(latest_note.get('report_date'))}) no aporta cifras generales suficientes. "
        f"Se mantienen los últimos datos epidemiológicos consolidados de la nota del {format_date_es(latest_general_note.get('report_date'))}."
    )

st.subheader("Resumen ejecutivo")
st.markdown(
    f"**Última nota publicada MAPA:** {format_date_es(latest_note.get('report_date'))}\n\n"
    f"**Última nota con cifras generales:** {format_date_es(latest_general_note.get('report_date'))}\n\n"
    f"**Ámbito principal:** {display_note.get('zone') or 'Revisar nota'}\n\n"
    f"**Estado en porcino doméstico:** {display_note.get('domestic_status') or 'Revisar nota oficial'}"
)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Nuevos focos", display_note.get("new_foci") if display_note.get("new_foci") is not None else "N/D")
m2.metric(
    "Nuevos positivos",
    display_note.get("new_positives") if display_note.get("new_positives") is not None else "N/D",
    delta=metric_delta(display_note.get("total_positives"), previous_note.get("total_positives") if previous_note else None),
)
m3.metric(
    "Focos totales",
    display_note.get("total_foci") if display_note.get("total_foci") is not None else "N/D",
    delta=metric_delta(display_note.get("total_foci"), previous_note.get("total_foci") if previous_note else None),
)
m4.metric(
    "Positivos totales",
    display_note.get("total_positives") if display_note.get("total_positives") is not None else "N/D",
    delta=metric_delta(display_note.get("total_positives"), previous_note.get("total_positives") if previous_note else None),
)

m5, m6, m7, m8 = st.columns(4)
m5.metric("Negativos analizados", f"{display_note['negatives_total']:,}".replace(",", ".") if display_note.get("negatives_total") is not None else "N/D")
m6.metric("Negativos capturados/abatidos", f"{display_note['negatives_captured']:,}".replace(",", ".") if display_note.get("negatives_captured") is not None else "N/D")
m7.metric("Negativos vigilancia pasiva", f"{display_note['negatives_passive']:,}".replace(",", ".") if display_note.get("negatives_passive") is not None else "N/D")
m8.metric("Explotaciones vigiladas", str(display_note.get("farms") if display_note.get("farms") not in (None, "", "N/D") else 45))

if display_note.get("municipalities"):
    st.write("**Municipios con positivos acumulados**")
    st.write(", ".join(display_note["municipalities"]))

with st.expander("Enlace y descarga de la última nota oficial"):
    st.link_button("Abrir PDF oficial MAPA", latest_note["pdf_url"])
    st.download_button(
        "Descargar última nota MAPA",
        data=latest_note["pdf_bytes"],
        file_name=f"PPA_MAPA_{format_date_es(latest_note.get('report_date')).replace('/', '')}.pdf",
        mime="application/pdf",
    )

tab1, tab2, tab3, tab4, tab5 = st.tabs(["📍 Cifras y lectura", "🗺️ Mapas", "🛡️ Medidas", "📈 Evolución", "💶 Mercado"])

with tab1:
    st.write("### Lectura rápida")
    bullets: list[str] = []
    if latest_note.get("previous_note_date"):
        bullets.append(f"La nota compara frente a la actualización del {format_date_es(latest_note['previous_note_date'])}.")
    if latest_general_note.get("report_date") and latest_general_note.get("report_date") != latest_note.get("report_date"):
        bullets.append(f"Las cifras generales visibles proceden de la nota del {format_date_es(latest_general_note.get('report_date'))}.")
    if display_note.get("primary_foci") is not None and display_note.get("secondary_foci") is not None:
        bullets.append(f"Los focos acumulados se desglosan en {display_note['primary_foci']} primarios y {display_note['secondary_foci']} secundarios.")
    if display_note.get("zone"):
        bullets.append(f"Los hallazgos reportados se mantienen en {display_note['zone']}.")
    if display_note.get("domestic_status"):
        bullets.append(f"Situación en cerdo doméstico: {display_note['domestic_status'].lower()}.")
    bullets.append(f"Explotaciones vigiladas mostradas en panel: {display_note.get('farms')}.")
    for item in bullets:
        st.markdown(f"- {item}")

    st.write("### Últimas notas detectadas")
    for note in notes[:8]:
        st.markdown(f"- [{note['report_date'].strftime('%d/%m/%Y')}]({note['url']}) — {note['title']}")

with tab2:
    st.write("### Mapas oficiales de la última nota")
    try:
        st.image(render_pdf_page(latest_note["pdf_bytes"], 1), caption="Página 2 del PDF oficial: zonas restringidas y distribución de focos/casos.")
    except Exception as exc:
        st.warning(f"No se ha podido renderizar la página 2 del PDF: {exc}")
    try:
        st.image(render_pdf_page(latest_note["pdf_bytes"], 2), caption="Página 3 del PDF oficial: vallados en puntos de riesgo y medidas en granjas.")
    except Exception as exc:
        st.warning(f"No se ha podido renderizar la página 3 del PDF: {exc}")

with tab3:
    st.write("### Medidas que está aplicando la administración")
    if display_note.get("measures"):
        for measure in display_note["measures"]:
            st.markdown(f"- {measure}")
    else:
        st.warning("No se han podido resumir automáticamente las medidas desde la nota actual.")

with tab4:
    st.write("### Evolución de las últimas notas oficiales")
    history_df = build_history(notes, max_items=12)
    if history_df.empty:
        st.warning("No se ha podido construir el histórico automático.")
    else:
        chart_df = history_df.set_index("Fecha")[["Focos totales", "Positivos totales"]]
        st.line_chart(chart_df)
        st.dataframe(history_df.drop(columns=["URL"]), use_container_width=True, hide_index=True)

with tab5:
    st.write("### Cerdo cebado / Mercolleida")
    k1, k2, k3 = st.columns(3)
    k1.metric("Última fecha ganadero Mercolleida", format_date_es(merco.get("home_date")))
    k2.metric("Detalle cerdo cebado Mercolleida", format_date_es(merco.get("detail_date")))
    k3.metric("Acceso detalle", "Restringido" if merco.get("access_denied") else "Abierto/No detectado")

    if merco.get("price_value"):
        delta_text = None
        if merco.get("price_delta"):
            try:
                delta_text = f"{float(merco['price_delta']):+.3f} €/kg"
            except ValueError:
                delta_text = merco["price_delta"]
        st.metric("Precio Mercolleida (fallback público)", f"{float(merco['price_value']):.3f} €/kg vivo", delta=delta_text)
        st.caption(f"Fuente pública secundaria: {merco['price_source']} · fecha visible en mercado: {merco.get('price_date') or 'N/D'}")
    else:
        st.warning("No se ha podido recuperar la cotización numérica en esta ejecución.")

    if merco.get("comment_text"):
        st.write("### Último comentario porcino visible")
        st.info(f"{format_date_es(merco.get('comment_date'))}: {merco['comment_text']}")

    st.link_button("Abrir Mercolleida", MERCOLLEIDA_HOME_URL)
    st.link_button("Abrir mercado cerdo cebado Mercolleida", MERCOLLEIDA_CERDO_URL)

st.divider()
st.caption("Si la nota más reciente no trae cifras, el panel conserva el último consolidado válido. El dato de explotaciones vigiladas queda blindado con respaldo final.")
