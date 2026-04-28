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


st.set_page_config(page_title="PPA España - Comité de Dirección", page_icon="🐗", layout="wide")

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

GENERAL_FIELDS = [
    "total_foci",
    "primary_foci",
    "secondary_foci",
    "total_positives",
    "negatives_total",
    "negatives_captured",
    "negatives_passive",
    "farms",
    "municipalities",
    "zone",
    "domestic_status",
    "measures",
]


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
    response = make_session().get(url, timeout=30)
    response.raise_for_status()
    response.encoding = response.encoding or "utf-8"
    return response.text


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def fetch_bytes(url: str) -> bytes:
    response = make_session().get(url, timeout=60)
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
        if not report_date or not href.lower().endswith(".pdf"):
            continue
        records[href] = {"title": title, "url": href, "report_date": report_date}

    return sorted(records.values(), key=lambda row: row["report_date"], reverse=True)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def extract_pdf_text(pdf_bytes: bytes, max_pages: int | None = None) -> str:
    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        page_count = doc.page_count if max_pages is None else min(doc.page_count, max_pages)
        return "\n".join(doc.load_page(i).get_text("text") for i in range(page_count))


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
    if "busqueda" in text_lower and "cadaveres" in text_lower and "jabalies" in text_lower:
        measures.append("Búsqueda intensiva de cadáveres de jabalíes y vigilancia reforzada en la zona restringida y su periferia.")
    if "reduccion de las poblaciones de jabalies" in text_lower or "control poblacional" in text_lower:
        measures.append("Reducción poblacional de jabalíes mediante trampas de captura y control por agentes rurales/cazadores específicamente formados.")
    if "vallados" in text_lower or "barreras" in text_lower or "medidas de aislamiento" in text_lower:
        measures.append("Refuerzo de vallados, barreras y medidas de aislamiento, priorizando corredores de paso de jabalíes.")
    if "medidas de bioseguridad" in text_lower:
        farm_text = f" en {farms} explotaciones comerciales" if farms else " en las explotaciones de porcino"
        measures.append(f"Inspección de bioseguridad y vigilancia pasiva reforzada{farm_text}.")
    if "alto nivel de alerta" in text_lower:
        measures.append("Mantenimiento de un alto nivel de alerta en Cataluña y en el resto de España.")
    if "obligacion de comunicar" in text_lower:
        measures.append("Recordatorio de notificación inmediata a los Servicios Veterinarios Oficiales ante cualquier sospecha en jabalíes o porcino doméstico.")
    return measures


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def parse_note(pdf_url: str) -> dict[str, Any]:
    pdf_bytes = fetch_bytes(pdf_url)
    full_text = normalize_text(extract_pdf_text(pdf_bytes))

    date_match = re.search(r"(\d{2}[/.]\d{2}[/.]\d{4})", full_text)
    note_date = parse_date_generic(date_match.group(1)) if date_match else None

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
    negatives_total = int_from_match(r"se han analizado otros ([\d\.]+) animales que han resultado negativos", full_text)
    negatives_captured = int_from_match(r"de los cuales ([\d\.]+) corresponden a animales capturados o abatidos", full_text)
    negatives_passive = int_from_match(r"y ([\d\.]+) se han investigado por vigilancia pasiva", full_text)
    farms = int_from_match(r"en las ([\d\.]+) explotaciones comerciales", full_text)

    municipalities = []
    municipalities_match = re.search(r"en\s+(\d+)\s+municipios:\s*(.+?)\s*\(ver mapa 2\)", full_text, re.IGNORECASE)
    if municipalities_match:
        municipalities = split_municipalities(municipalities_match.group(2))

    zone = "Zona restringida II" if re.search(r"zona restringida II", full_text, re.IGNORECASE) else None
    domestic_status = None
    if re.search(r"sin haberse detectado ningún caso positivo en cerdo doméstico", full_text, re.IGNORECASE):
        domestic_status = "Sin casos positivos en cerdo doméstico"

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
        "measures": measures,
    }


def has_general_data(note: dict[str, Any]) -> bool:
    return any(note.get(field) not in (None, [], "") for field in [
        "total_foci", "total_positives", "negatives_total", "farms", "municipalities", "domestic_status"
    ])


def overlay_general_data(base_note: dict[str, Any], fallback_note: dict[str, Any] | None) -> dict[str, Any]:
    merged = dict(base_note)
    if not fallback_note:
        return merged
    for field in GENERAL_FIELDS:
        current = merged.get(field)
        if current in (None, [], ""):
            merged[field] = fallback_note.get(field)
    return merged


def compute_new_value(current_total: int | None, previous_total: int | None) -> int | None:
    if current_total is None or previous_total is None:
        return None
    delta = current_total - previous_total
    return delta if delta >= 0 else None


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def build_notes_bundle(max_notes: int = 12) -> dict[str, Any]:
    notes = get_mapa_notes()
    parsed: list[dict[str, Any]] = []
    for meta in notes[:max_notes]:
        try:
            note = parse_note(meta["url"])
            note["title"] = meta["title"]
            note["url"] = meta["url"]
            parsed.append(note)
        except Exception:
            continue

    if not parsed:
        raise RuntimeError("No se ha podido procesar ninguna nota del MAPA.")

    latest_note = parsed[0]
    latest_general_note = next((n for n in parsed if has_general_data(n)), latest_note)

    latest_display = overlay_general_data(latest_note, latest_general_note)

    previous_general_note = None
    passed_latest_general = False
    for note in parsed:
        if note["url"] == latest_general_note["url"]:
            passed_latest_general = True
            continue
        if passed_latest_general and has_general_data(note):
            previous_general_note = note
            break

    if latest_display.get("new_foci") is None:
        latest_display["new_foci"] = compute_new_value(
            latest_display.get("total_foci"),
            previous_general_note.get("total_foci") if previous_general_note else None,
        )
    if latest_display.get("new_positives") is None:
        latest_display["new_positives"] = compute_new_value(
            latest_display.get("total_positives"),
            previous_general_note.get("total_positives") if previous_general_note else None,
        )

    rows = []
    for note in parsed:
        if not has_general_data(note):
            continue
        rows.append({
            "Fecha": note.get("report_date"),
            "Nuevos focos": note.get("new_foci"),
            "Nuevos positivos": note.get("new_positives"),
            "Focos totales": note.get("total_foci"),
            "Positivos totales": note.get("total_positives"),
            "Negativos analizados": note.get("negatives_total"),
            "URL": note.get("url"),
        })
    history_df = pd.DataFrame(rows).sort_values("Fecha").reset_index(drop=True) if rows else pd.DataFrame()

    return {
        "notes": notes,
        "parsed_notes": parsed,
        "latest_note": latest_note,
        "latest_general_note": latest_general_note,
        "previous_general_note": previous_general_note,
        "display_note": latest_display,
        "history_df": history_df,
    }


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
        "price_delta": None,
    }

    try:
        html = fetch_text(MERCOLLEIDA_HOME_URL)
        text = normalize_text(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
        m = re.search(r"Comentario porcino - (\d{2}/\d{2}/\d{4})\s+(.*?)(?:Comentario|Suscríbete|$)", text, re.IGNORECASE)
        if m:
            result["comment_date"] = parse_date_generic(m.group(1))
            result["comment_text"] = normalize_text(m.group(2))
        m = re.search(r"Últimas cotizaciones del mercado ganadero\s+(\d{2}/\d{2}/\d{4})", text, re.IGNORECASE)
        if m:
            result["home_date"] = parse_date_generic(m.group(1))
    except Exception:
        pass

    try:
        html = fetch_text(MERCOLLEIDA_CERDO_URL)
        text = normalize_text(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
        m = re.search(r"Última actualización:\s*(\d{2}/\d{2}/\d{4})", text, re.IGNORECASE)
        if m:
            result["detail_date"] = parse_date_generic(m.group(1))
        result["access_denied"] = "Acceso denegado" in text
    except Exception:
        pass

    try:
        html = fetch_text(THREETHREE_PRICES_URL)
        text = normalize_text(BeautifulSoup(html, "html.parser").get_text(" ", strip=True))
        m = re.search(
            r"Mercolleida\s+Vivo\s+(\d{1,2}\s+[A-Za-záéíóúñ]{3})\s+([\d,\.]+)\s+EUR\s+kg\s+([\-\d,\.]+)",
            text,
            re.IGNORECASE,
        )
        if m:
            result["price_source"] = "3tres3"
            result["price_date"] = m.group(1)
            result["price_value"] = m.group(2).replace(".", "").replace(",", ".")
            result["price_delta"] = m.group(3).replace(".", "").replace(",", ".")
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


st.title("🐗 Actualización automática PPA España")
st.caption("Panel Streamlit para Comité de Dirección: cifras clave, mapas oficiales, medidas administrativas y mercado porcino.")

c1, c2 = st.columns([1, 4])
with c1:
    if st.button("🔄 Forzar actualización", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
with c2:
    st.info("Esta versión mantiene siempre las cifras generales. Si la última nota del MAPA no trae focos/positivos, la app reutiliza automáticamente el último consolidado válido.")

try:
    bundle = build_notes_bundle(max_notes=15)
except Exception as exc:
    st.error(f"No se ha podido construir la actualización automática desde MAPA: {exc}")
    st.stop()

notes = bundle["notes"]
latest_note = bundle["latest_note"]
latest_general_note = bundle["latest_general_note"]
display_note = bundle["display_note"]
previous_general_note = bundle["previous_general_note"]
history_df = bundle["history_df"]
merco = get_mercolleida_snapshot()

with st.sidebar:
    st.header("Fuentes")
    st.markdown(f"- [Noticias sanidad animal MAPA]({MAPA_NEWS_URL})")
    st.markdown(f"- [Ficha PPA MAPA]({MAPA_DISEASE_URL})")
    st.markdown(f"- [Mercolleida]({MERCOLLEIDA_HOME_URL})")
    st.caption(f"Última nota publicada MAPA: {format_date_es(latest_note.get('report_date'))}")
    st.caption(f"Última nota con cifras generales: {format_date_es(latest_general_note.get('report_date'))}")

st.subheader("Resumen ejecutivo")
if latest_note.get("url") != latest_general_note.get("url"):
    st.warning(
        f"La última comunicación del MAPA ({format_date_es(latest_note.get('report_date'))}) no aporta cifras epidemiológicas completas. "
        f"Se mantienen los últimos datos generales válidos de la nota del {format_date_es(latest_general_note.get('report_date'))}."
    )

st.markdown(
    f"**Última nota publicada MAPA:** {format_date_es(latest_note.get('report_date'))}  \n"
    f"**Última nota con cifras generales:** {format_date_es(latest_general_note.get('report_date'))}  \n"
    f"**Ámbito principal:** {display_note.get('zone') or 'Revisar nota'}  \n"
    f"**Estado en porcino doméstico:** {display_note.get('domestic_status') or 'Revisar nota oficial'}"
)

m1, m2, m3, m4 = st.columns(4)
m1.metric("Nuevos focos", display_note.get("new_foci") if display_note.get("new_foci") is not None else "N/D")
m2.metric("Nuevos positivos", display_note.get("new_positives") if display_note.get("new_positives") is not None else "N/D")
m3.metric(
    "Focos totales",
    display_note.get("total_foci") if display_note.get("total_foci") is not None else "N/D",
    delta=metric_delta(display_note.get("total_foci"), previous_general_note.get("total_foci") if previous_general_note else None),
)
m4.metric(
    "Positivos totales",
    display_note.get("total_positives") if display_note.get("total_positives") is not None else "N/D",
    delta=metric_delta(display_note.get("total_positives"), previous_general_note.get("total_positives") if previous_general_note else None),
)

m5, m6, m7, m8 = st.columns(4)
m5.metric("Negativos analizados", f"{display_note['negatives_total']:,}".replace(",", ".") if display_note.get("negatives_total") is not None else "N/D")
m6.metric("Negativos capturados/abatidos", f"{display_note['negatives_captured']:,}".replace(",", ".") if display_note.get("negatives_captured") is not None else "N/D")
m7.metric("Negativos vigilancia pasiva", f"{display_note['negatives_passive']:,}".replace(",", ".") if display_note.get("negatives_passive") is not None else "N/D")
m8.metric("Explotaciones vigiladas", display_note.get("farms") if display_note.get("farms") is not None else "N/D")

if display_note.get("municipalities"):
    st.write("**Municipios con positivos acumulados**")
    st.write(", ".join(display_note["municipalities"]))


tab1, tab2, tab3, tab4, tab5 = st.tabs(["📍 Cifras y lectura", "🗺️ Mapas", "🛡️ Medidas", "📈 Evolución", "💶 Mercado"])

with tab1:
    st.write("### Lectura rápida")
    bullets = []
    if latest_general_note.get("primary_foci") is not None and latest_general_note.get("secondary_foci") is not None:
        bullets.append(f"Desglose acumulado: {latest_general_note['primary_foci']} focos primarios y {latest_general_note['secondary_foci']} secundarios.")
    if display_note.get("zone"):
        bullets.append(f"Los datos generales se muestran para {display_note['zone']}.")
    if display_note.get("domestic_status"):
        bullets.append(display_note["domestic_status"] + ".")
    if latest_note.get("url") != latest_general_note.get("url"):
        bullets.append("La última nota publicada se usa para mapas y contexto, pero las cifras generales proceden de la última nota con datos epidemiológicos completos.")
    for item in bullets:
        st.markdown(f"- {item}")

    st.write("### Últimas notas detectadas")
    for note in notes[:8]:
        st.markdown(f"- [{note['report_date'].strftime('%d/%m/%Y')}]({note['url']}) — {note['title']}")

    st.write("### Texto base para briefing")
    briefing = [
        f"Última nota publicada MAPA: {format_date_es(latest_note.get('report_date'))}",
        f"Última nota con cifras generales: {format_date_es(latest_general_note.get('report_date'))}",
        f"Nuevos focos: {display_note.get('new_foci', 'N/D')}",
        f"Nuevos positivos: {display_note.get('new_positives', 'N/D')}",
        f"Focos totales: {display_note.get('total_foci', 'N/D')}",
        f"Positivos acumulados: {display_note.get('total_positives', 'N/D')}",
        f"Negativos analizados: {f'{display_note.get('negatives_total'):,}'.replace(',', '.') if display_note.get('negatives_total') is not None else 'N/D'}",
        f"Estado en cerdo doméstico: {display_note.get('domestic_status') or 'Revisar nota oficial'}",
    ]
    st.code("\n".join(briefing), language="text")

with tab2:
    st.write("### Mapas oficiales")
    map_source = latest_note
    try:
        st.image(render_pdf_page(map_source["pdf_bytes"], 1), caption=f"Página 2 del PDF oficial ({format_date_es(map_source.get('report_date'))}).")
    except Exception as exc:
        st.warning(f"No se ha podido renderizar la página 2 del PDF: {exc}")
    try:
        st.image(render_pdf_page(map_source["pdf_bytes"], 2), caption=f"Página 3 del PDF oficial ({format_date_es(map_source.get('report_date'))}).")
    except Exception as exc:
        st.warning(f"No se ha podido renderizar la página 3 del PDF: {exc}")

with tab3:
    st.write("### Medidas que está aplicando la administración")
    if display_note.get("measures"):
        for measure in display_note["measures"]:
            st.markdown(f"- {measure}")
    else:
        st.warning("No se han podido resumir automáticamente las medidas.")

with tab4:
    st.write("### Evolución de las últimas notas con cifras")
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
    if merco.get("comment_text"):
        st.info(f"{format_date_es(merco.get('comment_date'))}: {merco['comment_text']}")

st.divider()
st.caption("Versión simplificada: solo necesitas sustituir main.py. La app busca la última nota publicada y, si esa nota no trae cifras, mantiene automáticamente el último consolidado válido.")
