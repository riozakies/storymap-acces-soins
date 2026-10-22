"""Prépare les données de la storymap à partir du projet QGIS atlas_departemental."""
import json, re, unicodedata, sys
from pathlib import Path
import geopandas as gpd, pandas as pd, numpy as np, warnings
warnings.filterwarnings("ignore")
SRC = Path(sys.argv[1]); OUT = Path(sys.argv[2])
(OUT / "data" / "dept").mkdir(parents=True, exist_ok=True)

def key(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]", "", s)
    return {"mafouetafamba": "mefouetafamba", "malentouen": "malantouen"}.get(s, s)

def slug(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")

REG_FR = {"1": "Centre", "2": "Est", "3": "Extrême-Nord", "4": "Nord", "5": "Nord-Ouest", "6": "Ouest",
          "7": "Sud-Ouest", "8": "Adamaoua", "9": "Littoral", "10": "Sud"}
dl = pd.read_excel(SRC / "departement_list.xlsx")
dl["k"] = dl.Nom_Dept.map(key)
com = gpd.read_file(SRC / "commune_arrondissement.shp").to_crs(4326)
com["k"] = com.Nom_Dept.map(key)
assert set(com.k) == set(dl.k), set(com.k) ^ set(dl.k)
# --- départements = union des communes (le shapefile département du projet ne contient que le Sud-Ouest)
dep = com.dissolve("k").reset_index()[["k", "geometry"]]
dep["geometry"] = dep.geometry.buffer(0)
dep = dep.merge(dl.rename(columns={"%pop_couvrte": "pop1h"})[["k", "Nom_Dept", "Code_Dept", "Code_Reg", "pop1h", "population couverte", "population"]], on="k")
REG_FR = {"adamaoua": "Adamaoua", "centre": "Centre", "est": "Est", "extremenord": "Extrême-Nord", "littoral": "Littoral", "nord": "Nord", "northwest": "Nord-Ouest", "nordouest": "Nord-Ouest", "ouest": "Ouest", "sud": "Sud", "southwest": "Sud-Ouest", "sudouest": "Sud-Ouest"}
dep["region"] = dep.k.map(dict(zip(com.k, com.Nom_Region.map(key)))).map(REG_FR)
assert dep.region.notna().all()
dep["slug"] = dep.Nom_Dept.map(slug)

ds = gpd.read_file(SRC / "District_S.shp").to_crs(4326)
dx = pd.read_excel(SRC / "districts.xlsx")
dx["k"] = dx.Nom_Dept.map(key)
dx["kd"] = dx.District_S.map(key)
cb = pd.read_excel(SRC / "commune_bis.xlsx")
cb["k"] = cb.Nom_Dept.map(key)
cb["kc"] = cb.Nom_commun.map(key)
UNMATCHED = {"districts": [], "communes": []}
fo = gpd.read_file(SRC / "fosa_fonc_privee.shp", columns=["Fosa", "categorie", "statut_fos", "District d", "Aire de sa"]).to_crs(4326)
fo = fo[fo.geometry.notna()]
# la couche contient des doublons (même identifiant, même position) : on ne garde qu'une ligne par FOSA
fo_ids = gpd.read_file(SRC / "fosa_fonc_privee.shp", columns=["id"]).loc[fo.index, "id"]
n_avant = len(fo)
fo = fo[~fo_ids.duplicated()]
print(f"FOSA : {n_avant} lignes, {n_avant - len(fo)} doublons supprimés -> {len(fo)}")

# --- règle : un district de santé appartient à UN seul département, celui qui contient la plus grande part de sa surface
M = "ESRI:102022"
ds["geometry"] = ds.geometry.buffer(0)
_ov = gpd.overlay(ds[["District_S", "geometry"]].to_crs(M), dep[["k", "geometry"]].to_crs(M), how="intersection", keep_geom_type=True)
_ov["a"] = _ov.area
_ov["part"] = _ov.a / _ov.District_S.map(_ov.groupby("District_S").a.sum()) * 100
ds["dept_k"] = ds.District_S.map(_ov.sort_values("a").groupby("District_S").k.last())
DEP_NAME = dict(zip(dep.k, dep.Nom_Dept))
# exceptions décidées par le MINSANTE (district -> département), prioritaires sur la règle de la surface
EXCEPTIONS = {"Nkolafamba": "Mefou et Afamba"}
for n, dn in EXCEPTIONS.items():
    ds.loc[ds.District_S == n, "dept_k"] = key(dn)
_mult = _ov[_ov.part >= 1].groupby("District_S").filter(lambda g: len(g) > 1)
A_CHEVAL = {}
for dn, g in _mult.groupby("District_S"):
    lst = [(DEP_NAME[r.k], round(float(r.part))) for r in g.sort_values("part", ascending=False).itertuples()]
    if dn in EXCEPTIONS:   # le département de rattachement en premier
        lst.sort(key=lambda x: x[0] != EXCEPTIONS[dn])
    A_CHEVAL[dn] = lst
xl_dept = dict(zip(dx.kd, dx.k))
print("Districts à cheval :", {k: v for k, v in A_CHEVAL.items()})
print("Rattachement modifié :", [(n, DEP_NAME.get(xl_dept.get(key(n)), "?"), DEP_NAME[k]) for n, k in zip(ds.District_S, ds.dept_k) if xl_dept.get(key(n)) != k])
CAT_PUB = {"hr_hra": ["hr_hra"], "hd": ["hd"], "cma": ["cma"], "csi": ["csi", "infirmerie"]}
def fosa_counts(sel):
    pub = sel[sel.statut_fos.isin(["public", "parapublic"])]
    out = {"nb_fosa": int(len(sel)), "nb_pub": int(len(pub)), "nb_priv": int(len(sel) - len(pub))}
    for c, cats in CAT_PUB.items():
        out[c] = int(pub.categorie.isin(cats).sum())
    return out

# --- indicateurs (mêmes expressions que la mise en page : within(point_on_surface, @atlas_geometry))
def count_within(gdf, poly, pos=True):
    pts = gdf.geometry.representative_point() if pos else gdf.geometry
    return pts.within(poly)

def num(v):
    try:
        f = float(v)
        return None if np.isnan(f) else (int(f) if f.is_integer() else round(f, 1))
    except Exception:
        return None if pd.isna(v) else str(v)

routes = gpd.read_file(SRC / "routes.shp", columns=["label_n"], where="label_n IN ('main road','secondary')").to_crs(4326)
rivers = gpd.read_file(SRC / "cours_eaux.shp", columns=["waterway", "name"]).to_crs(4326)
rivers = rivers[rivers.waterway.isin(["river", "stream", "canal", "wadi"])]

ds_pos = ds.geometry.representative_point(); com_pos = com.geometry.representative_point()
summary = []
FOSA_COLS = ["District_S", "Pop_2026", "public", "hr_hra", "hd", "cma", "csi", "classification", "% Population à 1h FOSA", "communes_couvertes"]
COM_COLS = ["Nom_commun", "public", "prive", "hr_hra", "hd", "cma", "csi"]
def fc(gdf, props, prec=5):
    gdf = gdf.copy()
    return json.loads(gdf[props + ["geometry"]].to_json(drop_id=True, to_wgs84=False))

def round_coords(obj, p=5):
    if isinstance(obj, list):
        return [round_coords(x, p) for x in obj]
    if isinstance(obj, float):
        return round(obj, p)
    if isinstance(obj, dict):
        return {k: round_coords(v, p) for k, v in obj.items()}
    return obj

def district_rows(tab_ds, in_ds_geo, poly):
    """Une ligne par district : FOSA comptées sur les points (doublons retirés), autres colonnes de districts.xlsx."""
    rows = []
    geo = {key(n): g for n, g in zip(in_ds_geo.District_S, in_ds_geo.geometry)}
    geo_name = {key(n): n for n in in_ds_geo.District_S}
    for _, r in tab_ds.iterrows():
        g = geo.get(r.kd)
        txt = lambda c: None if pd.isna(r[c]) or str(r[c]).strip() in ("", "/") else str(r[c]).strip().rstrip(",")
        if g is not None:
            sel = fo[fo.within(g)]
            cnt = fosa_counts(sel)
            hors = int((~sel.within(poly)).sum())
            pop = num(ds.loc[ds.District_S == geo_name[r.kd], "Pop_2026"].iloc[0])
            name = geo_name[r.kd]
        else:   # district absent de la couche cartographique : chiffres de districts.xlsx
            cnt = {"nb_pub": num(r["public"]), "nb_priv": num(r["prive"]), **{c: num(r[c]) for c in CAT_PUB}}
            hors, pop, name = 0, num(r["Pop_2026"]), str(r.District_S)
        parts = A_CHEVAL.get(name, [])
        rows.append([name, pop, cnt["nb_pub"], cnt["nb_priv"], cnt["hr_hra"], cnt["hd"], cnt["cma"], cnt["csi"],
                     txt("classification"), num(r["% Population à 1h FOSA"]), txt("communes_couvertes"), hors,
                     "; ".join(f"{a} {b} %" for a, b in parts)])
    return sorted(rows, key=lambda x: key(x[0]))

for d in dep.itertuples():
    poly = d.geometry
    in_fo = fo[fo.within(poly)]
    in_com = com[com_pos.within(poly)]
    # districts du département = ceux dont la plus grande part est dans le département (+ ceux absents de la carte, d'après districts.xlsx)
    in_ds_geo = ds[ds.dept_k == d.k]
    geo_keys = set(ds.District_S.map(key))
    tab_ds = dx[dx.kd.isin(set(in_ds_geo.District_S.map(key))) | ((dx.k == d.k) & ~dx.kd.isin(geo_keys))]
    # FOSA des districts du département situées hors du département (affichées seulement quand on isole le district)
    union_ds = in_ds_geo.geometry.union_all() if len(in_ds_geo) else None
    fo_hors = fo[~fo.index.isin(in_fo.index) & fo.within(union_ds)] if union_ds is not None else fo.iloc[0:0]
    tab_cb = cb[cb.k == d.k].sort_values("Nom_commun")
    stats = {
        "nom": d.Nom_Dept, "slug": d.slug, "region": d.region, "code": d.Code_Dept,
        "fosa": int(len(in_fo)), "communes": int(len(in_com)), "districts": int(len(tab_ds)),
        "population": int(pd.to_numeric(in_ds_geo.Pop_2026, errors="coerce").sum()), "pop1h": num(d.pop1h),
        "fosa_publiques": int(in_fo.statut_fos.isin(["public", "parapublic"]).sum()),
        "fosa_privees": int(len(in_fo) - in_fo.statut_fos.isin(["public", "parapublic"]).sum()),
        "bbox": [round(v, 4) for v in poly.bounds],
        "tab_districts": district_rows(tab_ds, in_ds_geo, poly),
        "tab_communes": [[num(r[c]) if c != "Nom_commun" else str(r[c]) for c in COM_COLS] for _, r in tab_cb.iterrows()],
        "districts_voisins": [[n, v[0][0], part, int(in_fo.within(ds.loc[ds.District_S == n, "geometry"].iloc[0]).sum())]
                              for n, v in A_CHEVAL.items() for (dn, part) in v[1:] if dn == d.Nom_Dept],
    }
    summary.append(stats)
    buf = poly.buffer(0.02)
    r_clip = gpd.clip(routes[routes.intersects(buf)], buf)
    w_clip = gpd.clip(rivers[rivers.intersects(buf)], buf)
    tol = 0.0004
    # --- relations district <-> commune (recouvrement > 5 % de la plus petite des deux surfaces)
    ds_m, com_m = in_ds_geo.to_crs("ESRI:102022"), in_com.to_crs("ESRI:102022")
    d2c = {n: [] for n in in_ds_geo.District_S}
    c2d = {n: [] for n in in_com.Nom_commun}
    for dn, dg in zip(in_ds_geo.District_S, ds_m.geometry):
        for cn, cg in zip(in_com.Nom_commun, com_m.geometry):
            if not dg.intersects(cg):
                continue
            a = dg.intersection(cg).area
            if a > 0.05 * min(dg.area, cg.area):
                d2c[dn].append(cn)
                c2d[cn].append(dn)
    # FOSA : district et commune d'appartenance (spatial)
    in_fo = in_fo.copy()
    fo_lay_hors = fo_hors.copy()
    if len(fo_lay_hors):
        fo_lay_hors["ds"] = gpd.sjoin(fo_lay_hors[["geometry"]], in_ds_geo[["District_S", "geometry"]], how="left", predicate="within").groupby(level=0)["District_S"].first()
        fo_lay_hors["cm"] = None
    in_fo["ds"] = gpd.sjoin(in_fo[["geometry"]], in_ds_geo[["District_S", "geometry"]], how="left", predicate="within").groupby(level=0)["District_S"].first()
    in_fo["cm"] = gpd.sjoin(in_fo[["geometry"]], in_com[["Nom_commun", "geometry"]], how="left", predicate="within").groupby(level=0)["Nom_commun"].first()
    # --- attributs cliquables : district de santé
    dprops = []
    for _, g in in_ds_geo.iterrows():
        sel = in_fo[in_fo.within(g.geometry)]
        row = dx[(dx.kd == key(g.District_S)) & (dx.k == d.k)]
        if row.empty:
            row = dx[dx.kd == key(g.District_S)]
        if row.empty:
            UNMATCHED["districts"].append(g.District_S)
        r = row.iloc[0] if len(row) else {}
        dprops.append({
            "communes_in": "|".join(sorted(d2c.get(g.District_S, []))),
            "pop": num(g.Pop_2026), "classification": None if not len(row) or pd.isna(r["classification"]) else str(r["classification"]),
            "pop1h": num(r["% Population à 1h FOSA"]) if len(row) else None,
            "communes_couvertes": None if not len(row) or pd.isna(r["communes_couvertes"]) else str(r["communes_couvertes"]).strip().rstrip(","),
            **fosa_counts(fo[fo.within(g.geometry)]),
        })
    ds_layer = in_ds_geo.assign(geometry=in_ds_geo.geometry.simplify(tol)).reset_index(drop=True)
    ds_layer = pd.concat([ds_layer[["District_S", "geometry"]], pd.DataFrame(dprops)], axis=1)
    # --- attributs cliquables : commune
    cprops = []
    for _, g in in_com.iterrows():
        sel = in_fo[in_fo.within(g.geometry)]
        row = cb[(cb.kc == key(g.Nom_commun)) & (cb.k == d.k)]
        if row.empty:
            UNMATCHED["communes"].append(g.Nom_commun)
        r = row.iloc[0] if len(row) else {}
        cprops.append({
            "districts_in": "|".join(sorted(c2d.get(g.Nom_commun, []))),
            "departement": d.Nom_Dept, "nb_fosa": int(len(sel)),
            **{c: (num(r[c]) if len(row) else None) for c in ("public", "prive", "hr_hra", "hd", "cma", "csi")},
        })
    com_layer = in_com.assign(geometry=in_com.geometry.simplify(tol)).reset_index(drop=True)
    com_layer = pd.concat([com_layer[["Nom_commun", "geometry"]], pd.DataFrame(cprops)], axis=1)
    layers = {
        "communes": fc(gpd.GeoDataFrame(com_layer, geometry="geometry", crs=4326), [c for c in com_layer.columns if c != "geometry"]),
        "districts": fc(gpd.GeoDataFrame(ds_layer, geometry="geometry", crs=4326), [c for c in ds_layer.columns if c != "geometry"]),
        "fosa": fc(pd.concat([in_fo.assign(hors=0), fo_lay_hors.assign(hors=1)]).rename(columns={"District d": "district", "Aire de sa": "aire"}), ["Fosa", "categorie", "statut_fos", "district", "aire", "ds", "cm", "hors"]),
        "routes": fc(r_clip.assign(geometry=r_clip.geometry.simplify(tol / 2)).explode(index_parts=False), ["label_n"]),
        "labels": [[str(n), "c", round(pt.x, 4), round(pt.y, 4)] for n, pt in zip(in_com.Nom_commun, in_com.geometry.representative_point())]
                  + [[str(n), "d", round(pt.x, 4), round(pt.y, 4)] for n, pt in zip(in_ds_geo.District_S, in_ds_geo.geometry.representative_point())],
        "rivieres": fc(w_clip.assign(geometry=w_clip.geometry.simplify(tol / 2)).explode(index_parts=False), ["waterway", "name"]),
    }
    (OUT / "data" / "dept" / f"{d.slug}.json").write_text(json.dumps(round_coords(layers), ensure_ascii=False, separators=(",", ":")))

# national
depg = dep.assign(geometry=dep.geometry.simplify(0.003))
nat = fc(depg, ["Nom_Dept", "slug", "region"])
for f, pt in zip(nat["features"], dep.geometry.representative_point()):
    f["properties"]["lbl"] = [round(pt.x, 4), round(pt.y, 4)]
pays = gpd.GeoDataFrame(geometry=[dep.union_all().simplify(0.003)], crs=4326)
(OUT / "data" / "departements.json").write_text(json.dumps(round_coords(nat, 4), ensure_ascii=False, separators=(",", ":")))
(OUT / "data" / "cameroun.json").write_text(json.dumps(round_coords(fc(pays, []), 4), separators=(",", ":")))
order = ["Adamaoua", "Centre", "Est", "Extrême-Nord", "Littoral", "Nord", "Nord-Ouest", "Ouest", "Sud", "Sud-Ouest"]
summary.sort(key=lambda s: (order.index(s["region"]), key(s["nom"])))
(OUT / "data" / "atlas.json").write_text(json.dumps({"regions": order, "departements": summary}, ensure_ascii=False, separators=(",", ":")))
print("Sans correspondance :", UNMATCHED)
pd.DataFrame([{"district": n, "repartition": "; ".join(f"{a} {b} %" for a, b in v), "rattache_a": v[0][0]} for n, v in sorted(A_CHEVAL.items())]).to_csv(OUT / "data" / "districts_a_cheval.csv", index=False)
tot = pd.DataFrame(summary)
print(tot.groupby("region")[["fosa", "communes", "districts", "population"]].sum())
print("TOTAL", tot[["fosa", "communes", "districts", "population"]].sum().to_dict(), "FOSA hors départements:", len(fo) - tot.fosa.sum())
