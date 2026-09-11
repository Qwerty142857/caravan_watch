"""Build postcodes.json: a centre point for every UK postcode district.

Source: the ONS Postcode Directory (ONSPD, latest) served from the ONS Open
Geography portal under the Open Government Licence v3. The full directory is
1.8 million live postcodes — far too much to ship, and far too slow to page
through — so this samples postcodes whose unit part ends "AA", "AB", "AD" or
"AE" and averages them by district. Every sector has at least one of those, so
a district is covered as soon as any of its sectors is; four passes take the
coverage past 99%.

A district centre built this way lands a mile or two from any particular
address inside it, which doesn't show against the 5-50 mile radii the map
searches with, and it keeps the whole country in one small file with nothing
to look up online at page-load time.

    python build_postcodes.py > postcodes.json
"""
import json, re, sys, time, urllib.parse, urllib.request
from collections import defaultdict

BASE = ('https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/'
        'ONSPD_Online_latest_Postcode_Centroids/FeatureServer/0/query')
LIVE = 'DOTERM IS NULL'          # postcodes not yet terminated
UNITS = ['AA', 'AB', 'AD', 'AE']
PAGE = 2000

DISTRICT_RE = re.compile(r'^([A-Z]{1,2}\d[A-Z\d]?)\s')


def fetch(unit, after_id):
    """One page of a sample, walking forward by OBJECTID.

    The portal refuses deep resultOffset paging on this table, so each page
    asks for the next block of ids instead.
    """
    q = urllib.parse.urlencode({
        'where': f"{LIVE} AND PCDS LIKE '%{unit}' AND OBJECTID > {after_id}",
        'outFields': 'OBJECTID,PCDS,LAT,LONG',
        'returnGeometry': 'false',
        'orderByFields': 'OBJECTID',
        'resultRecordCount': PAGE,
        'f': 'json',
    })
    for attempt in range(4):
        try:
            with urllib.request.urlopen(BASE + '?' + q, timeout=180) as r:
                data = json.loads(r.read().decode('utf-8'))
        except Exception as e:                  # the portal rate-limits now and then
            if attempt == 3:
                raise
            sys.stderr.write(f'  retrying {unit} after {after_id}: {e}\n')
            time.sleep(3)
            continue
        if 'error' in data:
            if attempt == 3:
                raise SystemExit('ONS query failed: ' + json.dumps(data['error']))
            time.sleep(3)
            continue
        return data


def main():
    points = defaultdict(list)
    seen = 0
    for unit in UNITS:
        last_id = 0
        while True:
            rows = fetch(unit, last_id).get('features', [])
            if not rows:
                break
            for f in rows:
                a = f['attributes']
                last_id = max(last_id, a['OBJECTID'])
                pcds, lat, lon = a.get('PCDS'), a.get('LAT'), a.get('LONG')
                if not pcds or lat is None or lon is None:
                    continue
                if lat > 61.5 or lat < 49:      # ONSPD parks unlocatable ones at (99.999, 0)
                    continue
                m = DISTRICT_RE.match(pcds.upper())
                if m:
                    points[m.group(1)].append((lat, lon))
            seen += len(rows)
            if len(rows) < PAGE:
                break
        sys.stderr.write(f'  ...{unit}: {seen} sampled postcodes, {len(points)} districts\n')

    out = [{
        'district': code,
        'latitude': round(sum(p[0] for p in pts) / len(pts), 5),
        'longitude': round(sum(p[1] for p in pts) / len(pts), 5),
        'samples': len(pts),
    } for code, pts in sorted(points.items())]

    sys.stderr.write(f'{len(out)} districts from {seen} sampled postcodes\n')
    json.dump(out, sys.stdout, separators=(',', ':'))


if __name__ == '__main__':
    main()
