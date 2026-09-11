"""Rebuild the county geometry embedded in map.html.

Source: ONS "Counties and Unitary Authorities (December 2025) Boundaries UK BUC"
(ultra-generalised, Open Government Licence v3) — 218 areas, fetched live from
the ONS Open Geography portal.

England's unitary authorities, metropolitan districts and London boroughs are
merged into the ceremonial county they belong to; Wales keeps its 22 principal
areas and Scotland its 32 council areas, which are the names the parks data and
holidaymakers actually use. Northern Ireland is left out, as the map has always
been GB-only. The result is 101 counties.

Output is a single line of JSON, written to counties.json, which map.html
fetches for its "search by county" sidebar. Nothing is drawn from it — the map
itself still shows the ten regions — it exists so a park can be placed in a
county by its own coordinates rather than by the patchy county text in
parks.json. For each county it holds:
  rings  polygon outlines already projected into the map's 580x760 viewBox
  pin    label anchor (centre of the largest ring)
  fill   colour, chosen so no two neighbouring counties share one
plus `projection`, the constants map.html uses to put a park's longitude and
latitude into that same space.

    python build_county_map.py > counties.json
"""
import io, json, math, sys, urllib.request
from collections import defaultdict

ONS = ('https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/'
       'Counties_and_Unitary_Authorities_December_2025_Boundaries_UK_BUC/'
       'FeatureServer/0/query?where=1%3D1&outFields=*&outSR=4326&f=geojson'
       '&resultRecordCount=400')

# --- English unitaries / districts -> ceremonial county ---------------------
TO_CEREMONIAL = {
    'Bolton': 'Greater Manchester', 'Bury': 'Greater Manchester', 'Manchester': 'Greater Manchester',
    'Oldham': 'Greater Manchester', 'Rochdale': 'Greater Manchester', 'Salford': 'Greater Manchester',
    'Stockport': 'Greater Manchester', 'Tameside': 'Greater Manchester', 'Trafford': 'Greater Manchester',
    'Wigan': 'Greater Manchester',
    'Knowsley': 'Merseyside', 'Liverpool': 'Merseyside', 'Sefton': 'Merseyside',
    'St. Helens': 'Merseyside', 'Wirral': 'Merseyside',
    'Barnsley': 'South Yorkshire', 'Doncaster': 'South Yorkshire', 'Rotherham': 'South Yorkshire',
    'Sheffield': 'South Yorkshire',
    'Gateshead': 'Tyne and Wear', 'Newcastle upon Tyne': 'Tyne and Wear',
    'North Tyneside': 'Tyne and Wear', 'South Tyneside': 'Tyne and Wear', 'Sunderland': 'Tyne and Wear',
    'Birmingham': 'West Midlands', 'Coventry': 'West Midlands', 'Dudley': 'West Midlands',
    'Sandwell': 'West Midlands', 'Solihull': 'West Midlands', 'Walsall': 'West Midlands',
    'Wolverhampton': 'West Midlands',
    'Bradford': 'West Yorkshire', 'Calderdale': 'West Yorkshire', 'Kirklees': 'West Yorkshire',
    'Leeds': 'West Yorkshire', 'Wakefield': 'West Yorkshire',
    'Bath and North East Somerset': 'Somerset',
    'Bedford': 'Bedfordshire',
    'Blackburn with Darwen': 'Lancashire',
    'Blackpool': 'Lancashire',
    'Bournemouth, Christchurch and Poole': 'Dorset',
    'Bracknell Forest': 'Berkshire',
    'Brighton and Hove': 'East Sussex',
    'Bristol, City of': 'Bristol',
    'Central Bedfordshire': 'Bedfordshire',
    'Cheshire East': 'Cheshire',
    'Cheshire West and Chester': 'Cheshire',
    'Cumberland': 'Cumbria',
    'Darlington': 'County Durham',
    'Derby': 'Derbyshire',
    'Halton': 'Cheshire',
    'Hartlepool': 'County Durham',
    'Herefordshire, County of': 'Herefordshire',
    'Isles of Scilly': 'Cornwall',
    'Kingston upon Hull, City of': 'East Riding of Yorkshire',
    'Leicester': 'Leicestershire',
    'Luton': 'Bedfordshire',
    'Medway': 'Kent',
    'Middlesbrough': 'North Yorkshire',
    'Milton Keynes': 'Buckinghamshire',
    'North East Lincolnshire': 'Lincolnshire',
    'North Lincolnshire': 'Lincolnshire',
    'North Northamptonshire': 'Northamptonshire',
    'North Somerset': 'Somerset',
    'Nottingham': 'Nottinghamshire',
    'Peterborough': 'Cambridgeshire',
    'Plymouth': 'Devon',
    'Portsmouth': 'Hampshire',
    'Reading': 'Berkshire',
    'Redcar and Cleveland': 'North Yorkshire',
    'Slough': 'Berkshire',
    'South Gloucestershire': 'Gloucestershire',
    'Southampton': 'Hampshire',
    'Southend-on-Sea': 'Essex',
    'Stockton-on-Tees': 'County Durham',   # ceremonially split by the Tees
    'Stoke-on-Trent': 'Staffordshire',
    'Swindon': 'Wiltshire',
    'Telford and Wrekin': 'Shropshire',
    'Thurrock': 'Essex',
    'Torbay': 'Devon',
    'Warrington': 'Cheshire',
    'West Berkshire': 'Berkshire',
    'West Northamptonshire': 'Northamptonshire',
    'Westmorland and Furness': 'Cumbria',
    'Windsor and Maidenhead': 'Berkshire',
    'Wokingham': 'Berkshire',
    'York': 'North Yorkshire',
}

W, H, MARGIN = 580, 760, 8
TOL = 0.0035          # simplification, in degrees (~300 m; 1 px is ~1.6 km)
MIN_AREA = 0.0016     # square degrees: drops specks, keeps Anglesey/Wight/Scilly
PALETTE = ['#12203F', '#387FD6', '#1C305F', '#235FA9', '#1C4C87',
           '#2970C7', '#16264B', '#20386F', '#0F1A33', '#192C57']


def county_for(props):
    code, name = props['CTYUA25CD'], props['CTYUA25NM']
    if code.startswith('N'):
        return None
    if code.startswith('E09'):
        return 'Greater London'
    return TO_CEREMONIAL.get(name, name)


def rings_of(geom):
    if geom['type'] == 'Polygon':
        return [geom['coordinates'][0]]
    if geom['type'] == 'MultiPolygon':
        return [poly[0] for poly in geom['coordinates']]
    return []


def key(pt):
    return (round(pt[0], 6), round(pt[1], 6))


def dissolve(rings):
    """Merge touching rings by cancelling shared edges, then re-stitch.

    ONS areas are cut from one topology, so the border between two of them is
    the same run of vertices in both: walk every edge, drop the ones seen from
    both sides, and sew what's left back into rings.
    """
    edges = defaultdict(int)
    for ring in rings:
        pts = [key(p) for p in ring]
        if pts[0] != pts[-1]:
            pts.append(pts[0])
        for a, b in zip(pts, pts[1:]):
            if a != b:
                edges[(a, b) if a < b else (b, a)] += 1

    kept = [e for e, n in edges.items() if n % 2 == 1]
    if not kept:
        return None
    adj = defaultdict(list)
    for a, b in kept:
        adj[a].append(b)
        adj[b].append(a)

    seen, out = set(), []
    for start in list(adj):
        if start in seen:
            continue
        ring, cur, prev = [start], start, None
        seen.add(start)
        while True:
            nxt = next((c for c in adj[cur] if c != prev and c not in seen), None)
            if nxt is None:
                if start in adj[cur] and len(ring) > 2:
                    ring.append(start)
                break
            ring.append(nxt)
            seen.add(nxt)
            prev, cur = cur, nxt
        if len(ring) > 3:
            out.append([list(p) for p in ring])
    return out or None


def ring_area(ring):
    a = 0.0
    for (x1, y1), (x2, y2) in zip(ring, ring[1:] + ring[:1]):
        a += x1 * y2 - x2 * y1
    return abs(a) / 2


def simplify(ring, tol):
    if len(ring) < 4:
        return ring

    def seg_dist(p, a, b):
        (px, py), (ax, ay), (bx, by) = p, a, b
        dx, dy = bx - ax, by - ay
        if dx == 0 and dy == 0:
            return math.hypot(px - ax, py - ay)
        t = max(0, min(1, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
        return math.hypot(px - (ax + t * dx), py - (ay + t * dy))

    keep = [False] * len(ring)
    keep[0] = keep[-1] = True
    stack = [(0, len(ring) - 1)]
    while stack:
        i, j = stack.pop()
        worst, at = tol, None
        for k in range(i + 1, j):
            d = seg_dist(ring[k], ring[i], ring[j])
            if d > worst:
                worst, at = d, k
        if at is not None:
            keep[at] = True
            stack += [(i, at), (at, j)]
    return [p for p, k in zip(ring, keep) if k]


def main():
    with urllib.request.urlopen(ONS, timeout=180) as r:
        src = json.loads(r.read().decode('utf-8'))
    sys.stderr.write(f'{len(src["features"])} ONS areas\n')

    pieces = defaultdict(list)
    for f in src['features']:
        name = county_for(f['properties'])
        if name:
            pieces[name].extend(rings_of(f['geometry']))

    # which counties touch which — used to colour neighbours differently
    owner, adjacency = {}, defaultdict(set)
    for name, rings in pieces.items():
        for ring in rings:
            pts = [key(p) for p in ring]
            for a, b in zip(pts, pts[1:]):
                e = (a, b) if a < b else (b, a)
                other = owner.setdefault(e, name)
                if other != name:
                    adjacency[name].add(other)
                    adjacency[other].add(name)

    geo = {}
    for name, rings in pieces.items():
        merged = dissolve(rings) or [[list(p) for p in r] for r in rings]
        out = []
        for r in merged:
            if ring_area(r) < MIN_AREA:
                continue
            s = simplify(r, TOL)
            if len(s) >= 4:
                out.append(s)
        if not out:
            out = [simplify(max(merged, key=ring_area), TOL / 2)]
        geo[name] = out

    lons = [x for v in geo.values() for r in v for x, y in r]
    lats = [y for v in geo.values() for r in v for x, y in r]
    lon0, lon1, lat0, lat1 = min(lons), max(lons), min(lats), max(lats)
    cosm = math.cos(math.radians((lat0 + lat1) / 2))
    span_w, span_h = (lon1 - lon0) * cosm, lat1 - lat0
    k = min((W - 2 * MARGIN) / span_w, (H - 2 * MARGIN) / span_h)
    ox, oy = (W - span_w * k) / 2, (H - span_h * k) / 2

    def project(x, y):
        return [round(ox + (x - lon0) * cosm * k, 1), round(oy + (lat1 - y) * k, 1)]

    order = sorted(geo, key=lambda n: (-len(adjacency[n]), n))
    colour = {}
    for name in order:
        taken = {colour[o] for o in adjacency[name] if o in colour}
        colour[name] = next((c for c in PALETTE if c not in taken), PALETTE[0])

    counties = {}
    for name, rings in geo.items():
        pr = [[project(x, y) for x, y in r] for r in rings]
        big = max(pr, key=ring_area)
        counties[name] = {
            'rings': pr,
            'pin': [round(sum(p[0] for p in big) / len(big), 1),
                    round(sum(p[1] for p in big) / len(big), 1)],
            'fill': colour[name],
        }

    sys.stderr.write(f'{len(counties)} counties, '
                     f'{sum(len(r) for c in counties.values() for r in c["rings"])} points\n')
    json.dump({
        'viewBox': f'0 0 {W} {H}',
        'projection': {'lon0': round(lon0, 6), 'lat1': round(lat1, 6),
                       'cos': round(cosm, 8), 'k': round(k, 6),
                       'ox': round(ox, 4), 'oy': round(oy, 4)},
        'order': sorted(counties),
        'counties': counties,
    }, sys.stdout, separators=(',', ':'))


if __name__ == '__main__':
    main()
