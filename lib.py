''' Support functions for changewithin.py script.
'''
import time, json, requests, os, sys
import urllib
from lxml import etree
from sets import Set
from ModestMaps.Geo import MercatorProjection, Location, Coordinate
from tempfile import mkstemp

dir_path = os.path.dirname(os.path.abspath(__file__))

def get_state():
    r = requests.get('http://planet.openstreetmap.org/replication/day/state.txt')
    return r.text.split('\n')[1].split('=')[1]

def get_osc(stateurl=None):
    if not stateurl:
        state = get_state()

        # zero-pad state so it can be safely split.
        state = '000000000' + state
        path = '%s/%s/%s' % (state[-9:-6], state[-6:-3], state[-3:])
        stateurl = 'http://planet.openstreetmap.org/replication/day/%s.osc.gz' % path

    sys.stderr.write('downloading %s...\n' % stateurl)
    # prepare a local file to store changes
    handle, filename = mkstemp(prefix='change-', suffix='.osc.gz')
    os.close(handle)
    status = os.system('wget --quiet %s -O %s' % (stateurl, filename))

    if status:
        status = os.system('curl --silent %s -o %s' % (stateurl, filename))
    
    if status:
        raise Exception('Failure from both wget and curl')
    
    sys.stderr.write('extracting %s...\n' % filename)
    os.system('gunzip -f %s' % filename)

    # knock off the ".gz" suffix and return
    return filename[:-3]

# Returns -lon, -lat, +lon, +lat
#
#    +---[+lat]---+
#    |            |
# [-lon]       [+lon]
#    |            |
#    +---[-lat]-- +
def get_bbox(poly):
    box = [200, 200, -200, -200]
    for p in poly:
        if p[0] < box[0]: box[0] = p[0]
        if p[0] > box[2]: box[2] = p[0]
        if p[1] < box[1]: box[1] = p[1]
        if p[1] > box[3]: box[3] = p[1]
    return box

def point_in_box(x, y, box):
    return x > box[0] and x < box[2] and y > box[1] and y < box[3]

def point_in_poly(x, y, poly):
    n = len(poly)
    inside = False
    p1x, p1y = poly[0]
    for i in xrange(n + 1):
        p2x, p2y = poly[i % n]
        if y > min(p1y, p2y):
            if y <= max(p1y, p2y):
                if x <= max(p1x, p2x):
                    if p1y != p2y:
                        xints = (y - p1y) * (p2x - p1x) / (p2y - p1y) + p1x
                    if p1x == p2x or x <= xints:
                        inside = not inside
        p1x, p1y = p2x, p2y
    return inside

def get_extent(gjson):
    extent = {}
    m = MercatorProjection(0)

    b = get_bbox(extract_coords(gjson))
    points = [[b[3], b[0]], [b[1], b[2]]]

    if (points[0][0] - points[1][0] == 0) or (points[1][1] - points[0][1] == 0):
        extent['lat'] = points[0][0]
        extent['lon'] = points[1][1]
        extent['zoom'] = 18
    else:
        i = float('inf')
         
        w = 800
        h = 600
         
        tl = [min(map(lambda x: x[0], points)), min(map(lambda x: x[1], points))]
        br = [max(map(lambda x: x[0], points)), max(map(lambda x: x[1], points))]
         
        c1 = m.locationCoordinate(Location(tl[0], tl[1]))
        c2 = m.locationCoordinate(Location(br[0], br[1]))
         
        while (abs(c1.column - c2.column) * 256.0) < w and (abs(c1.row - c2.row) * 256.0) < h:
            c1 = c1.zoomBy(1)
            c2 = c2.zoomBy(1)
         
        center = m.coordinateLocation(Coordinate(
            (c1.row + c2.row) / 2,
            (c1.column + c2.column) / 2,
            c1.zoom))
        
        extent['lat'] = center.lat
        extent['lon'] = center.lon
        if c1.zoom > 18:
            extent['zoom'] = 18
        else:
            extent['zoom'] = c1.zoom
        
    return extent

def has_building_tag(n):
    return n.find(".//tag[@k='building']") is not None
    
def get_address_tags(tags):
    addr_tags = []
    for t in tags:
        key = t.get('k')
        if key.split(':')[0] == 'addr':
            addr_tags.append(t.attrib)
    return addr_tags
    
def has_address_change(gid, addr, version, elem):
    url = 'http://api.openstreetmap.org/api/0.6/%s/%s/history' % (elem, gid)
    r = requests.get(url)
    if not r.text: return False
    e = etree.fromstring(r.text.encode('utf-8'))
    previous_elem = e.find(".//%s[@version='%s']" % (elem, (version - 1)))
    previous_addr = get_address_tags(previous_elem.findall(".//tag[@k]"))
    if len(addr) != len(previous_addr):
        return True
    else:
        for a in addr:
            if a not in previous_addr: return True
    return False

def load_changeset(changeset):
    changeset['wids'] = list(changeset['wids'])
    changeset['nids'] = changeset['nodes'].keys()
    changeset['addr_chg_nids'] = changeset['addr_chg_nd'].keys()
    changeset['addr_chg_way'] = list(changeset['addr_chg_way'])
    points = map(get_point, changeset['nodes'].values())
    polygons = map(get_polygon, changeset['wids'])
    gjson = geojson_feature_collection(points=points, polygons=polygons)
    extent = get_extent(gjson)
    url = 'http://api.openstreetmap.org/api/0.6/changeset/%s' % changeset['id']
    r = requests.get(url)
    if not r.text: return changeset
    t = etree.fromstring(r.text.encode('utf-8'))
    changeset['details'] = dict(t.find('.//changeset').attrib)
    comment = t.find(".//tag[@k='comment']")
    created_by = t.find(".//tag[@k='created_by']")
    if comment is not None: changeset['comment'] = comment.get('v')
    if created_by is not None: changeset['created_by'] = created_by.get('v')
    changeset['map_img'] = 'http://api.tiles.mapbox.com/v3/lxbarth.map-lxoorpwz/geojson(%s)/%s,%s,%s/600x400.png' % (urllib.quote(json.dumps(gjson)), extent['lon'], extent['lat'], extent['zoom'])
    if len(changeset['map_img']) > 2048:
        changeset['map_img'] = 'http://api.tiles.mapbox.com/v3/lxbarth.map-lxoorpwz/geojson(%s)/%s,%s,%s/600x400.png' % (urllib.quote(json.dumps(bbox_from_geojson(gjson))), extent['lon'], extent['lat'], extent['zoom'])
    changeset['map_link'] = 'http://www.openstreetmap.org/?lat=%s&lon=%s&zoom=%s&layers=M' % (extent['lat'], extent['lon'], extent['zoom'])
    changeset['addr_count'] = len(changeset['addr_chg_way']) + len(changeset['addr_chg_nids'])
    changeset['bldg_count'] = len(changeset['wids'])
    return changeset

def add_changeset(el, cid, changesets):
    if not changesets.get(cid, False):
        changesets[cid] = {
            'id': cid,
            'user': el.get('user'),
            'uid': el.get('uid'),
            'wids': set(),
            'nodes': {},
            'addr_chg_way': set(),
            'addr_chg_nd': {}
        }

def add_node(el, nid, nodes):
    if not nodes.get(nid, False):
        nodes[nid] = {
            'id': nid,
            'lat': float(el.get('lat')),
            'lon': float(el.get('lon'))
        }

def geojson_multi_point(coords):
    return {
      "type": "Feature",
      "properties": {},
      "geometry": {
        "type": "MultiPoint",
        "coordinates": coords
      }
    }

def geojson_polygon(coords):
    return {
      "type": "Feature",
      "properties": {},
      "geometry": {
        "type": "Polygon",
        "coordinates": coords
      }
    }

def extract_coords(gjson):
    coords = []
    for f in gjson['features']:
        if f['geometry']['type'] == 'Polygon':
            for c in f['geometry']['coordinates']:
                coords.extend(c)
        elif f['geometry']['type'] == 'MultiPoint':
            coords.extend(f['geometry']['coordinates'])
        elif f['type'] == 'Point':
            coords.append(f['geometry']['coordinates'])
    return coords

def bbox_from_geojson(gjson):
    b = get_bbox(extract_coords(gjson))
    return geojson_polygon([[[b[0], b[1]], [b[0], b[3]], [b[2], b[3]], [b[2], b[1]], [b[0], b[1]]]])

def get_polygon(wid):
    coords = []
    query = '''
        [out:xml][timeout:25];
        (
          way(%s);
        );
        out body;
        >;
        out skel qt;
    '''
    r = requests.post('http://overpass-api.de/api/interpreter', data=(query % wid))
    if not r.text: return coords
    e = etree.fromstring(r.text.encode('utf-8'))
    lookup = {}
    for n in e.findall(".//node"):
        lookup[n.get('id')] = [float(n.get('lon')), float(n.get('lat'))]
    for n in e.findall(".//nd"):
        if n.get('ref') in lookup:
            coords.append(lookup[n.get('ref')])
    return coords

def get_point(node):
    return [node["lon"], node["lat"]]

def geojson_feature_collection(points=[], polygons=[]):
    collection = {"type": "FeatureCollection", "features": []}
    if len(points):
        collection["features"].append(geojson_multi_point(points))
    for p in polygons:
        if len(p):
            collection["features"].append(geojson_polygon([p]))
    return collection

#
# Templates for generated emails.
#

html_tmpl = '''
<div style='font-family:"Helvetica Neue",Helvetica,Arial,sans-serif;color:#333;max-width:600px;'>
<p style='float:right;'>{{date}}</p>
<h1 style='margin-bottom:10px;'>Summary</h1>
{{#stats}}
<ul style='font-size:15px;line-height:17px;list-style:none;margin-left:0;padding-left:0;'>
<li>Total changesets: <strong>{{total}}</strong></li>
<li>Total address changes: <strong>{{addresses}}</strong></li>
<li>Total building footprint changes: <strong>{{buildings}}</strong></li>
</ul>
{{#limit_exceed}}
<p style='font-size:13px;font-style:italic;'>{{limit_exceed}}</p>
{{/limit_exceed}}
{{/stats}}
{{#changesets}}
<h2 style='border-bottom:1px solid #ddd;padding-top:15px;padding-bottom:8px;'>Changeset <a href='http://openstreetmap.org/browse/changeset/{{id}}' style='text-decoration:none;color:#3879D9;'>#{{id}}</a></h2>
<p style='font-size:14px;line-height:17px;margin-bottom:20px;'>
<a href='http://openstreetmap.org/user/{{#details}}{{user}}{{/details}}' style='text-decoration:none;color:#3879D9;font-weight:bold;'>{{#details}}{{user}}{{/details}}</a>: {{comment}}
</p>
<p style='font-size:14px;line-height:17px;margin-bottom:0;'>
{{#bldg_count}}Changed buildings ({{bldg_count}}): {{#wids}}<a href='http://openstreetmap.org/browse/way/{{.}}/history' style='text-decoration:none;color:#3879D9;'>#{{.}}</a> {{/wids}}{{/bldg_count}}
</p>
<p style='font-size:14px;line-height:17px;margin-top:5px;margin-bottom:20px;'>
{{#addr_count}}Changed addresses ({{addr_count}}): {{#addr_chg_nids}}<a href='http://openstreetmap.org/browse/node/{{.}}/history' style='text-decoration:none;color:#3879D9;'>#{{.}}</a> {{/addr_chg_nids}}{{#addr_chg_way}}<a href='http://openstreetmap.org/browse/way/{{.}}/history' style='text-decoration:none;color:#3879D9;'>#{{.}}</a> {{/addr_chg_way}}{{/addr_count}}
</p>
<a href='{{map_link}}'><img src='{{map_img}}' style='border:1px solid #ddd;' /></a>
{{/changesets}}
</div>
'''

text_tmpl = '''
### Summary ###
{{date}}

{{#stats}}
Total changesets: {{total}}
Total building footprint changes: {{buildings}}
Total address changes: {{addresses}}
{{#limit_exceed}}

{{limit_exceed}}

{{/limit_exceed}}
{{/stats}}

{{#changesets}}
--- Changeset #{{id}} ---
URL: http://openstreetmap.org/browse/changeset/{{id}}
User: http://openstreetmap.org/user/{{#details}}{{user}}{{/details}}
Comment: {{comment}}

{{#bldg_count}}Changed buildings ({{bldg_count}}): {{wids}}{{/bldg_count}}
{{#addr_count}}Changed addresses ({{addr_count}}): {{addr_chg_nids}} {{addr_chg_way}}{{/addr_count}}
{{/changesets}}
'''
