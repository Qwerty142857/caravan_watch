# Builds towns.json: a searchable list of UK towns with coordinates, used by
# map.html's "parks near a town" search.
#   * every distinct town named in parks.json, placed at the centroid of that
#     town's parks (source "park-centroid")
#   * a curated list of well-known UK towns and cities placed at their town
#     centre, which both adds places with no park of their own and corrects
#     the centroid for places that do (source "reference")
import json, re, io
from collections import defaultdict

CURATED = [
 ("London", 51.5074, -0.1278), ("Birmingham", 52.4862, -1.8904), ("Manchester", 53.4808, -2.2426),
 ("Leeds", 53.8008, -1.5491), ("Liverpool", 53.4084, -2.9916), ("Sheffield", 53.3811, -1.4701),
 ("Bristol", 51.4545, -2.5879), ("Newcastle upon Tyne", 54.9783, -1.6178), ("Nottingham", 52.9548, -1.1581),
 ("Leicester", 52.6369, -1.1398), ("Coventry", 52.4068, -1.5197), ("Bradford", 53.7960, -1.7594),
 ("Stoke-on-Trent", 53.0027, -2.1794), ("Wolverhampton", 52.5870, -2.1288), ("Plymouth", 50.3755, -4.1427),
 ("Southampton", 50.9097, -1.4044), ("Portsmouth", 50.8198, -1.0880), ("Reading", 51.4543, -0.9781),
 ("Derby", 52.9225, -1.4746), ("Brighton", 50.8225, -0.1372), ("Kingston upon Hull", 53.7676, -0.3274),
 ("Preston", 53.7632, -2.7031), ("Milton Keynes", 52.0406, -0.7594), ("Northampton", 52.2405, -0.9027),
 ("Norwich", 52.6309, 1.2974), ("Luton", 51.8787, -0.4200), ("Swindon", 51.5558, -1.7797),
 ("Oxford", 51.7520, -1.2577), ("Cambridge", 52.2053, 0.1218), ("York", 53.9600, -1.0873),
 ("Ipswich", 52.0567, 1.1482), ("Peterborough", 52.5695, -0.2405), ("Exeter", 50.7184, -3.5339),
 ("Gloucester", 51.8642, -2.2380), ("Cheltenham", 51.9000, -2.0800), ("Bath", 51.3811, -2.3590),
 ("Bournemouth", 50.7192, -1.8808), ("Poole", 50.7150, -1.9872), ("Blackpool", 53.8175, -3.0357),
 ("Middlesbrough", 54.5742, -1.2349), ("Sunderland", 54.9069, -1.3838), ("Doncaster", 53.5228, -1.1285),
 ("Wakefield", 53.6833, -1.4977), ("Huddersfield", 53.6458, -1.7850), ("Rotherham", 53.4302, -1.3568),
 ("Barnsley", 53.5526, -1.4797), ("Chesterfield", 53.2350, -1.4210), ("Lincoln", 53.2307, -0.5406),
 ("Grimsby", 53.5675, -0.0800), ("Scarborough", 54.2830, -0.3990), ("Whitby", 54.4863, -0.6133),
 ("Bridlington", 54.0830, -0.1900), ("Skegness", 53.1435, 0.3350), ("Great Yarmouth", 52.6083, 1.7300),
 ("Lowestoft", 52.4750, 1.7500), ("Clacton-on-Sea", 51.7900, 1.1500), ("Southend-on-Sea", 51.5400, 0.7100),
 ("Margate", 51.3850, 1.3860), ("Ramsgate", 51.3350, 1.4200), ("Dover", 51.1279, 1.3134),
 ("Folkestone", 51.0800, 1.1700), ("Hastings", 50.8552, 0.5729), ("Eastbourne", 50.7687, 0.2840),
 ("Worthing", 50.8179, -0.3729), ("Chichester", 50.8365, -0.7792), ("Newport, Isle of Wight", 50.7010, -1.2900),
 ("Ryde", 50.7290, -1.1600), ("Shanklin", 50.6300, -1.1800), ("Weymouth", 50.6140, -2.4570),
 ("Torquay", 50.4619, -3.5253), ("Paignton", 50.4350, -3.5650), ("Newquay", 50.4155, -5.0737),
 ("Penzance", 50.1186, -5.5370), ("St Ives", 50.2110, -5.4800), ("Falmouth", 50.1530, -5.0700),
 ("Truro", 50.2632, -5.0510), ("Bude", 50.8270, -4.5450), ("Barnstaple", 51.0800, -4.0600),
 ("Ilfracombe", 51.2080, -4.1200), ("Minehead", 51.2050, -3.4750), ("Weston-super-Mare", 51.3460, -2.9770),
 ("Taunton", 51.0150, -3.1000), ("Salisbury", 51.0688, -1.7945), ("Winchester", 51.0632, -1.3080),
 ("Guildford", 51.2360, -0.5700), ("Maidstone", 51.2720, 0.5290), ("Canterbury", 51.2800, 1.0780),
 ("Colchester", 51.8890, 0.9040), ("Chelmsford", 51.7360, 0.4790), ("St Albans", 51.7520, -0.3360),
 ("Watford", 51.6560, -0.3960), ("Slough", 51.5100, -0.5950), ("Basingstoke", 51.2665, -1.0870),
 ("Crawley", 51.1090, -0.1870), ("Bedford", 52.1360, -0.4670), ("Kettering", 52.3980, -0.7250),
 ("Shrewsbury", 52.7070, -2.7540), ("Telford", 52.6780, -2.4450), ("Worcester", 52.1920, -2.2200),
 ("Hereford", 52.0560, -2.7160), ("Stratford-upon-Avon", 52.1920, -1.7070), ("Warwick", 52.2820, -1.5850),
 ("Stafford", 52.8060, -2.1170), ("Crewe", 53.0990, -2.4420), ("Chester", 53.1900, -2.8900),
 ("Warrington", 53.3900, -2.5970), ("Wigan", 53.5450, -2.6320), ("Bolton", 53.5780, -2.4290),
 ("Stockport", 53.4080, -2.1490), ("Oldham", 53.5410, -2.1180), ("Rochdale", 53.6140, -2.1560),
 ("Salford", 53.4870, -2.2900), ("Southport", 53.6480, -3.0100), ("Lancaster", 54.0470, -2.8010),
 ("Morecambe", 54.0700, -2.8600), ("Kendal", 54.3280, -2.7450), ("Windermere", 54.3800, -2.9050),
 ("Keswick", 54.6010, -3.1340), ("Carlisle", 54.8920, -2.9320), ("Whitehaven", 54.5490, -3.5870),
 ("Barrow-in-Furness", 54.1110, -3.2280), ("Durham", 54.7770, -1.5750), ("Darlington", 54.5230, -1.5590),
 ("Hartlepool", 54.6900, -1.2120), ("Berwick-upon-Tweed", 55.7710, -2.0060), ("Alnwick", 55.4130, -1.7060),
 ("Harrogate", 53.9920, -1.5410), ("Ripon", 54.1380, -1.5230), ("Skipton", 53.9610, -2.0160),
 ("Northallerton", 54.3390, -1.4400), ("Bangor, Gwynedd", 53.2270, -4.1290), ("Llandudno", 53.3240, -3.8270),
 ("Rhyl", 53.3210, -3.4900), ("Colwyn Bay", 53.2960, -3.7280), ("Caernarfon", 53.1400, -4.2750),
 ("Porthmadog", 52.9250, -4.1300), ("Barmouth", 52.7220, -4.0550), ("Aberystwyth", 52.4150, -4.0830),
 ("Cardigan", 52.0820, -4.6580), ("Tenby", 51.6730, -4.7020), ("Pembroke", 51.6740, -4.9170),
 ("Haverfordwest", 51.8010, -4.9700), ("Swansea", 51.6214, -3.9436), ("Cardiff", 51.4816, -3.1791),
 ("Newport, Wales", 51.5842, -2.9977), ("Wrexham", 53.0430, -2.9930), ("Brecon", 51.9470, -3.3900),
 ("Edinburgh", 55.9533, -3.1883), ("Glasgow", 55.8642, -4.2518), ("Aberdeen", 57.1497, -2.0943),
 ("Dundee", 56.4620, -2.9707), ("Inverness", 57.4778, -4.2247), ("Perth", 56.3950, -3.4300),
 ("Stirling", 56.1165, -3.9369), ("Ayr", 55.4586, -4.6292), ("Oban", 56.4150, -5.4710),
 ("Fort William", 56.8190, -5.1050), ("Dumfries", 55.0700, -3.6030), ("St Andrews", 56.3400, -2.7950),
 ("Pitlochry", 56.7030, -3.7300), ("Belfast", 54.5973, -5.9301), ("Londonderry", 54.9966, -7.3086),
]


def clean(name):
    n = re.sub(r',?\s*unparished area$', '', str(name or '').strip(), flags=re.I)
    return re.sub(r'\s+', ' ', n).strip()


def key(name):
    return re.sub(r'[^a-z0-9]+', ' ', str(name).lower()).strip()


parks = json.load(io.open('parks.json', encoding='utf-8'))
groups = defaultdict(list)
for p in parks:
    t = clean(p.get('town'))
    lat, lon = p.get('Latitude'), p.get('Longitude')
    if not t or not isinstance(lat, (int, float)) or not isinstance(lon, (int, float)):
        continue
    groups[key(t)].append((t, p, lat, lon))

towns = {}
for k, rows in groups.items():
    name = rows[0][0]
    lat = sum(r[2] for r in rows) / len(rows)
    lon = sum(r[3] for r in rows) / len(rows)
    counties = [str(r[1].get('county') or '').strip() for r in rows if r[1].get('county')]
    regions = [str(r[1].get('region') or '').strip() for r in rows if r[1].get('region')]
    towns[k] = {
        'name': name,
        'county': max(set(counties), key=counties.count) if counties else '',
        'region': max(set(regions), key=regions.count) if regions else '',
        'latitude': round(lat, 5),
        'longitude': round(lon, 5),
        'park_count': len(rows),
        'source': 'park-centroid',
    }

added = 0
for name, lat, lon in CURATED:
    k = key(name.split(',')[0])
    if k in towns:
        towns[k]['latitude'] = lat
        towns[k]['longitude'] = lon
        towns[k]['source'] = 'reference'
    else:
        towns[k] = {'name': name, 'county': '', 'region': '', 'latitude': lat,
                    'longitude': lon, 'park_count': 0, 'source': 'reference'}
        added += 1

out = sorted(towns.values(), key=lambda t: t['name'].lower())
io.open('towns.json', 'w', encoding='utf-8').write(json.dumps(out, indent=2, ensure_ascii=False) + '\n')
print(len(out), 'towns;', added, 'curated-only;',
      sum(1 for t in out if t['source'] == 'reference'), 'at a true town centre')
