"""Export Infoantenas stations and their assigned frequency ranges to dataset/stations_raw.csv.

Run this file with Python 3.8 or later. The CSV opens in Excel. Each frequency
cell contains the site's reported MHz ranges separated by semicolons. A failed
detail request is reported in the terminal and leaves that frequency cell blank.
"""

import csv
import html
import json
import math
import re
import sys
import tempfile
import time
import unicodedata
from html.parser import HTMLParser
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import urlopen

from settings import (
    API_URL,
    API_ZOOM,
    BASE_URL,
    RAW_STATIONS_FILE as OUTPUT_FILE,
    REQUEST_ATTEMPTS,
    REQUEST_PAUSE,
    REQUEST_TIMEOUT,
    STUDY_BOUNDS,
    TILE_SIZE_DEGREES,
)


STATION_COLUMNS = (
    "site_id",
    "longitude",
    "latitude",
    "operator",
    "supported_technologies",
)
OUTPUT_COLUMNS = STATION_COLUMNS + ("frequency_bands_mhz",)
FREQUENCY_RANGE = re.compile(r"(\d+(?:[.,]\d+)?)\s*[-–—−]\s*(\d+(?:[.,]\d+)?)")


class TableRows(HTMLParser):
    """Collect data rows from the detail page's nested tables."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.stack = []

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            for parent_row in self.stack:
                parent_row["nested"] = True
            self.stack.append({"cells": [], "cell": None, "nested": False})
        elif tag in ("td", "th") and self.stack:
            self.stack[-1]["cell"] = []
        elif tag == "br" and self.stack and self.stack[-1]["cell"] is not None:
            self.stack[-1]["cell"].append(" ")

    def handle_data(self, data):
        if self.stack and self.stack[-1]["cell"] is not None:
            self.stack[-1]["cell"].append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self.stack:
            row = self.stack[-1]
            if row["cell"] is not None:
                cell_text = " ".join("".join(row["cell"]).split())
                row["cells"].append(cell_text)
                row["cell"] = None
        elif tag == "tr" and self.stack:
            row = self.stack.pop()
            if not row["nested"] and row["cells"]:
                self.rows.append(row["cells"])


def make_tiles(bounds):
    """Split the study rectangle into tiles clipped at its north and east edges."""
    west, south, east, north = bounds
    column_count = math.ceil((east - west) / TILE_SIZE_DEGREES)
    row_count = math.ceil((north - south) / TILE_SIZE_DEGREES)

    for column in range(column_count):
        left = west + column * TILE_SIZE_DEGREES
        right = min(east, west + (column + 1) * TILE_SIZE_DEGREES)
        for row in range(row_count):
            bottom = south + row * TILE_SIZE_DEGREES
            top = min(north, south + (row + 1) * TILE_SIZE_DEGREES)
            yield left, bottom, right, top


def fetch_text(url):
    """Retrieve a response using the site's declared encoding."""
    for attempt in range(1, REQUEST_ATTEMPTS + 1):
        try:
            with urlopen(url, timeout=REQUEST_TIMEOUT) as response:
                encoding = response.headers.get_content_charset() or "utf-8"
                return response.read().decode(encoding)
        except HTTPError as error:
            if error.code < 500 or attempt == REQUEST_ATTEMPTS:
                raise
        except (URLError, TimeoutError):
            if attempt == REQUEST_ATTEMPTS:
                raise
        time.sleep(attempt)


def fetch_tile(tile):
    """Request the GeoJSON features for one tile."""
    parameters = urlencode(
        {
            "idCapa": "null",
            "bbox": ",".join(f"{coordinate:.9f}" for coordinate in tile),
            "zoom": API_ZOOM,
        }
    )
    data = json.loads(fetch_text(f"{API_URL}?{parameters}"))

    # The viewer's endpoint sometimes returns an empty object for empty tiles.
    if data == {}:
        return []
    if not isinstance(data, dict) or not isinstance(data.get("features"), list):
        raise ValueError("Expected a GeoJSON feature list from Infoantenas.")
    return data["features"]


def normalize_label(label):
    normalized = unicodedata.normalize("NFKD", label.casefold())
    return "".join(character for character in normalized if character.isalnum())


def read_technologies(properties):
    """Copy an explicit technology value when the API includes one."""
    technology_fields = {
        "supportedtechnologies",
        "technology",
        "technologies",
        "tecnologia",
        "tecnologias",
        "tecnologiasoportadas",
        "tecnologiassoportadas",
        "gistecnologia",
        "gistecnologias",
    }
    for name, value in properties.items():
        normalized = normalize_label(name)
        if normalized not in technology_fields or value is None or value == "":
            continue
        if isinstance(value, (list, dict)):
            return json.dumps(value, ensure_ascii=False)
        return value
    return ""


def extract_station(feature, bounds):
    """Extract the five requested columns from a point in the study area."""
    geometry = feature.get("geometry") or {}
    if geometry.get("type") != "Point":
        return None

    longitude, latitude = geometry["coordinates"][:2]
    west, south, east, north = bounds
    if not (west <= longitude <= east and south <= latitude <= north):
        return None

    properties = feature.get("properties") or {}
    full_code = (
        properties.get("Gis_Codigo")
        or properties.get("Código")
        or feature.get("id")
        or ""
    )
    # The compound code is typically "OPERATOR - SITE_ID".
    company, _, code = str(full_code).rpartition(" - ")
    site_id = properties.get("Gis_ID")
    if site_id is None or site_id == "":
        site_id = code
    operator = (
        properties.get("Operador")
        or properties.get("operator")
        or properties.get("Organización")
        or company
    )

    return {
        "site_id": str(site_id).strip(),
        "longitude": longitude,
        "latitude": latitude,
        "operator": str(operator).strip(),
        "supported_technologies": read_technologies(properties),
    }


def station_detail_url(reference):
    """Resolve the station's detail reference and encode its query parameters."""
    if not reference:
        raise ValueError("The station record has no detail-page reference.")

    reference = html.unescape(reference).strip()
    reference = reference.replace("@@<url-aplicacion>", BASE_URL)
    parts = urlsplit(urljoin(f"{BASE_URL}/", reference))
    expected = urlsplit(f"{BASE_URL}/detalleEstacion.do")
    if (parts.scheme, parts.netloc, parts.path) != (
        expected.scheme,
        expected.netloc,
        expected.path,
    ):
        raise ValueError("The station detail reference has an unexpected URL.")

    query = urlencode(parse_qsl(parts.query, keep_blank_values=True))
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, ""))


def parse_frequency_bands(page):
    """Read MHz ranges from the assigned-band column in the technical table."""
    parser = TableRows()
    parser.feed(page)
    parser.close()

    band_column = None
    bands = []
    for cells in parser.rows:
        labels = [normalize_label(cell) for cell in cells]
        if any(
            "nivelesmedidos" in label or "levelsmeasured" in label for label in labels
        ):
            break

        if "operador" in labels or "operator" in labels:
            header = next(
                (
                    index
                    for index, label in enumerate(labels)
                    if "bandaasignada" in label or "assignedband" in label
                ),
                None,
            )
            if header is not None:
                band_column = header
                continue

        if band_column is not None and band_column < len(cells):
            for lower, upper in FREQUENCY_RANGE.findall(cells[band_column]):
                bands.append(f"{lower} - {upper}")

    if band_column is None:
        raise ValueError("The assigned frequency band table was not found.")
    return "; ".join(dict.fromkeys(bands))


def add_frequency_bands(records):
    """Read each distinct station detail URL and fill the frequency column."""
    bands_by_url = {}
    for number, (station, reference) in enumerate(records, start=1):
        station["frequency_bands_mhz"] = ""
        try:
            url = station_detail_url(reference)
            if url not in bands_by_url:
                try:
                    page = fetch_text(url)
                    bands_by_url[url] = parse_frequency_bands(page)
                finally:
                    time.sleep(REQUEST_PAUSE)
            station["frequency_bands_mhz"] = bands_by_url[url]
        except (OSError, ValueError) as error:
            print(
                f"Frequency bands unavailable for {station['site_id']} "
                f"({station['operator']}). {error}",
                file=sys.stderr,
            )

        if number % 25 == 0 or number == len(records):
            print(
                f"Checked frequency details for {number}/{len(records)} stations",
                flush=True,
            )


def write_csv(records, path):
    """Save the completed table with an Excel-compatible UTF-8 encoding."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8-sig",
            newline="",
            dir=path.parent,
            prefix="stations_raw_",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            writer = csv.DictWriter(handle, fieldnames=OUTPUT_COLUMNS)
            writer.writeheader()
            writer.writerows(records)
        temporary_path.replace(path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def main():
    tiles = list(make_tiles(STUDY_BOUNDS))
    records = []
    seen = set()

    for number, tile in enumerate(tiles, start=1):
        try:
            features = fetch_tile(tile)
            for feature in features:
                station = extract_station(feature, STUDY_BOUNDS)
                if station is None:
                    continue
                # Remove repeated rows at shared tile boundaries.
                key = tuple(station[column] for column in STATION_COLUMNS)
                if key not in seen:
                    seen.add(key)
                    properties = feature.get("properties") or {}
                    records.append((station, properties.get("Detalle", "")))
        except (OSError, ValueError, TypeError, KeyError) as error:
            raise SystemExit(
                f"Tile {number}/{len(tiles)} {tile} failed. {error}"
            ) from error
        print(
            f"Tile {number}/{len(tiles)} returned {len(features)} features", flush=True
        )
        if number < len(tiles):
            time.sleep(REQUEST_PAUSE)

    add_frequency_bands(records)
    write_csv((station for station, _ in records), OUTPUT_FILE)
    print(f"Saved {len(records)} station records to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
